import pandas as pd
import numpy as np
from typing import List, Tuple, Dict, Optional


class StructureAnalyzer:
    def __init__(self, min_fvg_strength):
        # Minimum FVG size: 0.10% of coin value
        self.min_fvg_strength = min_fvg_strength

    def detect_swing_points(self, highs: np.ndarray, lows: np.ndarray):
        """Detect swing highs and lows in the price data

        Returns: swing_highs, swing_lows
        """
        swing_highs = np.full(shape=len(highs), fill_value=np.nan)
        swing_lows = np.full(shape=len(highs), fill_value=np.nan)

        for i in range(2, len(highs) - 3):
            if (highs[i] > highs[i - 1]
                    and highs[i] > highs[i - 2]
                    and highs[i] > highs[i - 3]
                    and highs[i] > highs[i + 1]
                    and highs[i] > highs[i + 2]
                    and highs[i] > highs[i + 3]):
                swing_highs[i] = highs[i]
            if (lows[i] < lows[i - 1]
                    and lows[i] < lows[i - 2]
                    and lows[i] < lows[i - 3]
                    and lows[i] < lows[i - 1]
                    and lows[i] < lows[i + 1]
                    and lows[i] < lows[i + 2]):
                swing_lows[i] = lows[i]

        return swing_highs, swing_lows


    def detect_fvg(self, df: pd.DataFrame) -> List[Dict]:
        """Detect Fair Value Gaps (FVG) - imbalance between candles"""
        fvgs = []

        highs = df['high'].to_numpy()
        lows = df['low'].to_numpy()
        closes = df['close'].to_numpy()
        times = df['T'].to_numpy()

        avg_candle_size = np.mean(highs[-24:] - lows[-24:])

        for i in range(2, len(highs)):
            # Bullish FVG: gap between candle 1's high and candle 3's low
            c1_high = highs[i - 2]
            c2_high = highs[i - 1]
            c3_high = highs[i]

            c1_low = lows[i - 2]
            c2_low = lows[i - 1]
            c3_low = lows[i]

            if (c3_low > c1_high and
                    c2_low > c1_low and
                    c3_high > c2_high):
                
                # Calculate FVG size and validate structure
                fvg_size = c3_low - c1_high
                fvg_midpoint = c1_high + (fvg_size / 2)
                c2_size = c2_high - c2_low
                
                # Validate: candle before can't go more than 1/3 up the FVG
                max_before_penetration = c2_low + (c2_size / 3)
                # Validate: candle after can't go more than 1/3 down the FVG  
                min_after_penetration = c2_high - (c2_size / 3)
                
                # Check if candle before (c1) didn't penetrate too much
                before_valid = c1_high <= max_before_penetration
                # Check if candle after (c3) didn't penetrate too much
                after_valid = c3_low >= min_after_penetration
                
                # Check if FVG candle (c2) is larger than both surrounding candles
                c1_size = c1_high - c1_low
                c3_size = c3_high - c3_low
                fvg_candle_largest = (c2_size > c1_size) and (c2_size > c3_size)
                
                if fvg_size > avg_candle_size:
                    fvg = {
                        'type': 'bullish',
                        'time': pd.to_datetime(times[i - 1]),
                        'top': c3_low,
                        'bottom': c1_high,
                        'strength': fvg_size,
                        'filled': False
                    }
                    if fvg['strength'] >= closes[i] * self.min_fvg_strength:
                        fvgs.append(fvg)

            # Bearish FVG: gap between candle 1's low and candle 3's high
            c1_high = highs[i - 2]
            c2_high = highs[i - 1]
            c3_high = highs[i]

            c1_low = lows[i - 2]
            c2_low = lows[i - 1]
            c3_low = lows[i]

            if (c3_high < c1_low and
                    c2_high < c1_high and
                    c3_low < c2_low):
                
                # Calculate FVG size and validate structure
                fvg_size = c1_low - c3_high
                fvg_midpoint = c3_high + (fvg_size / 2)
                c2_size = c2_high - c2_low 
                
                # Validate: candle before can't go more than 1/3 down the FVG
                min_before_penetration = c2_high - (c2_size / 3)
                # Validate: candle after can't go more than 1/3 up the FVG
                max_after_penetration = c2_low + (c2_size / 3)
                
                # Check if candle before (c1) didn't penetrate too much
                before_valid = c1_low >= min_before_penetration
                # Check if candle after (c3) didn't penetrate too much
                after_valid = c3_high <= max_after_penetration
                
                # Check if FVG candle (c2) is larger than both surrounding candles
                c1_size = c1_high - c1_low 
                c3_size = c3_high - c3_low
                fvg_candle_largest = (c2_size > c1_size) and (c2_size > c3_size)
                
                if fvg_size > avg_candle_size:
                    fvg = {
                        'type': 'bearish',
                        'time': pd.to_datetime(times[i - 1]),
                        'top': c1_low,
                        'bottom': c3_high,
                        'strength': fvg_size,
                        'filled': False
                    }
                    if fvg['strength'] >= closes[i] * self.min_fvg_strength:
                        fvgs.append(fvg)

        return fvgs

    def analyze_structure(self, df: pd.DataFrame) -> pd.DataFrame:
        """Complete structure analysis combining all methods"""
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()

        # Apply all analysis methods
        swing_highs, swing_lows = self.detect_swing_points(highs, lows)
        df.loc[:,"swing_high"] = swing_highs
        df.loc[:,"swing_low"] = swing_lows

        return df

    def check_fvg_touch(self, current_price: float, fvg: Dict) -> bool:
        return fvg['bottom'] <= current_price <= fvg['top']