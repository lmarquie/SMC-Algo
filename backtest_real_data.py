import pandas as pd
import numpy as np
from typing import Dict, List, Optional
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
from datetime import datetime, timedelta, timezone
import requests
import asyncio
import json
from polygon import RESTClient

from config import *
from structure_analysis import StructureAnalyzer
from trading_strategy import FVGStrategy
from hyperliquid.info import Info
from hyperliquid.utils import constants
from hyperliquid_client import HyperliquidClient


class RealDataBacktester:
    def __init__(self, config: Dict):
        self.config = config
        self.strategy = FVGStrategy(config, send_notifications=False)
        self.analyzer = StructureAnalyzer(lookback=config.get('BOS_LOOKBACK', 10))

        # Initialize Hyperliquid Info client with error handling
        try:
            self.info_client = Info(constants.MAINNET_API_URL, skip_ws=True)
            self.api_available = True
        except Exception as e:
            print(f"Warning: Could not connect to Hyperliquid API: {e}")
            print("Will use synthetic data for backtesting")
            self.info_client = None
            self.api_available = False

        # Backtest results
        self.trades = []
        self.equity_curve = []
        self.current_position = None
        self.initial_balance = 10000  # $10,000 starting capital
        self.current_balance = self.initial_balance
        self.last_stop_idx = -10  # Track last stop loss exit index for cooldown

        # Setup logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

        # Add leverage mapping to config
        self.config['MAX_LEVERAGE'] = MAX_LEVERAGE


    async def fetch_polygon_data(self, symbol: str):

        with open('market_data/recent_avax.json', 'r') as f:
            aggs_list = json.load(f)

        df = pd.DataFrame()

        # Create DataFrame with required columns
        df['open'] = [agg['open'] for agg in aggs_list]
        df['high'] = [agg['high'] for agg in aggs_list]
        df['low'] = [agg['low'] for agg in aggs_list]
        df['close'] = [agg['close'] for agg in aggs_list]
        df['T'] = [agg['T'] for agg in aggs_list]

        # Convert timestamp to datetime and set as index
        df['T'] = pd.to_datetime(df['T'], unit='ms')
        df.set_index('T', inplace=True)

        self.logger.info(f"✅ Successfully loaded {len(df)} candles from JSON file for {symbol}")
        return df


    async def fetch_hyperliquid_data(self, symbol: str):
        # Fetch exactly 5000 candles
        target_candles = 5000

        client = HyperliquidClient(api_key="0xa90b4285bc34a56a8b102b71d18bd2a82f7e7b464965e5d3a9e064f4eb7ad4df")
        start_time = int(datetime.strptime("2025-07-29", "%Y-%m-%d").timestamp() * 1000)
        end_time = int(datetime.strptime("2025-07-30", "%Y-%m-%d").timestamp() * 1000)

        # Fetch the most recent 5000 candles
        df = await client.get_ohlcv(
            "AVAX",
            timeframe="1m",
            limit=target_candles,
            #start_time=start_time,
            #end_time=end_time,
        )

        # Ensure we have exactly 5000 candles (or as many as available)
        if len(df) > target_candles:
            df = df.tail(target_candles)

        df = df[["open", "high", "low", "close", "T"]]

        self.logger.info(f"✅ Successfully fetched {len(df)} real candles for {symbol}")
        return df


    def fetch_data(self, symbol):
        df = self.fetch_polygon_data(symbol)
        #df = self.fetch_hyperliquid_data(symbol)
        return df


    async def run_backtest(self, symbol):
        """Run backtest on real market data"""
        self.logger.info(f"Starting real data backtest for {symbol}...")

        # Initialize results
        self.trades = []
        self.current_position = None
        self.current_balance = self.initial_balance

        # Fetch real market data
        data = await self.fetch_data(symbol)

        # Create HTF data by resampling
        htf_data = data.resample('15T').agg({ ### CHANGE TO 5 MINUTES
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
        }).dropna()

        self.logger.info(f"Running backtest on {len(data)} candles...")

        # Run through each candle
        for i in range(50, len(data)):  # Start from 50 to have enough history

            if i % 50 == 0:
                print(f"Current Iteration: {i}/{len(data) - 1}, Balance: ${self.current_balance:.2f}")
            current_candle = data.iloc[i]
            current_price = current_candle['close']
            current_low = current_candle['low']
            current_high = current_candle['high']

            # Get data up to current point
            current_data = data.iloc[max(0, i - 49):i + 1]

            # Get corresponding HTF data
            current_time = current_candle.name
            htf_end_idx = htf_data.index.get_indexer([current_time], method='ffill')[0]
            current_htf_data = htf_data.iloc[max(0, htf_end_idx - 49):htf_end_idx + 1]

            # --- HARD STOP LOSS ENFORCEMENT ---
            if self.current_position:
                stop_loss = self.current_position['stop_loss']
                direction = self.current_position['direction']

                # Update trailing stop BEFORE checking stop loss
                self.strategy.update_trailing_stop(current_data, self.current_position)

                # Check if stop loss is hit
                if direction == 'long' and current_low <= stop_loss:
                    self._close_position(stop_loss, current_candle.name, "Stop Loss")
                elif direction == 'short' and current_high >= stop_loss:
                    self._close_position(stop_loss, current_candle.name, "Stop Loss")

            # Check for new entry if no position
            if not self.current_position:
                verbose = 1 if i % 50 == 0 else 0
                setup = self.strategy.check_entry_conditions(current_data, current_htf_data, verbose)
                if setup:
                    setup['symbol'] = symbol  # Add symbol to setup
                    self._open_position(setup, current_price, current_candle.name)

        # Close any remaining position
        if self.current_position:
            final_price = data['close'].iloc[-1]
            self._close_position(final_price, data.index[-1], "End of backtest")

        self.logger.info(f"Real data backtest completed. Total trades: {len(self.trades)}")
        return self.trades


    def _open_position(self, setup: Dict, current_price: float, timestamp):
        """Open a new position"""
        try:
            # Calculate position size
            risk_amount = abs(setup['entry_price'] - setup['stop_loss'])
            position_size, adjusted_stop = self._calculate_position_size(risk_amount, setup['entry_price'], setup,
                                                                         setup['symbol'])

            # Use adjusted stop if it was changed
            final_stop = adjusted_stop if adjusted_stop != setup['stop_loss'] else setup['stop_loss']

            # Calculate expected loss if stop is hit
            final_risk_amount = abs(setup['entry_price'] - final_stop)
            expected_loss = position_size * final_risk_amount

            self.current_position = {
                'direction': setup['direction'],
                'entry_price': setup['entry_price'],
                'stop_loss': final_stop,
                'take_profit': setup.get('take_profit'),
                'size': position_size,
                'entry_time': timestamp,
                'reason': setup['reason'],
                'symbol': setup['symbol']
            }

            self.logger.info(f"Position opened: {self.current_position}")
            self.logger.info(
                f"Risk amount: ${risk_amount:.4f}, Position size: {position_size:.4f}, Expected loss if stopped: ${expected_loss:.2f}")

        except Exception as e:
            self.logger.error(f"Error opening position: {e}")


    def _should_exit_position(self, data: pd.DataFrame, current_price: float) -> bool:
        """Check if position should be exited"""
        if not self.current_position:
            return False

        try:
            return self.strategy.should_exit_position(data, self.current_position)
        except Exception as e:
            self.logger.error(f"Error checking exit conditions: {e}")
            return False


    def _close_position(self, current_price: float, timestamp, reason: str):
        """Close current position"""
        if not self.current_position:
            return
        try:
            # Always use 'Stop Loss Hit' as the reason
            reason = "Stop Loss Hit"
            # Calculate P&L
            if self.current_position['direction'] == 'long':
                pnl_pct = (current_price - self.current_position['entry_price']) / self.current_position['entry_price']
            else:
                pnl_pct = (self.current_position['entry_price'] - current_price) / self.current_position['entry_price']
            # Calculate dollar P&L - FIXED: Use position size × price difference
            price_diff = abs(current_price - self.current_position['entry_price'])
            pnl_dollar = self.current_position['size'] * price_diff
            # Apply direction
            if self.current_position['direction'] == 'long':
                pnl_dollar = pnl_dollar if current_price > self.current_position['entry_price'] else -pnl_dollar
            else:
                pnl_dollar = pnl_dollar if current_price < self.current_position['entry_price'] else -pnl_dollar
            # Debug P&L calculation
            self.logger.info(
                f"P&L Debug: Entry: ${self.current_position['entry_price']:.4f}, Exit: ${current_price:.4f}")
            self.logger.info(
                f"P&L Debug: Price diff: ${price_diff:.4f}, Position size: {self.current_position['size']:.4f}")
            self.logger.info(f"P&L Debug: Raw P&L: ${pnl_dollar:.2f}")
            # Update balance
            self.current_balance += pnl_dollar
            # Record trade
            trade = {
                'entry_time': self.current_position['entry_time'],
                'exit_time': timestamp,
                'direction': self.current_position['direction'],
                'entry_price': self.current_position['entry_price'],
                'exit_price': current_price,
                'size': self.current_position['size'],
                'pnl_pct': pnl_pct,
                'pnl_dollar': pnl_dollar,
                'reason': reason,
                'exit_reason': reason,
                'symbol': self.current_position['symbol']
            }
            self.trades.append(trade)
            self.logger.info(f"Position closed: {pnl_pct:.4f} ({pnl_dollar:.2f}) - {reason}")
            # Reset position
            self.current_position = None
        except Exception as e:
            self.logger.error(f"Error closing position: {e}")


    def _calculate_position_size(self, risk_amount: float, entry_price: float, setup: Dict,
                                 symbol: str = "SOL-USD") -> tuple:
        """Calculate position size based on risk management rules with capital and leverage constraints"""
        target_risk = 150  # $150 fixed risk

        # Position size = Target Risk / Price Risk per Unit
        # This guarantees we risk exactly $150
        position_size = target_risk / risk_amount

        # Calculate position value (size × entry price)
        position_value = position_size * entry_price

        # Get leverage for this symbol
        leverage = self.config['MAX_LEVERAGE'].get(symbol, 20)  # Default to 20x if not found

        # Capital constraints: $10,000 capital with leverage = max position value
        max_position_value = 10000 * leverage  # Dynamic based on symbol leverage

        # Check if position value exceeds maximum allowed
        if position_value > max_position_value:
            # First, try to double the stop loss distance to reduce position size
            original_stop = setup['stop_loss']
            direction = setup['direction']

            if direction == 'long':
                # Double the distance from entry to stop (widen stop down)
                new_stop_distance = (entry_price - original_stop) * 2
                new_stop = entry_price - new_stop_distance
            else:  # short
                # Double the distance from entry to stop (widen stop up)
                new_stop_distance = (original_stop - entry_price) * 2
                new_stop = entry_price + new_stop_distance

            # Recalculate position size with new stop
            new_risk_amount = abs(entry_price - new_stop)
            position_size = target_risk / new_risk_amount
            position_value = position_size * entry_price

            # Check if this fits within capital constraints
            if position_value <= max_position_value:
                self.logger.warning(
                    f"Stop loss widened to fit capital constraints. New stop: ${new_stop:.4f} (was ${original_stop:.4f})")
                return position_size, new_stop
            else:
                # If still too large, scale down position size as last resort
                position_size = max_position_value / entry_price
                actual_risk = position_size * new_risk_amount
                self.logger.warning(
                    f"Position size reduced due to capital constraints. Risk: ${actual_risk:.2f} instead of $150")
                return position_size, new_stop

        return position_size, setup['stop_loss']


