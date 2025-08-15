import pandas as pd
from helpers.hyperliquid_client import HyperliquidClient
from helpers.telegram_setup import send_telegram_message, is_stop_requested, send_telegram_image
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
import matplotlib.pyplot as plt
import os
import shutil

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

        self.last_candle = None
        self.full_data = pd.DataFrame()

        self.plot_times = []
        self.plot_opens = []


    def create_and_send_images(self):
        plt.plot(self.plot_times, self.plot_opens, color="blue", alpha=0.5, linewidth=1)

        winning_trades = [t for t in self.trades if t['pnl_dollar'] > 0]
        losing_trades = [t for t in self.trades if t['pnl_dollar'] < 0]

        for trade in winning_trades:
            entry_idx = trade["entry_time"]
            exit_idx = trade["exit_time"]

            plt.plot([entry_idx, exit_idx],  # x-coordinates of the two points
                     [trade["entry_price"], trade["exit_price"]],  # y-coordinates of the two points
                     color='green', linestyle='--', linewidth=5)

        for trade in losing_trades:
            entry_idx = trade["entry_time"]
            exit_idx = trade["exit_time"]

            plt.plot([entry_idx, exit_idx],  # x-coordinates of the two points
                     [trade["entry_price"], trade["exit_price"]],  # y-coordinates of the two points
                     color='red', linestyle='--', linewidth=5)

        plt.savefig("summary.png")
        plt.close()

        send_telegram_image("summary.png", caption="All trades plotted")

        if os.path.exists('trades'):
            shutil.rmtree("trades")
        os.makedirs("trades", exist_ok=True)
        os.makedirs("trades/wins", exist_ok=True)
        os.makedirs("trades/losses", exist_ok=True)
        for i, trade in enumerate(self.trades):
            entry_idx = trade['entry_idx']
            exit_idx = trade['exit_idx']

            plt.figure(figsize=(15, 8))
            plt.margins(x=0.1)
            plt.tight_layout()
            fig, ax = plt.subplots()
            print(f"Trade {i + 1}, entry {entry_idx}, fvg index {trade['fvg']['start_idx']}")

            for idx in range(trade['fvg']['start_idx'] - 2, min(exit_idx+9, len(self.full_data) - 1)):
                candle = self.full_data.iloc[idx]
                boxplot_data = [[candle["low"], candle["open"], candle["close"], candle["high"]]]

                if idx == entry_idx:
                    boxprops = {'facecolor': 'green', 'alpha': 1}
                elif idx == exit_idx:
                    boxprops = {'facecolor': 'red', 'alpha': 1}
                elif idx == self.full_data.index.get_loc(trade['fvg']['start_idx']):
                    boxprops = {'facecolor': 'orange', 'alpha': 1}
                elif idx in trade['mss']:
                    boxprops = {'facecolor': 'yellow', 'alpha': 1}
                elif idx in trade['bos']:
                    boxprops = {'facecolor': 'blue', 'alpha': 1}
                elif idx > entry_idx and idx < exit_idx:
                    if candle["close"] >= candle["open"]:
                        boxprops = {'facecolor': 'green', 'alpha': 0.4}
                    else:
                        boxprops = {'facecolor': 'red', 'alpha': 0.4}
                else:
                    boxprops = {'facecolor': 'white', 'alpha': 1.0}

                ax.boxplot(
                    boxplot_data,
                    positions=[(idx-entry_idx+1) * 3],
                    widths=2,
                    showfliers=False,
                    manage_ticks=True,
                    medianprops={'linewidth': 0},
                    boxprops=boxprops,
                    patch_artist=True,
                )

            trade_direction = trade['direction'].upper()
            trade_result = "WIN" if trade['pnl_dollar'] > 0 else "LOSS"
            plt.title(f"Trade #{entry_idx}: {trade_direction} - {trade_result} (${trade['pnl_dollar']:.2f})")

            ax.tick_params(axis='both', labelsize=6)
            plt.tight_layout()

            if trade in winning_trades:
                plt.savefig(f"trades/wins/trade_{i+1}.png", dpi=600, bbox_inches="tight")
                send_telegram_image(f"trades/wins/trade_{i+1}.png", caption=f"Trade #{entry_idx}: {trade_direction} - {trade_result} (${trade['pnl_dollar']:.2f})")
                plt.close("all")
            else:
                plt.savefig(f"trades/losses/trade_{i+1}.png", dpi=600, bbox_inches="tight")
                send_telegram_image(f"trades/losses/trade_{i+1}.png", caption=f"Trade #{entry_idx}: {trade_direction} - {trade_result} (${trade['pnl_dollar']:.2f})")
                plt.close("all")

            plt.close("all")




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
            message = f"🚀 Starting {self.symbol} paper trading for {duration_minutes} minutes...\n"
            print(message)
            start_time = datetime.now()
            end_time = start_time + timedelta(minutes=duration_minutes)
        else:
            message = f"🚀 Starting {self.symbol} paper trading INDEFINITELY...\n"
            print(message)
            print("Press Ctrl+C to stop the bot")
            end_time = None

        print(f"Trading symbol: {self.symbol}")
        message += f"Trading symbol: {self.symbol}\n"
        print(f"Risk per trade: ${RISK_PER_TRADE}")
        message += f"Risk per trade: {RISK_PER_TRADE}"

        send_telegram_message(message)
        ltf_data, htf_data, current_price = await self.fetch_initial_data()
        self.full_data = ltf_data

        subscribe_msg = {
            "method": "subscribe",
            "subscription": {
                "type": "candle",
                "coin": "SOL",
                "interval": "1m"
            }
        }
        url = "wss://api.hyperliquid.xyz/ws"

        while True:
            if end_time and datetime.now() >= end_time:
                break
            elif is_stop_requested():
                send_telegram_message("🛑 Bot stopped by user")
                break

            # Check the cooldown period
            cooldown_remaining = None
            if self.last_position_close_time:
                time_since_close = datetime.now() - self.last_position_close_time
                cooldown_remaining = 300 - time_since_close.total_seconds()  # 5 minutes = 300 seconds

            try:
                async with websockets.connect(
                    url,
                    ssl=self.ssl_context,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5
                ) as ws:
                    await ws.send(json.dumps(subscribe_msg))
                    await ws.recv()

                    candle = await ws.recv()
                    candle = json.loads(candle)['data']

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

                        self.full_data = pd.concat([self.full_data, pd.DataFrame([self.last_candle])], ignore_index=True)
                        self.plot_times.append(self.last_candle['T'])
                        self.plot_opens.append(self.last_candle['open'])
                        self.iteration = len(self.full_data) - 1


                        if self.last_candle['T'] >= htf_data['T'].iloc[-1] + timedelta(minutes=HTF_TIMEFRAME_INT):
                            print("Adding htf candle")
                            htf_data = pd.concat([htf_data, pd.DataFrame([self.last_candle])], ignore_index=True)
                            htf_data = htf_data.iloc[-self.htf_lookback:].reset_index(drop=True)

                        if cooldown_remaining and cooldown_remaining > 0:
                            print(f"⏳ COOLDOWN ACTIVE for {self.symbol}: {cooldown_remaining:.0f} seconds remaining")
                            await asyncio.sleep(1)
                            continue

                        self.single_iteration(ltf_data=ltf_data, htf_data=htf_data, current_time=ltf_data['T'].iloc[-1], telegram=True)
                        self.last_candle = None

            except websockets.exceptions.ConnectionClosedError as e:
                print(f"Connection closed, trying to reconnect in 30 seconds... ({e})")
                await asyncio.sleep(30)
            except Exception as e:
                print(f"Unexpected error, attempting to continue in 60 seconds ({e})")
                await asyncio.sleep(60)

        if self.current_position:
            final_price = ltf_data['close'].iloc[-1]
            self._close_live_position(final_price, "Finished trading")

        self.full_data.set_index('T', inplace=True)
        self.full_data.sort_index(inplace=True)
        self.create_and_send_images()


trader = LiveTrader("SOL")
asyncio.run(trader.run_paper_trading(duration_minutes=60*24))