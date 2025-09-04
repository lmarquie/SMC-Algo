import pandas as pd
from helpers.hyperliquid_client import HyperliquidClient
from helpers.telegram_setup import send_telegram_message, is_stop_requested, send_telegram_image
from config import *
from credentials import *
from datetime import datetime, timedelta
import asyncio
from base_trader import BaseTrader
import ssl
import certifi
import matplotlib.pyplot as plt
import os
import shutil
import ccxt

class LiveTrader(BaseTrader):
    def __init__(self, symbol):
        super().__init__(symbol, 10_000)

        self.client = HyperliquidClient(
            HYPERLIQUID_API_KEY,
            HYPERLIQUID_SUBACCOUNT,
        )

        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

        self.working_candle = None
        self.full_data = pd.DataFrame()
        self.ltf_data = pd.DataFrame()
        self.htf_data = pd.DataFrame()

        self.plot_times = []
        self.plot_opens = []

        self.current_price = 0
        self.last_price = 0


    def create_and_send_images(self):
        winning_trades = [t for t in self.trades if t['pnl_dollar'] > 0]
        losing_trades = [t for t in self.trades if t['pnl_dollar'] < 0]

        plt.plot(self.plot_times, self.plot_opens, color="blue", alpha=0.5, linewidth=1)

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

        plt.savefig("backtest.png")
        plt.close()

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
                elif idx in trade['indicator']:
                    color = 'yellow' if trade['indicator_type'] == 'mss' else 'blue'
                    boxprops = {'facecolor': color, 'alpha': 1}
                elif idx > entry_idx and idx < exit_idx:
                    if candle["close"] >= candle["open"]:
                        boxprops = {'facecolor': 'green', 'alpha': 0.4}
                    else:
                        boxprops = {'facecolor': 'red', 'alpha': 0.4}
                else:
                    boxprops = {'facecolor': 'white', 'alpha': 1.0}

                ax.boxplot(
                    boxplot_data,
                    positions=[(idx - entry_idx + 1) * 3],
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
                plt.savefig(f"trades/wins/trade_{i + 1}.png", dpi=600, bbox_inches="tight")
            else:
                plt.savefig(f"trades/losses/trade_{i + 1}.png", dpi=600, bbox_inches="tight")

            plt.close("all")


    async def fetch_initial_data(self):
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


            if ltf_data.empty or htf_data.empty:
                print(f"Failed to fetch market data for {self.symbol}")
                return None, None, None

            return ltf_data, htf_data

        except Exception as e:
            print(f"Error fetching live data for {self.symbol}: {e}")
            return None, None, None


    async def handle_candle_data(self, candle):
        if not self.working_candle:
            self.working_candle = candle
        elif candle['T'] == self.working_candle['T']:
            self.working_candle['high'] = candle['high']
            self.working_candle['low'] = candle['low']
            self.working_candle['close'] = candle['close']
        else:
            print("Adding ltf candle")
            self.ltf_data = pd.concat([self.ltf_data, pd.DataFrame([self.working_candle])], ignore_index=True)
            self.ltf_data = self.ltf_data.iloc[-self.ltf_lookback:].reset_index(drop=True)

            self.full_data = pd.concat([self.full_data, pd.DataFrame([self.working_candle])], ignore_index=True)
            self.plot_times.append(self.working_candle['T'])
            self.plot_opens.append(self.working_candle['open'])
            self.iteration = len(self.full_data) - 1

            if self.working_candle['T'] >= self.htf_data['T'].iloc[-1] + timedelta(minutes=HTF_TIMEFRAME_INT):
                print("Adding htf candle")
                self.htf_data = pd.concat([self.htf_data, pd.DataFrame([self.working_candle])], ignore_index=True)
                self.htf_data = self.htf_data.iloc[-self.htf_lookback:].reset_index(drop=True)

            self.working_candle = None
            self.process_new_candle(ltf_data=self.ltf_data, htf_data=self.htf_data, timestamp=datetime.now(), telegram=True)


    def check_position_opened(self, current_candle):
        sorted_setups = sorted(
            self.strategy.active_setups,
            key = lambda setup: setup['fvg']['top'],
            reverse=True,
        )

        for setup in sorted_setups:
            fvg_midpoint = (setup['fvg']['top'] + setup['fvg']['bottom']) / 2
            if current_candle['high'] >= fvg_midpoint >= current_candle['low']:
                return setup


    async def handle_price_data(self, current_candle, current_price, last_price):
        if not self.current_position:
            position = self.check_position_opened(current_candle)
            if position:
                self.handle_position_open(position, datetime.now(), telegram=True)
        else:
            if self.check_position_closed(current_price):
                self.handle_position_close(current_price, datetime.now(), telegram=True)


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
        self.ltf_data, self.htf_data = await self.fetch_initial_data()
        self.full_data = self.ltf_data

        dex = ccxt.hyperliquid({
            "walletAddress": HYPERLIQUID_ACCOUNT_ADDRESS,
            "privateKey": HYPERLIQUID_API_KEY,
        })

        while True:
            if end_time and datetime.now() >= end_time:
                break
            elif is_stop_requested():
                send_telegram_message("🛑 Bot stopped by user")
                break

            try:
                current_candle = dex.fetch_ohlcv(self.symbol + '/USDC:USDC', timeframe='1m', limit=1)
                current_candle = {
                    'T': datetime.fromtimestamp(current_candle[0][0] / 1000),
                    'open': current_candle[0][1],
                    'high': current_candle[0][2],
                    'low': current_candle[0][3],
                    'close': current_candle[0][4],
                }

                self.last_price = self.current_price
                self.current_price = dex.fetch_ticker(self.symbol + '/USDC:USDC')['last']

                if self.last_position_close_time:
                    if datetime.now() - self.last_position_close_time > timedelta(minutes=5):
                        continue

                await self.handle_candle_data(current_candle)
                await self.handle_price_data(current_candle=current_candle, current_price=self.current_price, last_price=self.last_price)


            except Exception as e:
                print(f"Unexpected error, attempting to continue in 60 seconds ({e})")
                await asyncio.sleep(60)


        if self.current_position:
            final_price = self.ltf_data['close'].iloc[-1]
            self.handle_position_close(final_price, datetime.now(), telegram=True)

        self.create_and_send_images()


trader = LiveTrader("SOL")
asyncio.run(trader.run_paper_trading(duration_minutes=60*24))