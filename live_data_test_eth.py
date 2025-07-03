import asyncio
import pandas as pd
from datetime import datetime, timedelta
import logging
from trading_strategy import FVGStrategy
from hyperliquid_client import HyperliquidClient
from config import *
from notifications import send_telegram_message

class ETHPaperTradingBot:
    def __init__(self):
        self.config = {
            'HYPERLIQUID_API_KEY': HYPERLIQUID_API_KEY,
            'HYPERLIQUID_SUBACCOUNT': HYPERLIQUID_SUBACCOUNT,
            'SYMBOLS': ["ETH"],  # Only ETH
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
        """Fetch live market data for ETH"""
        try:
            self.logger.info(f"Fetching live data for ETH...")
            
            # Fetch LTF data (1m) - increase to 1000 candles
            ltf_data = await self.client.get_ohlcv(
                symbol="ETH",
                timeframe=self.config['TIMEFRAME'],
                limit=1000
            )
            
            # Fetch HTF data (15m) - increase to 500 candles
            htf_data = await self.client.get_ohlcv(
                symbol="ETH",
                timeframe=self.config['HTF_TIMEFRAME'],
                limit=500
            )
            
            # Get current price
            current_price = self.client.get_current_price("ETH")
            
            if ltf_data.empty or htf_data.empty:
                self.logger.error(f"Failed to fetch market data for ETH")
                return None, None, None
            
            return ltf_data, htf_data, current_price
            
        except Exception as e:
            self.logger.error(f"Error fetching live data for ETH: {e}")
            return None, None, None
    
    def calculate_position_size(self, entry_price, stop_loss, direction):
        """Calculate position size based on risk per trade with capital and leverage constraints"""
        risk_amount = abs(entry_price - stop_loss)
        position_size = self.config['RISK_PER_TRADE'] / risk_amount
        
        # Calculate position value (size × entry price)
        position_value = position_size * entry_price
        
        # Get leverage for ETH
        leverage = self.config['MAX_LEVERAGE'].get("ETH", 25)  # Default to 25x for ETH
        
        # Capital constraints: $10,000 capital with leverage = max position value
        max_position_value = 10000 * leverage
        
        self.logger.info(f"DEBUG: Position calculation for ETH:")
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
    
    def open_paper_position(self, setup, current_price):
        """Open a paper trading position for ETH"""
        if self.current_position is not None:
            self.logger.warning(f"Already in a position for ETH, cannot open new one")
            return False
        
        try:
            self.logger.info(f"DEBUG: Opening position for ETH with setup: {setup}")
            
            position_size, adjusted_stop = self.calculate_position_size(setup['entry_price'], setup['stop_loss'], setup['direction'])
            
            # Use adjusted stop if it was changed
            final_stop = adjusted_stop if adjusted_stop != setup['stop_loss'] else setup['stop_loss']
            
            # Get leverage for ETH
            leverage = self.config['MAX_LEVERAGE'].get("ETH", 25)
            
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
            
            self.logger.info(f"DEBUG: Position created for ETH")
            
            self.logger.info(f"📈 PAPER POSITION OPENED FOR ETH:")
            self.logger.info(f"  Direction: {setup['direction'].upper()}")
            self.logger.info(f"  Entry: ${setup['entry_price']:.4f}")
            self.logger.info(f"  Stop: ${final_stop:.4f}")
            self.logger.info(f"  Target: {setup['take_profit'] if setup['take_profit'] is not None else 'None'}")
            self.logger.info(f"  Size: {position_size:.4f}")
            self.logger.info(f"  Risk: ${self.config['RISK_PER_TRADE']}")
            self.logger.info(f"  Leverage: {leverage}x")
            
            self.logger.info(f"DEBUG: About to send Telegram notification for ETH")
            send_telegram_message(
                f"Trade OPENED: ETH {setup['direction'].upper()} at ${setup['entry_price']:.4f} | Stop: ${final_stop:.4f} | Leverage: {leverage}x"
            )
            self.logger.info(f"DEBUG: Telegram notification sent for ETH")
            
            # Start continuous stop monitoring
            self.start_stop_monitoring()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error opening paper position: {e}")
            import traceback
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            return False
    
    def close_paper_position(self, current_price, reason):
        """Close the current paper trading position for ETH"""
        if self.current_position is None:
            self.logger.warning(f"❌ ATTEMPTED TO CLOSE NON-EXISTENT POSITION for ETH")
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
            self.logger.info(f"🚨 BALANCE UPDATE for ETH: ${old_balance:.2f} → ${self.paper_balance:.2f} (+${pnl_dollar:.2f})")
            self.logger.info(f"🚨 TOTAL P&L UPDATE for ETH: ${old_total_pnl:.2f} → ${self.total_pnl:.2f} (+${pnl_dollar:.2f})")
            
            # Record trade
            trade = {
                'symbol': 'ETH',
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
            
            self.logger.info(f"DEBUG: Balance updated for ETH: P&L ${pnl_dollar:.2f}, New balance: ${self.paper_balance:.2f}, Total trades: {self.total_trades}, Winning trades: {self.winning_trades}")
            self.logger.info(f"DEBUG: Trade history now has {len(self.trade_history)} trades")
            
            # Log the trade
            self.logger.info(f"📉 PAPER POSITION CLOSED FOR ETH:")
            self.logger.info(f"  Exit Price: ${current_price:.4f}")
            self.logger.info(f"  P&L: {pnl_pct:.2%} (${pnl_dollar:.2f})")
            self.logger.info(f"  Reason: {reason}")
            self.logger.info(f"  New Balance: ${self.paper_balance:.2f}")
            
            # Reset position
            self.current_position = None
            self.logger.info(f"DEBUG: Position deleted for ETH")
            
            # Calculate win rate
            win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
            
            send_telegram_message(
                f"Trade CLOSED: ETH {position['direction'].upper()} | Entry: ${position['entry_price']:.4f} | Exit: ${current_price:.4f} | Size: {position['size']:.4f} | P&L: ${pnl_dollar:.2f}"
            )
            
            # Send comprehensive balance update after every trade
            send_telegram_message(
                f"💰 TOTAL BALANCE: ${self.paper_balance:.2f} | Total P&L: ${self.total_pnl:.2f} | Trades: {self.total_trades} | Win Rate: {win_rate:.1f}%"
            )
            
        except Exception as e:
            self.logger.error(f"Error closing paper position: {e}")
    
    def check_position_exits(self, current_price, current_low, current_high, candle_idx, ltf_data, cycle_count):
        """Check if current position should be closed for ETH"""
        if self.current_position is None:
            return
        
        position = self.current_position
        direction = position['direction']
        old_stop_loss = position['stop_loss']
        
        # Debug: Show when we're checking trailing stops
        if cycle_count % 5 == 0:  # Show every 5 cycles
            self.logger.info(f"🔍 CHECKING TRAILING STOPS for ETH - Current stop: ${old_stop_loss:.4f}")
        
        # Update trailing stop BEFORE checking stop loss
        self.strategy.update_trailing_stop(ltf_data, position)
        
        # Debug: Show trailing stop updates
        if position['stop_loss'] != old_stop_loss:
            self.logger.info(f"🔄 TRAILING STOP UPDATED for ETH: ${old_stop_loss:.4f} → ${position['stop_loss']:.4f}")
            send_telegram_message(f"🔄 TRAILING STOP UPDATED: ETH ${old_stop_loss:.4f} → ${position['stop_loss']:.4f}")
        else:
            # Show current stop status even when not changed
            if cycle_count % 10 == 0:  # Show every 10 cycles to avoid spam
                self.logger.info(f"📊 ETH STOP STATUS: Current stop: ${position['stop_loss']:.4f} (unchanged)")
        
        # Check if stop loss is hit
        if direction == 'long' and current_low <= position['stop_loss']:
            self.logger.info(f"🛑 STOP LOSS HIT for ETH LONG: Low ${current_low:.4f} <= Stop ${position['stop_loss']:.4f}")
            send_telegram_message(f"🛑 STOP LOSS HIT: ETH LONG at ${position['stop_loss']:.4f}")
            self.close_paper_position(position['stop_loss'], "Stop Loss Hit")
            self.last_stop_idx = candle_idx
        elif direction == 'short' and current_high >= position['stop_loss']:
            self.logger.info(f"🛑 STOP LOSS HIT for ETH SHORT: High ${current_high:.4f} >= Stop ${position['stop_loss']:.4f}")
            send_telegram_message(f"🛑 STOP LOSS HIT: ETH SHORT at ${position['stop_loss']:.4f}")
            self.close_paper_position(position['stop_loss'], "Stop Loss Hit")
            self.last_stop_idx = candle_idx
    
    def print_paper_trading_summary(self, current_price, trend_info, setup):
        """Print comprehensive paper trading summary for ETH"""
        print(f"\n📊 ETH PAPER TRADING SUMMARY")
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
        
        if self.current_position is not None:
            position = self.current_position
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
            stop_status = "TRAILING" if rr_ratio > 1.0 else "STATIC"
            
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
            self.logger.info(f"🚀 Starting ETH paper trading for {duration_minutes} minutes...")
            start_time = datetime.now()
            end_time = start_time + timedelta(minutes=duration_minutes)
        else:
            self.logger.info(f"🚀 Starting ETH paper trading INDEFINITELY...")
            self.logger.info("Press Ctrl+C to stop the bot")
            end_time = None
        
        self.logger.info(f"Trading symbol: ETH")
        self.logger.info(f"Starting balance: ${self.paper_balance:.2f}")
        self.logger.info(f"Risk per trade: ${self.config['RISK_PER_TRADE']}")
        
        # Send startup notification
        send_telegram_message(
            f"🚀 ETH BOT STARTED: Balance: ${self.paper_balance:.2f} | Risk: ${self.config['RISK_PER_TRADE']}"
        )
        
        # Test notification
        send_telegram_message("🧪 TEST: ETH notification system is working")
        
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
                    self.logger.info(f"🔄 ETH paper trading cycle {cycle_count} - Balance: ${self.paper_balance:.2f}, Total P&L: ${self.total_pnl:.2f}")
                    self.logger.info(f"DEBUG: Current totals - Total trades: {self.total_trades}, Winning trades: {self.winning_trades}, Trade history length: {len(self.trade_history)}")
                    # Send periodic balance update
                    if self.total_trades > 0:
                        win_rate = (self.winning_trades / self.total_trades * 100)
                        send_telegram_message(
                            f"📊 ETH PERIODIC UPDATE: Balance: ${self.paper_balance:.2f} | P&L: ${self.total_pnl:.2f} | Trades: {self.total_trades} | Win Rate: {win_rate:.1f}%"
                        )
                
                try:
                    # Fetch live data for ETH
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
                                self.logger.info(f"🔄 TRAILING STOP UPDATED for ETH: ${old_stop_loss:.4f} → ${self.current_position['stop_loss']:.4f}")
                                send_telegram_message(f"🔄 TRAILING STOP UPDATED: ETH ${old_stop_loss:.4f} → ${self.current_position['stop_loss']:.4f}")
                        
                        # Check for new entry if no position
                        if self.current_position is None:
                            setup = self.strategy.check_entry_conditions(ltf_data, htf_data)
                            if setup and (candle_idx - self.last_stop_idx >= 5):  # Cooldown check
                                setup['symbol'] = 'ETH'  # Add symbol to setup
                                self.logger.info(f"🎯 TRADE SETUP DETECTED for ETH: {setup['direction'].upper()} at ${setup['entry_price']:.4f}")
                                send_telegram_message(f"🎯 TRADE SETUP: ETH {setup['direction'].upper()} at ${setup['entry_price']:.4f}")
                                success = self.open_paper_position(setup, current_price)
                                if not success:
                                    self.logger.error(f"❌ FAILED to open position for ETH")
                                else:
                                    self.logger.info(f"✅ SUCCESSFULLY opened position for ETH")
                            elif setup:
                                self.logger.info(f"⏳ SETUP DETECTED but in cooldown for ETH (cooldown: {candle_idx - self.last_stop_idx}/5)")
                        else:
                            self.logger.info(f"📊 ETH already has position: {self.current_position['direction']} at ${self.current_position['entry_price']:.4f}")
                        
                        # Print summary for ETH (less frequently to avoid spam)
                        if cycle_count % 5 == 0:  # Print every 5 cycles (about 2.5 minutes)
                            trend_info = self.strategy.identify_larger_trend(htf_data)
                            self.print_paper_trading_summary(current_price, trend_info, setup if self.current_position is None else None)
                    
                    candle_idx += 1
                    # Wait before next cycle
                    await asyncio.sleep(30)  # Check every 30 seconds
                    
                except Exception as e:
                    self.logger.error(f"Error in paper trading cycle: {e}")
                    await asyncio.sleep(30)  # Wait before retrying
                    
        except KeyboardInterrupt:
            self.logger.info("🛑 ETH paper trading stopped by user (Ctrl+C)")
        
        # Close any remaining position
        if self.current_position is not None:
            current_price = self.client.get_current_price("ETH")
            if current_price:
                self.close_paper_position(current_price, "Session End")
        
        # Print final summary
        self.print_final_summary()
        self.client.close()
    
    def print_final_summary(self):
        """Print final trading summary"""
        print("\n" + "="*70)
        print("🏁 FINAL ETH PAPER TRADING SUMMARY")
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

    async def monitor_stops_continuously(self):
        """Monitor stops every second when in a position to minimize slippage"""
        self.logger.info("🔍 Starting continuous stop monitoring for ETH")
        self.stop_monitoring_active = True
        
        while self.stop_monitoring_active and self.current_position is not None:
            try:
                # Get current price
                current_price = self.client.get_current_price("ETH")
                
                if current_price is None:
                    await asyncio.sleep(1)
                    continue
                
                position = self.current_position
                direction = position['direction']
                
                # Check if stop loss is hit
                if direction == 'long' and current_price <= position['stop_loss']:
                    self.logger.info(f"🛑 CONTINUOUS MONITOR: STOP LOSS HIT for ETH LONG: Price ${current_price:.4f} <= Stop ${position['stop_loss']:.4f}")
                    send_telegram_message(f"🛑 CONTINUOUS MONITOR: ETH LONG STOP HIT at ${position['stop_loss']:.4f}")
                    self.close_paper_position(position['stop_loss'], "Stop Loss Hit (Continuous Monitor)")
                    self.stop_monitoring_active = False
                    break
                elif direction == 'short' and current_price >= position['stop_loss']:
                    self.logger.info(f"🛑 CONTINUOUS MONITOR: STOP LOSS HIT for ETH SHORT: Price ${current_price:.4f} >= Stop ${position['stop_loss']:.4f}")
                    send_telegram_message(f"🛑 CONTINUOUS MONITOR: ETH SHORT STOP HIT at ${position['stop_loss']:.4f}")
                    self.close_paper_position(position['stop_loss'], "Stop Loss Hit (Continuous Monitor)")
                    self.stop_monitoring_active = False
                    break
                
                # Check take profit if set
                if position.get('take_profit') is not None:
                    if direction == 'long' and current_price >= position['take_profit']:
                        self.logger.info(f"🎯 CONTINUOUS MONITOR: TAKE PROFIT HIT for ETH LONG: Price ${current_price:.4f} >= Target ${position['take_profit']:.4f}")
                        send_telegram_message(f"🎯 CONTINUOUS MONITOR: ETH LONG TAKE PROFIT at ${position['take_profit']:.4f}")
                        self.close_paper_position(position['take_profit'], "Take Profit Hit (Continuous Monitor)")
                        self.stop_monitoring_active = False
                        break
                    elif direction == 'short' and current_price <= position['take_profit']:
                        self.logger.info(f"🎯 CONTINUOUS MONITOR: TAKE PROFIT HIT for ETH SHORT: Price ${current_price:.4f} <= Target ${position['take_profit']:.4f}")
                        send_telegram_message(f"🎯 CONTINUOUS MONITOR: ETH SHORT TAKE PROFIT at ${position['take_profit']:.4f}")
                        self.close_paper_position(position['take_profit'], "Take Profit Hit (Continuous Monitor)")
                        self.stop_monitoring_active = False
                        break
                
                # Wait 1 second before next check
                await asyncio.sleep(1)
                
            except Exception as e:
                self.logger.error(f"Error in continuous stop monitoring: {e}")
                await asyncio.sleep(1)
        
        self.logger.info("🔍 Continuous stop monitoring stopped for ETH")

    def start_stop_monitoring(self):
        """Start the continuous stop monitoring task"""
        if self.current_position is not None and not self.stop_monitoring_active:
            self.stop_monitoring_task = asyncio.create_task(self.monitor_stops_continuously())
            self.logger.info("🚀 Started continuous stop monitoring for ETH")

    def stop_stop_monitoring(self):
        """Stop the continuous stop monitoring task"""
        self.stop_monitoring_active = False
        if self.stop_monitoring_task and not self.stop_monitoring_task.done():
            self.stop_monitoring_task.cancel()
        self.logger.info("🛑 Stopped continuous stop monitoring for ETH")

async def main():
    """Main function to run ETH paper trading"""
    bot = ETHPaperTradingBot()
    
    try:
        # Run paper trading indefinitely (no time limit)
        await bot.run_paper_trading(duration_minutes=None)
        
        # Alternatively, if you want a time limit, uncomment the line below:
        # await bot.run_paper_trading(duration_minutes=60)  # 60 minutes
    except Exception as e:
        logging.error(f"Main error: {e}")

if __name__ == "__main__":
    print("🚀 ETH Paper Trading Bot - No Real Money")
    print("This bot simulates trading ETH with virtual money.")
    print("Starting balance: $10,000")
    print("Risk per trade: $100")
    print("Bot will run INDEFINITELY until you press Ctrl+C")
    print("Make sure you have set up your Hyperliquid API key in the .env file.")
    print()
    
    asyncio.run(main()) 