async def run_real_data_backtest(symbol):
    """Run the real data backtest for all 3 cryptocurrencies"""
    from config import SYMBOLS, HYPERLIQUID_API_KEY, HYPERLIQUID_SUBACCOUNT, TIMEFRAME, HTF_TIMEFRAME, POSITION_SIZE, \
        BOS_LOOKBACK, DISPLACEMENT_THRESHOLD, STOP_LOSS_BUFFER, TAKE_PROFIT_RATIO, RISK_PER_TRADE, \
        TRAILING_CONFIRMATION_CANDLES

    config = {
        'HYPERLIQUID_API_KEY': HYPERLIQUID_API_KEY,
        'HYPERLIQUID_SUBACCOUNT': HYPERLIQUID_SUBACCOUNT,
        'SYMBOLS': SYMBOLS,  # Use all symbols from config
        'TIMEFRAME': TIMEFRAME,
        'HTF_TIMEFRAME': HTF_TIMEFRAME,
        'POSITION_SIZE': POSITION_SIZE,
        'BOS_LOOKBACK': BOS_LOOKBACK,
        'DISPLACEMENT_THRESHOLD': DISPLACEMENT_THRESHOLD,
        'STOP_LOSS_BUFFER': STOP_LOSS_BUFFER,
        'TAKE_PROFIT_RATIO': TAKE_PROFIT_RATIO,
        'RISK_PER_TRADE': RISK_PER_TRADE,
        'TRAILING_CONFIRMATION_CANDLES': TRAILING_CONFIRMATION_CANDLES,
        'DAILY_LOSS_LIMIT': 1000,  # $1000 daily loss limit
        'LEVERAGE': 20,  # 20x leverage
        'TRAILING_STOP': True,
        'MIN_VOLUME': 1000,
        'MIN_FVG_SIZE': 0.5,
        'MAX_FVG_SIZE': 5.0,
        'FVG_TIMEOUT': 100,
        'MSS_CONFIRMATION': 3,
        'BOS_CONFIRMATION': 2
    }

    backtester = RealDataBacktester(config)

    # Run backtest for all symbols
    all_trades = []
    all_equity_curves = []

    print(f"\n{'=' * 80}")
    print(f"📊 BACKTESTING {symbol}")
    print(f"{'=' * 80}")

    # Reset strategy state for each symbol
    backtester.strategy = FVGStrategy(config, send_notifications=False)  # Fresh strategy instance
    backtester.current_position = None
    backtester.trades = []

    trades = await backtester.run_backtest(symbol=symbol)
    print(len(trades))

    # Add symbol info to trades
    for trade in trades:
        trade['symbol'] = symbol

    all_trades.extend(trades)

    # Print individual symbol results
    if trades:
        symbol_pnl = sum(trade['pnl_dollar'] for trade in trades)
        symbol_trades = len(trades)
        symbol_wins = len([t for t in trades if t['pnl_dollar'] > 0])
        symbol_win_rate = (symbol_wins / symbol_trades) * 100 if symbol_trades > 0 else 0

        print(f"{symbol} Results:")
        print(f"  Trades: {symbol_trades}")
        print(f"  P&L: ${symbol_pnl:.2f}")
        print(f"  Win Rate: {symbol_win_rate:.1f}%")
    else:
        print(f"{symbol}: No trades")

    # Small delay between symbols
    await asyncio.sleep(1)

    # Print combined results
    print("\n" + "=" * 70)
    print("🏁 MULTI-SYMBOL REAL DATA BACKTEST SUMMARY")
    print("=" * 70)

    initial_balance = 10000
    total_trades = len(all_trades)

    print(f"Symbol Traded: {symbol}")
    print(f"Initial Balance: ${initial_balance:,.2f}")
    print(f"Total Trades: {total_trades}")

    if all_trades:
        # Calculate overall performance
        total_pnl = sum(trade['pnl_dollar'] for trade in all_trades)
        final_balance = initial_balance + total_pnl
        total_return = (total_pnl / initial_balance) * 100

        winning_trades = [t for t in all_trades if t['pnl_dollar'] > 0]
        losing_trades = [t for t in all_trades if t['pnl_dollar'] < 0]

        win_rate = (len(winning_trades) / total_trades) * 100
        avg_win = np.mean([t['pnl_dollar'] for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t['pnl_dollar'] for t in losing_trades]) if losing_trades else 0

        total_wins = sum([t['pnl_dollar'] for t in winning_trades])
        total_losses = abs(sum([t['pnl_dollar'] for t in losing_trades]))
        profit_factor = total_wins / total_losses if total_losses > 0 else 0

        avg_rr = avg_win / 100

        print(f"Final Balance: ${final_balance:,.2f}")
        print(f"Total P&L: ${total_pnl:.2f}")
        print(f"Total Return: {total_return:.2f}%")
        print(f"Winning Trades: {len(winning_trades)}")
        print(f"Losing Trades: {len(losing_trades)}")
        print(f"Win Rate: {win_rate:.2f}%")
        print(f"Average Win: ${avg_win:.2f}")
        print(f"Average Loss: ${avg_loss:.2f}")
        print(f"Profit Factor: {profit_factor:.2f}")
        print(f"Average R:R: {avg_rr:.2f}")  # Add average R:R
    else:
        print("No trades executed")
        print("Final Balance: $10,000.00")
        print("Total P&L: $0.00")
        print("Total Return: 0.00%")

    print("=" * 70)


SYMBOL = "AVAX"
if __name__ == "__main__":
    asyncio.run(run_real_data_backtest(SYMBOL))