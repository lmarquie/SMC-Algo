import pandas as pd
from helpers.hyperliquid_client import HyperliquidClient
from helpers.telegram_setup import send_telegram_message
from config import *
from credentials import *
from datetime import datetime, timedelta
import asyncio
import time
from base_trader import BaseTrader
import ssl
import certifi
import websockets
import json

class LiveTrader(BaseTrader):
    def __init__(self, symbol):
        super().__init__(symbol, self._open_live_position, self._close_live_position, 10_000)

        self.client = HyperliquidClient(
            HYPERLIQUID_API_KEY,
            HYPERLIQUID_SUBACCOUNT,
        )
        self.last_position_close_time = None  # Track last position close time for cooldown
        self.last_candle_timestamp = None

        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

        self.ltf_lookback = 100
        self.htf_lookback = 50

        self.tail_data = None
        self.last_candle = None


    async def fetch_initial_data(self):
        """Fetch live market data"""
        try:
            print(f"{datetime.now()}, balance {self.current_balance}...")

            # Fetch LTF data (1m) - increase to 1000 candles
            ltf_data = await self.client.get_ohlcv(
                symbol=self.symbol,
                timeframe=TIMEFRAME,
                limit=self.ltf_lookback,
            )

            ltf_data = ltf_data[["open", "high", "low", "close", "T"]]
            ltf_data['T'] = pd.to_datetime(ltf_data['T'], unit='ms')
            ltf_data = ltf_data.reset_index(drop=True)

            # Fetch HTF data (15m) - reduced to 300 candles
            htf_data = await self.client.get_ohlcv(
                symbol=self.symbol,
                timeframe=HTF_TIMEFRAME,
                limit=self.htf_lookback,
            )

            htf_data = htf_data[["open", "high", "low", "close", "T"]]
            htf_data['T'] = pd.to_datetime(htf_data['T'], unit='ms')
            htf_data = htf_data.reset_index(drop=True)

            # Get current price
            current_price = self.client.get_current_price(self.symbol)

            if ltf_data.empty or htf_data.empty:
                print(f"Failed to fetch market data for {self.symbol}")
                return None, None, None

            self.last_candle_timestamp = ltf_data['T'].iloc[-1]
            return ltf_data, htf_data, current_price

        except Exception as e:
            print(f"Error fetching live data for {self.symbol}: {e}")
            return None, None, None


    def _open_live_position(self, setup, current_price, current_time):
        """Open a new position"""
        self.current_position = self.create_open_order(setup, current_price, current_time)
        if self.current_position is not None:
            telegram_text = ""
            telegram_text += "===== New Position Opened =====\n"
            telegram_text += f"Direction: {self.current_position['direction']}\n"
            telegram_text += f"Entry price: ${self.current_position['entry_price']:.4f}\n"
            telegram_text += f"Stop loss: ${self.current_position['stop_loss']:.4f}\n"
            telegram_text += f"Position size: {self.current_position['size']:.4f}\n"

            send_telegram_message(telegram_text)


    def _close_live_position(self, current_price, reason):
        """Close current position"""
        if not self.current_position:
            return
        try:
            # Calculate P&L
            if self.current_position['direction'] == 'long':
                pnl_pct = (current_price - self.current_position['entry_price']) / self.current_position['entry_price']
            else:
                pnl_pct = (self.current_position['entry_price'] - current_price) / self.current_position['entry_price']

            # Calculate dollar P&L - FIXED: Use position size × price difference
            price_diff = abs(current_price - self.current_position['entry_price'])
            pnl_dollar = self.current_position['size'] * price_diff

            # Apply direction
            if self.current_position['direction'] == 'long':
                pnl_dollar = pnl_dollar if current_price > self.current_position['entry_price'] else -pnl_dollar
            else:
                pnl_dollar = pnl_dollar if current_price < self.current_position['entry_price'] else -pnl_dollar

            pnl_dollar -= 40
            # Debug P&L calculation
            print(f"P&L Debug: Entry: ${self.current_position['entry_price']:.4f}, Exit: ${current_price:.4f}")
            print(f"P&L Debug: Price diff: ${price_diff:.4f}, Position size: {self.current_position['size']:.4f}")
            print(f"P&L Debug: Raw P&L: ${pnl_dollar:.2f}")
            # Update balance
            self.current_balance += pnl_dollar
            # Record trade
            trade = {
                'entry_time': self.current_position['entry_time'],
                'exit_time': datetime.now(),
                'direction': self.current_position['direction'],
                'entry_price': self.current_position['entry_price'],
                'exit_price': current_price,
                'size': self.current_position['size'],
                'pnl_pct': pnl_pct,
                'fvg': self.current_position['fvg'],
                'pnl_dollar': pnl_dollar,
                'reason': reason,
                'exit_reason': reason,
                'symbol': self.current_position['symbol']
            }
            self.trades.append(trade)
            print(f"Position closed: {pnl_pct:.4f} ({pnl_dollar:.2f}) - {reason}")

            telegram_text = ""
            telegram_text += "===== Position Closed =====\n"
            telegram_text += f"Direction: {self.current_position['direction']}\n"
            telegram_text += f"Exit price: ${current_price:.4f}\n"
            telegram_text += f"Total time in trade: {trade['exit_time'] - trade['entry_time']}\n"
            telegram_text += f"P&L: ${pnl_dollar:.2f}\n"
            telegram_text += f"Total trades taken: {len(self.trades)}\n"
            telegram_text += f"Current balance: ${self.current_balance:.2f}\n"

            send_telegram_message(telegram_text)

            # Reset position
            self.current_position = None
            self.last_position_close_time = datetime.now()


        except Exception as e:
            print(f"Error closing position: {e}")


    async def run_paper_trading(self, duration_minutes=None):
        """Run paper trading indefinitely or for specified duration"""

        if duration_minutes:
            print(f"🚀 Starting {self.symbol} paper trading for {duration_minutes} minutes...")
            start_time = datetime.now()
            end_time = start_time + timedelta(minutes=duration_minutes)
        else:
            print(f"🚀 Starting {self.symbol} paper trading INDEFINITELY...")
            print("Press Ctrl+C to stop the bot")
            end_time = None

        print(f"Trading symbol: {self.symbol}")
        print(f"Risk per trade: ${RISK_PER_TRADE}")

        ltf_data, htf_data, current_price = await self.fetch_initial_data()

        url = "wss://api.hyperliquid.xyz/ws"
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            # Subscribe to SOL 1m candle feed (example format)
            subscribe_msg = {
                "method": "subscribe",
                "subscription": {
                    "type": "candle",
                    "coin": "SOL",
                    "interval": "1m"
                }
            }
            await ws.send(json.dumps(subscribe_msg))
            connection_msg = await ws.recv()
            if "error" in connection_msg:
                print("Error subscribing to candles feed")
                return

            print("Successfully subscribed to candles feed")

            candle_idx = 0
            while True:
                if end_time and datetime.now() >= end_time:
                    break

                # Check the cooldown period
                cooldown_remaining = None
                if self.last_position_close_time:
                    time_since_close = datetime.now() - self.last_position_close_time
                    cooldown_remaining = 300 - time_since_close.total_seconds()  # 5 minutes = 300 seconds

                if cooldown_remaining and cooldown_remaining > 0:
                    print(f"⏳ COOLDOWN ACTIVE for {self.symbol}: {cooldown_remaining:.0f} seconds remaining")
                    candle_idx += 1
                    if candle_idx % 15 == 0:
                        try:
                            pong_waiter = ws.ping()
                            await pong_waiter  # resolves when Pong is received
                        except websockets.exceptions.ConnectionClosedError as e:
                            await asyncio.sleep(5)
                    await asyncio.sleep(1)
                    continue

                try:
                    candle = await ws.recv()
                    candle = json.loads(candle)['data']
                except websockets.exceptions.ConnectionClosedError as e:
                    await asyncio.sleep(5)


                if not self.last_candle:
                    self.last_candle = {
                        'T': datetime.fromtimestamp(candle['T'] / 1000),
                        'open': float(candle['o']),
                        'high': float(candle['h']),
                        'low': float(candle['l']),
                    }
                elif datetime.now() < self.last_candle['T']:
                    self.last_candle['close'] = float(candle['c'])
                else:
                    print("Adding ltf candle")
                    ltf_data = pd.concat([ltf_data, pd.DataFrame([self.last_candle])], ignore_index=True)
                    ltf_data = ltf_data.iloc[-self.ltf_lookback:].reset_index(drop=True)
                    self.last_candle_timestamp = self.last_candle['T']

                    if self.last_candle['T'] >= htf_data['T'].iloc[-1] + timedelta(minutes=HTF_TIMEFRAME_INT):
                        print("Adding htf candle")
                        htf_data = pd.concat([htf_data, pd.DataFrame([self.last_candle])], ignore_index=True)
                        htf_data = htf_data.iloc[-self.htf_lookback:].reset_index(drop=True)

                    self.single_iteration(ltf_data=ltf_data, htf_data=htf_data, current_time=ltf_data['T'].iloc[-1])
                    self.last_candle = None


        if self.current_position:
            final_price = ltf_data['close'].iloc[-1]
            self._close_live_position(final_price, "Finished trading")


trader = LiveTrader("AVAX")
asyncio.run(trader.run_paper_trading(duration_minutes=60*24))