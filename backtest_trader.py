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


class BacktestTrader(BaseTrader):
    def __init__(self, symbol, initial_balance=10_000):
        super().__init__(symbol, self._open_position, self._close_position, initial_balance)

        self.plot_indices = np.array([])
        self.plot_opens = np.array([])
        self.open_times = np.array([])
        self.open_values = np.array([])
        self.close_times = np.array([])
        self.close_values = np.array([])

        self.long_count = 0
        self.short_count = 0

        self.htf_lookback = 50
        self.ltf_lookback = 100


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

            self.single_iteration(current_data, current_htf_data, current_time)

        # Close any remaining position
        if self.current_position:
            final_price = data['close'].iloc[-1]
            self._close_position(final_price, data.index[-1])

        print(f"Real data backtest completed. Total trades: {len(self.trades)}")
        return self.trades


    def _open_position(self, setup: Dict, current_price: float, timestamp):
        position = self.create_open_order(setup, current_price, timestamp)
        self.current_position = position


    def _close_position(self, current_price: float, timestamp):
        """Close current position"""
        if not self.current_position:
            return
        try:
            # Always use 'Stop Loss Hit' as the reason
            reason = "Stop Loss Hit"
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
            # Debug P&L calculation
            print(f"P&L Debug: Entry: ${self.current_position['entry_price']:.4f}, Exit: ${current_price:.4f}")
            print(f"P&L Debug: Price diff: ${price_diff:.4f}, Position size: {self.current_position['size']:.4f}")
            print(f"P&L Debug: Raw P&L: ${pnl_dollar:.2f}")
            # Update balance
            self.current_balance += pnl_dollar
            # Record trade
            trade = {
                'entry_time': self.current_position['entry_time'],
                'exit_time': timestamp,
                'direction': self.current_position['direction'],
                'entry_price': self.current_position['entry_price'],
                'entry_idx': self.current_position['entry_idx'],
                'exit_price': current_price,
                'exit_idx': self.iteration,
                'size': self.current_position['size'],
                'pnl_pct': pnl_pct,
                'fvg': self.current_position['fvg'],
                'bos': self.current_position['bos'],
                'mss': self.current_position['mss'],
                'pnl_dollar': pnl_dollar,
                'reason': reason,
                'exit_reason': reason,
                'symbol': self.current_position['symbol']
            }
            self.trades.append(trade)
            print(f"Position closed: {pnl_pct:.4f} ({pnl_dollar:.2f}) - {reason}")
            # Reset position
            self.current_position = None
        except Exception as e:
            print(f"Error closing position: {e}")