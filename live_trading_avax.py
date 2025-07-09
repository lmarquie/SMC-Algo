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
        self.pending_order = None  # Track pending ALO order for monitoring
        
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

    # Add these new methods for ALO limit orders
    async def place_limit_order(self, symbol, is_buy, size, limit_price, order_type="Alo"):
        """Place a limit order using the official SDK"""
        try:
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
        """Check order status using client order ID - using official SDK pattern"""
        try:
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
            
            # Use the official SDK pattern to query order status
            # Get all open orders first to find our cloid and get the oid
            try:
                open_orders = self.exchange.open_orders()
                for order in open_orders:
                    order_cloid = order.get('cloid', '')
                    if order_cloid == cloid:
                        # Found our order - get the oid and query its status
                        oid = order.get('oid')
                        if oid:
                            self.logger.info(f"🔍 Found our cloid in open orders, querying by oid: {oid}")
                            
                            # Use the official SDK pattern you mentioned
                            order_status = self.info.query_order_by_oid(self.wallet.address, oid)
                            self.logger.info(f"🔍 Order status by oid: {order_status}")
                            
                            if order_status and order_status.get('status') != 'unknownOid':
                                # Order exists - check if it's filled or still resting
                                if 'filled' in order_status:
                                    self.logger.info(f"✅ CLIENT ORDER FILLED (via oid query)!")
                                    return {
                                        'status': 'filled_via_oid_query',
                                        'cloid': cloid,
                                        'oid': oid,
                                        'order_data': order_status
                                    }
                                else:
                                    self.logger.info(f"⏳ CLIENT ORDER STILL RESTING (via oid query)")
                                    return {
                                        'status': 'resting',
                                        'cloid': cloid,
                                        'oid': oid,
                                        'order_data': order_status
                                    }
                            else:
                                # Order not found via oid query - might be filled
                                self.logger.info(f"⚠️ Order not found via oid query - might be filled")
                                return {
                                    'status': 'filled_via_oid_disappearance',
                                    'cloid': cloid,
                                    'oid': oid,
                                    'message': 'Order not found via oid query - was filled'
                                }
                        else:
                            self.logger.warning(f"⚠️ Found cloid but no oid in order: {order}")
                            return {
                                'status': 'resting',
                                'cloid': cloid,
                                'order_data': order,
                                'message': 'Client order found in open orders (no oid)'
                            }
                
                # Our cloid not found in open orders - it was filled
                self.logger.info(f"✅ CLIENT ORDER FILLED (not found in open orders)")
                return {
                    'status': 'filled_via_disappearance',
                    'cloid': cloid,
                    'message': 'Client order not found in open orders - was filled'
                }
                
            except Exception as e:
                self.logger.error(f"❌ Error checking open orders: {e}")
                return {'status': 'error', 'error': str(e)}
            
        except Exception as e:
            self.logger.error(f"❌ Error checking order status by cloid: {e}")
            return {'status': 'error', 'error': str(e)}

    async def check_order_status_via_open_orders(self, symbol, order_id):
        """Check order status by looking through open orders"""
        try:
            self.logger.info(f"🔍 Checking order {order_id} via open orders...")
            
            # Get all open orders
            open_orders = self.exchange.open_orders()
            self.logger.info(f"📋 Found {len(open_orders)} open orders")
            
            # Look for our specific order
            for order in open_orders:
                if order.get('oid') == order_id:
                    self.logger.info(f"✅ FOUND ORDER: {order}")
                    return {
                        'status': 'found',
                        'order_id': order_id,
                        'order_data': order
                    }
            
            # Order not found in open orders
            self.logger.warning(f"⚠️ Order {order_id} not found in open orders")
            return {
                'status': 'not_found',
                'order_id': order_id,
                'message': 'Order not found in open orders'
            }
            
        except Exception as e:
            self.logger.error(f"❌ Error checking open orders: {e}")
            return {'status': 'error', 'error': str(e)}

    async def cancel_order(self, symbol, order_id):
        """Cancel order using the official SDK - simplified version"""
        try:
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
        check_interval = 0.5   # Check every 0.5 seconds
        
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
                
                # Check order status CONSTANTLY using client order ID
                # FIXED: Check for both 'ALO' and 'INVERTED_ALO' order types
                if self.pending_order['order_type'] in ['ALO', 'INVERTED_ALO']:
                    cloid = self.pending_order['order_id'] # Use the client order ID for ALO
                    self.logger.info(f"🔍 Using Client Order ID for monitoring: {cloid}")
                    status = await self.check_order_status_by_cloid(symbol, cloid)
                else: # For other order types, use the original order_id
                    self.logger.info(f"🔍 Using regular Order ID for monitoring: {order_id}")
                    status = await self.check_order_status(symbol, order_id)
                
                if status['status'] in ['found', 'resting_via_query']:
                    # Order still exists and is resting
                    order_data = status['order_data']
                    self.logger.info(f"⏳ ORDER STILL RESTING: {status['status']}")
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
                        f"✅ MOMENTUM ALO FILLED: AVAX {direction.upper()} at ${self.current_position['entry_price']:.4f} | "
                        f"FVG Stop: ${self.current_position['stop_loss']:.4f} | Size: {self.current_position['size']:.4f}"
                    )
                    
                    # START STOP MONITORING IMMEDIATELY
                    self.start_stop_monitoring()
                    
                    # Clear pending order
                    self.pending_order = None
                    return
                        
                elif status['status'] == 'filled_via_position':
                    # Order not found but we have a position - order was filled
                    self.logger.info(f"✅ ALO ORDER FILLED (detected via position)! Starting stop monitoring IMMEDIATELY")
                    
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
                        f"✅ ALO ORDER FILLED: AVAX {direction.upper()} at ${self.current_position['entry_price']:.4f} | "
                        f"Starting stop monitoring | Stop: ${self.current_position['stop_loss']:.4f}"
                    )
                    
                    # START STOP MONITORING IMMEDIATELY
                    self.start_stop_monitoring()
                    
                    # Clear pending order
                    self.pending_order = None
                    return
                
                elif status['status'] == 'filled_via_history':
                    # Order found in fill history
                    self.logger.info(f"✅ ALO ORDER FILLED (detected via history)! Starting stop monitoring IMMEDIATELY")
                    
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
                        f"✅ ALO ORDER FILLED: AVAX {direction.upper()} at ${self.current_position['entry_price']:.4f} | "
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
                                    self.logger.info(f"✅ ALO ORDER FILLED (found in positions)! Starting stop monitoring IMMEDIATELY")
                                    
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
                                        f"✅ ALO ORDER FILLED: AVAX {direction.upper()} at ${self.current_position['entry_price']:.4f} | "
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
                
                # Log progress every 60 checks (every 30 seconds)
                if check_count % 60 == 0:
                    elapsed = (datetime.now() - start_time).total_seconds()
                    remaining = timeout_seconds - elapsed
                    self.logger.info(f"🔍 ALO MONITOR #{check_count}: {elapsed:.0f}s elapsed, {remaining:.0f}s remaining")
                    
                    # Also get current price to show progress
                    current_price = self.client.get_current_price(symbol)
                    if current_price:
                        if direction == 'long':
                            distance = limit_price - current_price
                            self.logger.info(f"  Current: ${current_price:.4f} | Need: ${limit_price:.4f} | Distance: ${distance:.4f}")
                        else:  # short
                            distance = current_price - limit_price
                            self.logger.info(f"  Current: ${current_price:.4f} | Need: ${limit_price:.4f} | Distance: ${distance:.4f}")
                
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
            send_telegram_message(f"⏰ ALO ORDER CANCELLED: AVAX {direction.upper()} (5min timeout)")
        else:
            self.logger.error(f"❌ FAILED TO CANCEL ALO ORDER: {cancel_result}")
        
        # Clear pending order
        self.pending_order = None

    async def open_live_position(self, setup, current_price):
        """Open a live trading position using INVERTED ALO limit orders for momentum"""
        if self.current_position is not None or self.position_lock:
            self.logger.warning(f"Already in a position for AVAX or position lock active, cannot open new one")
            return False
        
        try:
            # Set position lock to prevent race conditions
            self.position_lock = True
            
            self.logger.info(f"DEBUG: Opening live position for AVAX with setup: {setup}")
            
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
            
            # Get leverage for AVAX
            leverage = self.config['MAX_LEVERAGE'].get("AVAX", 10)
            
            # Place the actual order on Hyperliquid using INVERTED ALO limit order
            is_buy = setup['direction'] == 'long'
            
            # INVERTED ALO limit price - FOLLOWING MOMENTUM
            # For LONG: Place limit order ABOVE current price (follows momentum up)
            # For SHORT: Place limit order BELOW current price (follows momentum down)
            
            if is_buy:
                # LONG: Place limit buy ABOVE current price to follow momentum
                # Use FVG entry target as base, with 0.05 cent buffer for ALO
                limit_price = setup['entry_price'] + 0.0005  # 0.05 cents ABOVE FVG entry
                self.logger.info(f"📈 MOMENTUM LONG SETUP: Placing ALO limit buy ABOVE FVG entry")
                self.logger.info(f"  FVG Entry Target: ${setup['entry_price']:.4f}")
                self.logger.info(f"  ALO Limit Price: ${limit_price:.4f} (0.05 cents ABOVE FVG)")
            else:
                # SHORT: Place limit sell BELOW current price to follow momentum
                # Use FVG entry target as base, with 0.05 cent buffer for ALO
                limit_price = setup['entry_price'] - 0.0005  # 0.05 cents BELOW FVG entry
                self.logger.info(f"📉 MOMENTUM SHORT SETUP: Placing ALO limit sell BELOW FVG entry")
                self.logger.info(f"  ALO Limit Price: ${limit_price:.4f} (0.05 cents BELOW FVG)")
            
            # Round to 3 decimal places
            limit_price = round(limit_price, 3)
            
            self.logger.info(f"📈 Opening MOMENTUM position for AVAX (INVERTED ALO LIMIT ORDER):")
            self.logger.info(f"  Direction: {setup['direction'].upper()}")
            self.logger.info(f"  INVERTED ALO Limit Price: ${limit_price:.4f}")
            self.logger.info(f"  Current Market: ${current_price:.4f}")
            self.logger.info(f"  FVG Entry Target: ${setup['entry_price']:.4f}")
            self.logger.info(f"  FVG-Based Stop: ${final_stop:.4f}")
            self.logger.info(f"  Size: {position_size:.4f}")
            self.logger.info(f"  Risk: $10")
            self.logger.info(f"  Leverage: {leverage}x")
            self.logger.info(f"  Order Type: INVERTED ALO (Momentum Following)")
            
            # Place INVERTED ALO limit order
            order_result = await self.place_limit_order("AVAX", is_buy, position_size, limit_price, "Alo")
            
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
                    f"✅ MOMENTUM ALO FILLED: AVAX {setup['direction'].upper()} at ${order_result['fill_price']:.4f} | "
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
                    'symbol': 'AVAX',
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
                    f"⏳ MOMENTUM ALO PLACED: AVAX {setup['direction'].upper()} @ ${limit_price:.4f} | "
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
        """Monitor stops with FVG-based momentum logic"""
        self.logger.info("🔍 Starting FVG-based momentum stop monitoring for AVAX")
        self.logger.info("🔍 This should run every 0.5 seconds when in a position")
        self.stop_monitoring_active = True
        monitor_count = 0
        
        while self.stop_monitoring_active and self.current_position is not None:
            try:
                monitor_count += 1
                
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
                    self.logger.info(f"🔍 MONITOR #{monitor_count}: AVAX {direction.upper()}")
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
                        send_telegram_message(f"✅ MOMENTUM TRAILING ENABLED for AVAX")
                    
                    # Update trailing stop more frequently for momentum
                    if monitor_count % 10 == 0:  # Every 5 seconds
                        try:
                            ltf_data, _, _ = await self.fetch_live_data()
                            if ltf_data is not None:
                                old_stop = position['stop_loss']
                                updated = self.strategy.update_momentum_trailing_stop(ltf_data, position)
                                if updated:
                                    new_stop = position['stop_loss']
                                    self.logger.info(f"🔄 MOMENTUM TRAILING UPDATED!")
                                    self.logger.info(f"  Old Stop: ${old_stop:.4f} → New Stop: ${new_stop:.4f}")
                                    self.logger.info(f"  R:R: {rr_ratio:.2f} | Profit: ${current_pnl:.4f}")
                                    send_telegram_message(f"🔄 MOMENTUM TRAILING: AVAX ${old_stop:.4f} → ${new_stop:.4f} (R:R: {rr_ratio:.2f})")
                        except Exception as e:
                            self.logger.error(f"Error updating momentum trailing stop: {e}")
            
                # Check if stop loss is hit
                if direction == 'long' and current_price <= position['stop_loss']:
                    self.logger.info(f"🛑 MOMENTUM MONITOR #{monitor_count}: FVG STOP HIT for AVAX LONG")
                    self.logger.info(f"  Price: ${current_price:.4f} <= FVG Stop: ${position['stop_loss']:.4f}")
                    self.logger.info(f"  Final P&L: ${current_pnl:.4f} | R:R: {rr_ratio:.2f}")
                    send_telegram_message(f"🛑 FVG STOP HIT: AVAX LONG at ${position['stop_loss']:.4f}")
                    self.close_live_position("FVG Stop Loss Hit")
                    self.stop_monitoring_active = False
                    break
                elif direction == 'short' and current_price >= position['stop_loss']:
                    self.logger.info(f"🛑 MOMENTUM MONITOR #{monitor_count}: FVG STOP HIT for AVAX SHORT")
                    self.logger.info(f"  Price: ${current_price:.4f} >= FVG Stop: ${position['stop_loss']:.4f}")
                    self.logger.info(f"  Final P&L: ${current_pnl:.4f} | R:R: {rr_ratio:.2f}")
                    send_telegram_message(f"🛑 FVG STOP HIT: AVAX SHORT at ${position['stop_loss']:.4f}")
                    self.close_live_position("FVG Stop Loss Hit")
                    self.stop_monitoring_active = False
                    break
                
                # Wait 0.5 seconds before next check
                await asyncio.sleep(0.5)
                
            except Exception as e:
                self.logger.error(f"Error in FVG momentum stop monitoring: {e}")
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
                    # Check if we're in a position OR have a pending order
                    if self.current_position is not None or self.pending_order is not None:
                        # Already in a position OR waiting for ALO order to fill
                        if self.current_position:
                            self.logger.info(f"📊 AVAX already has active position: {self.current_position['direction']} at ${self.current_position['entry_price']:.2f}")
                        if self.pending_order:
                            self.logger.info(f"⏳ AVAX has pending ALO order: {self.pending_order['direction']} @ ${self.pending_order['limit_price']:.2f}")
                        
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
                    
                    # MAIN LOOP: No position, no pending order, no cooldown - SEARCHING FOR TRADES
                    if cycle_count % 10 == 0:  # Log every 10 cycles when searching
                        self.logger.info(f"🔍 MAIN LOOP: Searching for AVAX trade setups... (Cycle #{cycle_count})")
                    
                    # Fetch live data for AVAX
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
                                setup['symbol'] = 'AVAX'  # Add symbol to setup
                                # Only log locally, don't send Telegram for setups
                                self.logger.info(f"✅ Setup found for AVAX: {setup['direction']} at ${setup['entry_price']:.2f}")
                                success = await self.open_live_position(setup, current_price)
                                if not success:
                                    self.logger.error(f"❌ FAILED to open position for AVAX")
                                else:
                                    self.logger.info(f"✅ SUCCESSFULLY opened position for AVAX")
                    
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
