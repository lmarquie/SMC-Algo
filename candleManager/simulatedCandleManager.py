from models.candle import Candle
from candleManager.candleManager import CandleManager
from helpers.fetch_data import fetch_binance_data, fetch_hyperliquid_data
from datetime import timedelta
from trading_logic.structure_analysis import StructureAnalyzer
import sys
from datetime import datetime

from config import LTF_LOOKBACK, HTF_LOOKBACK, HTF_CANDLE_DURATION, MIN_FVG_STRENGTH

import pandas as pd

class SimulatedCandleManager(CandleManager):
    def __init__(self, exchange, symbol):
        super().__init__()
        start_amount = 100
        # Exchange is either 'binance' or 'hyperliquid'
        if exchange == 'binance':
            data = fetch_binance_data(symbol)
        elif exchange == 'hyperliquid':
            data = fetch_hyperliquid_data(symbol)
        else:
            raise ValueError("Invalid exchange")

        self.analyzer = StructureAnalyzer(min_fvg_strength=MIN_FVG_STRENGTH)

        self.ltf_data = data.head(start_amount)

        time_idx_df = self.ltf_data.set_index('T', inplace=False, drop=False)
        self.htf_data = time_idx_df.resample('15min').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
        }).dropna()
        print(" - Candle Manager Initialized:")

        self.future_data = data.iloc[start_amount:]


    def process_candle(self, next_candle: Candle):
        # Adds candle to the end of working database
        # - Runs analysis based on analyze_structure.py, appends columns to working df
        # - If simulated, adds next item from future_candles df
        if len(self.future_data) == 0:
            return -1
        else:
            next_candle = self.future_data.iloc[[0]]
            self.ltf_data = pd.concat([self.ltf_data, next_candle], ignore_index=True)
            self.ltf_data = self.ltf_data[-LTF_LOOKBACK:]
            self.ltf_data = self.analyzer.analyze_structure(self.ltf_data)
            if next_candle['T'].iloc[0] >= self.htf_data.index[-1] + timedelta(minutes=HTF_CANDLE_DURATION):
                next_candle.set_index('T', inplace=True, drop=False)
                self.htf_data = pd.concat([self.ltf_data, next_candle])
                self.htf_data = self.htf_data[-HTF_LOOKBACK:]
                self.htf_data = self.analyzer.analyze_structure(self.htf_data)

            self.future_data = self.future_data.iloc[1:]
            return 0
