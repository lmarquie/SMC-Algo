import pandas as pd
import numpy as np
from typing import List, Tuple, Dict, Optional

class StructureAnalyzer:
    def __init__(self, lookback: int = 10):
        self.lookback = lookback
        
    def detect_swing_points(self, highs: np.array, lows: np.array):
        """Detect swing highs and lows in the price data

        Returns: swing_highs, swing_lows
        """
        swing_highs = np.full(shape=len(highs), fill_value=np.nan)
        swing_lows = np.full(shape=len(highs), fill_value=np.nan)

        for i in range(1, len(highs) - 2):
            if highs[i] > highs[i-1] and highs[i] > highs[i + 1]:
                swing_highs[i] = highs[i]
            if lows[i] < lows[i-1] and lows[i] < lows[i + 1]:
                swing_lows[i] = lows[i]



        return swing_highs, swing_lows
    
    def detect_bos(self, closes: np.array, swing_highs: np.array, swing_lows: np.array):
        """
        Detect Break of Structure (BOS) - price breaking above/below swing points

        Returns: bullish_bos, bearish_bos
        """

        bullish_bos = np.full(len(closes), 0)
        bearish_bos = np.full(len(closes), 0)

        last_swing_high = None
        last_swing_low = None

        for i in range(len(closes)):
            # Update last swing points
            if not pd.isna(swing_highs[i]):
                last_swing_high = swing_highs[i]
            if not pd.isna(swing_lows[i]):
                last_swing_low = swing_lows[i]

            # Detect bullish BOS: close above last swing high
            if last_swing_high and closes[i] > last_swing_high:
                bullish_bos[i] = 1
                last_swing_high = None  # Reset after BOS

            # Detect bearish BOS: close below last swing low
            if last_swing_low and closes[i] < last_swing_low:
                bearish_bos[i] = 1
                last_swing_low = None  # Reset after B1OS

        return bullish_bos, bearish_bos
    
    def detect_mss(self, swing_highs: np.array, swing_lows: np.array):
        """
        Detect Market Structure Shift (MSS) - change from higher highs to lower highs or vice versa

        Returns: bullish_mss, bearish_mss
        """

        bullish_mss = np.full(len(swing_highs), 0)
        bearish_mss = np.full(len(swing_highs), 0)

        # Drop NA Values
        swing_highs_dropna = swing_highs[np.logical_not(np.isnan(swing_highs))]
        swing_lows_dropna = swing_lows[np.logical_not(np.isnan(swing_lows))]

        if len(swing_lows_dropna) >= 3:
            for i in range(2, len(swing_lows_dropna)):
                if (swing_lows_dropna[i - 2] < swing_lows_dropna[i - 1] and  # Lower low
                        swing_lows_dropna[i] < swing_lows_dropna[i - 1]):  # Higher low
                    # Find the index of this swing low in the original dataframe
                    idx = np.where(swing_lows == swing_lows_dropna[i])[0][0]
                    bullish_mss[idx] = 1

        if len(swing_highs_dropna) >= 3:
            for i in range(2, len(swing_highs_dropna)):
                if (swing_highs_dropna[i - 2] > swing_highs_dropna[i - 1] and  # Higher high
                        swing_highs_dropna[i] > swing_highs_dropna[i - 1]):  # Lower high
                    # Find the index of this swing high in the original dataframe
                    idx = np.where(swing_highs == swing_highs_dropna[i])[0][0]
                    bearish_mss[idx] = 1

        return bullish_mss, bearish_mss
    
    def detect_fvg(self, df: pd.DataFrame) -> List[Dict]:
        """Detect Fair Value Gaps (FVG) - imbalance between candles"""
        fvgs = []

        highs = df['high'].to_numpy()
        lows = df['low'].to_numpy()

        for i in range(2, len(highs)):
            # Bullish FVG: gap between candle 1's high and candle 3's low
            c1_high = highs[i - 2]
            c2_low = lows[i - 1]
            c3_low = lows[i]

            if c3_low > c1_high:
                fvg = {
                    'type': 'bullish',
                    'start_idx': i - 1,
                    'end_idx': i,
                    'top': c2_low,
                    'bottom': c1_high,
                    'strength': c2_low - c1_high,
                    'filled': False
                }
                fvgs.append(fvg)

                # Bearish FVG: gap between candle 1's low and candle 3's high
                c1_low = lows[i - 2]
                c2_high = highs[i - 1]
                c3_high = highs[i]

                if c3_high < c1_low:
                    fvg = {
                        'type': 'bearish',
                        'start_idx': i - 1,
                        'end_idx': i,
                        'top': c1_low,
                        'bottom': c2_high,
                        'strength': c1_low - c2_high,
                        'filled': False
                    }
                    fvgs.append(fvg)

        return fvgs
    
    def detect_displacement(self, opens: np.array, closes: np.array, highs: np.array, lows: np.array, threshold: float = 0.6):
        """
        Detect displacement candles - strong moves with large body relative to wick

        Returns: bodies, wicks, displacements, displacement_directions
        """

        bodies = np.empty(len(opens))
        wicks = np.empty(len(opens))
        displacements = np.empty(len(opens))
        displacement_directions = np.empty(len(opens), dtype='str')

        for i in range(0, len(opens)):
            bodies[i] = abs(closes[i] - opens[i])
            wicks[i] = highs[i] - lows[i]

            if wicks[i] > 0:
                displacements[i] = bodies[i] / wicks[i] > threshold
            else:
                displacements[i] = False

            if displacements[i]:
                displacement_directions[i] = 'bullish' if closes[i] > opens[i] else 'bearish'
            else:
                displacement_directions[i] = 'none'

        return bodies, wicks, displacements, displacement_directions
    
    def detect_liquidity_sweep(self, highs: np.array, lows: np.array, swing_highs: np.array, swing_lows: np.array, threshold: float = 0.001):
        """
        Detect liquidity sweeps - wicks that extend beyond recent highs/lows

        Returns: bullish_sweeps, bearish_sweeps
        """

        bullish_sweeps = np.full(len(highs), 0)
        bearish_sweeps = np.full(len(highs), 0)

        for i in range(self.lookback, len(highs)):
            # Look for bullish sweeps: low extends below recent swing low
            recent_swing_lows = swing_lows[i - self.lookback:i]
            swing_lows_notna = recent_swing_lows[np.logical_not(np.isnan(recent_swing_lows))]
            if len(swing_lows_notna) > 0:
                min_low = swing_lows_notna.min()
                if lows[i] < min_low - threshold:
                    bullish_sweeps[i] = 1

            recent_swing_highs = swing_highs[i - self.lookback:i]
            swing_highs_notna = recent_swing_highs[np.logical_not(np.isnan(recent_swing_highs))]
            if len(swing_highs_notna) > 0:
                max_high = swing_highs_notna.max()
                if highs[i] > max_high + threshold:
                    bearish_sweeps[i] = 1

        return bullish_sweeps, bearish_sweeps
    
    def analyze_structure(self, df: pd.DataFrame) -> pd.DataFrame:
        """Complete structure analysis combining all methods"""
        df = df.copy()
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        opens = df["open"].to_numpy()
        closes = df["close"].to_numpy()
        
        # Apply all analysis methods
        swing_highs, swing_lows = self.detect_swing_points(highs, lows)
        bullish_bos, bearish_bos = self.detect_bos(closes, swing_highs, swing_lows)
        bullish_mss, bearish_mss = self.detect_mss(swing_highs, swing_lows)
        bodies, wicks, displacements, displacement_directions = self.detect_displacement(opens, closes, highs, lows)
        bullish_sweeps, bearish_sweeps = self.detect_liquidity_sweep(highs, lows, swing_highs, swing_lows)

        df["swing_high"] = swing_highs
        df["swing_low"] = swing_lows
        df["bullish_bos"] = bullish_bos
        df["bearish_bos"] = bearish_bos
        df["bullish_mss"] = bullish_mss
        df["bearish_mss"] = bearish_mss
        df["body"] = bodies
        df["wick"] = wicks
        df["displacement"] = displacements
        df["displacement_direction"] = displacement_directions
        df["bullish_sweep"] = bullish_sweeps
        df["bearish_sweep"] = bearish_sweeps
        
        return df
    
    def check_fvg_touch(self, current_price: float, fvg: Dict) -> bool:
        """Check if current price is touching a FVG zone"""
        if fvg['type'] == 'bullish':
            return fvg['bottom'] <= current_price <= fvg['top']
        else:  # bearish
            return fvg['bottom'] <= current_price <= fvg['top']
