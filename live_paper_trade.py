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
import numpy as np

class LiveTrader(BaseTrader):
    def __init__(self, symbol):
        super().__init__(symbol, 10_000, telegram=True)

        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

        self.working_candle = None
        self.full_data = pd.DataFrame()
        self.ltf_data = pd.DataFrame()
        self.htf_data = pd.DataFrame()

        self.dex = ccxt.hyperliquid({
            "walletAddress": HYPERLIQUID_ACCOUNT_ADDRESS,
            "privateKey": "",  # No private key needed for paper trading
        })

        self.current_price = 0


    async def fetch_initial_data(self):
        try:
            ltf_data = self.dex.fetch_ohlcv(self.symbol + '/USDC:USDC', timeframe='1m', limit=self.ltf_lookback)
            htf_data = self.dex.fetch_ohlcv(self.symbol + '/USDC:USDC', timeframe='15m',
                                            limit=self.ltf_lookback)

            ltf_list = []
            htf_list = []

            for index in range(len(ltf_data) - self.ltf_lookback - 1, len(ltf_data) - 1):
                ltf_list.append({
                    "T": datetime.fromtimestamp(ltf_data[index][0] / 1000),
                    "open": ltf_data[index][1],
                    "high": ltf_data[index][2],
                    "low": ltf_data[index][3],
                    "close": ltf_data[index][4],
                })
            for index in range(len(htf_data) - self.htf_lookback - 1, len(htf_data) - 1):
                htf_list.append({
                    "T": datetime.fromtimestamp(htf_data[index][0] / 1000),
                    "open": htf_data[index][1],
                    "high": htf_data[index][2],
                    "low": htf_data[index][3],
                    "close": htf_data[index][4],
                })

            ltf_df = pd.DataFrame(ltf_list)
            htf_df = pd.DataFrame(htf_list)

            return ltf_df, htf_df

        except Exception as e:
            print(f"🔴 FULL ERROR in fetch_initial_data: {e}")
            import traceback
            traceback.print_exc()
            return None, None, None


    def handle_candle_data(self, ltf_candle, htf_candle, current_time):
        last_ltf_time = self.ltf_data['T'].iloc[-1]
        last_htf_time = self.htf_data['T'].iloc[-1]

        if htf_candle['T'] > last_htf_time:
            self.htf_data = pd.concat([self.htf_data, pd.DataFrame([htf_candle])], ignore_index=True)
            self.htf_data = self.htf_data.iloc[-self.htf_lookback:].reset_index(drop=True)
        if ltf_candle['T'] > last_ltf_time:
            self.ltf_data = pd.concat([self.ltf_data, pd.DataFrame([ltf_candle])], ignore_index=True)
            self.ltf_data = self.ltf_data.iloc[-self.ltf_lookback:].reset_index(drop=True)
            print("Processing new candle")
            print("======================")
            print(f"LTF Tail: {self.ltf_data.tail(5)}")
            print(f"HTF Tail: {self.htf_data.tail(5)}")
            print(f"Active fvg count: {len(self.strategy.active_fvgs)}")
            print(f"Active fvgs: {self.strategy.active_fvgs}")
            print(f"Active setup count: {len(self.strategy.active_setups)}")
            print(f"Active setups: {self.strategy.active_setups}")
            print("======================")
            self.process_new_candle(ltf_data=self.ltf_data, htf_data=self.htf_data, timestamp=current_time)


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

        while True:
            if end_time and datetime.now() >= end_time:
                break
            elif is_stop_requested():
                send_telegram_message("🛑 Bot stopped by user")
                break

            try:
                ltf_candle = self.dex.fetch_ohlcv(self.symbol + '/USDC:USDC', timeframe='1m', limit=2)
                htf_candle = self.dex.fetch_ohlcv(self.symbol + '/USDC:USDC', timeframe='15m', limit=2)

                current_high = ltf_candle[-1][2]
                current_low = ltf_candle[-1][3]
                current_time = datetime.fromtimestamp(ltf_candle[-1][0] / 1000)

                ltf_candle = {
                    'T': datetime.fromtimestamp(ltf_candle[0][0] / 1000),
                    'open': ltf_candle[0][1],
                    'high': ltf_candle[0][2],
                    'low': ltf_candle[0][3],
                    'close': ltf_candle[0][4],
                }
                htf_candle = {
                    'T': datetime.fromtimestamp(htf_candle[0][0] / 1000),
                    'open': htf_candle[0][1],
                    'high': htf_candle[0][2],
                    'low': htf_candle[0][3],
                    'close': htf_candle[0][4],
                }

                current_ticker = self.dex.fetch_ticker(self.symbol + '/USDC:USDC')
                self.current_price = current_ticker['last']

                self.handle_candle_data(ltf_candle, htf_candle, current_time)

                if self.last_position_close_time:
                    if datetime.now() - self.last_position_close_time < timedelta(minutes=5):
                        print("Position recently closed, skipping candle")
                        continue

                self.handle_positions(ltf_data=self.ltf_data, current_price=self.current_price, current_high=current_high,
                                             current_low=current_low, current_time=current_time, trade_config="livetest")

                await asyncio.sleep(5)

            except Exception as e:
                print(f"🔴 FULL ERROR in main loop: {e}")
                import traceback
                traceback.print_exc()
                print(f"Attempting to continue in 15 seconds...")
                await asyncio.sleep(15)


        if self.current_position:
            self.handle_position_close(datetime.now())

        self.show_final_results(self.trades, "live test")

trader = LiveTrader("SOL")
asyncio.run(trader.run_paper_trading(duration_minutes=60*24*7))  # 7 days