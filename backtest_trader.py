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
        super().__init__(symbol, initial_balance, telegram=False)

        self.plot_opens = np.array([])
        self.open_times = np.array([])
        self.open_values = np.array([])
        self.close_times = np.array([])
        self.close_values = np.array([])


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
            htf_end_idx = htf_data.index.get_indexer([current_time], method='ffill')[0]
            current_htf_data = htf_data.iloc[max(0, htf_end_idx + 1 - self.htf_lookback):htf_end_idx + 1]

            self.process_new_candle(current_data, current_htf_data, current_time)

            if (not self.last_position_close_time
                or current_time - self.last_position_close_time > pd.Timedelta(minutes=self.trade_cooldown)):
                self.handle_positions(current_data, current_price=current_data['close'].iloc[-1], current_high=current_data['high'].iloc[-1], current_low=current_data['low'].iloc[-1], current_time=current_time)

            print("======================")
            print(f"Active setups: {self.strategy.active_setups}")

        # Close any remaining position
        if self.current_position:
            self.handle_position_close(datetime.fromtimestamp(data.index[-1]))

        print(f"Real data backtest completed. Total trades: {len(self.trades)}")
        return self.trades


