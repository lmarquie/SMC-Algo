import pandas as pd
import numpy as np
from typing import Dict, List, Optional
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
from datetime import datetime, timedelta, timezone
import requests
import asyncio

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

    async def fetch_real_data(self, symbol: str, days: int = 30) -> pd.DataFrame:
        """Fetch exactly 5000 candles of real market data"""
        if not self.api_available:
            self.logger.info("API not available, using synthetic data")
            return self._create_synthetic_data(symbol, days)

        try:
            # Fetch exactly 5000 candles
            target_candles = 5000

            client = HyperliquidClient(api_key="0xa90b4285bc34a56a8b102b71d18bd2a82f7e7b464965e5d3a9e064f4eb7ad4df")

            # Fetch the most recent 5000 candles
            df = await client.get_ohlcv(
                symbol,
                timeframe="1m",
                limit=target_candles
            )

            if df.empty:
                self.logger.error("No candle data received")
                return self._create_synthetic_data(symbol, days)

            # Ensure we have exactly 5000 candles (or as many as available)
            if len(df) > target_candles:
                df = df.tail(target_candles)

            self.logger.info(f"✅ Successfully fetched {len(df)} real candles for {symbol}")
            return df

        except Exception as e:
            self.logger.error(f"Error fetching real data: {e}")
            return self._create_synthetic_data(symbol, days)

    async def fetch_real_data_with_offset(self, symbol: str, days: int = 30, session_offset: int = 0) -> pd.DataFrame:
        """Fetch exactly 5000 candles with time offset for different sessions"""
        if not self.api_available:
            self.logger.info("API not available, using synthetic data")
            return self._create_synthetic_data(symbol, days)

        try:
            # Fetch exactly 5000 candles
            target_candles = 5000

            # Calculate time offset for different sessions
            # Each session gets data from a different time period
            base_end_time = datetime.now(timezone.utc)
            offset_days = session_offset * 3  # 3 days offset per session
            end_time = base_end_time - timedelta(days=offset_days)

            self.logger.info(f"Session {session_offset + 1}: Fetching 5000 candles ending at {end_time}")

            client = HyperliquidClient(api_key="")

            # Calculate start and end times for the 5000 candles
            end_time_ms = int(end_time.timestamp() * 1000)
            start_time_ms = end_time_ms - (target_candles * 60 * 1000)  # 5000 minutes back

            # Fetch the 5000 candles in the specified time range
            df = await client.get_ohlcv(
                symbol,
                timeframe="1m",
                start_time=start_time_ms,
                end_time=end_time_ms
            )

            if df.empty:
                self.logger.error("No candle data received")
                return self._create_synthetic_data(symbol, days)

            # Ensure we have exactly 5000 candles (or as many as available)
            if len(df) > target_candles:
                df = df.tail(target_candles)

            self.logger.info(f"✅ Successfully fetched {len(df)} real candles for session {session_offset + 1}")
            return df

        except Exception as e:
            self.logger.error(f"Error fetching real data with offset: {e}")
            return self._create_synthetic_data(symbol, days)

    def _create_synthetic_data(self, symbol: str, days: int) -> pd.DataFrame:
        """Create realistic synthetic data as fallback"""
        self.logger.info("Creating realistic synthetic data as fallback...")

        # Current SOL price (approximate)
        current_price = 100  # Approximate SOL price

        # Generate realistic data with crypto-like characteristics
        dates = pd.date_range(end=datetime.now(), periods=days * 24 * 60, freq='1min')
        np.random.seed(42)

        prices = [current_price]
        volumes = []

        # Crypto-like volatility parameters
        base_volatility = 0.003  # 0.3% base volatility
        trend_strength = 0.1  # Slight trend tendency
        volume_volatility = 0.5  # Volume variation

        for i in range(1, len(dates)):
            # Add some trend and mean reversion
            trend = trend_strength * np.sin(i / 100)  # Cyclical trend
            volatility = base_volatility * (1 + 0.5 * np.sin(i / 50))  # Variable volatility

            # Price movement with trend
            change = np.random.normal(trend, volatility)
            new_price = prices[-1] * (1 + change)

            # Ensure price doesn't go negative
            new_price = max(new_price, 1.0)
            prices.append(new_price)

            # Generate realistic volume
            base_volume = 5000
            volume_change = np.random.normal(0, volume_volatility)
            volume = base_volume * (1 + volume_change) * (1 + abs(change) * 10)  # Higher volume on big moves
            volume = max(volume, 100)  # Minimum volume
            volumes.append(volume)

        # Create OHLC data
        data = pd.DataFrame({
            'open': prices[:-1],
            'close': prices[1:],
            'volume': volumes
        }, index=dates[1:])

        # Add realistic high/low with proper OHLC relationship
        for i in range(len(data)):
            open_price = data['open'].iloc[i]
            close_price = data['close'].iloc[i]

            # Calculate body size
            body_size = abs(close_price - open_price)

            # Add realistic wicks (highs and lows)
            wick_size = body_size * np.random.uniform(0.5, 3.0)

            if close_price > open_price:  # Bullish candle
                high = close_price + wick_size * np.random.uniform(0.3, 1.0)
                low = open_price - wick_size * np.random.uniform(0.3, 1.0)
            else:  # Bearish candle
                high = open_price + wick_size * np.random.uniform(0.3, 1.0)
                low = close_price - wick_size * np.random.uniform(0.3, 1.0)

            data.loc[data.index[i], 'high'] = high
            data.loc[data.index[i], 'low'] = low

        # Ensure OHLC relationship is maintained
        data['high'] = data[['open', 'high', 'close']].max(axis=1)
        data['low'] = data[['open', 'low', 'close']].min(axis=1)

        # Add some market structure (swing highs and lows)
        for i in range(20, len(data) - 20):
            # Create some swing points
            if i % 100 == 0:  # Every ~100 minutes
                if np.random.random() > 0.5:
                    # Create swing high
                    data.loc[data.index[i], 'high'] *= 1.02
                    data.loc[data.index[i], 'close'] = data.loc[data.index[i], 'high'] * 0.99
                else:
                    # Create swing low
                    data.loc[data.index[i], 'low'] *= 0.98
                    data.loc[data.index[i], 'close'] = data.loc[data.index[i], 'low'] * 1.01

        self.logger.info(f"Created {len(data)} candles of realistic synthetic data")
        return data

    async def run_backtest(self, symbol: str = "SOL", days: int = 7) -> tuple:
        """Run backtest on real market data"""
        self.logger.info(f"Starting real data backtest for {symbol}...")

        # Initialize results
        self.trades = []
        self.equity_curve = []
        self.current_position = None
        self.current_balance = self.initial_balance

        # Fetch real market data
        data = await self.fetch_real_data(symbol, days)
        if data.empty:
            self.logger.error("Failed to fetch market data")
            return [], []

        # Create HTF data by resampling
        htf_data = data.resample('15T').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()

        self.logger.info(f"Running backtest on {len(data)} candles...")

        # Run through each candle
        for i in range(50, len(data)):  # Start from 50 to have enough history

            print(f"Current Iteration: {i}/{len(data) - 1}, Balance: ${self.current_balance:.2f}")
            current_candle = data.iloc[i]
            current_price = current_candle['close']
            current_low = current_candle['low']
            current_high = current_candle['high']

            # Get data up to current point
            current_data = data.iloc[:i + 1]

            # Get corresponding HTF data
            current_time = current_candle.name
            htf_end_idx = htf_data.index.get_indexer([current_time], method='ffill')[0]
            current_htf_data = htf_data.iloc[:htf_end_idx + 1]

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
                setup = self._check_entry_setup(current_data, current_htf_data, current_price)
                if setup:
                    setup['symbol'] = symbol  # Add symbol to setup
                    self._open_position(setup, current_price, current_candle.name)

            # Only update equity curve if in position
            self._update_equity_curve(current_price, current_candle.name)

        # Close any remaining position
        if self.current_position:
            final_price = data['close'].iloc[-1]
            self._close_position(final_price, data.index[-1], "End of backtest")

        self.logger.info(f"Real data backtest completed. Total trades: {len(self.trades)}")
        return self.trades, self.equity_curve

    async def run_backtest_with_time_offset(self, symbol: str = "SOL", days: int = 7, session_offset: int = 0) -> tuple:
        """Run backtest on real data with different time periods for each session"""
        self.logger.info(f"Starting backtest for session {session_offset + 1} with time offset...")

        # Initialize results
        self.trades = []
        self.equity_curve = []
        self.current_position = None
        self.current_balance = self.initial_balance

        # Fetch real market data with time offset
        data = await self.fetch_real_data_with_offset(symbol, days, session_offset)
        if data.empty:
            self.logger.error("Failed to fetch market data")
            return [], []

        # Create HTF data by resampling
        htf_data = data.resample('15T').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()

        self.logger.info(f"Running backtest on {len(data)} candles for session {session_offset + 1}...")

        # Run through each candle
        for i in range(50, len(data)):  # Start from 50 to have enough history
            current_candle = data.iloc[i]
            current_price = current_candle['close']
            current_low = current_candle['low']
            current_high = current_candle['high']

            # Get data up to current point
            current_data = data.iloc[:i + 1]

            # Get corresponding HTF data
            current_time = current_candle.name
            htf_end_idx = htf_data.index.get_indexer([current_time], method='ffill')[0]
            current_htf_data = htf_data.iloc[:htf_end_idx + 1]

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
                setup = self._check_entry_setup(current_data, current_htf_data, current_price)
                if setup:
                    setup['symbol'] = symbol  # Add symbol to setup
                    self._open_position(setup, current_price, current_candle.name)

            # Only update equity curve if in position
            self._update_equity_curve(current_price, current_candle.name)

            # After stop loss check, if position still exists, update trailing stop
            if self.current_position:
                # Update trailing stop
                self.strategy.update_trailing_stop(current_data, self.current_position)

        # Close any remaining position
        if self.current_position:
            self._close_position(current_price, data.index[-1], "End of Data")

        return self.trades, self.equity_curve

    def _check_entry_setup(self, data: pd.DataFrame, htf_data: pd.DataFrame, current_price: float) -> Optional[Dict]:
        """Check for entry setup at current point"""
        try:
            return self.strategy.check_entry_conditions(data, htf_data)
        except Exception as e:
            self.logger.error(f"Error checking entry setup: {e}")
            return None

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

    def _update_equity_curve(self, current_price: float, timestamp):
        """Update equity curve with current position value"""
        equity = self.current_balance

        if self.current_position:
            # Add unrealized P&L
            if self.current_position['direction'] == 'long':
                unrealized_pnl = (current_price - self.current_position['entry_price']) / self.current_position[
                    'entry_price']
            else:
                unrealized_pnl = (self.current_position['entry_price'] - current_price) / self.current_position[
                    'entry_price']

            position_value = self.current_position['size'] * self.current_position['entry_price']
            equity += unrealized_pnl * position_value

        self.equity_curve.append({
            'timestamp': timestamp,
            'equity': equity,
            'price': current_price
        })

    def _calculate_performance(self) -> Dict:
        """Calculate performance metrics"""
        if not self.trades:
            return {
                'total_return': 0,
                'total_trades': 0,
                'win_rate': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'profit_factor': 0,
                'max_drawdown': 0,
                'avg_rr': 0,
                'initial_balance': self.initial_balance,
                'final_balance': self.current_balance
            }

        # Basic metrics
        total_trades = len(self.trades)
        winning_trades = [t for t in self.trades if t['pnl_dollar'] > 0]
        losing_trades = [t for t in self.trades if t['pnl_dollar'] < 0]

        win_rate = len(winning_trades) / total_trades if total_trades > 0 else 0

        avg_win = np.mean([t['pnl_dollar'] for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t['pnl_dollar'] for t in losing_trades]) if losing_trades else 0

        total_profit = sum([t['pnl_dollar'] for t in winning_trades])
        total_loss = abs(sum([t['pnl_dollar'] for t in losing_trades]))

        profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')

        # Calculate average R:R ratio
        rr_ratios = []
        for trade in self.trades:
            # Calculate risk (entry to stop loss distance)
            risk_distance = abs(trade['entry_price'] - trade.get('stop_loss', trade['entry_price'] * 0.98))
            # Calculate reward (actual P&L distance)
            reward_distance = abs(trade['exit_price'] - trade['entry_price'])

            if risk_distance > 0:
                rr_ratio = reward_distance / risk_distance
                rr_ratios.append(rr_ratio)

        avg_rr = np.mean(rr_ratios) if rr_ratios else 0

        # Calculate drawdown
        equity_values = [e['equity'] for e in self.equity_curve]
        peak = equity_values[0]
        max_drawdown = 0

        for equity in equity_values:
            if equity > peak:
                peak = equity
            drawdown = (peak - equity) / peak
            max_drawdown = max(max_drawdown, drawdown)

        # Total return
        total_return = ((self.current_balance - self.initial_balance) / self.initial_balance) * 100

        return {
            'total_return': total_return,
            'total_trades': total_trades,
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate * 100,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'max_drawdown': max_drawdown * 100,
            'avg_rr': avg_rr,
            'final_balance': self.current_balance,
            'initial_balance': self.initial_balance
        }

    def plot_results(self, data: pd.DataFrame, save_path: str = None):
        """Plot backtest results with detailed trade analysis"""
        if not self.equity_curve:
            self.logger.warning("No equity curve data to plot")
            return

        # Create subplots
        fig = make_subplots(
            rows=3, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.05,
            subplot_titles=('Price Action & Trades', 'Equity Curve', 'Drawdown'),
            row_heights=[0.6, 0.2, 0.2]
        )

        # Candlestick chart with trades
        fig.add_trace(
            go.Candlestick(
                x=data.index,
                open=data['open'],
                high=data['high'],
                low=data['low'],
                close=data['close'],
                name='Price',
                increasing_line_color='green',
                decreasing_line_color='red'
            ),
            row=1, col=1
        )

        # Add trade markers with detailed annotations
        for i, trade in enumerate(self.trades):
            color = 'green' if trade['pnl_dollar'] > 0 else 'red'

            # Entry point
            fig.add_trace(
                go.Scatter(
                    x=[trade['entry_time']],
                    y=[trade['entry_price']],
                    mode='markers+text',
                    marker=dict(color=color, size=12,
                                symbol='triangle-up' if trade['direction'] == 'long' else 'triangle-down'),
                    text=[f'T{i + 1} ENTRY'],
                    textposition='top center',
                    showlegend=False,
                    hovertemplate=f"<b>Trade {i + 1} Entry</b><br>" +
                                  f"Direction: {trade['direction'].upper()}<br>" +
                                  f"Price: ${trade['entry_price']:.4f}<br>" +
                                  f"Time: {trade['entry_time'].strftime('%Y-%m-%d %H:%M')}<br>" +
                                  f"Size: {trade['size']:.4f} SOL<extra></extra>"
                ),
                row=1, col=1
            )

            # Exit point
            fig.add_trace(
                go.Scatter(
                    x=[trade['exit_time']],
                    y=[trade['exit_price']],
                    mode='markers+text',
                    marker=dict(color=color, size=12, symbol='x'),
                    text=[f'T{i + 1} EXIT'],
                    textposition='bottom center',
                    showlegend=False,
                    hovertemplate=f"<b>Trade {i + 1} Exit</b><br>" +
                                  f"Price: ${trade['exit_price']:.4f}<br>" +
                                  f"P&L: {trade['pnl_pct']:.2f}% (${trade['pnl_dollar']:.2f})<br>" +
                                  f"Time: {trade['exit_time'].strftime('%Y-%m-%d %H:%M')}<br>" +
                                  f"Reason: {trade['exit_reason']}<extra></extra>"
                ),
                row=1, col=1
            )

            # Add stop loss line for each trade
            if trade['direction'] == 'long':
                fig.add_hline(
                    y=trade.get('stop_loss', trade['entry_price'] * 0.98),
                    line_dash="dash",
                    line_color="red",
                    line_width=1,
                    opacity=0.7,
                    annotation_text=f"SL T{i + 1}",
                    annotation_position="right",
                    row=1, col=1
                )
            else:
                fig.add_hline(
                    y=trade.get('stop_loss', trade['entry_price'] * 1.02),
                    line_dash="dash",
                    line_color="red",
                    line_width=1,
                    opacity=0.7,
                    annotation_text=f"SL T{i + 1}",
                    annotation_position="right",
                    row=1, col=1
                )

        # Equity curve
        equity_df = pd.DataFrame(self.equity_curve)
        fig.add_trace(
            go.Scatter(
                x=equity_df['timestamp'],
                y=equity_df['equity'],
                mode='lines',
                name='Equity',
                line=dict(color='green', width=2)
            ),
            row=2, col=1
        )

        # Add horizontal line for initial balance
        fig.add_hline(
            y=self.initial_balance,
            line_dash="dash",
            line_color="gray",
            row=2, col=1
        )

        # Drawdown
        equity_values = equity_df['equity'].values
        peak = equity_values[0]
        drawdown = []

        for equity in equity_values:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100
            drawdown.append(dd)

        fig.add_trace(
            go.Scatter(
                x=equity_df['timestamp'],
                y=drawdown,
                mode='lines',
                name='Drawdown %',
                line=dict(color='red'),
                fill='tonexty'
            ),
            row=3, col=1
        )

        # Update layout
        fig.update_layout(
            title=f'FVG Strategy Real Data Backtest - {len(self.trades)} Trades',
            xaxis_title='Time',
            height=1000,
            showlegend=True
        )

        # Update axes
        fig.update_xaxes(title_text="Time", row=3, col=1)
        fig.update_yaxes(title_text="Price", row=1, col=1)
        fig.update_yaxes(title_text="Equity", row=2, col=1)
        fig.update_yaxes(title_text="Drawdown %", row=3, col=1)

        # Show or save
        if save_path:
            fig.write_html(save_path)
            self.logger.info(f"Chart saved to {save_path}")
        else:
            fig.show()

    def print_summary(self, results: Dict):
        """Print backtest summary"""
        print("\n" + "=" * 50)
        print("REAL DATA BACKTEST SUMMARY")
        print("=" * 50)
        print(f"Initial Balance: ${results.get('initial_balance', 10000):,.2f}")
        print(f"Final Balance: ${results.get('final_balance', 10000):,.2f}")
        print(f"Total Return: {results.get('total_return', 0):.2f}%")
        print(f"Total Trades: {results.get('total_trades', 0)}")
        print(f"Winning Trades: {results.get('winning_trades', 0)}")
        print(f"Losing Trades: {results.get('losing_trades', 0)}")
        print(f"Win Rate: {results.get('win_rate', 0):.2f}%")
        print(f"Average Win: ${results.get('avg_win', 0):.2f}")
        print(f"Average Loss: ${results.get('avg_loss', 0):.2f}")
        print(f"Profit Factor: {results.get('profit_factor', 0):.2f}")
        print(f"Average R:R: {results.get('avg_rr', 0):.2f}")
        print(f"Max Drawdown: {results.get('max_drawdown', 0):.2f}%")
        print("=" * 50)


