import asyncio
import pandas as pd
from datetime import datetime, timedelta
import logging
from trading_strategy import FVGStrategy
from hyperliquid_client import HyperliquidClient
from config import *
from notifications import send_telegram_message

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
            'RISK_PER_TRADE': RISK_PER_TRADE,
            'MAX_LEVERAGE': MAX_LEVERAGE  # Add leverage mapping from config
        }
        
        self.client = HyperliquidClient(
            api_key=self.config['HYPERLIQUID_API_KEY'],
            subaccount=self.config['HYPERLIQUID_SUBACCOUNT']
        )
        self.strategy = FVGStrategy(self.config, send_notifications=False)
        
        # Paper trading state - multi-symbol
        self.paper_balance = 10000  # Starting with $10k
        self.current_positions = {}  # Track positions per symbol
        self.trade_history = []
        self.total_trades = 0
        self.winning_trades = 0
        self.total_pnl = 0
        self.last_stop_idx = {symbol: -10 for symbol in SYMBOLS}  # Track last stop loss exit index per symbol
        
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
            
            # Fetch LTF data (1m) - increase to 1000 candles
            ltf_data = await self.client.get_ohlcv(
                symbol=symbol,
                timeframe=self.config['TIMEFRAME'],
                limit=1000  # Increased from 200
            )
            
            # Fetch HTF data (15m) - increase to 500 candles
            htf_data = await self.client.get_ohlcv(
                symbol=symbol,
                timeframe=self.config['HTF_TIMEFRAME'],
                limit=500  # Increased from 100
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
    
    def calculate_position_size(self, entry_price, stop_loss, direction, symbol):
        """Calculate position size based on risk per trade with capital and leverage constraints"""
        risk_amount = abs(entry_price - stop_loss)
        position_size = self.config['RISK_PER_TRADE'] / risk_amount
        
        # Calculate position value (size × entry price)
        position_value = position_size * entry_price
        
        # Get leverage for this symbol
        leverage = self.config['MAX_LEVERAGE'].get(symbol, 20)  # Default to 20x if not found
        
        # Capital constraints: $10,000 capital with leverage = max position value
        max_position_value = 10000 * leverage  # Dynamic based on symbol leverage
        
        self.logger.info(f"DEBUG: Position calculation for {symbol}:")
        self.logger.info(f"  Entry: ${entry_price:.4f}, Stop: ${stop_loss:.4f}")
        self.logger.info(f"  Risk amount: ${risk_amount:.4f}")
        self.logger.info(f"  Position size: {position_size:.4f}")
        self.logger.info(f"  Position value: ${position_value:.2f}")
        self.logger.info(f"  Leverage: {leverage}x")
        self.logger.info(f"  Max position value: ${max_position_value:.2f}")
        
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
            self.logger.info(f"DEBUG: Opening position for {symbol} with setup: {setup}")
            
            position_size, adjusted_stop = self.calculate_position_size(setup['entry_price'], setup['stop_loss'], setup['direction'], symbol)
            
            # Use adjusted stop if it was changed
            final_stop = adjusted_stop if adjusted_stop != setup['stop_loss'] else setup['stop_loss']
            
            # Get leverage for this symbol
            leverage = self.config['MAX_LEVERAGE'].get(symbol, 20)  # Default to 20x if not found
            
            self.current_positions[symbol] = {
                'direction': setup['direction'],
                'entry_price': setup['entry_price'],
                'stop_loss': final_stop,
                'take_profit': setup.get('take_profit'),
                'size': position_size,
                'entry_time': datetime.now(),
                'reason': setup['reason'],
                'leverage': leverage  # Store leverage for reference
            }
            
            self.logger.info(f"📈 PAPER POSITION OPENED FOR {symbol}:")
            self.logger.info(f"  Direction: {setup['direction'].upper()}")
            self.logger.info(f"  Entry: ${setup['entry_price']:.4f}")
            self.logger.info(f"  Stop: ${final_stop:.4f}")
            self.logger.info(f"  Target: {setup['take_profit'] if setup['take_profit'] is not None else 'None'}")
            self.logger.info(f"  Size: {position_size:.4f}")
            self.logger.info(f"  Risk: ${self.config['RISK_PER_TRADE']}")
            self.logger.info(f"  Leverage: {leverage}x")
            
            self.logger.info(f"DEBUG: About to send Telegram notification for {symbol}")
            send_telegram_message(
                f"Trade OPENED: {symbol} {setup['direction'].upper()} at ${setup['entry_price']:.4f} | Stop: ${final_stop:.4f} | Leverage: {leverage}x"
            )
            self.logger.info(f"DEBUG: Telegram notification sent for {symbol}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error opening paper position: {e}")
            import traceback
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
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
            
            # Calculate win rate
            win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
            
            send_telegram_message(
                f"Trade CLOSED: {symbol} {position['direction'].upper()} | Entry: ${position['entry_price']:.4f} | Exit: ${current_price:.4f} | Size: {position['size']:.4f} | P&L: ${pnl_dollar:.2f}"
            )
            
            # Send comprehensive balance update after every trade
            send_telegram_message(
                f"💰 TOTAL BALANCE: ${self.paper_balance:.2f} | Total P&L: ${self.total_pnl:.2f} | Trades: {self.total_trades} | Win Rate: {win_rate:.1f}%"
            )
            
        except Exception as e:
            self.logger.error(f"Error closing paper position: {e}")
    
    def check_position_exits(self, symbol, current_price, current_low, current_high, candle_idx, ltf_data, cycle_count):
        """Check if current position should be closed for a specific symbol - using backtest logic"""
        if symbol not in self.current_positions:
            return
        
        position = self.current_positions[symbol]
        direction = position['direction']
        old_stop_loss = position['stop_loss']
        
        # Debug: Show when we're checking trailing stops
        if cycle_count % 5 == 0:  # Show every 5 cycles
            self.logger.info(f"🔍 CHECKING TRAILING STOPS for {symbol} - Current stop: ${old_stop_loss:.4f}")
        
        # Update trailing stop BEFORE checking stop loss (like backtest)
        self.strategy.update_trailing_stop(ltf_data, position)
        
        # Debug: Show trailing stop updates (always show, not just when changed)
        if position['stop_loss'] != old_stop_loss:
            self.logger.info(f"🔄 TRAILING STOP UPDATED for {symbol}: ${old_stop_loss:.4f} → ${position['stop_loss']:.4f}")
            send_telegram_message(f"🔄 TRAILING STOP UPDATED: {symbol} ${old_stop_loss:.4f} → ${position['stop_loss']:.4f}")
        else:
            # Show current stop status even when not changed
            if cycle_count % 10 == 0:  # Show every 10 cycles to avoid spam
                self.logger.info(f"📊 {symbol} STOP STATUS: Current stop: ${position['stop_loss']:.4f} (unchanged)")
        
        # Check if stop loss is hit
        if direction == 'long' and current_low <= position['stop_loss']:
            self.logger.info(f"🛑 STOP LOSS HIT for {symbol} LONG: Low ${current_low:.4f} <= Stop ${position['stop_loss']:.4f}")
            send_telegram_message(f"🛑 STOP LOSS HIT: {symbol} LONG at ${position['stop_loss']:.4f}")
            self.close_paper_position(symbol, position['stop_loss'], "Stop Loss Hit")
            self.last_stop_idx[symbol] = candle_idx
        elif direction == 'short' and current_high >= position['stop_loss']:
            self.logger.info(f"🛑 STOP LOSS HIT for {symbol} SHORT: High ${current_high:.4f} >= Stop ${position['stop_loss']:.4f}")
            send_telegram_message(f"🛑 STOP LOSS HIT: {symbol} SHORT at ${position['stop_loss']:.4f}")
            self.close_paper_position(symbol, position['stop_loss'], "Stop Loss Hit")
            self.last_stop_idx[symbol] = candle_idx
    
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
            stop_status = "TRAILING" if rr_ratio > 1.0 else "STATIC ($150)"
            
            print(f"\n🎯 ACTIVE POSITION:")
            print(f"  Direction: {position['direction'].upper()}")
            print(f"  Entry: ${position['entry_price']:.4f}")
            print(f"  Current: ${current_price:.4f}")
            print(f"  Unrealized P&L: {unrealized_pnl:.2%} (${unrealized_dollar:.2f})")
            print(f"  R:R Ratio: {rr_ratio:.2f}")
            print(f"  Stop: ${position['stop_loss']:.4f} ({stop_status})")
            print(f"  Target: {position['take_profit'] if position['take_profit'] is not None else 'None'}")
        elif setup:
            print(f"\n🎯 TRADE SETUP DETECTED:")
            print(f"  Direction: {setup['direction'].upper()}")
            print(f"  Entry: ${setup['entry_price']:.4f}")
            print(f"  Stop: ${setup['stop_loss']:.4f}")
            print(f"  Target: {setup['take_profit'] if setup['take_profit'] is not None else 'None'}")
            print(f"  Reason: {setup['reason']}")
        else:
            print(f"\n❌ No active position or trade setup")
        
        print("="*50)
    
    async def run_paper_trading(self, duration_minutes=None):
        """Run paper trading indefinitely or for specified duration"""
        if duration_minutes:
            self.logger.info(f"🚀 Starting multi-symbol paper trading for {duration_minutes} minutes...")
            start_time = datetime.now()
            end_time = start_time + timedelta(minutes=duration_minutes)
        else:
            self.logger.info(f"🚀 Starting multi-symbol paper trading INDEFINITELY...")
            self.logger.info("Press Ctrl+C to stop the bot")
            end_time = None
        
        self.logger.info(f"Trading symbols: {', '.join(self.config['SYMBOLS'])}")
        self.logger.info(f"Starting balance: ${self.paper_balance:.2f}")
        self.logger.info(f"Risk per trade: ${self.config['RISK_PER_TRADE']}")
        
        # Send startup notification
        send_telegram_message(
            f"🚀 BOT STARTED: Trading {', '.join(self.config['SYMBOLS'])} | Balance: ${self.paper_balance:.2f} | Risk: ${self.config['RISK_PER_TRADE']}"
        )
        
        # Test notification
        send_telegram_message("🧪 TEST: Notification system is working")
        
        candle_idx = 0  # Track candle index for cooldown
        cycle_count = 0
        
        try:
            while True:  # Run indefinitely
                # Check if we have a time limit
                if end_time and datetime.now() >= end_time:
                    self.logger.info("⏰ Time limit reached - stopping paper trading")
                    break
                
                cycle_count += 1
                if cycle_count % 20 == 0:  # Log every 20 cycles (about 10 minutes)
                    self.logger.info(f"🔄 Paper trading cycle {cycle_count} - Balance: ${self.paper_balance:.2f}, Total P&L: ${self.total_pnl:.2f}")
                    # Send periodic balance update
                    if self.total_trades > 0:
                        win_rate = (self.winning_trades / self.total_trades * 100)
                        send_telegram_message(
                            f"📊 PERIODIC UPDATE: Balance: ${self.paper_balance:.2f} | P&L: ${self.total_pnl:.2f} | Trades: {self.total_trades} | Win Rate: {win_rate:.1f}%"
                        )
                
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
                            
                            # Check position exits first (using backtest logic)
                            self.check_position_exits(symbol, current_price, current_low, current_high, candle_idx, ltf_data, cycle_count)
                            
                            # Check for new entry if no position (like backtest)
                            if symbol not in self.current_positions:
                                setup = self.strategy.check_entry_conditions(ltf_data, htf_data)
                                if setup and (candle_idx - self.last_stop_idx[symbol] >= 5):  # Cooldown check
                                    setup['symbol'] = symbol  # Add symbol to setup like backtest
                                    self.logger.info(f"🎯 TRADE SETUP DETECTED for {symbol}: {setup['direction'].upper()} at ${setup['entry_price']:.4f}")
                                    send_telegram_message(f"🎯 TRADE SETUP: {symbol} {setup['direction'].upper()} at ${setup['entry_price']:.4f}")
                                    success = self.open_paper_position(symbol, setup, current_price)
                                    if not success:
                                        self.logger.error(f"❌ FAILED to open position for {symbol}")
                                    else:
                                        self.logger.info(f"✅ SUCCESSFULLY opened position for {symbol}")
                                elif setup:
                                    self.logger.info(f"⏳ SETUP DETECTED but in cooldown for {symbol} (cooldown: {candle_idx - self.last_stop_idx[symbol]}/5)")
                            
                            # AVAX-specific stop loss move at 3:1 RR (like backtest)
                            if symbol == "AVAX" and symbol in self.current_positions:
                                position = self.current_positions[symbol]
                                initial_risk = abs(position['entry_price'] - position['stop_loss'])
                                if position['direction'] == 'long':
                                    current_profit = current_price - position['entry_price']
                                else:
                                    current_profit = position['entry_price'] - current_price
                                rr_ratio = current_profit / initial_risk if initial_risk > 0 else 0

                                # Debug: Show R:R ratio every cycle for AVAX
                                if cycle_count % 3 == 0:  # Show every 3 cycles to avoid spam
                                    self.logger.info(f"📊 AVAX R:R DEBUG: Current R:R = {rr_ratio:.2f} | Profit: ${current_profit:.4f} | Risk: ${initial_risk:.4f}")

                                # If RR >= 3, move stop loss to 1:1 RR
                                if rr_ratio >= 3:
                                    old_stop = position['stop_loss']
                                    if position['direction'] == 'long':
                                        new_stop = position['entry_price'] + initial_risk
                                        if position['stop_loss'] < new_stop:
                                            position['stop_loss'] = new_stop
                                            self.logger.info(f"🎯 AVAX 3:1 RR TRIGGERED! Stop moved: ${old_stop:.4f} → ${new_stop:.4f} (1:1 RR)")
                                            send_telegram_message(f"🎯 AVAX 3:1 RR TRIGGERED! Stop moved: ${old_stop:.4f} → ${new_stop:.4f} (1:1 RR)")
                                    else:
                                        new_stop = position['entry_price'] - initial_risk
                                        if position['stop_loss'] > new_stop:
                                            position['stop_loss'] = new_stop
                                            self.logger.info(f"🎯 AVAX 3:1 RR TRIGGERED! Stop moved: ${old_stop:.4f} → ${new_stop:.4f} (1:1 RR)")
                                            send_telegram_message(f"🎯 AVAX 3:1 RR TRIGGERED! Stop moved: ${old_stop:.4f} → ${new_stop:.4f} (1:1 RR)")
                            
                            # Print summary for this symbol (less frequently to avoid spam)
                            if cycle_count % 5 == 0:  # Print every 5 cycles (about 2.5 minutes)
                                trend_info = self.strategy.identify_larger_trend(htf_data)
                                self.print_paper_trading_summary(symbol, current_price, trend_info, setup if symbol not in self.current_positions else None)
                        
                        # Small delay between symbols
                        await asyncio.sleep(2)
                    candle_idx += 1
                    # Wait before next cycle
                    await asyncio.sleep(30)  # Check every 30 seconds
                    
                except Exception as e:
                    self.logger.error(f"Error in paper trading cycle: {e}")
                    await asyncio.sleep(30)  # Wait before retrying
                    
        except KeyboardInterrupt:
            self.logger.info("🛑 Paper trading stopped by user (Ctrl+C)")
        
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
        # Run paper trading indefinitely (no time limit)
        await bot.run_paper_trading(duration_minutes=None)
        
        # Alternatively, if you want a time limit, uncomment the line below:
        # await bot.run_paper_trading(duration_minutes=60)  # 60 minutes
    except Exception as e:
        logging.error(f"Main error: {e}")

if __name__ == "__main__":
    print("🚀 Multi-Symbol Paper Trading Bot - No Real Money")
    print("This bot simulates trading SOL and ETH with virtual money.")
    print("Starting balance: $10,000")
    print("Risk per trade: $100 per symbol")
    print("Bot will run INDEFINITELY until you press Ctrl+C")
    print("Make sure you have set up your Hyperliquid API key in the .env file.")
    print()
    
    asyncio.run(main()) 
