import asyncio
import pandas as pd
from datetime import datetime, timedelta
import logging
from trading_strategy import FVGStrategy
from hyperliquid_client import HyperliquidClient
from config import *
from notifications import send_telegram_message

class AVAXPaperTradingBot:
    def __init__(self):
        self.config = {
            'HYPERLIQUID_API_KEY': HYPERLIQUID_API_KEY,
            'HYPERLIQUID_SUBACCOUNT': HYPERLIQUID_SUBACCOUNT,
            'SYMBOLS': ["AVAX"],  # Only AVAX
            'TIMEFRAME': TIMEFRAME,
            'HTF_TIMEFRAME': HTF_TIMEFRAME,
            'POSITION_SIZE': POSITION_SIZE,
            'BOS_LOOKBACK': BOS_LOOKBACK,
            'DISPLACEMENT_THRESHOLD': DISPLACEMENT_THRESHOLD,
            'STOP_LOSS_BUFFER': STOP_LOSS_BUFFER,
            'TAKE_PROFIT_RATIO': TAKE_PROFIT_RATIO,
            'RISK_PER_TRADE': RISK_PER_TRADE,
            'MAX_LEVERAGE': MAX_LEVERAGE
        }
        
        self.client = HyperliquidClient(
            api_key=self.config['HYPERLIQUID_API_KEY'],
            subaccount=self.config['HYPERLIQUID_SUBACCOUNT']
        )
        self.strategy = FVGStrategy(self.config, send_notifications=False)
        
        # Paper trading state - single symbol
        self.paper_balance = 10000  # Starting with $10k
        self.current_position = None  # Single position tracking
        self.position_lock = False  # Lock to prevent race conditions
        self.trade_history = []
        self.total_trades = 0
        self.winning_trades = 0
        self.total_pnl = 0
        self.last_stop_idx = -10  # Track last stop loss exit index for cooldown
        
        # Stop monitoring state
        self.stop_monitoring_task = None
        self.stop_monitoring_active = False
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
    async def fetch_live_data(self):
        """Fetch live market data for AVAX"""
        try:
            self.logger.info(f"Fetching live data for AVAX...")
            
            # Fetch LTF data (1m) - increase to 1000 candles
            ltf_data = await self.client.get_ohlcv(
                symbol="AVAX",
                timeframe=self.config['TIMEFRAME'],
                limit=1000
            )
            
            # Fetch HTF data (15m) - increase to 500 candles
            htf_data = await self.client.get_ohlcv(
                symbol="AVAX",
                timeframe=self.config['HTF_TIMEFRAME'],
                limit=500
            )
            
            # Get current price
            current_price = self.client.get_current_price("AVAX")
            
            if ltf_data.empty or htf_data.empty:
                self.logger.error(f"Failed to fetch market data for AVAX")
                return None, None, None
            
            return ltf_data, htf_data, current_price
            
        except Exception as e:
            self.logger.error(f"Error fetching live data for AVAX: {e}")
            return None, None, None
    
    def calculate_position_size(self, entry_price, stop_loss, direction):
        """Calculate position size based on risk per trade with capital and leverage constraints"""
        risk_amount = abs(entry_price - stop_loss)
        position_size = self.config['RISK_PER_TRADE'] / risk_amount
        
        # Calculate position value (size × entry price)
        position_value = position_size * entry_price
        
        # Get leverage for AVAX
        leverage = self.config['MAX_LEVERAGE'].get("AVAX", 25)  # Default to 25x for AVAX
        
        # Capital constraints: $10,000 capital with leverage = max position value
        max_position_value = 10000 * leverage
        
        self.logger.info(f"DEBUG: Position calculation for AVAX:")
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
        
        # NEW LOGIC: If position size is too large (stop is too tight), widen stop so risk is $100
        max_position_size = max_position_value / entry_price
        if position_size > max_position_size:
            position_size = max_position_size
            # Recalculate stop so that risk = $100 with this position size
            if direction == 'long':
                stop_loss = entry_price - (self.config['RISK_PER_TRADE'] / position_size)
            else:
                stop_loss = entry_price + (self.config['RISK_PER_TRADE'] / position_size)
            self.logger.warning(f"Stop loss widened to ensure $100 risk. New stop: ${stop_loss:.4f}")
            return position_size, stop_loss
        
        return position_size, stop_loss
    
    def open_paper_position(self, setup, current_price):
        """Open a paper trading position for AVAX"""
        if self.current_position is not None or self.position_lock:
            self.logger.warning(f"Already in a position for AVAX or position lock active, cannot open new one")
            return False
        
        try:
            # Set position lock to prevent race conditions
            self.position_lock = True
            
            self.logger.info(f"DEBUG: Opening position for AVAX with setup: {setup}")
            
            position_size, adjusted_stop = self.calculate_position_size(setup['entry_price'], setup['stop_loss'], setup['direction'])
            
            # Use adjusted stop if it was changed
            final_stop = adjusted_stop if adjusted_stop != setup['stop_loss'] else setup['stop_loss']
            
            # Get leverage for AVAX
            leverage = self.config['MAX_LEVERAGE'].get("AVAX", 20)
            
            self.current_position = {
                'direction': setup['direction'],
                'entry_price': setup['entry_price'],
                'stop_loss': final_stop,
                'take_profit': setup.get('take_profit'),
                'size': position_size,
                'entry_time': datetime.now(),
                'reason': setup['reason'],
                'leverage': leverage
            }
            
            self.logger.info(f"DEBUG: Position created for AVAX")
            
            self.logger.info(f"📈 PAPER POSITION OPENED FOR AVAX:")
            self.logger.info(f"  Direction: {setup['direction'].upper()}")
            self.logger.info(f"  Entry: ${setup['entry_price']:.2f}")
            self.logger.info(f"  Stop: ${final_stop:.2f}")
            self.logger.info(f"  Target: {setup['take_profit'] if setup['take_profit'] is not None else 'None'}")
            self.logger.info(f"  Size: {position_size:.4f}")
            self.logger.info(f"  Risk: ${self.config['RISK_PER_TRADE']}")
            self.logger.info(f"  Leverage: {leverage}x")
            
            self.logger.info(f"DEBUG: About to send Telegram notification for AVAX")
            send_telegram_message(
                f"Trade OPENED: AVAX {setup['direction'].upper()} at ${setup['entry_price']:.2f} | Stop: ${final_stop:.2f} | Leverage: {leverage}x"
            )
            self.logger.info(f"DEBUG: Telegram notification sent for AVAX")
            
            # Start continuous stop monitoring
            self.start_stop_monitoring()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error opening paper position: {e}")
            import traceback
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            # Release lock on error
            self.position_lock = False
            return False
    
    def close_paper_position(self, current_price, reason):
        """Close the current paper trading position for AVAX"""
        if self.current_position is None:
            self.logger.warning(f"❌ ATTEMPTED TO CLOSE NON-EXISTENT POSITION for AVAX")
            return
        
        try:
            # Stop continuous stop monitoring first
            self.stop_stop_monitoring()
            
            position = self.current_position
            
            # Calculate P&L
            if position['direction'] == 'long':
                pnl_pct = (current_price - position['entry_price']) / position['entry_price']
            else:
                pnl_pct = (position['entry_price'] - current_price) / position['entry_price']
            
            # Calculate dollar P&L
            position_value = position['size'] * position['entry_price']
            pnl_dollar = pnl_pct * position_value
            
            # Update balance
            old_balance = self.paper_balance
            old_total_pnl = self.total_pnl
            self.paper_balance += pnl_dollar
            self.total_pnl += pnl_dollar
            self.logger.info(f"🚨 BALANCE UPDATE for AVAX: ${old_balance:.2f} → ${self.paper_balance:.2f} (+${pnl_dollar:.2f})")
            self.logger.info(f"🚨 TOTAL P&L UPDATE for AVAX: ${old_total_pnl:.2f} → ${self.total_pnl:.2f} (+${pnl_dollar:.2f})")
            
            # Record trade
            trade = {
                'symbol': 'AVAX',
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
            
            self.logger.info(f"DEBUG: Balance updated for AVAX: P&L ${pnl_dollar:.2f}, New balance: ${self.paper_balance:.2f}, Total trades: {self.total_trades}, Winning trades: {self.winning_trades}")
            self.logger.info(f"DEBUG: Trade history now has {len(self.trade_history)} trades")
            
            # Log the trade
            self.logger.info(f"📉 PAPER POSITION CLOSED FOR AVAX:")
            self.logger.info(f"  Exit Price: ${current_price:.4f}")
            self.logger.info(f"  P&L: {pnl_pct:.2%} (${pnl_dollar:.2f})")
            self.logger.info(f"  Reason: {reason}")
            self.logger.info(f"  New Balance: ${self.paper_balance:.2f}")
            
            # Reset position
            self.current_position = None
            self.position_lock = False  # Release position lock
            self.logger.info(f"DEBUG: Position deleted for AVAX")
            
            # Calculate win rate
            win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
            
            send_telegram_message(
                f"Trade CLOSED: AVAX {position['direction'].upper()} | Entry: ${position['entry_price']:.4f} | Exit: ${current_price:.4f} | Size: {position['size']:.4f} | P&L: ${pnl_dollar:.2f}"
            )
            
            # Send comprehensive balance update after every trade
            send_telegram_message(
                f"💰 TOTAL BALANCE: ${self.paper_balance:.2f} | Total P&L: ${self.total_pnl:.2f} | Trades: {self.total_trades} | Win Rate: {win_rate:.1f}%"
            )
            
        except Exception as e:
            self.logger.error(f"Error closing paper position: {e}")
    
    def check_position_exits(self, current_price, current_low, current_high, candle_idx, ltf_data, cycle_count):
        """Check if current position should be closed for AVAX"""
        if self.current_position is None:
            return
        
        position = self.current_position
        direction = position['direction']
        old_stop_loss = position['stop_loss']
        
        # Debug: Show when we're checking trailing stops
        if cycle_count % 5 == 0:  # Show every 5 cycles
            self.logger.info(f"🔍 CHECKING TRAILING STOPS for AVAX - Current stop: ${old_stop_loss:.4f}")
        
        # Update trailing stop BEFORE checking stop loss
        self.strategy.update_trailing_stop(ltf_data, position)
        
        # Debug: Show trailing stop updates
        if position['stop_loss'] != old_stop_loss:
            self.logger.info(f"🔄 TRAILING STOP UPDATED for AVAX: ${old_stop_loss:.4f} → ${position['stop_loss']:.4f}")
            send_telegram_message(f"🔄 TRAILING STOP UPDATED: AVAX ${old_stop_loss:.4f} → ${position['stop_loss']:.4f}")
        else:
            # Show current stop status even when not changed
            if cycle_count % 10 == 0:  # Show every 10 cycles to avoid spam
                self.logger.info(f"📊 AVAX STOP STATUS: Current stop: ${position['stop_loss']:.4f} (unchanged)")
        
        # Check if stop loss is hit
        if direction == 'long' and current_low <= position['stop_loss']:
            self.logger.info(f"🛑 STOP LOSS HIT for AVAX LONG: Low ${current_low:.4f} <= Stop ${position['stop_loss']:.4f}")
            send_telegram_message(f"🛑 STOP LOSS HIT: AVAX LONG at ${position['stop_loss']:.4f}")
            self.close_paper_position(position['stop_loss'], "Stop Loss Hit")
            self.last_stop_idx = candle_idx
        elif direction == 'short' and current_high >= position['stop_loss']:
            self.logger.info(f"🛑 STOP LOSS HIT for AVAX SHORT: High ${current_high:.4f} >= Stop ${position['stop_loss']:.4f}")
            send_telegram_message(f"🛑 STOP LOSS HIT: AVAX SHORT at ${position['stop_loss']:.4f}")
            self.close_paper_position(position['stop_loss'], "Stop Loss Hit")
            self.last_stop_idx = candle_idx
    
    async def run_paper_trading(self, duration_minutes=None):
        """Run paper trading indefinitely or for specified duration"""
        if duration_minutes:
            self.logger.info(f"🚀 Starting AVAX paper trading for {duration_minutes} minutes...")
            start_time = datetime.now()
            end_time = start_time + timedelta(minutes=duration_minutes)
        else:
            self.logger.info(f"🚀 Starting AVAX paper trading INDEFINITELY...")
            self.logger.info("Press Ctrl+C to stop the bot")
            end_time = None
        
        self.logger.info(f"Trading symbol: AVAX")
        self.logger.info(f"Risk per trade: ${self.config['RISK_PER_TRADE']}")

        candle_idx = 0  # Track candle index for cooldown
        cycle_count = 0
        
        try:
            while True:  # Run indefinitely
                if end_time and datetime.now() >= end_time:
                    break
                
                cycle_count += 1
                
                try:
                    # Fetch live data for AVAX
                    ltf_data, htf_data, current_price = await self.fetch_live_data()
                    if ltf_data is not None and current_price is not None:
                        # Get current candle info for stop loss checks
                        current_candle = ltf_data.iloc[-1]
                        current_low = current_candle['low']
                        current_high = current_candle['high']
                        
                        # Only check for trailing stop updates in the main loop
                        if self.current_position is not None:
                            old_stop_loss = self.current_position['stop_loss']
                            self.strategy.update_trailing_stop(ltf_data, self.current_position)
                            # For long: move stop up; for short: move stop down
                            if (self.current_position['direction'] == 'long' and self.current_position['stop_loss'] > old_stop_loss) or \
                               (self.current_position['direction'] == 'short' and self.current_position['stop_loss'] < old_stop_loss):
                                self.logger.info(f"🔄 TRAILING STOP UPDATED for AVAX: ${old_stop_loss:.4f} → ${self.current_position['stop_loss']:.4f}")
                                send_telegram_message(f"🔄 TRAILING STOP UPDATED: AVAX ${old_stop_loss:.4f} → ${self.current_position['stop_loss']:.4f}")
                        
                        # Check for new entry if no position
                        if self.current_position is None:
                            setup = self.strategy.check_entry_conditions(ltf_data, htf_data)
                            if setup and (candle_idx - self.last_stop_idx >= 300):  # 5 minute cooldown (300 seconds)
                                setup['symbol'] = 'AVAX'  # Add symbol to setup
                                self.logger.info(f"🎯 TRADE SETUP DETECTED for AVAX: {setup['direction'].upper()} at ${setup['entry_price']:.2f}")
                                send_telegram_message(f"🎯 TRADE SETUP: AVAX {setup['direction'].upper()} at ${setup['entry_price']:.2f}")
                                success = self.open_paper_position(setup, current_price)
                                if not success:
                                    self.logger.error(f"❌ FAILED to open position for AVAX")
                                else:
                                    self.logger.info(f"✅ SUCCESSFULLY opened position for AVAX")
                            elif setup:
                                self.logger.info(f"⏳ SETUP DETECTED but in cooldown for AVAX (cooldown: {candle_idx - self.last_stop_idx}/300)")
                        else:
                            self.logger.info(f"📊 AVAX already has position: {self.current_position['direction']} at ${self.current_position['entry_price']:.2f}")
                        
                        if self.current_position is not None:
                            position = self.current_position
                            initial_risk = abs(position['entry_price'] - position['stop_loss'])
                            if initial_risk > 0:
                                if position['direction'] == 'long':
                                    current_profit = current_price - position['entry_price']
                                else:
                                    current_profit = position['entry_price'] - current_price
                                rr_ratio = current_profit / initial_risk

                                # If RR >= 3, move stop loss to 1:1 RR
                                if rr_ratio >= 3:
                                    if position['direction'] == 'long':
                                        new_stop = position['entry_price'] + initial_risk
                                        if position['stop_loss'] < new_stop:
                                            self.logger.info(f"Moved AVAX stop loss to 1:1 RR (${new_stop:.4f}) after reaching 3:1 RR")
                                            position['stop_loss'] = new_stop
                                            send_telegram_message(f"AVAX stop loss moved to 1:1 RR (${new_stop:.4f}) after 3:1 RR")
                                    else:
                                        new_stop = position['entry_price'] - initial_risk
                                        if position['stop_loss'] > new_stop:
                                            self.logger.info(f"Moved AVAX stop loss to 1:1 RR (${new_stop:.4f}) after reaching 3:1 RR")
                                            position['stop_loss'] = new_stop
                                            send_telegram_message(f"AVAX stop loss moved to 1:1 RR (${new_stop:.4f}) after 3:1 RR")
                    
                    candle_idx += 1
                    await asyncio.sleep(1)
                except Exception as e:
                    self.logger.error(f"Error in paper trading cycle: {e}")
                    await asyncio.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("🛑 AVAX paper trading stopped by user (Ctrl+C)")
        
        if self.current_position is not None:
            current_price = self.client.get_current_price("AVAX")
            if current_price:
                self.close_paper_position(current_price, "Session End")
        
        self.client.close()

    async def monitor_stops_continuously(self):
        """Monitor stops every second when in a position to minimize slippage"""
        self.logger.info("🔍 Starting continuous stop monitoring for AVAX")
        self.stop_monitoring_active = True
        
        while self.stop_monitoring_active and self.current_position is not None:
            try:
                # Get current price
                current_price = self.client.get_current_price("AVAX")
                
                if current_price is None:
                    await asyncio.sleep(1)
                    continue
                
                position = self.current_position
                direction = position['direction']
                
                # Check if stop loss is hit
                if direction == 'long' and current_price <= position['stop_loss']:
                    self.logger.info(f"🛑 CONTINUOUS MONITOR: STOP LOSS HIT for AVAX LONG: Price ${current_price:.4f} <= Stop ${position['stop_loss']:.4f}")
                    send_telegram_message(f"🛑 CONTINUOUS MONITOR: AVAX LONG STOP HIT at ${position['stop_loss']:.4f}")
                    self.close_paper_position(position['stop_loss'], "Stop Loss Hit (Continuous Monitor)")
                    self.stop_monitoring_active = False
                    break
                elif direction == 'short' and current_price >= position['stop_loss']:
                    self.logger.info(f"🛑 CONTINUOUS MONITOR: STOP LOSS HIT for AVAX SHORT: Price ${current_price:.4f} >= Stop ${position['stop_loss']:.4f}")
                    send_telegram_message(f"🛑 CONTINUOUS MONITOR: AVAX SHORT STOP HIT at ${position['stop_loss']:.4f}")
                    self.close_paper_position(position['stop_loss'], "Stop Loss Hit (Continuous Monitor)")
                    self.stop_monitoring_active = False
                    break
                
                # Check take profit if set
                if position.get('take_profit') is not None:
                    if direction == 'long' and current_price >= position['take_profit']:
                        self.logger.info(f"🎯 CONTINUOUS MONITOR: TAKE PROFIT HIT for AVAX LONG: Price ${current_price:.4f} >= Target ${position['take_profit']:.4f}")
                        send_telegram_message(f"🎯 CONTINUOUS MONITOR: AVAX LONG TAKE PROFIT at ${position['take_profit']:.4f}")
                        self.close_paper_position(position['take_profit'], "Take Profit Hit (Continuous Monitor)")
                        self.stop_monitoring_active = False
                        break
                    elif direction == 'short' and current_price <= position['take_profit']:
                        self.logger.info(f"🎯 CONTINUOUS MONITOR: TAKE PROFIT HIT for AVAX SHORT: Price ${current_price:.4f} <= Target ${position['take_profit']:.4f}")
                        send_telegram_message(f"🎯 CONTINUOUS MONITOR: AVAX SHORT TAKE PROFIT at ${position['take_profit']:.4f}")
                        self.close_paper_position(position['take_profit'], "Take Profit Hit (Continuous Monitor)")
                        self.stop_monitoring_active = False
                        break
                
                # Wait 1 second before next check
                await asyncio.sleep(1)
                
            except Exception as e:
                self.logger.error(f"Error in continuous stop monitoring: {e}")
                await asyncio.sleep(1)
        
        self.logger.info("🔍 Continuous stop monitoring stopped for AVAX")

    def start_stop_monitoring(self):
        """Start the continuous stop monitoring task"""
        if self.current_position is not None and not self.stop_monitoring_active:
            self.stop_monitoring_task = asyncio.create_task(self.monitor_stops_continuously())
            self.logger.info("🚀 Started continuous stop monitoring for AVAX")

    def stop_stop_monitoring(self):
        """Stop the continuous stop monitoring task"""
        self.stop_monitoring_active = False
        if self.stop_monitoring_task and not self.stop_monitoring_task.done():
            self.stop_monitoring_task.cancel()
        self.logger.info("🛑 Stopped continuous stop monitoring for AVAX")

async def main():
    """Main function to run AVAX paper trading"""
    bot = AVAXPaperTradingBot()
    
    try:
        # Run paper trading indefinitely (no time limit)
        await bot.run_paper_trading(duration_minutes=None)
        
        # Alternatively, if you want a time limit, uncomment the line below:
        # await bot.run_paper_trading(duration_minutes=60)  # 60 minutes
    except Exception as e:
        logging.error(f"Main error: {e}")

if __name__ == "__main__":
    print("🚀 AVAX Paper Trading Bot - No Real Money")
    print("This bot simulates trading AVAX with virtual money.")
    print("Starting balance: $10,000")
    print("Risk per trade: $100")
    print("Bot will run INDEFINITELY until you press Ctrl+C")
    print("Make sure you have set up your Hyperliquid API key in the .env file.")
    print()
    
    asyncio.run(main()) 
