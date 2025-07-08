import asyncio
import pandas as pd
from datetime import datetime, timedelta
import logging
from trading_strategy import FVGStrategy
from hyperliquid_client import HyperliquidClient
from config import *
from notifications import send_telegram_message

class AVAXLiveTradingBot:
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
        
        # Initialize the correct Hyperliquid SDK clients
        from hyperliquid.info import Info
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants
        from eth_account import Account
        
        self.wallet = Account.from_key(HYPERLIQUID_API_KEY)
        self.info = Info(constants.MAINNET_API_URL, skip_ws=True)
        self.exchange = Exchange(
            wallet=self.wallet,
            base_url=constants.MAINNET_API_URL,
            account_address=HYPERLIQUID_ACCOUNT_ADDRESS
        )
        
        self.strategy = FVGStrategy(self.config, send_notifications=False)
        
        # Live trading state
        self.current_position = None  # Single position tracking
        self.position_lock = False  # Lock to prevent race conditions
        self.trade_history = []
        self.total_trades = 0
        self.winning_trades = 0
        self.total_pnl = 0
        self.last_position_close_time = None  # Track last position close time for cooldown
        
        # Stop monitoring state
        self.stop_monitoring_task = None
        self.stop_monitoring_active = False
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
        # Initialize client for data fetching
        self.client = HyperliquidClient(
            api_key=self.config['HYPERLIQUID_API_KEY'],
            subaccount=self.config['HYPERLIQUID_SUBACCOUNT']
        )
    
    async def fetch_live_data(self):
        """Fetch live market data for AVAX"""
        try:
            self.logger.info(f"Fetching live data for AVAX...")
            
            # Fetch LTF data (1m) - increase to 1000 candles
            ltf_data = await self.client.get_ohlcv(
                symbol="AVAX",
                timeframe=self.config['TIMEFRAME'],
                limit=500
            )
            
            # Fetch HTF data (15m) - reduced to 192 candles
            htf_data = await self.client.get_ohlcv(
                symbol="AVAX",
                timeframe=self.config['HTF_TIMEFRAME'],
                limit=100  # 2 days of 15m candles
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
        """Calculate position size based on risk management rules with capital and leverage constraints"""
        # AVAX-specific: Ensure minimum 3-cent stop loss distance
        min_stop_distance = 0.03  # 3 cents minimum
        
        if direction == 'long':
            current_stop_distance = entry_price - stop_loss
            if current_stop_distance < min_stop_distance:
                stop_loss = entry_price - min_stop_distance
                self.logger.info(f"AVAX: Stop loss adjusted to minimum 3-cent distance: ${stop_loss:.4f}")
        else:  # short
            current_stop_distance = stop_loss - entry_price
            if current_stop_distance < min_stop_distance:
                stop_loss = entry_price + min_stop_distance
                self.logger.info(f"AVAX: Stop loss adjusted to minimum 3-cent distance: ${stop_loss:.4f}")
        
        # FIXED: Use $10 fixed risk as originally requested
        target_risk = 1  # $10 fixed risk (as you wanted)
        
        # Position size = Target Risk / Price Risk per Unit
        # This guarantees we risk exactly $10
        risk_amount = abs(entry_price - stop_loss)
        position_size = target_risk / risk_amount
        
        # ROUND THE POSITION SIZE to avoid float precision issues
        position_size = round(position_size, 2)  # Round to 2 decimal places for Hyperliquid
        
        # Check minimum order size for Hyperliquid (usually 0.01)
        min_order_size = 0.01
        if position_size < min_order_size:
            position_size = min_order_size
            self.logger.warning(f"Position size too small, using minimum: {min_order_size}")
        
        # Calculate position value (size × entry price)
        position_value = position_size * entry_price
        
        # Get leverage for AVAX
        leverage = self.config['MAX_LEVERAGE'].get("AVAX", 10)  # Use 10x for AVAX
        
        # Capital constraints: $10,000 capital with leverage = max position value
        max_position_value = 10000 * leverage
        
        self.logger.info(f"DEBUG: Position calculation for AVAX:")
        self.logger.info(f"  Entry: ${entry_price:.4f}, Stop: ${stop_loss:.4f}")
        self.logger.info(f"  Risk amount: ${risk_amount:.4f}")
        self.logger.info(f"  Position size: {position_size:.2f}")
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
            position_size = target_risk / new_risk_amount
            position_size = round(position_size, 2)  # Round to 2 decimal places
            if position_size < min_order_size:
                position_size = min_order_size
            position_value = position_size * entry_price
            
            # Check if this fits within capital constraints
            if position_value <= max_position_value:
                self.logger.warning(f"Stop loss widened to fit capital constraints. New stop: ${new_stop:.4f} (was ${original_stop:.4f})")
                return position_size, new_stop
            else:
                # If still too large, scale down position size as last resort
                position_size = max_position_value / entry_price
                position_size = round(position_size, 2)  # Round to 2 decimal places
                if position_size < min_order_size:
                    position_size = min_order_size
                actual_risk = position_size * new_risk_amount
                self.logger.warning(f"Position size reduced due to capital constraints. Risk: ${actual_risk:.2f} instead of $10")
                return position_size, new_stop
        
        return position_size, stop_loss
    
    def round_to_tick(self, price, tick_size=0.001):
        return round(round(price / tick_size) * tick_size, 3)

    async def open_live_position(self, setup, current_price):
        """Open a live trading position on Hyperliquid for AVAX"""
        if self.current_position is not None or self.position_lock:
            self.logger.warning(f"Already in a position for AVAX or position lock active, cannot open new one")
            return False
        
        try:
            # Set position lock to prevent race conditions
            self.position_lock = True
            
            self.logger.info(f"DEBUG: Opening live position for AVAX with setup: {setup}")
            
            position_size, adjusted_stop = self.calculate_position_size(setup['entry_price'], setup['stop_loss'], setup['direction'])
            
            # Use adjusted stop if it was changed
            final_stop = adjusted_stop if adjusted_stop != setup['stop_loss'] else setup['stop_loss']
            
            # Get leverage for AVAX
            leverage = self.config['MAX_LEVERAGE'].get("AVAX", 10)
            
            # Place the actual order on Hyperliquid using market_open
            is_buy = setup['direction'] == 'long'
            
            # Use instant market execution
            self.logger.info(f"📈 Opening live position for AVAX (INSTANT EXECUTION):")
            self.logger.info(f"  Direction: {setup['direction'].upper()}")
            self.logger.info(f"  Target Entry: ${setup['entry_price']:.4f}")
            self.logger.info(f"  Stop: ${final_stop:.4f}")
            self.logger.info(f"  Size: {position_size:.4f}")
            self.logger.info(f"  Risk: $10")
            self.logger.info(f"  Leverage: {leverage}x")
            self.logger.info(f"  Order Type: MARKET (instant execution)")
            
            # Place market order for instant execution
            self.logger.info(f"📋 PLACING MARKET ORDER: AVAX {'BUY' if is_buy else 'SELL'} {position_size} (instant execution)")
            order_result = self.exchange.market_open(
                name="AVAX",
                is_buy=is_buy,
                sz=position_size,
                px=None,  # No limit price - instant market execution
                slippage=0.001
            )
            

            
            self.logger.info(f"📋 IMMEDIATE ORDER RESULT: {order_result}")
            
            if order_result and 'status' in order_result and order_result['status'] == 'ok':
                # Extract order information
                filled_data = order_result.get('response', {}).get('data', {}).get('statuses', [{}])[0].get('filled', {})
                
                if filled_data:
                    actual_entry_price = float(filled_data.get('avgPx', setup['entry_price']))
                    actual_size = float(filled_data.get('totalSz', position_size))
                    
                    self.current_position = {
                        'direction': setup['direction'],
                        'entry_price': actual_entry_price,
                        'stop_loss': final_stop,
                        'take_profit': setup.get('take_profit'),
                        'size': actual_size,
                        'entry_time': datetime.now(),
                        'reason': setup['reason'],
                        'leverage': leverage,
                        'order_id': filled_data.get('oid')
                    }
                    
                    self.logger.info(f"✅ LIVE POSITION OPENED FOR AVAX (INSTANT EXECUTION):")
                    self.logger.info(f"  Actual Entry: ${actual_entry_price:.4f}")
                    self.logger.info(f"  Actual Size: {actual_size:.4f}")
                    self.logger.info(f"  Order ID: {filled_data.get('oid')}")
                    
                    # Send Telegram notification
                    send_telegram_message(
                        f"🚀 INSTANT TRADE OPENED: AVAX {setup['direction'].upper()} at ${actual_entry_price:.4f} | "
                        f"Stop: ${final_stop:.4f} | Size: {actual_size:.4f} | Risk: $10"  # FIXED: Show $10 risk
                    )
                    
                    # Start stop monitoring
                    self.start_stop_monitoring()
                    
                    
                    self.position_lock = False
                    return True  # Return True to indicate order was placed successfully
            else:
                self.logger.error(f"Failed to place order: {order_result}")
                self.position_lock = False
                return False
                
        except Exception as e:
            self.logger.error(f"Error opening live position: {e}")
            self.position_lock = False
            return False
    
    def close_live_position(self, reason="manual"):
        """Close the current live position on Hyperliquid"""
        if self.current_position is None:
            self.logger.warning("No position to close")
            return False
        
        try:
            self.logger.info(f"📉 Closing live position for AVAX (INSTANT EXECUTION): {reason}")
            
            # Use market_close for instant execution
            close_result = self.exchange.market_close(
                coin="AVAX",
                px=None,  # No limit price - instant market execution
                slippage=0.001
            )
            
            if close_result and 'status' in close_result and close_result['status'] == 'ok':
                # Extract close information
                filled_data = close_result.get('response', {}).get('data', {}).get('statuses', [{}])[0].get('filled', {})
                
                if filled_data:
                    close_price = float(filled_data.get('avgPx', 0))
                    close_size = float(filled_data.get('totalSz', 0))
                    
                    # Calculate P&L
                    entry_price = self.current_position['entry_price']
                    position_size = self.current_position['size']
                    direction = self.current_position['direction']
                    
                    if direction == 'long':
                        pnl = (close_price - entry_price) * position_size
                    else:  # short
                        pnl = (entry_price - close_price) * position_size
                    
                    # Update statistics
                    self.total_trades += 1
                    self.total_pnl += pnl
                    if pnl > 0:
                        self.winning_trades += 1
                    
                    # Record trade
                    trade_record = {
                        'entry_time': self.current_position['entry_time'],
                        'exit_time': datetime.now(),
                        'direction': direction,
                        'entry_price': entry_price,
                        'exit_price': close_price,
                        'size': position_size,
                        'pnl': pnl,
                        'reason': reason
                    }
                    self.trade_history.append(trade_record)
                    
                    self.logger.info(f"✅ LIVE POSITION CLOSED FOR AVAX (INSTANT EXECUTION):")
                    self.logger.info(f"  Exit Price: ${close_price:.4f}")
                    self.logger.info(f"  P&L: ${pnl:.2f}")
                    self.logger.info(f"  Reason: {reason}")
                    self.logger.info(f"  Total P&L: ${self.total_pnl:.2f}")
                    self.logger.info(f"  Win Rate: {(self.winning_trades/self.total_trades)*100:.1f}%")
                    
                    # Send Telegram notification
                    pnl_emoji = "" if pnl > 0 else "🔴"
                    send_telegram_message(
                        f"{pnl_emoji} INSTANT TRADE CLOSED: AVAX at ${close_price:.4f} | "
                        f"P&L: ${pnl:.2f} | Reason: {reason} | Total: ${self.total_pnl:.2f}"
                    )
                    
                    # Stop monitoring
                    self.stop_stop_monitoring()
                    
                    # Clear position
                    self.current_position = None
                    self.last_position_close_time = datetime.now()
                    
                    return True
            else:
                self.logger.error(f"Failed to close position: {close_result}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error closing live position: {e}")
            return False
    
    def start_stop_monitoring(self):
        """Start the continuous stop monitoring task"""
        if self.current_position is not None and not self.stop_monitoring_active:
            self.stop_monitoring_task = asyncio.create_task(self.monitor_stops_continuously())
            self.logger.info("Started continuous stop monitoring for AVAX")
    
    def stop_stop_monitoring(self):
        """Stop the continuous stop monitoring task"""
        self.stop_monitoring_active = False
        if self.stop_monitoring_task and not self.stop_monitoring_task.done():
            self.stop_monitoring_task.cancel()
        self.logger.info("Stopped continuous stop monitoring for AVAX")
    

    async def monitor_stops_continuously(self):
        """Monitor stops every second when in a position to minimize slippage"""
        self.logger.info("🔍 Starting continuous stop monitoring for AVAX")
        self.logger.info("🔍 This should run every 0.5 seconds when in a position")
        self.stop_monitoring_active = True
        monitor_count = 0
        
        while self.stop_monitoring_active and self.current_position is not None:
            try:
                monitor_count += 1
                
                # Debug: Log every 20 checks (every 10 seconds) to confirm loop is running
                if monitor_count % 20 == 0:
                    self.logger.info(f"🔍 CONTINUOUS LOOP CONFIRMED: Monitor #{monitor_count} - Loop is running!")
                

                
                # Get current price
                current_price = self.client.get_current_price("AVAX")
                
                if current_price is None:
                    self.logger.debug(f"🔍 MONITOR #{monitor_count}: Failed to get current price")
                    await asyncio.sleep(0.5)
                    continue
                
                position = self.current_position
                direction = position['direction']
                entry_price = position['entry_price']
                stop_loss = position['stop_loss']
                
                # Calculate current P&L and R:R ratio
                if direction == 'long':
                    current_pnl = current_price - entry_price
                    current_risk = entry_price - stop_loss
                    rr_ratio = current_pnl / current_risk if current_risk > 0 else 0
                    stop_distance = current_price - stop_loss
                else:  # short
                    current_pnl = entry_price - current_price
                    current_risk = stop_loss - entry_price
                    rr_ratio = current_pnl / current_risk if current_risk > 0 else 0
                    stop_distance = stop_loss - current_price
                
                # Log detailed monitoring info every 10 checks (every 5 seconds)
                if monitor_count % 10 == 0:
                    self.logger.info(f"🔍 MONITOR #{monitor_count}: AVAX {direction.upper()}")
                    self.logger.info(f"  Price: ${current_price:.4f} | Entry: ${entry_price:.4f} | Stop: ${stop_loss:.4f}")
                    self.logger.info(f"  P&L: ${current_pnl:.4f} | R:R: {rr_ratio:.2f} | Stop Distance: ${stop_distance:.4f}")
                
                # Trailing stop logic
                if ('trailing_enabled' in position and position['trailing_enabled']) or rr_ratio >= 1.0:
                    # Enable trailing if not already enabled
                    if 'trailing_enabled' not in position:
                        self.logger.info(f"✅ TRAILING ENABLED! R:R = {rr_ratio:.2f} >= 1.0")
                        position['trailing_enabled'] = True
                        send_telegram_message(f"✅ TRAILING ENABLED for AVAX - R:R = {rr_ratio:.2f}")
                    # Check for trailing stop updates every 10 seconds (every 20 checks)
                    if monitor_count % 20 == 0:
                        try:
                            # Fetch fresh data for trailing stop
                            ltf_data, _, _ = await self.fetch_live_data()
                            if ltf_data is not None:
                                old_stop = position['stop_loss']
                                updated = self.strategy.update_trailing_stop(ltf_data, position)
                                if updated:
                                    new_stop = position['stop_loss']
                                    self.logger.info(f"🔄 TRAILING STOP UPDATED in continuous monitor!")
                                    self.logger.info(f"  Old Stop: ${old_stop:.4f} → New Stop: ${new_stop:.4f}")
                                    self.logger.info(f"  R:R: {rr_ratio:.2f} | Profit: ${current_pnl:.4f}")
                                    send_telegram_message(f"🔄 TRAILING STOP UPDATED: AVAX ${old_stop:.4f} → ${new_stop:.4f} (R:R: {rr_ratio:.2f})")
                        except Exception as e:
                            self.logger.error(f"Error updating trailing stop: {e}")
                elif rr_ratio >= 0.5:  # Log when approaching 1:1 R:R
                    if monitor_count % 20 == 0:  # Every 10 seconds
                        self.logger.info(f"⏳ APPROACHING TRAILING: R:R = {rr_ratio:.2f} (need >= 1.0)")
                
                # Check if stop loss is hit
                if direction == 'long' and current_price <= position['stop_loss']:
                    self.logger.info(f"🛑 CONTINUOUS MONITOR #{monitor_count}: STOP LOSS HIT for AVAX LONG")
                    self.logger.info(f"  Price: ${current_price:.4f} <= Stop: ${position['stop_loss']:.4f}")
                    self.logger.info(f"  Final P&L: ${current_pnl:.4f} | R:R: {rr_ratio:.2f}")
                    send_telegram_message(f"🛑 CONTINUOUS MONITOR: AVAX LONG STOP HIT at ${position['stop_loss']:.4f}")
                    self.close_live_position("Stop Loss Hit (Continuous Monitor)")
                    self.stop_monitoring_active = False
                    break
                elif direction == 'short' and current_price >= position['stop_loss']:
                    self.logger.info(f"🛑 CONTINUOUS MONITOR #{monitor_count}: STOP LOSS HIT for AVAX SHORT")
                    self.logger.info(f"  Price: ${current_price:.4f} >= Stop: ${position['stop_loss']:.4f}")
                    self.logger.info(f"  Final P&L: ${current_pnl:.4f} | R:R: {rr_ratio:.2f}")
                    send_telegram_message(f"🛑 CONTINUOUS MONITOR: AVAX SHORT STOP HIT at ${position['stop_loss']:.4f}")
                    self.close_live_position("Stop Loss Hit (Continuous Monitor)")
                    self.stop_monitoring_active = False
                    break
                
                # Wait 1 second before next check
                await asyncio.sleep(0.5)
                
            except Exception as e:
                self.logger.error(f"Error in continuous stop monitoring: {e}")
                await asyncio.sleep(0.5)
        
        self.logger.info("🔍 Continuous stop monitoring stopped for AVAX")
    
    async def run_live_trading(self):
        """Main live trading loop"""
        self.logger.info("🚀 Starting AVAX Live Trading Bot...")
        self.logger.info(f"Trading symbol: AVAX")
        self.logger.info(f"Risk per trade: $1")  # FIXED: Show $10 risk
        
        candle_idx = 0  # Track candle index for cooldown
        cycle_count = 0
        
        try:
            while True:  # Run indefinitely
                cycle_count += 1
                
                try:
                    # Check if we're in a position
                    if self.current_position is not None:
                        # Already in a position, just monitor
                        await asyncio.sleep(0.5)
                        continue
                    

                    
                    # Check cooldown period (5 minutes instead of 1 minute)
                    cooldown_remaining = None
                    if self.last_position_close_time:
                        time_since_close = datetime.now() - self.last_position_close_time
                        cooldown_remaining = 300 - time_since_close.total_seconds()  # 5 minutes = 300 seconds
                    
                    if cooldown_remaining and cooldown_remaining > 0:
                        self.logger.info(f"⏳ COOLDOWN ACTIVE for AVAX: {cooldown_remaining:.0f}s remaining")
                        candle_idx += 1
                        await asyncio.sleep(0.5)
                        continue
                    
                    # MAIN LOOP: No position, no cooldown - SEARCHING FOR TRADES
                    if cycle_count % 10 == 0:  # Log every 10 cycles when searching
                        self.logger.info(f"🔍 MAIN LOOP: Searching for AVAX trade setups... (Cycle #{cycle_count})")
                    
                    # Fetch live data for AVAX
                    ltf_data, htf_data, current_price = await self.fetch_live_data()
                    if ltf_data is not None and current_price is not None:
                        # Get current candle info for stop loss checks
                        current_candle = ltf_data.iloc[-1]
                        current_low = current_candle['low']
                        current_high = current_candle['high']
                        

                        
                        # Check for new entry if no position
                        if self.current_position is None:
                            setup = self.strategy.check_entry_conditions(ltf_data, htf_data)
                            if setup:
                                setup['symbol'] = 'AVAX'  # Add symbol to setup
                                # Only log locally, don't send Telegram for setups
                                self.logger.info(f"✅ Setup found for AVAX: {setup['direction']} at ${setup['entry_price']:.2f}")
                                success = await self.open_live_position(setup, current_price)
                                if not success:
                                    self.logger.error(f"❌ FAILED to open position for AVAX")
                                else:
                                    self.logger.info(f"✅ SUCCESSFULLY opened position for AVAX")
                        else:
                            self.logger.info(f"📊 AVAX already has position: {self.current_position['direction']} at ${self.current_position['entry_price']:.2f}")
                    
                    candle_idx += 1
                    await asyncio.sleep(10)
                except Exception as e:
                    self.logger.error(f"Error in live trading cycle: {e}")
                    await asyncio.sleep(0.5)
        except KeyboardInterrupt:
            self.logger.info("🛑 AVAX live trading stopped by user (Ctrl+C)")
        
        if self.current_position is not None:
            current_price = self.client.get_current_price("AVAX")
            if current_price:
                self.close_live_position("Session End")
        
        self.client.close()

    async def check_for_existing_position(self):
        """Check if there's an existing position that wasn't detected by the bot"""
        try:
            # Use user_state to get current positions
            user_state = self.info.user_state(self.wallet.address)
            self.logger.info(f"🔍 CHECKING USER STATE: {user_state}")
            
            if user_state and 'assetPositions' in user_state:
                for asset_pos in user_state['assetPositions']:
                    if asset_pos.get('position', {}).get('coin') == 'AVAX':
                        position_data = asset_pos.get('position', {})
                        size = float(position_data.get('szi', 0))
                        
                        if size != 0:  # Position has size
                            self.logger.info(f"🔍 FOUND EXISTING AVAX POSITION: {position_data}")
                            
                            # Create position object from existing position
                            entry_price = float(position_data.get('entryPx', 0))
                            direction = 'long' if size > 0 else 'short'
                            
                            # Use a default stop loss for existing positions
                            stop_loss = entry_price * 0.95 if direction == 'long' else entry_price * 1.05
                            
                            position = {
                                'symbol': 'AVAX',
                                'direction': direction,
                                'entry_price': entry_price,
                                'stop_loss': stop_loss,
                                'take_profit': None,
                                'size': abs(size),
                                'entry_time': datetime.now(),
                                'reason': 'Existing Position Detected',
                                'leverage': 10
                            }
                            
                            # Set as current position
                            self.current_position = position
                            
                            self.logger.info(f"✅ EXISTING POSITION DETECTED AND SET:")
                            self.logger.info(f"  Direction: {direction.upper()}")
                            self.logger.info(f"  Entry Price: ${entry_price:.4f}")
                            self.logger.info(f"  Size: {abs(size):.4f}")
                            
                            # Start stop monitoring
                            self.start_stop_monitoring()
                            
                            return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error checking for existing position: {e}")
            return False

async def main():
    bot = AVAXLiveTradingBot()
    await bot.run_live_trading()

if __name__ == "__main__":
    print("🚀 AVAX Live Trading Bot - REAL MONEY")
    print("This bot trades AVAX with real money on Hyperliquid.")
    print("Risk per trade: $1")
    print("Bot will run INDEFINITELY until you press Ctrl+C")
    print("Make sure you have set up your Hyperliquid API key and have sufficient balance.")
    print()
    
    asyncio.run(main()) 