async def run_real_data_backtest():
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

    for symbol in SYMBOLS:
        print(f"\n{'=' * 80}")
        print(f"📊 BACKTESTING {symbol}")
        print(f"{'=' * 80}")

        # Reset strategy state for each symbol
        backtester.strategy = FVGStrategy(config, send_notifications=False)  # Fresh strategy instance
        backtester.current_position = None
        backtester.trades = []
        backtester.equity_curve = []

        trades, equity_curve = await backtester.run_backtest(symbol=symbol, days=7)

        # Add symbol info to trades
        for trade in trades:
            trade['symbol'] = symbol

        all_trades.extend(trades)
        all_equity_curves.extend(equity_curve)

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

    print(f"Symbols Traded: {', '.join(SYMBOLS)}")
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

        # Calculate average R:R ratio for all trades
        rr_ratios = []
        for trade in all_trades:
            # Calculate risk (entry to stop loss distance)
            risk_distance = abs(trade['entry_price'] - trade.get('stop_loss', trade['entry_price'] * 0.98))
            # Calculate reward (actual P&L distance)
            reward_distance = abs(trade['exit_price'] - trade['entry_price'])

            if risk_distance > 0:
                rr_ratio = reward_distance / risk_distance
                rr_ratios.append(rr_ratio)

        avg_rr = np.mean(rr_ratios) if rr_ratios else 0

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

        # Calculate max drawdown from combined equity curve
        if all_equity_curves:
            peak = initial_balance
            max_dd = 0
            for point in all_equity_curves:
                balance = point['equity']
                if balance > peak:
                    peak = balance
                dd = (peak - balance) / peak * 100
                if dd > max_dd:
                    max_dd = dd
            print(f"Max Drawdown: {max_dd:.2f}%")

        # Print breakdown by symbol
        print(f"\n📊 BREAKDOWN BY SYMBOL:")
        for symbol in SYMBOLS:
            symbol_trades = [t for t in all_trades if t['symbol'] == symbol]
            if symbol_trades:
                symbol_pnl = sum(t['pnl_dollar'] for t in symbol_trades)
                symbol_wins = len([t for t in symbol_trades if t['pnl_dollar'] > 0])
                symbol_win_rate = (symbol_wins / len(symbol_trades)) * 100
                print(
                    f"  {symbol}: {len(symbol_trades)} trades, ${symbol_pnl:.2f} P&L, {symbol_win_rate:.1f}% win rate")
            else:
                print(f"  {symbol}: No trades")
    else:
        print("No trades executed")
        print("Final Balance: $10,000.00")
        print("Total P&L: $0.00")
        print("Total Return: 0.00%")

    print("=" * 70)

    # Create and save chart
    if all_equity_curves:
        fig = make_subplots(
            rows=3, cols=1,
            subplot_titles=('Combined Equity Curve', 'Trade PnL by Symbol', 'Trade PnL Timeline'),
            vertical_spacing=0.08,
            row_heights=[0.5, 0.25, 0.25]
        )

        # Combined equity curve
        equity_values = [point['equity'] for point in all_equity_curves]
        fig.add_trace(
            go.Scatter(
                x=list(range(len(all_equity_curves))),
                y=equity_values,
                mode='lines',
                name='Combined Equity',
                line=dict(color='blue', width=2)
            ),
            row=1, col=1
        )

        # Add horizontal line for initial balance
        fig.add_hline(
            y=initial_balance,
            line_dash="dash",
            line_color="gray",
            row=1, col=1
        )

        # Trade PnL by symbol
        if all_trades:
            # Group trades by symbol
            for symbol in SYMBOLS:
                symbol_trades = [t for t in all_trades if t['symbol'] == symbol]
                if symbol_trades:
                    symbol_pnls = [t['pnl_dollar'] for t in symbol_trades]
                    colors = ['green' if pnl > 0 else 'red' for pnl in symbol_pnls]

                    fig.add_trace(
                        go.Bar(
                            x=[f"{symbol}-{i + 1}" for i in range(len(symbol_trades))],
                            y=symbol_pnls,
                            name=f'{symbol} Trades',
                            marker_color=colors,
                            showlegend=False
                        ),
                        row=2, col=1
                    )

            # Trade PnL timeline
            trade_pnls = [t['pnl_dollar'] for t in all_trades]
            trade_symbols = [t['symbol'] for t in all_trades]
            colors = ['green' if pnl > 0 else 'red' for pnl in trade_pnls]

            fig.add_trace(
                go.Bar(
                    x=[f"{symbol}-{i + 1}" for i, symbol in enumerate(trade_symbols)],
                    y=trade_pnls,
                    name='All Trades',
                    marker_color=colors,
                    showlegend=False
                ),
                row=3, col=1
            )

        fig.update_layout(
            title=f'Multi-Symbol Real Data Backtest Results - {len(all_trades)} Trades',
            height=1000,
            showlegend=True
        )

        fig.write_html('multi_symbol_real_data_backtest.html')
        print("Chart saved to multi_symbol_real_data_backtest.html")


if __name__ == "__main__":
    asyncio.run(run_real_data_backtest())