import json
import time
import subprocess
import os
import logging
from typing import Dict, List, Optional, Callable
from datetime import datetime
import asyncio
from env_loader import load_env, get_env, get_env_int

class ElixirOrderMonitor:
    """
    Python interface to the Elixir order monitor.
    This class handles communication between the Python trading bot and the Elixir order monitor.
    """
    
    def __init__(self, api_key: str = None, account_address: str = None, comm_file: str = None):
        # Load environment variables from .env file
        load_env()
        
        # Use provided values or fall back to environment variables
        self.api_key = api_key or get_env('HYPERLIQUID_API_KEY')
        self.account_address = account_address or get_env('HYPERLIQUID_ACCOUNT_ADDRESS')
        self.comm_file = comm_file or get_env('ELIXIR_COMM_FILE', 'order_updates.json')
        
        # Validate required parameters
        if not self.api_key:
            raise ValueError("HYPERLIQUID_API_KEY is required. Set it in .env file or pass to constructor.")
        if not self.account_address:
            raise ValueError("HYPERLIQUID_ACCOUNT_ADDRESS is required. Set it in .env file or pass to constructor.")
        
        self.elixir_process = None
        self.last_read_id = 0
        self.callbacks = {
            'order_filled': [],
            'order_cancelled': [],
            'order_rejected': [],
            'position_fill': [],
            'position_closed': []
        }
        
        # Setup logging
        self.logger = logging.getLogger(__name__)
        
        # Ensure the communication file exists
        if not os.path.exists(self.comm_file):
            with open(self.comm_file, 'w') as f:
                json.dump([], f)
    
    def start_monitor(self) -> bool:
        """Start the Elixir order monitor process"""
        try:
            self.logger.info("🚀 Starting Elixir order monitor...")
            
            # Set environment variables for the Elixir process
            env = os.environ.copy()
            env['HYPERLIQUID_API_KEY'] = self.api_key
            env['HYPERLIQUID_ACCOUNT_ADDRESS'] = self.account_address
            
            # Add other environment variables that might be set
            for key in ['ELIXIR_WS_URL', 'ELIXIR_HTTP_URL', 'ELIXIR_COMM_FILE', 
                       'ELIXIR_CHECK_INTERVAL', 'ELIXIR_RECONNECT_DELAY', 
                       'ELIXIR_MAX_RECONNECT_ATTEMPTS', 'ELIXIR_HEARTBEAT_INTERVAL']:
                value = get_env(key)
                if value:
                    env[key] = value
            
            # Start the Elixir process
            self.elixir_process = subprocess.Popen(
                ['mix', 'run', '--no-halt'],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Wait a moment for the process to start
            time.sleep(2)
            
            # Check if process is still running
            if self.elixir_process.poll() is None:
                self.logger.info("✅ Elixir order monitor started successfully")
                return True
            else:
                stdout, stderr = self.elixir_process.communicate()
                self.logger.error(f"❌ Failed to start Elixir order monitor")
                self.logger.error(f"STDOUT: {stdout}")
                self.logger.error(f"STDERR: {stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"❌ Error starting Elixir order monitor: {e}")
            return False
    
    def stop_monitor(self):
        """Stop the Elixir order monitor process"""
        if self.elixir_process:
            self.logger.info("🛑 Stopping Elixir order monitor...")
            self.elixir_process.terminate()
            
            try:
                # Wait for process to terminate gracefully
                self.elixir_process.wait(timeout=5)
                self.logger.info("✅ Elixir order monitor stopped")
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't terminate gracefully
                self.elixir_process.kill()
                self.logger.warning("⚠️ Force killed Elixir order monitor")
            
            self.elixir_process = None
    
    def add_pending_order(self, client_order_id: str, order_data: Dict):
        """Add a pending order to the Elixir monitor"""
        try:
            # Write order data to a temporary file for Elixir to read
            order_file = f"pending_order_{client_order_id}.json"
            with open(order_file, 'w') as f:
                json.dump({
                    'client_order_id': client_order_id,
                    'order_data': order_data,
                    'timestamp': datetime.now().isoformat()
                }, f)
            
            self.logger.info(f"📋 Added pending order to Elixir monitor: {client_order_id}")
            
        except Exception as e:
            self.logger.error(f"❌ Error adding pending order: {e}")
    
    def remove_pending_order(self, client_order_id: str):
        """Remove a pending order from the Elixir monitor"""
        try:
            # Remove the temporary order file
            order_file = f"pending_order_{client_order_id}.json"
            if os.path.exists(order_file):
                os.remove(order_file)
                self.logger.info(f"🗑️ Removed pending order from Elixir monitor: {client_order_id}")
            
        except Exception as e:
            self.logger.error(f"❌ Error removing pending order: {e}")
    
    def read_updates(self) -> List[Dict]:
        """Read new updates from the Elixir monitor"""
        try:
            if not os.path.exists(self.comm_file):
                return []
            
            with open(self.comm_file, 'r') as f:
                updates = json.load(f)
            
            # Filter for new updates only
            new_updates = [update for update in updates if update.get('id', 0) > self.last_read_id]
            
            if new_updates:
                self.last_read_id = max(update.get('id', 0) for update in new_updates)
                self.logger.info(f"📨 Read {len(new_updates)} new updates from Elixir monitor")
            
            return new_updates
            
        except Exception as e:
            self.logger.error(f"❌ Error reading updates: {e}")
            return []
    
    def register_callback(self, event_type: str, callback: Callable):
        """Register a callback for a specific event type"""
        if event_type in self.callbacks:
            self.callbacks[event_type].append(callback)
            self.logger.info(f"📝 Registered callback for {event_type}")
        else:
            self.logger.warning(f"⚠️ Unknown event type: {event_type}")
    
    def process_updates(self):
        """Process all pending updates and trigger callbacks"""
        updates = self.read_updates()
        
        for update in updates:
            event_type = update.get('type')
            
            if event_type in self.callbacks:
                # Trigger all registered callbacks for this event type
                for callback in self.callbacks[event_type]:
                    try:
                        callback(update)
                    except Exception as e:
                        self.logger.error(f"❌ Error in callback for {event_type}: {e}")
            else:
                self.logger.warning(f"⚠️ No callbacks registered for event type: {event_type}")
    
    async def monitor_loop(self, check_interval: float = None):
        """Main monitoring loop that continuously checks for updates"""
        # Use environment variable or default
        if check_interval is None:
            check_interval = get_env_int('ELIXIR_CHECK_INTERVAL', 500) / 1000.0  # Convert ms to seconds
        
        self.logger.info(f"🔍 Starting Elixir monitor loop (check interval: {check_interval}s)")
        
        while True:
            try:
                # Process any new updates
                self.process_updates()
                
                # Check if Elixir process is still running
                if self.elixir_process and self.elixir_process.poll() is not None:
                    self.logger.error("❌ Elixir order monitor process died unexpectedly")
                    # Try to restart
                    if not self.start_monitor():
                        self.logger.error("❌ Failed to restart Elixir order monitor")
                        break
                
                await asyncio.sleep(check_interval)
                
            except Exception as e:
                self.logger.error(f"❌ Error in monitor loop: {e}")
                await asyncio.sleep(check_interval)
    
    def is_running(self) -> bool:
        """Check if the Elixir monitor is running"""
        return self.elixir_process is not None and self.elixir_process.poll() is None
    
    def get_status(self) -> Dict:
        """Get the current status of the Elixir monitor"""
        return {
            'running': self.is_running(),
            'last_read_id': self.last_read_id,
            'comm_file': self.comm_file,
            'callbacks_registered': {event: len(callbacks) for event, callbacks in self.callbacks.items()}
        }

# Example usage and integration with the trading bot
class OrderMonitorIntegration:
    """
    Integration class that shows how to use the Elixir order monitor with the trading bot
    """
    
    def __init__(self, api_key: str = None, account_address: str = None):
        self.monitor = ElixirOrderMonitor(api_key, account_address)
        self.logger = logging.getLogger(__name__)
        
        # Register callbacks for different events
        self.monitor.register_callback('order_filled', self.on_order_filled)
        self.monitor.register_callback('order_cancelled', self.on_order_cancelled)
        self.monitor.register_callback('order_rejected', self.on_order_rejected)
        self.monitor.register_callback('position_fill', self.on_position_fill)
        self.monitor.register_callback('position_closed', self.on_position_closed)
    
    def start(self) -> bool:
        """Start the order monitor"""
        return self.monitor.start_monitor()
    
    def stop(self):
        """Stop the order monitor"""
        self.monitor.stop_monitor()
    
    def add_pending_order(self, client_order_id: str, order_data: Dict):
        """Add a pending order for monitoring"""
        self.monitor.add_pending_order(client_order_id, order_data)
    
    async def run_monitor_loop(self):
        """Run the monitoring loop"""
        await self.monitor.monitor_loop()
    
    # Callback methods for different events
    def on_order_filled(self, update: Dict):
        """Handle order filled event"""
        self.logger.info(f"✅ ORDER FILLED: {update['symbol']} at ${update['fill_price']}")
        self.logger.info(f"  Client Order ID: {update['client_order_id']}")
        self.logger.info(f"  Fill Size: {update['fill_size']}")
        
        # Here you would integrate with your trading bot
        # For example, update position status, start stop monitoring, etc.
    
    def on_order_cancelled(self, update: Dict):
        """Handle order cancelled event"""
        self.logger.info(f"❌ ORDER CANCELLED: {update['symbol']} - {update['reason']}")
        
        # Here you would handle the cancellation in your trading bot
        # For example, clear pending order status, try alternative entry, etc.
    
    def on_order_rejected(self, update: Dict):
        """Handle order rejected event"""
        self.logger.error(f"🚫 ORDER REJECTED: {update['symbol']} - {update['reason']}")
        
        # Here you would handle the rejection in your trading bot
        # For example, log the error, try different order parameters, etc.
    
    def on_position_fill(self, update: Dict):
        """Handle position fill event (detected via position update)"""
        self.logger.info(f"📈 POSITION FILL: {update['symbol']}")
        self.logger.info(f"  Client Order ID: {update['client_order_id']}")
        
        # Here you would handle the position fill in your trading bot
        # For example, create position object, start stop monitoring, etc.
    
    def on_position_closed(self, update: Dict):
        """Handle position closed event"""
        self.logger.info(f"📉 POSITION CLOSED: {update['symbol']}")
        
        # Here you would handle the position close in your trading bot
        # For example, update trade history, calculate P&L, etc.

# Example of how to integrate with the existing trading bot
def integrate_with_trading_bot(trading_bot):
    """
    Example function showing how to integrate the Elixir order monitor with an existing trading bot
    """
    
    # Create the order monitor integration
    order_monitor = OrderMonitorIntegration()
    
    # Start the monitor
    if not order_monitor.start():
        trading_bot.logger.error("❌ Failed to start Elixir order monitor")
        return None
    
    # Override the trading bot's order monitoring methods
    original_monitor_pending_order = trading_bot.monitor_pending_order
    
    def new_monitor_pending_order():
        """New monitoring method that uses Elixir instead of polling"""
        if not trading_bot.pending_order:
            return
        
        client_order_id = trading_bot.pending_order['order_id']
        
        # Add the order to Elixir monitor
        order_monitor.add_pending_order(client_order_id, trading_bot.pending_order)
        
        # The actual monitoring will be handled by Elixir callbacks
        # We just need to wait for the callback to be triggered
    
    # Override the method
    trading_bot.monitor_pending_order = new_monitor_pending_order
    
    # Register callbacks that will update the trading bot's state
    def on_order_filled_callback(update):
        """Callback when order is filled"""
        if trading_bot.pending_order and trading_bot.pending_order['order_id'] == update['client_order_id']:
            # Create position from the fill data
            trading_bot.current_position = {
                'direction': trading_bot.pending_order['direction'],
                'entry_price': float(update['fill_price']),
                'stop_loss': trading_bot.pending_order['stop_loss'],
                'take_profit': trading_bot.pending_order['setup'].get('take_profit'),
                'size': float(update['fill_size']),
                'entry_time': datetime.now(),
                'reason': trading_bot.pending_order['setup']['reason'],
                'leverage': trading_bot.pending_order['leverage'],
                'order_id': update['order_id'],
                'strategy_type': trading_bot.pending_order.get('strategy_type', 'standard'),
                'fvg': trading_bot.pending_order.get('fvg', {})
            }
            
            # Clear pending order
            trading_bot.pending_order = None
            
            # Start stop monitoring
            trading_bot.start_stop_monitoring()
            
            # Send notification
            from notifications import send_telegram_message
            send_telegram_message(
                f"✅ ORDER FILLED (Elixir): {update['symbol']} {trading_bot.current_position['direction'].upper()} "
                f"at ${update['fill_price']} | Size: {update['fill_size']}"
            )
    
    order_monitor.monitor.register_callback('order_filled', on_order_filled_callback)
    
    return order_monitor 