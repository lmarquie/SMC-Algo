import asyncio
import pandas as pd
from datetime import datetime, timedelta
import logging
from trading_strategy import FVGStrategy
from hyperliquid_client import HyperliquidClient
from config import *
from notifications import send_telegram_message

class SOLLiveTradingBot:
    def __init__(self):
        self.config = {
            'HYPERLIQUID_API_KEY': HYPERLIQUID_API_KEY,
            'HYPERLIQUID_SUBACCOUNT': HYPERLIQUID_SUBACCOUNT,
            'SYMBOLS': ["SOL"],  # Only SOL
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
        self.last_order_time = None  # Track last order placement time for 5-minute pause
        self.pending_order = None  # Track pending order for cancellation
        
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
        """Fetch live market data for SOL"""
        try:
            self.logger.info(f"Fetching live data for SOL...")
            
            # Fetch LTF data (1m) - increase to 1000 candles
            ltf_data = await self.client.get_ohlcv(
                symbol="SOL",
                timeframe=self.config['TIMEFRAME'],
                limit=500
            )
            
            # Fetch HTF data (15m) - reduced to 192 candles
            htf_data = await self.client.get_ohlcv(
                symbol="SOL",
                timeframe=self.config['HTF_TIMEFRAME'],
                limit=100  # 2 days of 15m candles
            )
            
            # Get current price
            current_price = self.client.get_current_price("SOL")
            
            if ltf_data.empty or htf_data.empty:
                self.logger.error(f"Failed to fetch market data for SOL")
                return None, None, None
            
            return ltf_data, htf_data, current_price
            
        except Exception as e:
            self.logger.error(f"Error fetching live data for SOL: {e}")
            return None, None, None
    
    def calculate_position_size(self, entry_price, stop_loss, direction):
        """Calculate position size based on risk management rules with capital and leverage constraints"""
        # SOL-specific: Ensure minimum 3-cent stop loss distance
        min_stop_distance = 0.03  # 3 cents minimum
        
        if direction == 'long':
            current_stop_distance = entry_price - stop_loss
            if current_stop_distance < min_stop_distance:
                stop_loss = entry_price - min_stop_distance
                self.logger.info(f"SOL: Stop loss adjusted to minimum 3-cent distance: ${stop_loss:.4f}")
        else:  # short
            current_stop_distance = stop_loss - entry_price
            if current_stop_distance < min_stop_distance:
                stop_loss = entry_price + min_stop_distance
                self.logger.info(f"SOL: Stop loss adjusted to minimum 3-cent distance: ${stop_loss:.4f}")
        
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
        
        # Get leverage for SOL
        leverage = self.config['MAX_LEVERAGE'].get("SOL", 10)  # Use 10x for SOL
        
        # Capital constraints: $10,000 capital with leverage = max position value
        max_position_value = 10000 * leverage
        
        self.logger.info(f"DEBUG: Position calculation for SOL:")
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
    
    async def open_live_position(self, setup, current_price):
        """Open a live trading position on Hyperliquid for SOL"""
        if self.current_position is not None or self.position_lock:
            self.logger.warning(f"Already in a position for SOL or position lock active, cannot open new one")
            return False
        
        try:
            # Set position lock to prevent race conditions
            self.position_lock = True
            
            self.logger.info(f"DEBUG: Opening live position for SOL with setup: {setup}")
            
            position_size, adjusted_stop = self.calculate_position_size(setup['entry_price'], setup['stop_loss'], setup['direction'])
            
            # Use adjusted stop if it was changed
            final_stop = adjusted_stop if adjusted_stop != setup['stop_loss'] else setup['stop_loss']
            
            # Get leverage for SOL
            leverage = self.config['MAX_LEVERAGE'].get("SOL", 10)
            
            # Place the actual order on Hyperliquid using market_open
            is_buy = setup['direction'] == 'long'
            
            self.logger.info(f"📈 Opening live position for SOL:")
            self.logger.info(f"  Direction: {setup['direction'].upper()}")
            self.logger.info(f"  Entry: ${setup['entry_price']:.4f}")
            self.logger.info(f"  Stop: ${final_stop:.4f}")
            self.logger.info(f"  Size: {position_size:.4f}")
            self.logger.info(f"  Risk: $10")  # FIXED: Show $10 risk
            self.logger.info(f"  Leverage: {leverage}x")
            
            # Get current BBO to calculate dynamic buffer and place order as close as possible
            try:
                meta = self.info.meta()
                for asset in meta['universe']:
                    if asset['name'] == 'SOL':
                        best_bid = float(asset.get('bidPx', 0))
                        best_ask = float(asset.get('askPx', 0))
                        break
                
                # Calculate spread width
                spread_width = best_ask - best_bid
                self.logger.info(f"📊 Current BBO: ${best_bid}@${best_ask} | Spread: ${spread_width:.3f}")
                
                # Dynamic buffer: spread width + 1 cent safety margin
                buffer = spread_width + 0.01  # At least 2 cents, or spread + 1 cent
                
                if setup['direction'] == 'long':
                    # For BUY orders: place just below the bid (as close as possible)
                    limit_price = round(best_bid - buffer, 3)
                    self.logger.info(f"  BUY order: ${limit_price:.3f} (${best_bid - limit_price:.3f} below bid)")
                else:  # short
                    # For SELL orders: place just above the ask (as close as possible)
                    limit_price = round(best_ask + buffer, 3)
                    self.logger.info(f"  SELL order: ${limit_price:.3f} (${limit_price - best_ask:.3f} above ask)")
                
            except Exception as e:
                self.logger.warning(f"Could not get BBO, using current price with fixed buffer: {e}")
                # Fallback to current price with fixed buffer
                current_price = self.client.get_current_price("SOL")
                if setup['direction'] == 'long':
                    limit_price = round(current_price - 0.05, 3)  # 5 cent buffer below
                else:
                    limit_price = round(current_price + 0.05, 3)  # 5 cent buffer above
            
            # Round and remove trailing zeros from actual order price
            limit_price = round(limit_price, 3)
            # Convert to string and remove trailing zeros, then back to float
            limit_price = float(f"{limit_price:.3f}".rstrip('0').rstrip('.'))
            self.logger.info(f"  Final Entry Price: ${limit_price}")
            
            # Use limit order as close to current price as possible (ALO - Always Limit Order for maker fees)
            self.logger.info(f"📋 PLACING ORDER: SOL {'BUY' if is_buy else 'SELL'} {position_size} @ ${limit_price:.4f}")
            
            order_result = self.exchange.order(
                name="SOL",
                is_buy=is_buy,
                sz=position_size,
                limit_px=limit_price,
                order_type={"limit": {"tif": "Alo"}},
                reduce_only=False
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
                    
                    self.logger.info(f"✅ LIVE POSITION OPENED FOR SOL:")
                    self.logger.info(f"  Actual Entry: ${actual_entry_price:.4f}")
                    self.logger.info(f"  Actual Size: {actual_size:.4f}")
                    self.logger.info(f"  Order ID: {filled_data.get('oid')}")
                    
                    # Send Telegram notification
                    send_telegram_message(
                        f" LIVE TRADE OPENED: SOL {setup['direction'].upper()} at ${actual_entry_price:.4f} | "
                        f"Stop: ${final_stop:.4f} | Size: {actual_size:.4f} | Risk: $10"  # FIXED: Show $10 risk
                    )
                    
                    # Set 5-minute pause after placing order
                    self.last_order_time = datetime.now()
                    self.logger.info(f"⏳ Order filled immediately, 5-minute pause started")
                    
                    # Start stop monitoring
                    self.start_stop_monitoring()
                    
                    self.position_lock = False
                    return True
                else:
                    # Order placed but not filled yet - wait 7 minutes then cancel
                    self.logger.info(f"⏳ Limit order placed but not filled yet. Waiting 7 minutes...")
                    send_telegram_message(f"⏳ SOL limit order placed at ${limit_price:.4f} - waiting 7 minutes for fill")
                    
                    # Store order info for cancellation
                    order_id = order_result.get('response', {}).get('data', {}).get('statuses', [{}])[0].get('oid')
                    
                    # Store order info and return - let main loop handle monitoring
                    self.pending_order = {
                        'order_id': order_id,
                        'stop_loss': final_stop,
                        'take_profit': setup.get('take_profit'),
                        'reason': setup['reason'],
                        'leverage': leverage,
                        'direction': setup['direction']
                    }
                    
                    # Set 5-minute pause after placing order
                    self.last_order_time = datetime.now()
                    self.logger.info(f"⏳ Limit order placed, 5-minute pause started. Returning to main loop for monitoring")
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
            self.logger.info(f"📉 Closing live position for SOL: {reason}")
            
            # Use limit_close to close the position
            # Get current price for limit
            current_price = self.client.get_current_price("SOL")
            close_result = self.exchange.order(
                name="SOL",
                is_buy=not (self.current_position['direction'] == 'long'),  # Opposite of entry direction
                sz=self.current_position['size'],
                limit_px=current_price,
                order_type={"limit": {"tif": "Gtc"}},
                reduce_only=True
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
                    
                    self.logger.info(f"✅ LIVE POSITION CLOSED FOR SOL:")
                    self.logger.info(f"  Exit Price: ${close_price:.4f}")
                    self.logger.info(f"  P&L: ${pnl:.2f}")
                    self.logger.info(f"  Reason: {reason}")
                    self.logger.info(f"  Total P&L: ${self.total_pnl:.2f}")
                    self.logger.info(f"  Win Rate: {(self.winning_trades/self.total_trades)*100:.1f}%")
                    
                    # Send Telegram notification
                    pnl_emoji = "" if pnl > 0 else "🔴"
                    send_telegram_message(
                        f"{pnl_emoji} LIVE TRADE CLOSED: SOL at ${close_price:.4f} | "
                        f"P&L: ${pnl:.2f} | Reason: {reason} | Total: ${self.total_pnl:.2f}"
                    )
                    
                    # Stop monitoring
                    self.stop_stop_monitoring()
                    
                    # Clear position
                    self.current_position = None
                    self.last_position_close_time = datetime.now()
                    
                    return True
                else:
                    self.logger.error(f"Position closed but no fill data: {close_result}")
                    return False
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
            self.logger.info("Started continuous stop monitoring for SOL")
    
    def stop_stop_monitoring(self):
        """Stop the continuous stop monitoring task"""
        self.stop_monitoring_active = False
        if self.stop_monitoring_task and not self.stop_monitoring_task.done():
            self.stop_monitoring_task.cancel()
        self.logger.info("Stopped continuous stop monitoring for SOL")
    
    async def monitor_stops_continuously(self):
        """Monitor stops every second when in a position to minimize slippage"""
        self.logger.info("🔍 Starting continuous stop monitoring for SOL")
        self.stop_monitoring_active = True
        monitor_count = 0
        
        while self.stop_monitoring_active and self.current_position is not None:
            try:
                monitor_count += 1
                
                # Get current price
                current_price = self.client.get_current_price("SOL")
                
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
                    self.logger.info(f"🔍 MONITOR #{monitor_count}: SOL {direction.upper()}")
                    self.logger.info(f"  Price: ${current_price:.4f} | Entry: ${entry_price:.4f} | Stop: ${stop_loss:.4f}")
                    self.logger.info(f"  P&L: ${current_pnl:.4f} | R:R: {rr_ratio:.2f} | Stop Distance: ${stop_distance:.4f}")
                
                # Check if stop loss is hit
                if direction == 'long' and current_price <= position['stop_loss']:
                    self.logger.info(f"🛑 CONTINUOUS MONITOR #{monitor_count}: STOP LOSS HIT for SOL LONG")
                    self.logger.info(f"  Price: ${current_price:.4f} <= Stop: ${position['stop_loss']:.4f}")
                    self.logger.info(f"  Final P&L: ${current_pnl:.4f} | R:R: {rr_ratio:.2f}")
                    send_telegram_message(f"🛑 CONTINUOUS MONITOR: SOL LONG STOP HIT at ${position['stop_loss']:.4f}")
                    await self.close_live_position("Stop Loss Hit (Continuous Monitor)")
                    self.stop_monitoring_active = False
                    break
                elif direction == 'short' and current_price >= position['stop_loss']:
                    self.logger.info(f"🛑 CONTINUOUS MONITOR #{monitor_count}: STOP LOSS HIT for SOL SHORT")
                    self.logger.info(f"  Price: ${current_price:.4f} >= Stop: ${position['stop_loss']:.4f}")
                    self.logger.info(f"  Final P&L: ${current_pnl:.4f} | R:R: {rr_ratio:.2f}")
                    send_telegram_message(f"🛑 CONTINUOUS MONITOR: SOL SHORT STOP HIT at ${position['stop_loss']:.4f}")
                    await self.close_live_position("Stop Loss Hit (Continuous Monitor)")
                    self.stop_monitoring_active = False
                    break
                
                # Check take profit if set
                if position.get('take_profit') is not None:
                    if direction == 'long' and current_price >= position['take_profit']:
                        self.logger.info(f"🎯 CONTINUOUS MONITOR #{monitor_count}: TAKE PROFIT HIT for SOL LONG")
                        self.logger.info(f"  Price: ${current_price:.4f} >= Target: ${position['take_profit']:.4f}")
                        self.logger.info(f"  Final P&L: ${current_pnl:.4f} | R:R: {rr_ratio:.2f}")
                        send_telegram_message(f"🎯 CONTINUOUS MONITOR: SOL LONG TAKE PROFIT at ${position['take_profit']:.4f}")
                        await self.close_live_position("Take Profit Hit (Continuous Monitor)")
                        self.stop_monitoring_active = False
                        break
                    elif direction == 'short' and current_price <= position['take_profit']:
                        self.logger.info(f"🎯 CONTINUOUS MONITOR #{monitor_count}: TAKE PROFIT HIT for SOL SHORT")
                        self.logger.info(f"  Price: ${current_price:.4f} <= Target: ${position['take_profit']:.4f}")
                        self.logger.info(f"  Final P&L: ${current_pnl:.4f} | R:R: {rr_ratio:.2f}")
                        send_telegram_message(f"🎯 CONTINUOUS MONITOR: SOL SHORT TAKE PROFIT at ${position['take_profit']:.4f}")
                        await self.close_live_position("Take Profit Hit (Continuous Monitor)")
                        self.stop_monitoring_active = False
                        break
                
                # Check for risk-reward stop loss management
                if self.current_position is not None:
                    position = self.current_position
                    initial_risk = abs(position['entry_price'] - position['stop_loss'])
                    if initial_risk > 0:
                        if position['direction'] == 'long':
                            current_profit = current_price - position['entry_price']
                        else:
                            current_profit = position['entry_price'] - current_price
                        rr_ratio = current_profit / initial_risk

                        # Log RR management every 20 checks (every 10 seconds)
                        if monitor_count % 20 == 0:
                            self.logger.info(f"📊 RR MONITOR #{monitor_count}: SOL {direction.upper()}")
                            self.logger.info(f"  Current RR: {rr_ratio:.2f} | Profit: ${current_profit:.4f} | Initial Risk: ${initial_risk:.4f}")
                            self.logger.info(f"  Current Stop: ${position['stop_loss']:.4f} | Breakeven Stop: ${position['entry_price']:.4f}")

                        # If RR >= 3, move stop loss to 1:1 RR
                        if rr_ratio >= 3:
                            if position['direction'] == 'long':
                                new_stop = position['entry_price'] + initial_risk
                                if position['stop_loss'] < new_stop:
                                    old_stop = position['stop_loss']
                                    self.logger.info(f"🔄 RR MANAGEMENT #{monitor_count}: Moving SOL LONG stop to 1:1 RR")
                                    self.logger.info(f"  Old Stop: ${old_stop:.4f} → New Stop: ${new_stop:.4f}")
                                    self.logger.info(f"  RR Ratio: {rr_ratio:.2f} | Profit: ${current_profit:.4f}")
                                    position['stop_loss'] = new_stop
                                    send_telegram_message(f"🔄 SOL LONG stop moved to 1:1 RR: ${old_stop:.4f} → ${new_stop:.4f} (RR: {rr_ratio:.2f})")
                            else:
                                new_stop = position['entry_price'] - initial_risk
                                if position['stop_loss'] > new_stop:
                                    old_stop = position['stop_loss']
                                    self.logger.info(f"🔄 RR MANAGEMENT #{monitor_count}: Moving SOL SHORT stop to 1:1 RR")
                                    self.logger.info(f"  Old Stop: ${old_stop:.4f} → New Stop: ${new_stop:.4f}")
                                    self.logger.info(f"  RR Ratio: {rr_ratio:.2f} | Profit: ${current_profit:.4f}")
                                    position['stop_loss'] = new_stop
                                    send_telegram_message(f"🔄 SOL SHORT stop moved to 1:1 RR: ${old_stop:.4f} → ${new_stop:.4f} (RR: {rr_ratio:.2f})")
                
                # Wait 1 second before next check
                await asyncio.sleep(0.5)
                
            except Exception as e:
                self.logger.error(f"Error in continuous stop monitoring: {e}")
                await asyncio.sleep(0.5)
        
        self.logger.info("🔍 Continuous stop monitoring stopped for SOL")
    
    async def run_live_trading(self):
        """Main live trading loop"""
        self.logger.info("🚀 Starting SOL Live Trading Bot...")
        self.logger.info(f"Trading symbol: SOL")
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
                    
                    # Check if we have a pending order - monitor and cancel after 7 minutes
                    if self.pending_order:
                        # Check if order has been pending for more than 7 minutes
                        order_placement_time = self.last_order_time
                        if order_placement_time:
                            time_since_order = datetime.now() - order_placement_time
                            if time_since_order.total_seconds() > 420:  # 7 minutes = 420 seconds
                                self.logger.info(f"⏰ Order pending for 7+ minutes, cancelling...")
                                send_telegram_message(f"⏰ SOL order pending for 7+ minutes, cancelling...")
                                
                                try:
                                    self.client.cancel_order("SOL", self.pending_order['order_id'])
                                    self.logger.info(f"✅ Order cancelled successfully")
                                    send_telegram_message(f"✅ SOL order cancelled successfully")
                                except Exception as e:
                                    self.logger.error(f"❌ Failed to cancel order: {e}")
                                    send_telegram_message(f"❌ Failed to cancel SOL order: {e}")
                                
                                self.pending_order = None
                                continue
                            else:
                                remaining_time = 420 - time_since_order.total_seconds()
                                self.logger.info(f"⏳ Pending order exists, {remaining_time:.0f}s remaining before cancellation")
                        
                        await asyncio.sleep(0.5)
                        continue
                    
                    # Check 5-minute pause after placing orders
                    pause_remaining = None
                    if self.last_order_time:
                        time_since_order = datetime.now() - self.last_order_time
                        pause_remaining = 300 - time_since_order.total_seconds()  # 5 minutes = 300 seconds
                    
                    if pause_remaining and pause_remaining > 0:
                        self.logger.info(f"⏳ ORDER PAUSE ACTIVE for SOL: {pause_remaining:.0f} seconds remaining")
                        await asyncio.sleep(0.5)
                        continue
                    
                    # Check cooldown period (5 minutes instead of 1 minute)
                    cooldown_remaining = None
                    if self.last_position_close_time:
                        time_since_close = datetime.now() - self.last_position_close_time
                        cooldown_remaining = 300 - time_since_close.total_seconds()  # 5 minutes = 300 seconds
                    
                    if cooldown_remaining and cooldown_remaining > 0:
                        self.logger.info(f"⏳ COOLDOWN ACTIVE for SOL: {cooldown_remaining:.0f} seconds remaining")
                        candle_idx += 1
                        await asyncio.sleep(0.5)
                        continue
                    
                    # Fetch live data for SOL
                    ltf_data, htf_data, current_price = await self.fetch_live_data()
                    if ltf_data is not None and current_price is not None:
                        # Get current candle info for stop loss checks
                        current_candle = ltf_data.iloc[-1]
                        current_low = current_candle['low']
                        current_high = current_candle['high']
                        
                        # Only check for trailing stop updates in the main loop
                        if self.current_position is not None:
                            old_stop_loss = self.current_position['stop_loss']
                            self.logger.info(f"🔄 TRAILING STOP CHECK: SOL {self.current_position['direction'].upper()}")
                            self.logger.info(f"  Current Stop: ${old_stop_loss:.4f} | Entry: ${self.current_position['entry_price']:.4f}")
                            
                            # Calculate current R:R to see if trailing is enabled
                            if self.current_position['direction'] == 'long':
                                current_profit = current_price - self.current_position['entry_price']
                                current_risk = self.current_position['entry_price'] - old_stop_loss
                            else:
                                current_profit = self.current_position['entry_price'] - current_price
                                current_risk = old_stop_loss - self.current_position['entry_price']
                            
                            rr_ratio = current_profit / current_risk if current_risk > 0 else 0
                            self.logger.info(f"  Current R:R: {rr_ratio:.2f} | Profit: ${current_profit:.4f} | Risk: ${current_risk:.4f}")
                            
                            # Check if trailing is enabled (R:R >= 1.0)
                            if rr_ratio < 1.0:
                                self.logger.info(f"  ⏳ Trailing not enabled yet - need R:R >= 1.0 (currently {rr_ratio:.2f})")
                            else:
                                self.logger.info(f"  ✅ Trailing enabled - R:R {rr_ratio:.2f} >= 1.0")
                            
                            # Update trailing stop
                            updated = self.strategy.update_trailing_stop(ltf_data, self.current_position)
                            
                            # For long: move stop up; for short: move stop down
                            if (self.current_position['direction'] == 'long' and self.current_position['stop_loss'] > old_stop_loss) or \
                               (self.current_position['direction'] == 'short' and self.current_position['stop_loss'] < old_stop_loss):
                                new_stop = self.current_position['stop_loss']
                                self.logger.info(f"🔄 TRAILING STOP UPDATED for SOL {self.current_position['direction'].upper()}")
                                self.logger.info(f"  Old Stop: ${old_stop_loss:.4f} → New Stop: ${new_stop:.4f}")
                                self.logger.info(f"  Entry: ${self.current_position['entry_price']:.4f} | Current Price: ${current_price:.4f}")
                                
                                # Calculate P&L and R:R
                                if self.current_position['direction'] == 'long':
                                    pnl = current_price - self.current_position['entry_price']
                                    risk = self.current_position['entry_price'] - new_stop
                                else:
                                    pnl = self.current_position['entry_price'] - current_price
                                    risk = new_stop - self.current_position['entry_price']
                                rr_ratio = pnl / risk if risk > 0 else 0
                                
                                self.logger.info(f"  P&L: ${pnl:.4f} | R:R: {rr_ratio:.2f}")
                                
                                # Place limit order at new trailing stop level
                                try:
                                    stop_order = self.exchange.order(
                                        name="SOL",
                                        is_buy=not (self.current_position['direction'] == 'long'),  # Opposite of entry direction
                                        sz=self.current_position['size'],
                                        limit_px=new_stop,
                                        order_type={"limit": {"tif": "Gtc"}},
                                        reduce_only=True
                                    )
                                    self.logger.info(f"📉 Placed trailing stop limit order at: ${new_stop:.4f}")
                                except Exception as e:
                                    self.logger.error(f"Failed to place trailing stop order: {e}")
                                
                                send_telegram_message(f"🔄 TRAILING STOP UPDATED: SOL {self.current_position['direction'].upper()} ${old_stop_loss:.4f} → ${new_stop:.4f} (R:R: {rr_ratio:.2f})")
                            elif updated:
                                self.logger.debug(f"🔄 Trailing stop check completed - no update needed for SOL")
                            else:
                                self.logger.debug(f"🔄 Trailing stop check completed - no valid swing levels found for SOL")
                        
                        # Check for new entry if no position
                        if self.current_position is None:
                            setup = self.strategy.check_entry_conditions(ltf_data, htf_data)
                            if setup:
                                setup['symbol'] = 'SOL'  # Add symbol to setup
                                # Only log locally, don't send Telegram for setups
                                self.logger.info(f"✅ Setup found for SOL: {setup['direction']} at ${setup['entry_price']:.2f}")
                                success = await self.open_live_position(setup, current_price)
                                if not success:
                                    self.logger.error(f"❌ FAILED to open position for SOL")
                                else:
                                    self.logger.info(f"✅ SUCCESSFULLY opened position for SOL")
                        else:
                            self.logger.info(f"📊 SOL already has position: {self.current_position['direction']} at ${self.current_position['entry_price']:.2f}")
                    
                    candle_idx += 1
                    await asyncio.sleep(10)
                except Exception as e:
                    self.logger.error(f"Error in live trading cycle: {e}")
                    await asyncio.sleep(0.5)
        except KeyboardInterrupt:
            self.logger.info("🛑 SOL live trading stopped by user (Ctrl+C)")
        
        if self.current_position is not None:
            current_price = self.client.get_current_price("SOL")
            if current_price:
                await self.close_live_position("Session End")
        
        self.client.close()

async def main():
    bot = SOLLiveTradingBot()
    await bot.run_live_trading()

if __name__ == "__main__":
    print("🚀 SOL Live Trading Bot - REAL MONEY")
    print("This bot trades SOL with real money on Hyperliquid.")
    print("Risk per trade: $1")
    print("Bot will run INDEFINITELY until you press Ctrl+C")
    print("Make sure you have set up your Hyperliquid API key and have sufficient balance.")
    print()
    
    asyncio.run(main()) 
