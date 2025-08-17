import asyncio
import websockets
import json
import requests
import pandas as pd
from typing import Dict, List, Optional
import logging
from datetime import datetime, timedelta
from hyperliquid.info import Info
from hyperliquid.utils import constants
from credentials import HYPERLIQUID_ACCOUNT_ADDRESS


class HyperliquidClient:
    def __init__(self, api_key: str, subaccount: str = "default"):
        self.api_key = api_key
        self.subaccount = subaccount
        # Use the correct API URL from the official SDK
        self.base_url = "https://api.hyperliquid.xyz"
        self.ws_url = "wss://api.hyperliquid.xyz/ws"

        # Initialize the official SDK client
        self.info_client = Info(constants.MAINNET_API_URL, skip_ws=True)

        # Setup logging
        self.logger = logging.getLogger(__name__)

        # Session for HTTP requests
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}' if api_key else ''
        })

    def get_account_info(self) -> Dict:
        """Get account information using the official SDK"""
        try:
            # Use the public wallet address, not subaccount
            user_state = self.info_client.user_state(HYPERLIQUID_ACCOUNT_ADDRESS)
            return user_state
        except Exception as e:
            self.logger.error(f"Error fetching account info: {e}")
            return {}

    async def get_ohlcv(self, symbol: str, timeframe: str = "1m", limit: int = 500, start_time: int = None,
                        end_time: int = None) -> pd.DataFrame:
        """Fetch OHLCV data from Hyperliquid using the correct API (POST /info candleSnapshot)"""
        try:
            # If start_time and end_time are provided, use them; otherwise use limit
            if start_time is None or end_time is None:
                end_time = int(datetime.now().timestamp() * 1000)
                interval_minutes = int(timeframe.replace('m', '')) if 'm' in timeframe else 1
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

    '''

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
            return result

        except Exception as e:
            self.logger.error(f"Error placing order: {e}")
            return {"error": str(e)}

    def cancel_order(self, symbol: str, order_id: str) -> Dict:
        """Cancel an existing order"""
        try:
            import time
            import json
            from eth_account import Account
            from eth_account.messages import encode_defunct

            # Get asset index for the symbol
            asset_mapping = {
                "AVAX": 0,
                "BTC": 1,
                "ETH": 2,
                "SOL": 3
            }

            asset_index = asset_mapping.get(symbol, 0)
            timestamp_ms = int(time.time() * 1000)

            # Create the payload without signature first
            payload = {
                "type": "cancel",
                "cancels": [
                    {
                        "a": asset_index,  # Asset index (integer)
                        "o": int(order_id)  # Order ID (integer)
                    }
                ],
                "nonce": timestamp_ms,
                "vaultAddress": None,
                "expiresAfter": timestamp_ms + 60000  # Expire in 1 minute
            }

            # Create signature using the private key
            # Convert payload to string for signing (without signature field)
            payload_str = json.dumps(payload, separators=(',', ':'))

            # Create the account from private key
            account = Account.from_key(self.api_key)

            # Sign the message
            message = encode_defunct(text=payload_str)
            signed_message = account.sign_message(message)

            # Add the signature to the payload
            payload["signature"] = {
                "r": hex(signed_message.r),
                "s": hex(signed_message.s),
                "v": signed_message.v
            }

            url = f"{self.base_url}/exchange"

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
        
    '''