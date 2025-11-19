from models.candle import Candle
import pandas as pd

class CandleManager:
    def __init__(self):
        pass

    def process_candle(self, next_candle: Candle):
        # Adds candle to end of working database
        # - Runs analysis based on analyze_structure.py, appends columns to working df
        # - If simulated, adds next item from future_candles df
        pass
