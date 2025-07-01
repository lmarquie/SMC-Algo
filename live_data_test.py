import asyncio
import pandas as pd
from datetime import datetime, timedelta
import logging
from trading_strategy import FVGStrategy
from hyperliquid_client import HyperliquidClient
from config import *

class PaperTradingBot:
    def __init__(self):
        self.config = {
            'HYPERLIQUID_API_KEY': HYPERLIQUID_API_KEY,
            'HYPERLIQUID_SUBACCOUNT': HYPERLIQUID_SUBACCOUNT,
            'SYMBOLS': SYMBOLS,  # Use all symbols for live trading
            'TIMEFRAME': TIMEFRAME,
            'HTF_TIMEFRAME': HTF_TIMEFRAME,
            'POSITION_SIZE': POSITION_SIZE,
            'BOS_LOOKBACK': BOS_LOOKBACK,
            'DISPLACEMENT_THRESHOLD': DISPLACEMENT_THRESHOLD,
            'STOP_LOSS_BUFFER': STOP_LOSS_BUFFER,
            'TAKE_PROFIT_RATIO': TAKE_PROFIT_RATIO,
            'RISK_PER_TRADE': RISK_PER_TRADE
        }
        
        self.client = HyperliquidClient(
            api_key=self.config['HYPERLIQUID_API_KEY'],
            subaccount=self.config['HYPERLIQUID_SUBACCOUNT']
        )
        self.strategy = FVGStrategy(self.config)
        
        # Paper trading state - multi-symbol
        self.paper_balance = 10000  # Starting with $10k
        self.current_positions = {}  # Track positions per symbol
        self.trade_history = []
        self.total_trades = 0
        self.winning_trades = 0
        self.total_pnl = 0
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
    async def fetch_live_data(self, symbol):
        """Fetch live market data from Hyperliquid for a specific symbol"""
        try:
            self.logger.info(f"Fetching live data for {symbol}...")
            
            # Fetch LTF data (1m)
            ltf_data = await self.client.get_ohlcv(
                symbol=symbol,
                timeframe=self.config['TIMEFRAME'],
                limit=200
            )
            
            # Fetch HTF data (15m)
            htf_data = await self.client.get_ohlcv(
                symbol=symbol,
                timeframe=self.config['HTF_TIMEFRAME'],
                limit=100
            )
            
            # Get current price
            current_price = self.client.get_current_price(symbol)
            
            if ltf_data.empty or htf_data.empty:
                self.logger.error(f"Failed to fetch market data for {symbol}")
                return None, None, None
            
            return ltf_data, htf_data, current_price
            
        except Exception as e:
            self.logger.error(f"Error fetching live data for {symbol}: {e}")
            return None, None, None
    
    def calculate_position_size(self, entry_price, stop_loss, direction):
        """Calculate position size based on risk per trade with capital and leverage constraints"""
        risk_amount = abs(entry_price - stop_loss)
        position_size = self.config['RISK_PER_TRADE'] / risk_amount
        
        # Calculate position value (size × entry price)
        position_value = position_size * entry_price
        
        # Capital constraints: $10,000 capital with 20x leverage = $200,000 max position value
        max_position_value = 10000 * 20  # $200,000
        
        # Check if position value exceeds maximum allowed
        if position_value > max_position_value:
            # First, try to double the stop loss distance to reduce position size
            original_stop = stop_loss
            
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
            position_size = self.config['RISK_PER_TRADE'] / new_risk_amount
            position_value = position_size * entry_price
            
            # Check if this fits within capital constraints
            if position_value <= max_position_value:
                self.logger.warning(f"Stop loss widened to fit capital constraints. New stop: ${new_stop:.4f} (was ${original_stop:.4f})")
                return position_size, new_stop
            else:
                # If still too large, scale down position size as last resort
                position_size = max_position_value / entry_price
                actual_risk = position_size * new_risk_amount
                self.logger.warning(f"Position size reduced due to capital constraints. Risk: ${actual_risk:.2f} instead of ${self.config['RISK_PER_TRADE']}")
                return position_size, new_stop
        
        return position_size, stop_loss
    
    def open_paper_position(self, symbol, setup, current_price):
        """Open a paper trading position for a specific symbol"""
        if symbol in self.current_positions:
            self.logger.warning(f"Already in a position for {symbol}, cannot open new one")
            return False
        
        try:
            position_size, adjusted_stop = self.calculate_position_size(setup['entry_price'], setup['stop_loss'], setup['direction'])
            
            # Use adjusted stop if it was changed
            final_stop = adjusted_stop if adjusted_stop != setup['stop_loss'] else setup['stop_loss']
            
            self.current_positions[symbol] = {
                'direction': setup['direction'],
                'entry_price': setup['entry_price'],
                'stop_loss': final_stop,
                'take_profit': setup.get('take_profit'),
                'size': position_size,
                'entry_time': datetime.now(),
                'reason': setup['reason']
            }
            
            self.logger.info(f"📈 PAPER POSITION OPENED FOR {symbol}:")
            self.logger.info(f"  Direction: {setup['direction'].upper()}")
            self.logger.info(f"  Entry: ${setup['entry_price']:.4f}")
            self.logger.info(f"  Stop: ${setup['stop_loss']:.4f}")
            self.logger.info(f"  Target: ${setup['take_profit']:.4f}")
            self.logger.info(f"  Size: {position_size:.4f}")
            self.logger.info(f"  Risk: ${self.config['RISK_PER_TRADE']}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error opening paper position: {e}")
            return False
    
    def close_paper_position(self, symbol, current_price, reason):
        """Close the current paper trading position for a specific symbol"""
        if symbol not in self.current_positions:
            return
        
        try:
            position = self.current_positions[symbol]
            
            # Calculate P&L
            if position['direction'] == 'long':
                pnl_pct = (current_price - position['entry_price']) / position['entry_price']
            else:
                pnl_pct = (position['entry_price'] - current_price) / position['entry_price']
            
            # Calculate dollar P&L
            position_value = position['size'] * position['entry_price']
            pnl_dollar = pnl_pct * position_value
            
            # Update balance
            self.paper_balance += pnl_dollar
            self.total_pnl += pnl_dollar
            
            # Record trade
            trade = {
                'symbol': symbol,
                'entry_time': position['entry_time'],
                'exit_time': datetime.now(),
                'direction': position['direction'],
                'entry_price': position['entry_price'],
                'exit_price': current_price,
                'size': position['size'],
                'pnl_pct': pnl_pct,
                'pnl_dollar': pnl_dollar,
                'reason': reason
            }
            
            self.trade_history.append(trade)
            self.total_trades += 1
            
            if pnl_dollar > 0:
                self.winning_trades += 1
            
            # Log the trade
            self.logger.info(f"📉 PAPER POSITION CLOSED FOR {symbol}:")
            self.logger.info(f"  Exit Price: ${current_price:.4f}")
            self.logger.info(f"  P&L: {pnl_pct:.2%} (${pnl_dollar:.2f})")
            self.logger.info(f"  Reason: {reason}")
            self.logger.info(f"  New Balance: ${self.paper_balance:.2f}")
            
            # Reset position
            del self.current_positions[symbol]
            
        except Exception as e:
            self.logger.error(f"Error closing paper position: {e}")
    
    def check_position_exits(self, symbol, current_price, current_low, current_high):
        """Check if current position should be closed for a specific symbol"""
        if symbol not in self.current_positions:
            return
        
        position = self.current_positions[symbol]
        direction = position['direction']
        stop_loss = position['stop_loss']
        
        # Check stop loss ONLY
        if direction == 'long' and current_low <= stop_loss:
            self.close_paper_position(symbol, stop_loss, "Stop Loss Hit")
        elif direction == 'short' and current_high >= stop_loss:
            self.close_paper_position(symbol, stop_loss, "Stop Loss Hit")
    
    def analyze_live_market(self, symbol, ltf_data, htf_data, current_price):
        """Analyze live market conditions for a specific symbol"""
        try:
            # Identify larger trend
            trend_info = self.strategy.identify_larger_trend(htf_data)
            
            # Check for trade setups (only if not in position for this symbol)
            setup = None
            if symbol not in self.current_positions:
                setup = self.strategy.check_entry_conditions(ltf_data, htf_data)
            
            return trend_info, setup
            
        except Exception as e:
            self.logger.error(f"Error analyzing market for {symbol}: {e}")
            return None, None
    
    def print_paper_trading_summary(self, symbol, current_price, trend_info, setup):
        """Print comprehensive paper trading summary for a specific symbol"""
        print(f"\n📊 {symbol} PAPER TRADING SUMMARY")
        print("="*50)
        print(f"Current Price: ${current_price:.4f}")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Paper Balance: ${self.paper_balance:.2f}")
        print(f"Total P&L: ${self.total_pnl:.2f}")
        print(f"Total Trades: {self.total_trades}")
        
        if self.total_trades > 0:
            win_rate = (self.winning_trades / self.total_trades) * 100
            print(f"Win Rate: {win_rate:.1f}%")
        
        if trend_info:
            print(f"\n📈 TREND: {trend_info['trend'].upper()} (Confidence: {trend_info['confidence']:.1%})")
        
        if symbol in self.current_positions:
            position = self.current_positions[symbol]
            # Calculate unrealized P&L
            if position['direction'] == 'long':
                unrealized_pnl = (current_price - position['entry_price']) / position['entry_price']
                current_profit = current_price - position['entry_price']
                current_risk = position['entry_price'] - position['stop_loss']
            else:
                unrealized_pnl = (position['entry_price'] - current_price) / position['entry_price']
                current_profit = position['entry_price'] - current_price
                current_risk = position['stop_loss'] - position['entry_price']
            
            position_value = position['size'] * position['entry_price']
            unrealized_dollar = unrealized_pnl * position_value
            
            # Determine stop loss status
            rr_ratio = current_profit / current_risk if current_risk > 0 else 0
            stop_status = "TRAILING" if rr_ratio > 1.0 else "STATIC ($250)"
            
            print(f"\n🎯 ACTIVE POSITION:")
            print(f"  Direction: {position['direction'].upper()}")
            print(f"  Entry: ${position['entry_price']:.4f}")
            print(f"  Current: ${current_price:.4f}")
            print(f"  Unrealized P&L: {unrealized_pnl:.2%} (${unrealized_dollar:.2f})")
            print(f"  R:R Ratio: {rr_ratio:.2f}")
            print(f"  Stop: ${position['stop_loss']:.4f} ({stop_status})")
            print(f"  Target: ${position['take_profit']:.4f}")
        elif setup:
            print(f"\n🎯 TRADE SETUP DETECTED:")
            print(f"  Direction: {setup['direction'].upper()}")
            print(f"  Entry: ${setup['entry_price']:.4f}")
            print(f"  Stop: ${setup['stop_loss']:.4f}")
            print(f"  Target: ${setup['take_profit']:.4f}")
            print(f"  Reason: {setup['reason']}")
        else:
            print(f"\n❌ No active position or trade setup")
        
        print("="*50)
    
    async def run_paper_trading(self, duration_minutes=60):
        """Run paper trading for specified duration"""
        self.logger.info(f"🚀 Starting multi-symbol paper trading for {duration_minutes} minutes...")
        self.logger.info(f"Trading symbols: {', '.join(self.config['SYMBOLS'])}")
        self.logger.info(f"Starting balance: ${self.paper_balance:.2f}")
        self.logger.info(f"Risk per trade: ${self.config['RISK_PER_TRADE']}")
        
        start_time = datetime.now()
        end_time = start_time + timedelta(minutes=duration_minutes)
        
        while datetime.now() < end_time:
            try:
                # Process each symbol
                for symbol in self.config['SYMBOLS']:
                    # Fetch live data for this symbol
                    ltf_data, htf_data, current_price = await self.fetch_live_data(symbol)
                    
                    if ltf_data is not None and current_price is not None:
                        # Get current candle info for stop loss checks
                        current_candle = ltf_data.iloc[-1]
                        current_low = current_candle['low']
                        current_high = current_candle['high']
                        
                        # Check position exits first
                        self.check_position_exits(symbol, current_price, current_low, current_high)
                        
                        # Analyze market
                        trend_info, setup = self.analyze_live_market(symbol, ltf_data, htf_data, current_price)
                        
                        # Open new position if setup detected and not in position for this symbol
                        if setup and symbol not in self.current_positions:
                            self.open_paper_position(symbol, setup, current_price)
                        
                        # Print summary for this symbol
                        self.print_paper_trading_summary(symbol, current_price, trend_info, setup)
                    
                    # Small delay between symbols
                    await asyncio.sleep(2)
                
                # Wait before next cycle
                await asyncio.sleep(30)  # Check every 30 seconds
                
            except KeyboardInterrupt:
                self.logger.info("Paper trading stopped by user")
                break
            except Exception as e:
                self.logger.error(f"Error in paper trading: {e}")
                await asyncio.sleep(30)
        
        # Close any remaining positions
        for symbol in list(self.current_positions.keys()):
            current_price = self.client.get_current_price(symbol)
            if current_price:
                self.close_paper_position(symbol, current_price, "Session End")
        
        # Print final summary
        self.print_final_summary()
        self.client.close()
    
    def print_final_summary(self):
        """Print final trading summary"""
        print("\n" + "="*70)
        print("🏁 FINAL PAPER TRADING SUMMARY")
        print("="*70)
        print(f"Final Balance: ${self.paper_balance:.2f}")
        print(f"Total P&L: ${self.total_pnl:.2f}")
        print(f"Total Trades: {self.total_trades}")
        
        if self.total_trades > 0:
            win_rate = (self.winning_trades / self.total_trades) * 100
            avg_win = sum([t['pnl_dollar'] for t in self.trade_history if t['pnl_dollar'] > 0]) / max(1, self.winning_trades)
            avg_loss = sum([t['pnl_dollar'] for t in self.trade_history if t['pnl_dollar'] < 0]) / max(1, self.total_trades - self.winning_trades)
            
            print(f"Win Rate: {win_rate:.1f}%")
            print(f"Average Win: ${avg_win:.2f}")
            print(f"Average Loss: ${avg_loss:.2f}")
        
        print("\n📋 TRADE HISTORY:")
        for i, trade in enumerate(self.trade_history[-5:], 1):  # Show last 5 trades
            print(f"  {i}. {trade['direction'].upper()} | Entry: ${trade['entry_price']:.4f} | Exit: ${trade['exit_price']:.4f} | P&L: {trade['pnl_pct']:.2%} (${trade['pnl_dollar']:.2f}) | {trade['reason']}")
        
        print("="*70)

async def main():
    """Main function to run paper trading"""
    bot = PaperTradingBot()
    
    try:
        # Run paper trading for 60 minutes (you can change this)
        await bot.run_paper_trading(duration_minutes=60)
    except Exception as e:
        logging.error(f"Main error: {e}")

if __name__ == "__main__":
    print("🚀 Multi-Symbol Paper Trading Bot - No Real Money")
    print("This bot simulates trading SOL, ETH, and XRP with virtual money.")
    print("Starting balance: $10,000")
    print("Risk per trade: $250 per symbol")
    print("Make sure you have set up your Hyperliquid API key in the .env file.")
    print()
    
    asyncio.run(main()) 