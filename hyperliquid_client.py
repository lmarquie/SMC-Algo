import asyncio
import websockets
import json
import requests
import pandas as pd
from typing import Dict, List, Optional
import logging
from datetime import datetime, timedelta
from notifications import send_telegram_message

class HyperliquidClient:
    def __init__(self, api_key: str, subaccount: str = "default"):
        self.api_key = api_key
        self.subaccount = subaccount
        # Use the correct API URL from the official SDK
        self.base_url = "https://api.hyperliquid.xyz"
        self.ws_url = "wss://api.hyperliquid.xyz/ws"
        
        # Setup logging
        self.logger = logging.getLogger(__name__)
        
        # Session for HTTP requests
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}' if api_key else ''
        })
    
    async def get_ohlcv(self, symbol: str, timeframe: str = "1m", limit: int = 500, start_time: int = None, end_time: int = None) -> pd.DataFrame:
        """Fetch OHLCV data from Hyperliquid using the correct API (POST /info candleSnapshot)"""
        try:
            # If start_time and end_time are provided, use them; otherwise use limit
            if start_time is None or end_time is None:
                end_time = int(datetime.now().timestamp() * 1000)
                interval_minutes = int(timeframe.replace('m','')) if 'm' in timeframe else 1
                start_time = end_time - (limit * interval_minutes * 60 * 1000)
            
            url = f"{self.base_url}/info"
            payload = {
                "type": "candleSnapshot",
                "req": {
                    "coin": symbol,
                    "interval": timeframe,
                    "startTime": start_time,
                    "endTime": end_time
                }
            }
            response = self.session.post(url, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list) or len(data) == 0:
                raise ValueError("Invalid response format for candles")
            # Convert to DataFrame
            df = pd.DataFrame(data)
            if df.empty:
                return df
            # Map columns
            df = df.rename(columns={
                't': 'timestamp',
                'o': 'open',
                'h': 'high',
                'l': 'low',
                'c': 'close',
                'v': 'volume'
            })
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            return df.sort_index()
        except Exception as e:
            self.logger.error(f"Error fetching OHLCV data: {e}")
            return pd.DataFrame()
    
    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price for a symbol using POST /info allMids"""
        try:
            url = f"{self.base_url}/info"
            payload = {"type": "allMids"}
            response = self.session.post(url, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            if symbol in data:
                return float(data[symbol])
            return None
        except Exception as e:
            self.logger.error(f"Error fetching current price: {e}")
            return None
    
    def get_account_info(self) -> Dict:
        """Get account information"""
        try:
            url = f"{self.base_url}/info/user"
            params = {"user": self.subaccount}
            
            response = self.session.get(url, params=params)
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            self.logger.error(f"Error fetching account info: {e}")
            return {}
    
    def place_order(self, symbol: str, side: str, size: float, 
                   order_type: str = "market", price: Optional[float] = None,
                   stop_loss: Optional[float] = None, take_profit: Optional[float] = None,
                   leverage: int = None) -> Dict:
        """Place an order on Hyperliquid with leverage"""
        try:
            # If leverage not provided, get from config based on symbol
            if leverage is None:
                from config import MAX_LEVERAGE
                leverage = MAX_LEVERAGE.get(symbol, 20)  # Default to 20x if not found
            
            url = f"{self.base_url}/exchange"
            
            order_data = {
                "type": "order",
                "user": self.subaccount,
                "coin": symbol,
                "is_buy": side.lower() == "buy",
                "sz": str(size),
                "limit_px": str(price) if price else "0",
                "reduce_only": False,
                "leverage": leverage
            }
            
            # Add stop loss if provided
            if stop_loss:
                order_data["stop_px"] = str(stop_loss)
            
            payload = {
                "action": "order",
                "args": order_data
            }
            
            response = self.session.post(url, json=payload)
            response.raise_for_status()
            
            result = response.json()
            self.logger.info(f"Order placed with {leverage}x leverage: {result}")
            send_telegram_message(
                f"Trade OPENED: {symbol} {side.upper()} at ${price:.4f} | Stop: ${stop_loss:.4f} | Leverage: {leverage}x"
            )
            return result
            
        except Exception as e:
            self.logger.error(f"Error placing order: {e}")
            return {"error": str(e)}
    
    def cancel_order(self, symbol: str, order_id: str) -> Dict:
        """Cancel an existing order"""
        try:
            url = f"{self.base_url}/exchange"
            
            payload = {
                "action": "cancel",
                "args": {
                    "user": self.subaccount,
                    "coin": symbol,
                    "oid": order_id
                }
            }
            
            response = self.session.post(url, json=payload)
            response.raise_for_status()
            
            result = response.json()
            self.logger.info(f"Order cancelled: {result}")
            return result
            
        except Exception as e:
            self.logger.error(f"Error cancelling order: {e}")
            return {"error": str(e)}
    
    def get_open_orders(self, symbol: str) -> List[Dict]:
        """Get open orders for a symbol"""
        try:
            url = f"{self.base_url}/info/open_orders"
            params = {
                "user": self.subaccount,
                "coin": symbol
            }
            
            response = self.session.get(url, params=params)
            response.raise_for_status()
            
            data = response.json()
            return data.get('data', [])
            
        except Exception as e:
            self.logger.error(f"Error fetching open orders: {e}")
            return []
    
    def get_positions(self, symbol: str) -> List[Dict]:
        """Get current positions for a symbol"""
        try:
            url = f"{self.base_url}/info/positions"
            params = {"user": self.subaccount}
            
            response = self.session.get(url, params=params)
            response.raise_for_status()
            
            data = response.json()
            positions = data.get('data', [])
            
            # Filter by symbol if provided
            if symbol:
                positions = [pos for pos in positions if pos.get('coin') == symbol]
            
            return positions
            
        except Exception as e:
            self.logger.error(f"Error fetching positions: {e}")
            return []
    
    def _timeframe_to_ms(self, timeframe: str) -> int:
        """Convert timeframe string to milliseconds"""
        timeframe_map = {
            "1m": 60000,
            "5m": 300000,
            "15m": 900000,
            "1h": 3600000,
            "4h": 14400000,
            "1d": 86400000
        }
        return timeframe_map.get(timeframe, 60000)
    
    def _timeframe_to_minutes(self, timeframe: str) -> int:
        """Convert timeframe string to minutes"""
        timeframe_map = {
            "1m": 1,
            "5m": 5,
            "15m": 15,
            "1h": 60,
            "4h": 240,
            "1d": 1440
        }
        return timeframe_map.get(timeframe, 1)
    
    async def subscribe_to_ticker(self, symbol: str, callback):
        """Subscribe to real-time ticker updates"""
        try:
            async with websockets.connect(self.ws_url) as websocket:
                # Subscribe to ticker
                subscribe_msg = {
                    "method": "subscribe",
                    "subscription": {
                        "type": "ticker",
                        "coin": symbol
                    }
                }
                
                await websocket.send(json.dumps(subscribe_msg))
                
                while True:
                    message = await websocket.recv()
                    data = json.loads(message)
                    
                    if 'data' in data:
                        callback(data['data'])
                        
        except Exception as e:
            self.logger.error(f"WebSocket error: {e}")
    
    def close(self):
        """Close the client connection"""
        if hasattr(self, 'session'):
            self.session.close()

