import pandas as pd
import numpy as np
import logging
from typing import Dict
import asyncio
import json
import shutil
import os

from helpers.hyperliquid_client import HyperliquidClient
import matplotlib.pyplot as plt
from base_trader import BaseTrader
from datetime import datetime


class BacktestTrader(BaseTrader):
    def __init__(self, symbol, initial_balance=10_000):
        super().__init__(symbol, initial_balance)

        self.plot_indices = np.array([])
        self.plot_opens = np.array([])
        self.open_times = np.array([])
        self.open_values = np.array([])
        self.close_times = np.array([])
        self.close_values = np.array([])

        self.long_count = 0
        self.short_count = 0


    async def run_backtest(self, data):
        """Run backtest on real market data"""
        print(f"Starting real data backtest for {self.symbol}...")

        # Create HTF data by resampling
        date_indexed_df = data.set_index('T', inplace=False, drop=False)
        htf_data = date_indexed_df.resample('15T').agg({ ### CHANGE TO 5 MINUTES
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
        }).dropna()

        print(f"Running backtest on {len(data)} candles...")

        # Run through each candle
        for i in range(50, len(data) - 1):  # Start from 50 to have enough history

            if i % 50 == 0:
                print(f"Current Iteration: {i}/{len(data) - 1}, Balance: ${self.current_balance:.2f}")

            if self.last_close:
                if data.iloc[-1]['T'] - self.last_close < pd.Timedelta(minutes=self.trade_cooldown):
                    continue

            # Get data up to current point
            current_data = data.iloc[max(0, i + 1 - self.ltf_lookback):i + 1]
            self.iteration = i

            # Get corresponding HTF data
            current_time = current_data['T'].iloc[-1]
            current_open = current_data['open'].iloc[-1]
            htf_end_idx = htf_data.index.get_indexer([current_time], method='ffill')[0]
            current_htf_data = htf_data.iloc[max(0, htf_end_idx + 1 - self.htf_lookback):htf_end_idx + 1]

            # Plotting data
            self.plot_indices = np.append(self.plot_indices, self.iteration)
            self.plot_opens = np.append(self.plot_opens, current_open)

            if (not self.last_position_close_time
                or current_time - self.last_position_close_time > pd.Timedelta(minutes=self.trade_cooldown)):
                self.handle_positions(current_data['close'].iloc[-1], current_data['high'].iloc[-1], current_data['low'].iloc[-1], current_time)

            self.process_new_candle(current_data, current_htf_data, current_time)

        # Close any remaining position
        if self.current_position:
            final_price = data['close'].iloc[-1]
            self.handle_position_close(final_price, datetime.fromtimestamp(data.index[-1]))

        print(f"Real data backtest completed. Total trades: {len(self.trades)}")
        return self.trades


    def handle_positions(self, current_price, current_high, current_low, current_time):
        if not self.current_position:
            position = self.check_position_opened(current_high, current_low)
            if position:
                print("POSITION FOUND")
                self.handle_position_open(position, current_time)
                if self.check_position_closed(current_price):
                    self.handle_position_close(current_price, current_time)
        else:
            if self.check_position_closed(current_price):
                self.handle_position_close(current_price, current_time)


    def check_position_opened(self, current_high, current_low):
        sorted_setups = sorted(
            self.strategy.active_setups,
            key = lambda setup: setup['fvg']['top'],
            reverse=True,
        )

        for setup in sorted_setups:
            fvg_midpoint = (setup['fvg']['top'] + setup['fvg']['bottom']) / 2
            if current_high >= fvg_midpoint >= current_low:
                return setup
        return None
