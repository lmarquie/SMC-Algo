import asyncio
import pandas as pd
import json
import os
import time
from datetime import datetime, timedelta
import logging
from trading_strategy import FVGStrategy
from hyperliquid_client import HyperliquidClient
from config import *
from notifications import send_telegram_message

class ETHLiveTradingBot:
    def __init__(self):
        self.config = {
            'HYPERLIQUID_API_KEY': HYPERLIQUID_API_KEY,
            'HYPERLIQUID_SUBACCOUNT': HYPERLIQUID_SUBACCOUNT,
            'SYMBOLS': ["ETH"],  
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
        
        # Setup logging FIRST (before any API calls)
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
        # Initialize the correct Hyperliquid SDK clients - BUT DON'T MAKE API CALLS YET
        from hyperliquid.info import Info
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants
        from eth_account import Account
        
        self.wallet = Account.from_key(HYPERLIQUID_API_KEY)
        
        # Store the classes but don't instantiate yet to avoid API calls
        self.Info = Info
        self.Exchange = Exchange
        self.constants = constants
        
        # These will be initialized only when needed (for placing orders)
        self.info = None
        self.exchange = None
        
        self.strategy = FVGStrategy(self.config, send_notifications=False)
        
        # Live trading state
        self.current_position = None  # Single position tracking
        self.position_lock = False  # Lock to prevent race conditions
        self.trade_history = []
        self.total_trades = 0
        self.winning_trades = 0
        self.total_pnl = 0
        self.last_position_close_time = None  # Track last position close time for cooldown
        self.pending_order = None  # Track pending ALO order for monitoring
        
        # Stop monitoring state
        self.stop_monitoring_task = None
        self.stop_monitoring_active = False
        
        # Initialize client for data fetching
        self.client = HyperliquidClient(
            api_key=self.config['HYPERLIQUID_API_KEY'],
            subaccount=self.config['HYPERLIQUID_SUBACCOUNT']
        )
        
        # Elixir monitor integration
        self.elixir_comm_file = "order_updates.json"
        self.last_order_update_time = None
        
        # Check if Elixir monitor is running and has data
        self.check_elixir_monitor_status()
    
    def check_elixir_monitor_status(self):
        """Check if Elixir monitor is running and has recent data"""
        try:
            if os.path.exists(self.elixir_comm_file):
                with open(self.elixir_comm_file, 'r') as f:
                    content = f.read().strip()
                    if content:
                        updates = json.loads(content)
                        if updates and len(updates) > 0:
                            latest_update = updates[-1]
                            update_time = latest_update.get('timestamp')
                            if update_time:
                                self.logger.info(f"✅ Elixir monitor data found: {len(updates)} updates")
                                self.logger.info(f"   Latest update: {update_time}")
                                return True
            
            self.logger.warning(f"⚠️ No Elixir monitor data found. Make sure to start the Elixir monitor when needed.")
            return False
            
        except Exception as e:
            self.logger.error(f"Error checking Elixir monitor status: {e}")
            return False
    
    def initialize_api_clients(self):
        """Initialize API clients only when needed (for placing orders)"""
        if self.info is None or self.exchange is None:
            self.logger.info("🔧 Initializing Hyperliquid API clients...")
            
            # Retry logic for API initialization to handle rate limiting
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    self.info = self.Info(self.constants.MAINNET_API_URL, skip_ws=True)
                    self.exchange = self.Exchange(
                        wallet=self.wallet,
                        base_url=self.constants.MAINNET_API_URL,
                        account_address=HYPERLIQUID_ACCOUNT_ADDRESS
                    )
                    self.logger.info("✅ API clients initialized successfully")
                    break  # Success, exit retry loop
                except Exception as e:
                    if "rate limited" in str(e) and attempt < max_retries - 1:
                        self.logger.warning(f"Rate limited on attempt {attempt + 1}, waiting 5 seconds...")
                        time.sleep(5)
                    else:
                        raise e  # Re-raise if not rate limited or max retries reached
    
    async def fetch_live_data(self):
        """Fetch live market data for ETH"""
        try:
            self.logger.info(f"Fetching live data for ETH...")
            
            # Add delay to prevent rate limiting
            await asyncio.sleep(2)
            
            # Fetch LTF data (1m) - increase to 1000 candles
            ltf_data = await self.client.get_ohlcv(
                symbol="ETH",
                timeframe=self.config['TIMEFRAME'],
                limit=500
            )
            
            # Add delay between API calls
            await asyncio.sleep(2)
            
            # Fetch HTF data (15m) - reduced to 192 candles
            htf_data = await self.client.get_ohlcv(
                symbol="ETH",
                timeframe=self.config['HTF_TIMEFRAME'],
                limit=100  # 2 days of 15m candles
            )
            
            # Add delay between API calls
            await asyncio.sleep(2)
            
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
        """Calculate position size based on risk management rules with capital and leverage constraints"""
        # ETH-specific: Ensure minimum 3-cent stop loss distance
        min_stop_distance = 0.03  # 3 cents minimum
        
        if direction == 'long':
            current_stop_distance = entry_price - stop_loss
            if current_stop_distance < min_stop_distance:
                stop_loss = entry_price - min_stop_distance
                self.logger.info(f"ETH: Stop loss adjusted to minimum 3-cent distance: ${stop_loss:.4f}")
        else:  # short
            current_stop_distance = stop_loss - entry_price
            if current_stop_distance < min_stop_distance:
                stop_loss = entry_price + min_stop_distance
                self.logger.info(f"ETH: Stop loss adjusted to minimum 3-cent distance: ${stop_loss:.4f}")
        
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
        
        # Get leverage for ETH
        leverage = self.config['MAX_LEVERAGE'].get("ETH", 10)  # Use 10x for ETH
        
        # Capital constraints: $10,000 capital with leverage = max position value
        max_position_value = 10000 * leverage
        
        self.logger.info(f"DEBUG: Position calculation for ETH:")
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

    def read_elixir_order_updates(self):
        """Read order updates from Elixir monitor's JSON file"""
        try:
            if not os.path.exists(self.elixir_comm_file):
                self.logger.debug(f"Elixir file not found: {self.elixir_comm_file}")
                return None
            
            with open(self.elixir_comm_file, 'r') as f:
                content = f.read().strip()
                if not content:
                    self.logger.debug(f"Elixir file is empty")
                    return None
                
                # Parse the JSON content (expecting a combined state object)
                state_data = json.loads(content)
                
                # Check if this is a new update
                update_time = state_data.get('timestamp')
                if update_time and self.last_order_update_time != update_time:
                    self.logger.info(f"📨 NEW ELIXIR UPDATE DETECTED!")
                    self.logger.info(f"   Previous time: {self.last_order_update_time}")
                    self.logger.info(f"   New time: {update_time}")
                    self.last_order_update_time = update_time
                    return state_data
                else:
                    self.logger.debug(f"No new update - same timestamp: {update_time}")
                
                return None
                
        except Exception as e:
            self.logger.error(f"Error reading Elixir order updates: {e}")
            return None

    def process_elixir_update(self, state_data):
        """Process state data from the Elixir monitor"""
        try:
            self.logger.info(f"🔍 PROCESSING ELIXIR STATE UPDATE")
            self.logger.info(f"   Timestamp: {state_data.get('timestamp')}")
            
            # Check if we have a pending order
            if not self.pending_order:
                self.logger.debug(f"No pending order to check")
                return False
            
            # Check fills for our pending order - only recent ones
            fills = state_data.get('fills', [])
            
            # Ensure fills is a list, not a string
            if isinstance(fills, str):
                self.logger.warning(f"Fills is a string, not a list: {fills}")
                fills = []
            elif not isinstance(fills, list):
                self.logger.warning(f"Fills is not a list: {type(fills)}")
                fills = []
                
            if fills and self.pending_order:
                # Only check fills that are newer than when our pending order was placed
                order_time = self.pending_order.get('order_time')
                if order_time:
                    # Filter fills to only those newer than our order
                    # Convert order_time to milliseconds timestamp for comparison
                    order_timestamp_ms = int(order_time.timestamp() * 1000)
                    recent_fills = []
                    for fill in fills:
                        fill_time = fill.get('time')  # This is milliseconds timestamp
                        if fill_time and isinstance(fill_time, (int, float)) and fill_time > order_timestamp_ms:
                            recent_fills.append(fill)
                    
                    self.logger.info(f"🔍 Checking {len(recent_fills)} recent fills (newer than order)")
                    
                    for fill in recent_fills:
                        # Check if this fill matches our pending order
                        fill_oid = fill.get('oid')
                        fill_coin = fill.get('coin')
                        
                        if fill_coin == 'ETH' and fill_oid:
                            # Check if this fill matches our pending order by either order ID
                            pending_regular_id = self.pending_order.get('regular_order_id')
                            pending_client_id = self.pending_order.get('order_id')  # This is the client order ID
                            
                            # Convert to same type for comparison
                            if isinstance(fill_oid, str):
                                fill_oid_int = int(fill_oid) if fill_oid.isdigit() else fill_oid
                            else:
                                fill_oid_int = fill_oid
                            
                            if isinstance(pending_regular_id, str):
                                pending_regular_id_int = int(pending_regular_id) if pending_regular_id.isdigit() else pending_regular_id
                            else:
                                pending_regular_id_int = pending_regular_id
                            
                            # Check if this fill matches our order
                            if (pending_regular_id_int == fill_oid_int or 
                                pending_client_id == fill_oid_int):
                                self.logger.info(f"🔍 POTENTIAL MATCH: ETH fill OID {fill_oid}")
                                self.logger.info(f"   Pending Regular ID: {pending_regular_id_int}")
                                self.logger.info(f"   Pending Client ID: {pending_client_id}")
                                self.logger.info(f"   Fill OID: {fill_oid_int}")
                                
                                # This is our order - process it
                                self.logger.info(f"✅ ORDER FILLED via Elixir monitor!")
                                
                                # Create position from fill data
                                fill_price = float(fill.get('px', self.pending_order['limit_price']))
                                fill_size = float(fill.get('sz', self.pending_order['size']))
                                
                                self.current_position = {
                                    'direction': self.pending_order['direction'],
                                    'entry_price': fill_price,
                                    'stop_loss': self.pending_order['stop_loss'],
                                    'take_profit': self.pending_order['setup'].get('take_profit'),
                                    'size': fill_size,
                                    'entry_time': datetime.now(),
                                    'reason': f"Momentum FVG - {self.pending_order['setup']['reason']}",
                                    'leverage': self.pending_order['leverage'],
                                    'order_id': self.pending_order['order_id'],
                                    'strategy_type': 'momentum',
                                    'fvg': self.pending_order.get('fvg', {})
                                }
                                
                                # Send Telegram notification
                                send_telegram_message(
                                    f"✅ MOMENTUM ALO FILLED: ETH {self.pending_order['direction'].upper()} at ${fill_price:.4f} | "
                                    f"FVG Stop: ${self.current_position['stop_loss']:.4f} | Size: {fill_size:.4f}"
                                )
                                
                                # Start stop monitoring
                                self.start_stop_monitoring()
                                
                                # Clear pending order
                                self.pending_order = None
                                return True
                else:
                    # Fallback: check all fills if we don't have order time
                    self.logger.info(f"🔍 Checking {len(fills)} fills (no order time available)")
                    
                    for fill in fills:
                        fill_oid = fill.get('oid')
                        fill_coin = fill.get('coin')
                        
                        if fill_coin == 'ETH' and fill_oid:
                            # Check if this fill matches our pending order by either order ID
                            pending_regular_id = self.pending_order.get('regular_order_id')
                            pending_client_id = self.pending_order.get('order_id')  # This is the client order ID
                            
                            # Convert to same type for comparison
                            if isinstance(fill_oid, str):
                                fill_oid_int = int(fill_oid) if fill_oid.isdigit() else fill_oid
                            else:
                                fill_oid_int = fill_oid
                            
                            if isinstance(pending_regular_id, str):
                                pending_regular_id_int = int(pending_regular_id) if pending_regular_id.isdigit() else pending_regular_id
                            else:
                                pending_regular_id_int = pending_regular_id
                            
                            # Check if this fill matches our order
                            if (pending_regular_id_int == fill_oid_int or 
                                pending_client_id == fill_oid_int):
                                self.logger.info(f"🔍 POTENTIAL MATCH: ETH fill OID {fill_oid}")
                                self.logger.info(f"   Pending Regular ID: {pending_regular_id_int}")
                                self.logger.info(f"   Pending Client ID: {pending_client_id}")
                                self.logger.info(f"   Fill OID: {fill_oid_int}")
                                
                                # This is our order - process it
                                self.logger.info(f"✅ ORDER FILLED via Elixir monitor!")
                                
                                # Create position from fill data
                                fill_price = float(fill.get('px', self.pending_order['limit_price']))
                                fill_size = float(fill.get('sz', self.pending_order['size']))
                                
                                self.current_position = {
                                    'direction': self.pending_order['direction'],
                                    'entry_price': fill_price,
                                    'stop_loss': self.pending_order['stop_loss'],
                                    'take_profit': self.pending_order['setup'].get('take_profit'),
                                    'size': fill_size,
                                    'entry_time': datetime.now(),
                                    'reason': f"Momentum FVG - {self.pending_order['setup']['reason']}",
                                    'leverage': self.pending_order['leverage'],
                                    'order_id': self.pending_order['order_id'],
                                    'strategy_type': 'momentum',
                                    'fvg': self.pending_order.get('fvg', {})
                                }
                                
                                # Send Telegram notification
                                send_telegram_message(
                                    f"✅ MOMENTUM ALO FILLED: ETH {self.pending_order['direction'].upper()} at ${fill_price:.4f} | "
                                    f"FVG Stop: ${self.current_position['stop_loss']:.4f} | Size: {fill_size:.4f}"
                                )
                                
                                # Start stop monitoring
                                self.start_stop_monitoring()
                                
                                # Clear pending order
                                self.pending_order = None
                                return True
            
            # Check positions for our pending order - only if we have a pending order
            positions = state_data.get('positions', [])
            
            # Ensure positions is a list, not a string
            if isinstance(positions, str):
                self.logger.warning(f"Positions is a string, not a list: {positions}")
                positions = []
            elif not isinstance(positions, list):
                self.logger.warning(f"Positions is not a list: {type(positions)}")
                positions = []
                
            if positions and self.pending_order:
                # Only check ETH positions
                ETH_positions = [pos for pos in positions if pos.get('coin') == 'ETH']
                if ETH_positions:
                    self.logger.info(f"🔍 Checking {len(ETH_positions)} ETH positions for our pending order")
                    
                    for position in ETH_positions:
                        position_size = float(position.get('size', 0))
                        
                        if position_size != 0:
                            self.logger.info(f"🔍 Found ETH position: size {position_size}")
                            
                            # If we have a pending order and find a position, assume it was filled
                            self.logger.info(f"✅ POSITION CREATED via Elixir monitor!")
                            
                            # Create position from position data
                            entry_price = float(position.get('entry_price', self.pending_order['limit_price']))
                            
                            self.current_position = {
                                'direction': self.pending_order['direction'],
                                'entry_price': entry_price,
                                'stop_loss': self.pending_order['stop_loss'],
                                'take_profit': self.pending_order['setup'].get('take_profit'),
                                'size': abs(position_size),
                                'entry_time': datetime.now(),
                                'reason': f"Momentum FVG - {self.pending_order['setup']['reason']}",
                                'leverage': self.pending_order['leverage'],
                                'order_id': self.pending_order['order_id'],
                                'strategy_type': 'momentum',
                                'fvg': self.pending_order.get('fvg', {})
                            }
                            
                            # Send Telegram notification
                            send_telegram_message(
                                f"✅ MOMENTUM ALO FILLED: ETH {self.pending_order['direction'].upper()} at ${entry_price:.4f} | "
                                f"FVG Stop: ${self.current_position['stop_loss']:.4f} | Size: {abs(position_size):.4f}"
                            )
                            
                            # Start stop monitoring
                            self.start_stop_monitoring()
                            
                            # Clear pending order
                            self.pending_order = None
                            return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Error processing Elixir update: {e}")
            return False

    def clear_order_updates_file(self):
        """Clear the order updates file after closing a position"""
        try:
            if os.path.exists(self.elixir_comm_file):
                # Clear the file by writing an empty array
                with open(self.elixir_comm_file, 'w') as f:
                    json.dump([], f)
                self.logger.info(f"🧹 Cleared order updates file: {self.elixir_comm_file}")
                # Reset the last update time
                self.last_order_update_time = None
        except Exception as e:
            self.logger.error(f"Error clearing order updates file: {e}")

    def add_order_to_elixir_monitor(self, order_result, setup, stop_loss):
        """Add order to Elixir monitor for tracking"""
        try:
            # Create order data for Elixir monitor
            order_data = {
                'symbol': 'ETH',
                'direction': setup['direction'],
                'size': order_result.get('fill_size', order_result.get('size')),
                'limit_price': setup['entry_price'],
                'stop_loss': stop_loss,
                'order_type': 'INVERTED_ALO',
                'strategy_type': 'momentum',
                'setup': setup
            }
            
            # Write to a file that the Elixir monitor can read
            elixir_order_file = "pending_orders.json"
            
            # Read existing orders
            existing_orders = []
            if os.path.exists(elixir_order_file):
                try:
                    with open(elixir_order_file, 'r') as f:
                        existing_orders = json.loads(f.read())
                except:
                    existing_orders = []
            
            # Add new order
            new_order = {
                'client_order_id': order_result.get('cloid'),
                'order_id': order_result.get('order_id'),
                'timestamp': datetime.now().isoformat(),
                'data': order_data
            }
            
            existing_orders.append(new_order)
            
            # Write back to file
            with open(elixir_order_file, 'w') as f:
                json.dump(existing_orders, f, indent=2)
            
            self.logger.info(f"📋 Added order to Elixir monitor: {order_result.get('cloid')}")
            
        except Exception as e:
            self.logger.error(f"Error adding order to Elixir monitor: {e}")

    # Add these new methods for ALO limit orders
    async def place_limit_order(self, symbol, is_buy, size, limit_price, order_type="Alo"):
        """Place a limit order using the official SDK"""
        try:
            # Initialize API clients if needed
            self.initialize_api_clients()
            
            self.logger.info(f"📋 Placing LIMIT ORDER: {symbol} {'BUY' if is_buy else 'SELL'} {size} @ ${limit_price:.4f}")
            
            # Generate a unique client order ID (128 bit hex string)
            import secrets
            cloid = "0x" + secrets.token_hex(16)  # 16 bytes = 128 bits
            
            self.logger.info(f"📋 Generated Client Order ID: {cloid}")
            
            # DEBUG: Let's see what the current market price is
            current_price = self.client.get_current_price(symbol)
            self.logger.info(f"📋 Current market price: ${current_price:.4f}")
            self.logger.info(f"📋 Limit price: ${limit_price:.4f}")
            
            if is_buy:
                self.logger.info(f"📋 For BUY: Limit price should be BELOW current price for ALO")
                if limit_price >= current_price:
                    self.logger.warning(f"⚠️ ALO BUY order limit price (${limit_price:.4f}) >= current price (${current_price:.4f}) - will be rejected!")
            else:
                self.logger.info(f"📋 For SELL: Limit price should be ABOVE current price for ALO")
                if limit_price <= current_price:
                    self.logger.warning(f"⚠️ ALO SELL order limit price (${limit_price:.4f}) <= current price (${current_price:.4f}) - will be rejected!")
            
            # Use the official SDK order method with client order ID in options
            order_result = self.exchange.order(
                symbol, 
                is_buy, 
                size, 
                limit_price, 
                {"limit": {"tif": order_type}, "cloid": cloid}  # Include cloid in options
            )
            
            self.logger.info(f"📋 LIMIT ORDER RESULT: {order_result}")
            
            # Check if order was rejected immediately
            if order_result and order_result["status"] == "ok":
                status = order_result["response"]["data"]["statuses"][0]
                
                if "filled" in status:
                    # Order was filled immediately
                    filled_data = status["filled"]
                    fill_price = float(filled_data.get("avgPx", limit_price))
                    fill_size = float(filled_data.get("totalSz", size))
                    order_id = filled_data.get("oid")
                    
                    self.logger.info(f"✅ LIMIT ORDER FILLED IMMEDIATELY!")
                    self.logger.info(f"  Fill Price: ${fill_price:.4f}")
                    self.logger.info(f"  Fill Size: {fill_size}")
                    self.logger.info(f"  Client Order ID: {cloid}")
                    
                    return {
                        'status': 'filled',
                        'order_id': order_id,
                        'cloid': cloid,
                        'fill_price': fill_price,
                        'fill_size': fill_size
                    }
                elif "resting" in status:
                    # Order placed but not filled (resting)
                    order_id = status["resting"]["oid"]
                    
                    self.logger.info(f"⏳ LIMIT ORDER PLACED BUT NOT FILLED (RESTING)")
                    self.logger.info(f"  Order ID: {order_id}")
                    self.logger.info(f"  Client Order ID: {cloid}")
                    
                    return {
                        'status': 'resting',
                        'order_id': order_id,
                        'cloid': cloid,
                        'limit_price': limit_price,
                        'size': size
                    }
                elif "cancelled" in status:
                    # Order was cancelled immediately (ALO rejection)
                    self.logger.warning(f"⚠️ ALO ORDER CANCELLED IMMEDIATELY (rejected)")
                    self.logger.warning(f"  This usually means the limit price would have matched immediately")
                    return {
                        'status': 'rejected',
                        'cloid': cloid,
                        'reason': 'ALO order would have matched immediately'
                    }
            else:
                self.logger.error(f"❌ LIMIT ORDER FAILED: {order_result}")
                return {'status': 'failed', 'error': order_result}
                
        except Exception as e:
            self.logger.error(f"❌ Error placing limit order: {e}")
            return {'status': 'error', 'error': str(e)}

    async def check_order_status(self, symbol, order_id, cloid=None):
        """Check order status using the official SDK - following the correct pattern"""
        try:
            # Initialize API clients if needed
            self.initialize_api_clients()
            
            self.logger.info(f"🔍 Checking order status:")
            self.logger.info(f"  Order ID: {order_id}")
            if cloid:
                self.logger.info(f"  Client Order ID: {cloid}")
            
            # If we have a client order ID, we should use that instead of the order ID
            if cloid:
                self.logger.info(f" Using Client Order ID for status check: {cloid}")
                
                # Try to query by client order ID first
                # Note: The SDK might not have a direct method for this, so we'll check positions
                user_state = self.info.user_state(self.wallet.address)
                if "assetPositions" in user_state:
                    for position in user_state["assetPositions"]:
                        pos_data = position["position"]
                        if pos_data.get("coin") == symbol:
                            size = float(pos_data.get("szi", 0))
                            if size != 0:
                                # We have a position - ALO order was filled
                                self.logger.info(f"✅ ALO ORDER FILLED (found in positions)!")
                                return {
                                    'status': 'filled_via_position',
                                    'order_id': order_id,
                                    'cloid': cloid,
                                    'position_data': pos_data
                                }
                
                # If no position found, the order might still be resting
                # For now, let's try the regular order ID as fallback
                self.logger.info(f" No position found, trying regular order ID as fallback")
            
            # Try converting order_id to int if it's a string
            if isinstance(order_id, str):
                try:
                    order_id_int = int(order_id)
                except ValueError:
                    self.logger.error(f"  Could not convert order_id to int: {order_id}")
                    return {'status': 'error', 'error': 'Invalid order ID format'}
            else:
                order_id_int = order_id
            
            # FIXED: Follow the correct pattern for querying order status by OID
            # This matches the pattern you mentioned
            order_status = self.info.query_order_by_oid(self.wallet.address, order_id_int)
            
            self.logger.info(f" ORDER STATUS for {order_id_int}: {order_status}")
            
            # Check if order exists and is not unknown
            if order_status and order_status.get('status') != 'unknownOid':
                # Order found - check if it's filled or still resting
                if 'filled' in order_status:
                    # Order was filled
                    self.logger.info(f"✅ ORDER FILLED (via query_order_by_oid)!")
                    return {
                        'status': 'filled_via_query',
                        'order_id': order_id_int,
                        'cloid': cloid,
                        'order_data': order_status
                    }
                else:
                    # Order is still resting
                    self.logger.info(f"⏳ ORDER STILL RESTING (via query_order_by_oid)")
                    return {
                        'status': 'resting_via_query',
                        'order_id': order_id_int,
                        'cloid': cloid,
                        'order_data': order_status
                    }
            else:
                # Order not found via query - check if we have a position (might have been filled)
                user_state = self.info.user_state(self.wallet.address)
                if "assetPositions" in user_state:
                    for position in user_state["assetPositions"]:
                        pos_data = position["position"]
                        if pos_data.get("coin") == symbol:
                            size = float(pos_data.get("szi", 0))
                            if size != 0:
                                # We have a position - ALO order was filled
                                self.logger.info(f"✅ ALO ORDER FILLED (found in positions)!")
                                return {
                                    'status': 'filled_via_position',
                                    'order_id': order_id_int,
                                    'cloid': cloid,
                                    'position_data': pos_data
                                }
                
                # Order not found and no position - might be cancelled or error
                self.logger.warning(f"⚠️ Order {order_id_int} not found and no position")
                return {
                    'status': 'not_found',
                    'order_id': order_id_int,
                    'cloid': cloid,
                    'message': f'Order not found: {order_status}'
                }
            
        except Exception as e:
            self.logger.error(f"❌ Error checking order status: {e}")
            return {'status': 'error', 'error': str(e)}

    async def check_order_status_by_cloid(self, symbol, cloid):
        """Check order status using client order ID - simplified approach without open_orders"""
        try:
            # Initialize API clients if needed
            self.initialize_api_clients()
            
            self.logger.info(f"🔍 Checking order status by Client Order ID: {cloid}")
            
            # First check if we have a position (order was filled)
            user_state = self.info.user_state(self.wallet.address)
            
            if "assetPositions" in user_state:
                for position in user_state["assetPositions"]:
                    pos_data = position["position"]
                    if pos_data.get("coin") == symbol:
                        size = float(pos_data.get("szi", 0))
                        if size != 0:
                            # We have a position - our cloid order was filled
                            self.logger.info(f"✅ CLIENT ORDER FILLED (found position)!")
                            return {
                                'status': 'filled_via_position',
                                'cloid': cloid,
                                'position_data': pos_data
                            }
            
            # Since we can't use open_orders(), we'll assume the order is still resting
            # if we don't find a position. This is a reasonable assumption for ALO orders.
            self.logger.info(f"⏳ CLIENT ORDER STILL RESTING (no position found)")
            return {
                'status': 'resting',
                'cloid': cloid,
                'message': 'Order assumed to be resting (no position found)'
            }
            
        except Exception as e:
            self.logger.error(f"❌ Error checking order status by cloid: {e}")
            return {'status': 'error', 'error': str(e)}

    async def check_order_status_via_open_orders(self, symbol, order_id):
        """Check order status by looking through open orders - simplified approach"""
        try:
            # Initialize API clients if needed
            self.initialize_api_clients()
            
            self.logger.info(f"🔍 Checking order {order_id} via open orders...")
            
            # Since open_orders() doesn't exist in the SDK, we'll use a simplified approach
            # Check if we have a position (order was filled)
            user_state = self.info.user_state(self.wallet.address)
            
            if "assetPositions" in user_state:
                for position in user_state["assetPositions"]:
                    pos_data = position["position"]
                    if pos_data.get("coin") == symbol:
                        size = float(pos_data.get("szi", 0))
                        if size != 0:
                            # We have a position - order was filled
                            self.logger.info(f"✅ ORDER FILLED (found position)!")
                            return {
                                'status': 'filled_via_position',
                                'order_id': order_id,
                                'position_data': pos_data
                            }
            
            # If no position found, assume order is still resting
            self.logger.info(f"⏳ ORDER STILL RESTING (no position found)")
            return {
                'status': 'resting',
                'order_id': order_id,
                'message': 'Order assumed to be resting (no position found)'
            }
            
        except Exception as e:
            self.logger.error(f"❌ Error checking order status: {e}")
            return {'status': 'error', 'error': str(e)}

    async def cancel_order(self, symbol, order_id):
        """Cancel order using the official SDK - simplified version"""
        try:
            # Initialize API clients if needed
            self.initialize_api_clients()
            
            self.logger.info(f"❌ Cancelling order: {symbol} - ID: {order_id}")
            
            # Try converting order_id to int if it's a string
            if isinstance(order_id, str):
                try:
                    order_id_int = int(order_id)
                except ValueError:
                    self.logger.error(f"Could not convert order_id to int: {order_id}")
                    return {'status': 'error', 'error': 'Invalid order ID format'}
            else:
                order_id_int = order_id
            
            # Use the official SDK cancel method with order ID (like in the example)
            cancel_result = self.exchange.cancel(symbol, order_id_int)
            
            self.logger.info(f"📋 CANCEL ORDER RESULT: {cancel_result}")
            
            if cancel_result and cancel_result["status"] == "ok":
                self.logger.info(f"✅ ORDER CANCELLED SUCCESSFULLY")
                return {'status': 'cancelled', 'order_id': order_id_int}
            else:
                self.logger.error(f"❌ FAILED TO CANCEL ORDER: {cancel_result}")
                return {'status': 'failed', 'error': cancel_result}
                
        except Exception as e:
            self.logger.error(f"❌ Error cancelling order: {e}")
            return {'status': 'error', 'error': str(e)}

    async def monitor_pending_order(self):
        """Monitor ALO pending order CONSTANTLY - check every 0.5 seconds for 5 minutes"""
        if not self.pending_order:
            return
        
        order_id = self.pending_order['order_id']
        symbol = self.pending_order['symbol']
        limit_price = self.pending_order['limit_price']
        direction = self.pending_order['direction']
        
        # 5 minute timeout with constant checking
        timeout_seconds = 300  # 5 minutes
        check_interval = 2   # Check every 0.5 seconds
        
        self.logger.info(f"🔍 STARTING CONSTANT ALO MONITORING:")
        self.logger.info(f"  Order ID: {order_id}")
        self.logger.info(f"  Direction: {direction.upper()}")
        self.logger.info(f"  Limit Price: ${limit_price:.4f}")
        self.logger.info(f"  Check Interval: {check_interval}s")
        self.logger.info(f"  Timeout: {timeout_seconds}s")
        self.logger.info(f"  Will check order status every 0.5 seconds...")
        
        start_time = datetime.now()
        check_count = 0
        
        while (datetime.now() - start_time).total_seconds() < timeout_seconds:
            try:
                check_count += 1
                
                # First check Elixir monitor updates (real-time)
                elixir_update = self.read_elixir_order_updates()
                if elixir_update:
                    self.logger.info(f"📨 Elixir update received: {elixir_update.get('type', 'unknown')}")
                    # Process Elixir update if it matches our pending order
                    if self.process_elixir_update(elixir_update):
                        return  # Order was filled via Elixir update
                
                # Log progress every 120 checks (every 60 seconds) - reduced frequency
                if check_count % 120 == 0:
                    elapsed = (datetime.now() - start_time).total_seconds()
                    remaining = timeout_seconds - elapsed
                    # Only log if needed for Elixir or error
                    pass  # No regular progress log

                # Fallback to direct API checking
                if self.pending_order['order_type'] in ['ALO', 'INVERTED_ALO']:
                    cloid = self.pending_order['order_id'] # Use the client order ID for ALO
                    status = await self.check_order_status_by_cloid(symbol, cloid)
                else: # For other order types, use the original order_id
                    status = await self.check_order_status(symbol, order_id)

                if status['status'] in ['found', 'resting_via_query']:
                    # Order still exists and is resting
                    order_data = status['order_data']
                    # Continue monitoring...
                    
                elif status['status'] in ['filled_via_query', 'filled_via_position', 'filled_via_disappearance', 'filled_via_oid_query', 'filled_via_oid_disappearance']:
                    # ALO order was filled! IMMEDIATELY start stop monitoring
                    self.logger.info(f"✅ ALO ORDER FILLED! Starting stop monitoring IMMEDIATELY")
                    
                    if status['status'] == 'filled_via_query':
                        order_data = status['order_data']
                        # Extract fill data from query result
                        entry_price = float(order_data.get('avgPx', limit_price))
                        size = float(order_data.get('totalSz', self.pending_order['size']))
                    else:  # filled_via_position
                        pos_data = status['position_data']
                        entry_price = float(pos_data.get("entryPx", limit_price))
                        size = abs(float(pos_data.get("szi", self.pending_order['size'])))
                    
                    # Create position immediately
                    self.current_position = {
                        'direction': self.pending_order['direction'],
                        'entry_price': entry_price,
                        'stop_loss': self.pending_order['stop_loss'],
                        'take_profit': self.pending_order['setup'].get('take_profit'),
                        'size': size,
                        'entry_time': datetime.now(),
                        'reason': f"Momentum FVG - {self.pending_order['setup']['reason']}",
                        'leverage': self.pending_order['leverage'],
                        'order_id': order_id,
                        'strategy_type': 'momentum',
                        'fvg': self.pending_order.get('fvg', {})
                    }
                    
                    self.logger.info(f"✅ POSITION CREATED:")
                    self.logger.info(f"  Entry Price: ${self.current_position['entry_price']:.4f}")
                    self.logger.info(f"  Size: {self.current_position['size']:.4f}")
                    self.logger.info(f"  Stop: ${self.current_position['stop_loss']:.4f}")
                    
                    # Send Telegram notification IMMEDIATELY
                    send_telegram_message(
                        f"✅ MOMENTUM ALO FILLED: ETH {direction.upper()} at ${self.current_position['entry_price']:.4f} | "
                        f"FVG Stop: ${self.current_position['stop_loss']:.4f} | Size: {self.current_position['size']:.4f}"
                    )
                    
                    # START STOP MONITORING IMMEDIATELY
                    self.start_stop_monitoring()
                    
                    # Place initial ALO stop order immediately
                    asyncio.create_task(self.place_alo_stop_order(
                        self.current_position['stop_loss'],
                        self.current_position['size']
                    ))
                    
                    # Clear pending order
                    self.pending_order = None
                    return
                        
                elif status['status'] == 'filled_via_position':
                    # Order not found but we have a position - order was filled
                    self.logger.info(f"✅ ALO ORDER FILLED (detected via position)!")
                    
                    pos_data = status['position_data']
                    size = float(pos_data.get("szi", 0))
                    
                    self.current_position = {
                        'direction': self.pending_order['direction'],
                        'entry_price': float(pos_data.get("entryPx", self.pending_order['limit_price'])),
                        'stop_loss': self.pending_order['stop_loss'],
                        'take_profit': self.pending_order['setup'].get('take_profit'),
                        'size': abs(size),
                        'entry_time': datetime.now(),
                        'reason': f"FVG Retracement - {self.pending_order['setup']['reason']}",
                        'leverage': self.pending_order['leverage'],
                        'order_id': order_id
                    }
                    
                    self.logger.info(f"✅ POSITION CREATED:")
                    self.logger.info(f"  Entry Price: ${self.current_position['entry_price']:.4f}")
                    self.logger.info(f"  Size: {self.current_position['size']:.4f}")
                    
                    # Send Telegram notification IMMEDIATELY
                    send_telegram_message(
                        f"✅ ALO ORDER FILLED: ETH {direction.upper()} at ${self.current_position['entry_price']:.4f} | "
                        f"Starting stop monitoring | Stop: ${self.current_position['stop_loss']:.4f}"
                    )
                    
                    # START STOP MONITORING IMMEDIATELY
                    self.start_stop_monitoring()
                    
                    # Clear pending order
                    self.pending_order = None
                    return
                
                elif status['status'] == 'filled_via_history':
                    # Order found in fill history
                    self.logger.info(f"✅ ALO ORDER FILLED (detected via history)!")
                    
                    fill_data = status['fill_data']
                    
                    self.current_position = {
                        'direction': self.pending_order['direction'],
                        'entry_price': float(fill_data.get("avgPx", self.pending_order['limit_price'])),
                        'stop_loss': self.pending_order['stop_loss'],
                        'take_profit': self.pending_order['setup'].get('take_profit'),
                        'size': float(fill_data.get("totalSz", self.pending_order['size'])),
                        'entry_time': datetime.now(),
                        'reason': f"FVG Retracement - {self.pending_order['setup']['reason']}",
                        'leverage': self.pending_order['leverage'],
                        'order_id': order_id
                    }
                    
                    self.logger.info(f"✅ POSITION CREATED:")
                    self.logger.info(f"  Entry Price: ${self.current_position['entry_price']:.4f}")
                    self.logger.info(f"  Size: {self.current_position['size']:.4f}")
                    
                    # Send Telegram notification IMMEDIATELY
                    send_telegram_message(
                        f"✅ ALO ORDER FILLED: ETH {direction.upper()} at ${self.current_position['entry_price']:.4f} | "
                        f"Starting stop monitoring | Stop: ${self.current_position['stop_loss']:.4f}"
                    )
                    
                    # START STOP MONITORING IMMEDIATELY
                    self.start_stop_monitoring()
                    
                    # Clear pending order
                    self.pending_order = None
                    return
                
                elif status['status'] == 'not_found':
                    # Order not found - check if we have a position (might have been filled)
                    user_state = self.info.user_state(self.wallet.address)
                    if "assetPositions" in user_state:
                        for position in user_state["assetPositions"]:
                            pos_data = position["position"]
                            if pos_data.get("coin") == symbol:
                                size = float(pos_data.get("szi", 0))
                                if size != 0:
                                    # We have a position - ALO order was filled
                                    self.logger.info(f"✅ ALO ORDER FILLED (found in positions)!")
                                    
                                    self.current_position = {
                                        'direction': self.pending_order['direction'],
                                        'entry_price': float(pos_data.get("entryPx", limit_price)),
                                        'stop_loss': self.pending_order['stop_loss'],
                                        'take_profit': self.pending_order['setup'].get('take_profit'),
                                        'size': abs(size),
                                        'entry_time': datetime.now(),
                                        'reason': f"FVG Retracement - {self.pending_order['setup']['reason']}",
                                        'leverage': self.pending_order['leverage'],
                                        'order_id': order_id
                                    }
                                    
                                    self.logger.info(f"✅ POSITION CREATED:")
                                    self.logger.info(f"  Entry Price: ${self.current_position['entry_price']:.4f}")
                                    self.logger.info(f"  Size: {self.current_position['size']:.4f}")
                                    
                                    # Send Telegram notification IMMEDIATELY
                                    send_telegram_message(
                                        f"✅ ALO ORDER FILLED: ETH {direction.upper()} at ${self.current_position['entry_price']:.4f} | "
                                        f"Starting stop monitoring | Stop: ${self.current_position['stop_loss']:.4f}"
                                    )
                                    
                                    # START STOP MONITORING IMMEDIATELY
                                    self.start_stop_monitoring()
                                    
                                    # Clear pending order
                                    self.pending_order = None
                                    return
                    
                    # Order not found and no position - might be cancelled or error
                    self.logger.warning(f"⚠️ Order {order_id} not found and no position - might be cancelled")
                    break
                
                # Log progress every 120 checks (every 60 seconds) - reduced frequency
                if check_count % 120 == 0:
                    elapsed = (datetime.now() - start_time).total_seconds()
                    remaining = timeout_seconds - elapsed
                    # Only log if needed for Elixir or error
                    pass  # No regular progress log

                # Wait 0.5 seconds before next check
                await asyncio.sleep(check_interval)
                
            except Exception as e:
                self.logger.error(f"Error in constant ALO monitoring: {e}")
                await asyncio.sleep(check_interval)
        
        # 5 minute timeout reached - cancel the ALO order
        elapsed = (datetime.now() - start_time).total_seconds()
        self.logger.info(f"⏰ ALO ORDER TIMEOUT after {elapsed:.0f}s")
        self.logger.info(f"  Order {order_id} did not fill within 5 minutes")
        self.logger.info(f"  Cancelling order...")
        
        # Use regular order ID for cancellation
        cancel_order_id = self.pending_order.get('regular_order_id', order_id)
        cancel_result = await self.cancel_order(symbol, cancel_order_id)
        
        if cancel_result['status'] == 'cancelled':
            self.logger.info(f"✅ ALO ORDER CANCELLED SUCCESSFULLY after timeout")
            send_telegram_message(f"⏰ ALO ORDER CANCELLED: ETH {direction.upper()} (5min timeout)")
        else:
            self.logger.error(f"❌ FAILED TO CANCEL ALO ORDER: {cancel_result}")
        
        # Clear pending order
        self.pending_order = None

    async def open_live_position(self, setup, current_price):
        """Open a live trading position using INVERTED ALO limit orders for momentum"""
        if self.current_position is not None or self.position_lock:
            self.logger.warning(f"Already in a position for ETH or position lock active, cannot open new one")
            return False
        
        try:
            # Set position lock to prevent race conditions
            self.position_lock = True
            
            self.logger.info(f"DEBUG: Opening live position for ETH with setup: {setup}")
            
            # MOMENTUM APPROACH: Stop placement based on FVG
            # For LONG: Stop right below the FVG bottom
            # For SHORT: Stop right above the FVG top
            
            if setup['direction'] == 'long':
                # LONG: Stop below FVG bottom (if price goes through FVG, plan failed)
                fvg_bottom = setup['fvg']['bottom']
                stop_loss = fvg_bottom - self.config.get('STOP_LOSS_BUFFER', 0.005)
                self.logger.info(f"📈 MOMENTUM LONG: Stop below FVG bottom")
                self.logger.info(f"  FVG Bottom: ${fvg_bottom:.4f}")
                self.logger.info(f"  Stop Loss: ${stop_loss:.4f}")
            else:
                # SHORT: Stop above FVG top (if price goes through FVG, plan failed)
                fvg_top = setup['fvg']['top']
                stop_loss = fvg_top + self.config.get('STOP_LOSS_BUFFER', 0.005)
                self.logger.info(f"📉 MOMENTUM SHORT: Stop above FVG top")
                self.logger.info(f"  FVG Top: ${fvg_top:.4f}")
                self.logger.info(f"  Stop Loss: ${stop_loss:.4f}")
            
            # Calculate position size with the FVG-based stop
            position_size, adjusted_stop = self.calculate_position_size(setup['entry_price'], stop_loss, setup['direction'])
            
            # Use adjusted stop if it was changed (respects minimum size rule)
            final_stop = adjusted_stop if adjusted_stop != stop_loss else stop_loss
            
            # Get leverage for ETH
            leverage = self.config['MAX_LEVERAGE'].get("ETH", 10)
            
            # Place the actual order on Hyperliquid using INVERTED ALO limit order
            is_buy = setup['direction'] == 'long'
            
            # INVERTED ALO limit price - FOLLOWING MOMENTUM
            # For LONG: Place limit order ABOVE current price (follows momentum up)
            # For SHORT: Place limit order BELOW current price (follows momentum down)
            
            if is_buy:
                # LONG: Place limit buy BELOW current price for ALO (subtract buffer)
                # Use FVG entry target as base, with 0.05 cent buffer BELOW for ALO
                limit_price = setup['entry_price'] - 0.0005  # 0.05 cents BELOW FVG entry
                self.logger.info(f"📈 MOMENTUM LONG SETUP: Placing ALO limit buy BELOW FVG entry")
                self.logger.info(f"  FVG Entry Target: ${setup['entry_price']:.4f}")
                self.logger.info(f"  ALO Limit Price: ${limit_price:.4f} (0.05 cents BELOW FVG)")
            else:
                # SHORT: Place limit sell ABOVE current price for ALO (add buffer)
                # Use FVG entry target as base, with 0.05 cent buffer ABOVE for ALO
                limit_price = setup['entry_price'] + 0.0005  # 0.05 cents ABOVE FVG entry
                self.logger.info(f"📉 MOMENTUM SHORT SETUP: Placing ALO limit sell ABOVE FVG entry")
                self.logger.info(f"  ALO Limit Price: ${limit_price:.4f} (0.05 cents ABOVE FVG)")
            
            # Round to 3 decimal places
            limit_price = round(limit_price, 3)
            
            self.logger.info(f"📈 Opening MOMENTUM position for ETH (INVERTED ALO LIMIT ORDER):")
            self.logger.info(f"  Direction: {setup['direction'].upper()}")
            self.logger.info(f"  INVERTED ALO Limit Price: ${limit_price:.4f}")
            self.logger.info(f"  Current Market: ${current_price:.4f}")
            self.logger.info(f"  FVG Entry Target: ${setup['entry_price']:.4f}")
            self.logger.info(f"  FVG-Based Stop: ${final_stop:.4f}")
            self.logger.info(f"  Size: {position_size:.4f}")
            self.logger.info(f"  Risk: $10")
            self.logger.info(f"  Leverage: {leverage}x")
            self.logger.info(f"  Order Type: INVERTED ALO (Momentum Following)")
            
            order_result = await self.place_limit_order("ETH", is_buy, position_size, limit_price, "Alo")
            
            if order_result['status'] in ['filled', 'resting']:
                self.add_order_to_elixir_monitor(order_result, setup, final_stop)
                
            if order_result['status'] == 'filled':
                # Order was filled immediately (more likely with momentum approach)
                self.current_position = {
                    'direction': setup['direction'],
                    'entry_price': order_result['fill_price'],
                    'stop_loss': final_stop,
                    'take_profit': setup.get('take_profit'),
                    'size': order_result['fill_size'],
                    'entry_time': datetime.now(),
                    'reason': f"Momentum FVG - {setup['reason']}",
                    'leverage': leverage,
                    'order_id': order_result['order_id'],
                    'strategy_type': 'momentum',  # Mark as momentum strategy
                    'fvg': setup['fvg']  # Store FVG info for reference
                }
                
                self.logger.info(f"✅ INVERTED ALO ORDER FILLED IMMEDIATELY:")
                self.logger.info(f"  Actual Entry: ${order_result['fill_price']:.4f}")
                self.logger.info(f"  Actual Size: {order_result['fill_size']:.4f}")
                self.logger.info(f"  FVG-Based Stop: ${final_stop:.4f}")
                
                # Send Telegram notification
                send_telegram_message(
                    f"✅ MOMENTUM ALO FILLED: ETH {setup['direction'].upper()} at ${order_result['fill_price']:.4f} | "
                    f"FVG Stop: ${final_stop:.4f} | Size: {order_result['fill_size']:.4f} | Risk: $10"
                )
                
                # Start stop monitoring
                self.start_stop_monitoring()
                
                self.position_lock = False
                return True
                
            elif order_result['status'] == 'resting':
                # Order placed but not filled - this is expected with ALO
                # FIXED: Store the CLIENT ORDER ID for monitoring, not the regular order ID
                self.pending_order = {
                    'order_id': order_result['cloid'],  # Store client order ID for monitoring
                    'regular_order_id': order_result['order_id'],  # Store regular order ID as backup
                    'symbol': 'ETH',
                    'direction': setup['direction'],
                    'limit_price': limit_price,
                    'size': position_size,
                    'stop_loss': final_stop,
                    'setup': setup,
                    'leverage': leverage,
                    'order_time': datetime.now(),
                    'order_type': 'INVERTED_ALO',
                    'strategy_type': 'momentum',
                    'fvg': setup['fvg']  # Store FVG info for reference
                }
                
                self.logger.info(f"⏳ INVERTED ALO LIMIT ORDER PLACED (RESTING):")
                self.logger.info(f"  Client Order ID: {order_result['cloid']}")
                self.logger.info(f"  Regular Order ID: {order_result['order_id']}")
                self.logger.info(f"  Waiting for momentum continuation to ${limit_price:.4f}")
                self.logger.info(f"  FVG-Based Stop: ${final_stop:.4f}")
                self.logger.info(f"  Will monitor for fill or cancel after timeout")
                
                # Send Telegram notification
                send_telegram_message(
                    f"⏳ MOMENTUM ALO PLACED: ETH {setup['direction'].upper()} @ ${limit_price:.4f} | "
                    f"FVG Stop: ${final_stop:.4f} | Waiting for momentum"
                )
                
                # Start monitoring the pending order
                asyncio.create_task(self.monitor_pending_order())
                
                self.position_lock = False
                return True
                
            else:
                self.logger.error(f"❌ FAILED to place inverted ALO limit order: {order_result}")
                self.position_lock = False
                return False
                
        except Exception as e:
            self.logger.error(f"Error opening momentum position: {e}")
            self.position_lock = False
            return False
    
    def close_live_position(self, reason="manual"):
        """Close the current live position on Hyperliquid and cancel stop order."""
        if self.current_position is None:
            self.logger.warning("No position to close")
            return False
        try:
            self.initialize_api_clients()
            self.logger.info(f"📉 Closing live position for ETH (INSTANT EXECUTION): {reason}")
            # Use market_close for instant execution
            close_result = self.exchange.market_close(
                coin="ETH",
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
                    
                    self.logger.info(f"✅ LIVE POSITION CLOSED FOR ETH (INSTANT EXECUTION):")
                    self.logger.info(f"  Exit Price: ${close_price:.4f}")
                    self.logger.info(f"  P&L: ${pnl:.2f}")
                    self.logger.info(f"  Reason: {reason}")
                    self.logger.info(f"  Total P&L: ${self.total_pnl:.2f}")
                    self.logger.info(f"  Win Rate: {(self.winning_trades/self.total_trades)*100:.1f}%")
                    
                    # Send Telegram notification
                    pnl_emoji = "" if pnl > 0 else "🔴"
                    send_telegram_message(
                        f"{pnl_emoji} INSTANT TRADE CLOSED: ETH at ${close_price:.4f} | "
                        f"P&L: ${pnl:.2f} | Reason: {reason} | Total: ${self.total_pnl:.2f}"
                    )
                    
                    # Stop monitoring
                    self.stop_stop_monitoring()
                    
                    # Clear position
                    self.current_position = None
                    self.last_position_close_time = datetime.now()
                    
                    # Clear order updates file after closing position
                    self.clear_order_updates_file()
                    
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
            self.logger.info("Started continuous stop monitoring for ETH")
    
    def stop_stop_monitoring(self):
        """Stop the continuous stop monitoring task"""
        self.stop_monitoring_active = False
        if self.stop_monitoring_task and not self.stop_monitoring_task.done():
            self.stop_monitoring_task.cancel()
        self.logger.info("Stopped continuous stop monitoring for ETH")
    

    async def monitor_stops_continuously(self):
        """Monitor stops with FVG-based momentum logic"""
        self.logger.info("🔍 Starting FVG-based momentum stop monitoring for ETH")
        self.logger.info("🔍 This should run every 0.5 seconds when in a position")
        self.stop_monitoring_active = True
        monitor_count = 0
        
        while self.stop_monitoring_active and self.current_position is not None:
            try:
                monitor_count += 1
                
                # Get current price
                current_price = self.client.get_current_price("ETH")
                
                if current_price is None:
                    self.logger.debug(f"🔍 MONITOR #{monitor_count}: Failed to get current price")
                    await asyncio.sleep(2)
                    continue
                
                position = self.current_position
                direction = position['direction']
                entry_price = position['entry_price']
                stop_loss = position['stop_loss']
                
                # Get FVG info for context
                fvg = position.get('fvg', {})
                fvg_bottom = fvg.get('bottom', 0)
                fvg_top = fvg.get('top', 0)
                
                # Calculate current P&L and R:R ratio
                if direction == 'long':
                    current_pnl = current_price - entry_price
                    current_risk = entry_price - stop_loss
                    rr_ratio = current_pnl / current_risk if current_risk > 0 else 0
                    stop_distance = current_price - stop_loss
                    fvg_distance = current_price - fvg_bottom
                else:  # short
                    current_pnl = entry_price - current_price
                    current_risk = stop_loss - entry_price
                    rr_ratio = current_pnl / current_risk if current_risk > 0 else 0
                    stop_distance = stop_loss - current_price
                    fvg_distance = fvg_top - current_price
                
                # Log detailed monitoring info every 10 checks (every 5 seconds)
                if monitor_count % 10 == 0:
                    self.logger.info(f"🔍 MONITOR #{monitor_count}: ETH {direction.upper()}")
                    self.logger.info(f"  Price: ${current_price:.4f} | Entry: ${entry_price:.4f} | Stop: ${stop_loss:.4f}")
                    self.logger.info(f"  P&L: ${current_pnl:.4f} | R:R: {rr_ratio:.2f} | Stop Distance: ${stop_distance:.4f}")
                    if fvg:
                        self.logger.info(f"  FVG Bottom: ${fvg_bottom:.4f} | FVG Top: ${fvg_top:.4f} | FVG Distance: ${fvg_distance:.4f}")
                
                # MOMENTUM APPROACH: More aggressive trailing
                # Start trailing immediately (no need to wait for 1:1 RR)
                if position.get('strategy_type') == 'momentum':
                    # Enable trailing immediately for momentum strategy
                    if 'trailing_enabled' not in position:
                        self.logger.info(f"✅ MOMENTUM TRAILING ENABLED immediately!")
                        position['trailing_enabled'] = True
                        position['original_stop_loss'] = position['stop_loss']
                        send_telegram_message(f"✅ MOMENTUM TRAILING ENABLED for ETH")
                    
                    # Update trailing stop more frequently for momentum
                    if monitor_count % 10 == 0:  # Every 5 seconds
                        try:
                            ltf_data, _, _ = await self.fetch_live_data()
                            if ltf_data is not None:
                                old_stop = position['stop_loss']
                                updated = self.strategy.update_trailing_stop(ltf_data, position)
                                if updated:
                                    new_stop = position['stop_loss']
                                    self.logger.info(f"🔄 MOMENTUM TRAILING UPDATED!")
                                    self.logger.info(f"  Old Stop: ${old_stop:.4f} → New Stop: ${new_stop:.4f}")
                                    self.logger.info(f"  R:R: {rr_ratio:.2f} | Profit: ${current_pnl:.4f}")
                                    send_telegram_message(f"🔄 MOMENTUM TRAILING: ETH ${old_stop:.4f} → ${new_stop:.4f} (R:R: {rr_ratio:.2f})")
                        except Exception as e:
                            self.logger.error(f"Error updating momentum trailing stop: {e}")
            
                # Check if stop loss is hit
                if direction == 'long' and current_price <= position['stop_loss']:
                    self.logger.info(f"🛑 MOMENTUM MONITOR #{monitor_count}: FVG STOP HIT for ETH LONG")
                    self.logger.info(f"  Price: ${current_price:.4f} <= FVG Stop: ${position['stop_loss']:.4f}")
                    self.logger.info(f"  Final P&L: ${current_pnl:.4f} | R:R: {rr_ratio:.2f}")
                    send_telegram_message(f"🛑 FVG STOP HIT: ETH LONG at ${position['stop_loss']:.4f}")
                    self.close_live_position("FVG Stop Loss Hit")
                    self.stop_monitoring_active = False
                    break
                elif direction == 'short' and current_price >= position['stop_loss']:
                    self.logger.info(f"🛑 MOMENTUM MONITOR #{monitor_count}: FVG STOP HIT for ETH SHORT")
                    self.logger.info(f"  Price: ${current_price:.4f} >= FVG Stop: ${position['stop_loss']:.4f}")
                    self.logger.info(f"  Final P&L: ${current_pnl:.4f} | R:R: {rr_ratio:.2f}")
                    send_telegram_message(f"🛑 FVG STOP HIT: ETH SHORT at ${position['stop_loss']:.4f}")
                    self.close_live_position("FVG Stop Loss Hit")
                    self.stop_monitoring_active = False
                    break
                
                # Remove market order logic on stop hit
                # Wait 0.5 seconds before next check
                await asyncio.sleep(0.5)
                
            except Exception as e:
                self.logger.error(f"Error in FVG momentum stop monitoring: {e}")
                await asyncio.sleep(0.5)
        
        self.logger.info("🔍 Continuous stop monitoring stopped for ETH")
    
    async def run_live_trading(self):
        """Main live trading loop"""
        self.logger.info("🚀 Starting ETH Live Trading Bot...")
        self.logger.info(f"Trading symbol: ETH")
        self.logger.info(f"Risk per trade: $1")  # FIXED: Show $10 risk
        
        candle_idx = 0  # Track candle index for cooldown
        cycle_count = 0
        
        try:
            while True:  # Run indefinitely
                cycle_count += 1
                
                try:
                    # DEBUG: Log every cycle for visibility
                    if cycle_count % 6 == 0:  # Every 60 seconds (6 cycles * 10.0s)
                        self.logger.info(f"🔄 TRADING CYCLE #{cycle_count} - Searching for ETH setups...")
                    
                    # Check if we're in a position OR have a pending order
                    if self.current_position is not None or self.pending_order is not None:
                        # Already in a position OR waiting for ALO order to fill
                        if self.current_position:
                            self.logger.info(f"📊 ETH already has active position: {self.current_position['direction']} at ${self.current_position['entry_price']:.2f}")
                        if self.pending_order:
                            self.logger.info(f"⏳ ETH has pending ALO order: {self.pending_order['direction']} @ ${self.pending_order['limit_price']:.2f}")
                        
                        await asyncio.sleep(10.0)
                        continue
                    
                    # Check cooldown period (5 minutes instead of 1 minute)
                    cooldown_remaining = None
                    if self.last_position_close_time:
                        time_since_close = datetime.now() - self.last_position_close_time
                        cooldown_remaining = 300 - time_since_close.total_seconds()  # 5 minutes = 300 seconds
                    
                    if cooldown_remaining and cooldown_remaining > 0:
                        self.logger.info(f"⏳ COOLDOWN ACTIVE for ETH: {cooldown_remaining:.0f}s remaining")
                        candle_idx += 1
                        await asyncio.sleep(10.0)
                        continue
                    
                    # Only check Elixir monitor if we have a pending order
                    if self.pending_order is not None:
                        elixir_update = self.read_elixir_order_updates()
                        if elixir_update:
                            self.logger.info(f"📨 ELIXIR UPDATE DETECTED!")
                            self.logger.info(f"   Timestamp: {elixir_update.get('timestamp', 'unknown')}")
                            # Process Elixir update
                            if self.process_elixir_update(elixir_update):
                                self.logger.info(f"✅ ELIXIR UPDATE PROCESSED SUCCESSFULLY!")
                    
                    # MAIN LOOP: No position, no pending order, no cooldown - SEARCHING FOR TRADES
                    if cycle_count % 3 == 0:  # Log every 30 seconds (3 cycles * 10.0s)
                        self.logger.info(f"🔍 MAIN LOOP: Searching for ETH trade setups... (Cycle #{cycle_count})")
                    
                    # Fetch live data for ETH
                    ltf_data, htf_data, current_price = await self.fetch_live_data()
                    if ltf_data is not None and current_price is not None:
                        # Get current candle info for stop loss checks
                        current_candle = ltf_data.iloc[-1]
                        current_low = current_candle['low']
                        current_high = current_candle['high']
                        
                        # Check for new entry if no position and no pending order
                        if self.current_position is None and self.pending_order is None:
                            setup = self.strategy.check_entry_conditions(ltf_data, htf_data)
                            if setup:
                                setup['symbol'] = 'ETH'  # Add symbol to setup
                                # Only log locally, don't send Telegram for setups
                                self.logger.info(f"✅ Setup found for ETH: {setup['direction']} at ${setup['entry_price']:.2f}")
                                success = await self.open_live_position(setup, current_price)
                                if not success:
                                    self.logger.error(f"❌ FAILED to open position for ETH")
                                else:
                                    self.logger.info(f"✅ SUCCESSFULLY opened position for ETH")
                    
                    candle_idx += 1
                    await asyncio.sleep(10.0)  # Search for new positions every 10 seconds
                    
                except Exception as e:
                    self.logger.error(f"Error in live trading cycle: {e}")
                    await asyncio.sleep(10.0)
                    
        except KeyboardInterrupt:
            self.logger.info("🛑 ETH live trading stopped by user (Ctrl+C)")
        
        if self.current_position is not None:
            current_price = self.client.get_current_price("ETH")
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
                    if asset_pos.get('position', {}).get('coin') == 'ETH':
                        position_data = asset_pos.get('position', {})
                        size = float(position_data.get('szi', 0))
                        
                        if size != 0:  # Position has size
                            self.logger.info(f"🔍 FOUND EXISTING ETH POSITION: {position_data}")
                            
                            # Create position object from existing position
                            entry_price = float(position_data.get('entryPx', 0))
                            direction = 'long' if size > 0 else 'short'
                            
                            # Use a default stop loss for existing positions
                            stop_loss = entry_price * 0.95 if direction == 'long' else entry_price * 1.05
                            
                            position = {
                                'symbol': 'ETH',
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
    bot = ETHLiveTradingBot()
    await bot.run_live_trading()

if __name__ == "__main__":
    print("🚀 ETH Live Trading Bot - REAL MONEY")
    print("This bot trades ETH with real money on Hyperliquid.")
    print("Risk per trade: $1")
    print("Bot will run INDEFINITELY until you press Ctrl+C")
    print("Make sure you have set up your Hyperliquid API key and have sufficient balance.")
    print()
    
    asyncio.run(main()) 
