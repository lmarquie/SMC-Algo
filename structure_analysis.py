import pandas as pd
import numpy as np
from typing import List, Tuple, Dict, Optional

class StructureAnalyzer:
    def __init__(self, lookback: int = 10):
        self.lookback = lookback
        
    def detect_swing_points(self, df: pd.DataFrame) -> pd.DataFrame:
        """Detect swing highs and lows in the price data"""
        df = df.copy()
        
        # Swing highs: high > previous high AND high > next high
        df['swing_high'] = np.where(
            (df['high'] > df['high'].shift(1)) & 
            (df['high'] > df['high'].shift(-1)),
            df['high'],
            np.nan
        )
        
        # Swing lows: low < previous low AND low < next low
        df['swing_low'] = np.where(
            (df['low'] < df['low'].shift(1)) & 
            (df['low'] < df['low'].shift(-1)),
            df['low'],
            np.nan
        )
        
        return df
    
    def detect_bos(self, df: pd.DataFrame) -> pd.DataFrame:
        """Detect Break of Structure (BOS) - price breaking above/below swing points"""
        df = df.copy()
        df = self.detect_swing_points(df)
        
        # Initialize BOS columns
        df['bullish_bos'] = 0
        df['bearish_bos'] = 0
        
        last_swing_high = None
        last_swing_low = None
        
        for i in range(len(df)):
            # Update last swing points
            if not pd.isna(df['swing_high'].iloc[i]):
                last_swing_high = df['swing_high'].iloc[i]
            if not pd.isna(df['swing_low'].iloc[i]):
                last_swing_low = df['swing_low'].iloc[i]
            
            # Detect bullish BOS: close above last swing high
            if last_swing_high and df['close'].iloc[i] > last_swing_high:
                df.loc[df.index[i], 'bullish_bos'] = 1
                last_swing_high = None  # Reset after BOS
            
            # Detect bearish BOS: close below last swing low
            if last_swing_low and df['close'].iloc[i] < last_swing_low:
                df.loc[df.index[i], 'bearish_bos'] = 1
                last_swing_low = None  # Reset after BOS
        
        return df
    
    def detect_mss(self, df: pd.DataFrame) -> pd.DataFrame:
        """Detect Market Structure Shift (MSS) - change from higher highs to lower highs or vice versa"""
        df = df.copy()
        df = self.detect_swing_points(df)
        
        df['bullish_mss'] = 0
        df['bearish_mss'] = 0
        
        swing_highs = df[df['swing_high'].notna()]['swing_high'].values
        swing_lows = df[df['swing_low'].notna()]['swing_low'].values
        
        # Detect bullish MSS: higher low after a series of lower lows
        if len(swing_lows) >= 3:
            for i in range(2, len(swing_lows)):
                if (swing_lows[i-2] > swing_lows[i-1] and  # Lower low
                    swing_lows[i] > swing_lows[i-1]):      # Higher low
                    # Find the index of this swing low in the original dataframe
                    idx = df[df['swing_low'] == swing_lows[i]].index[0]
                    df.loc[idx, 'bullish_mss'] = 1
        
        # Detect bearish MSS: lower high after a series of higher highs
        if len(swing_highs) >= 3:
            for i in range(2, len(swing_highs)):
                if (swing_highs[i-2] < swing_highs[i-1] and  # Higher high
                    swing_highs[i] < swing_highs[i-1]):      # Lower high
                    # Find the index of this swing high in the original dataframe
                    idx = df[df['swing_high'] == swing_highs[i]].index[0]
                    df.loc[idx, 'bearish_mss'] = 1
        
        return df
    
    def detect_fvg(self, df: pd.DataFrame) -> List[Dict]:
        """Detect Fair Value Gaps (FVG) - imbalance between candles"""
        fvgs = []
        
        for i in range(2, len(df)):
            # Bullish FVG: gap between candle 1's high and candle 3's low
            c1_high = df['high'].iloc[i-2]
            c2_low = df['low'].iloc[i-1]
            c3_low = df['low'].iloc[i]
            
            if c2_low > c1_high and c3_low > c1_high:
                fvg = {
                    'type': 'bullish',
                    'start_idx': i-1,
                    'end_idx': i,
                    'top': c2_low,
                    'bottom': c1_high,
                    'strength': c2_low - c1_high,
                    'filled': False
                }
                fvgs.append(fvg)
            
            # Bearish FVG: gap between candle 1's low and candle 3's high
            c1_low = df['low'].iloc[i-2]
            c2_high = df['high'].iloc[i-1]
            c3_high = df['high'].iloc[i]
            
            if c2_high < c1_low and c3_high < c1_low:
                fvg = {
                    'type': 'bearish',
                    'start_idx': i-1,
                    'end_idx': i,
                    'top': c1_low,
                    'bottom': c2_high,
                    'strength': c1_low - c2_high,
                    'filled': False
                }
                fvgs.append(fvg)
        
        return fvgs
    
    def detect_displacement(self, df: pd.DataFrame, threshold: float = 0.6) -> pd.DataFrame:
        """Detect displacement candles - strong moves with large body relative to wick"""
        df = df.copy()
        
        # Calculate body and wick sizes
        df['body'] = abs(df['close'] - df['open'])
        df['wick'] = df['high'] - df['low']
        
        # Detect displacement: body/wick ratio > threshold
        df['displacement'] = np.where(
            df['wick'] > 0,
            df['body'] / df['wick'] > threshold,
            False
        )
        
        # Add direction
        df['displacement_direction'] = np.where(
            df['displacement'],
            np.where(df['close'] > df['open'], 'bullish', 'bearish'),
            'none'
        )
        
        return df
    
    def detect_liquidity_sweep(self, df: pd.DataFrame, threshold: float = 0.001) -> pd.DataFrame:
        """Detect liquidity sweeps - wicks that extend beyond recent highs/lows"""
        df = df.copy()
        df = self.detect_swing_points(df)
        
        df['bullish_sweep'] = 0
        df['bearish_sweep'] = 0
        
        for i in range(self.lookback, len(df)):
            # Look for bullish sweeps: low extends below recent swing low
            recent_lows = df['swing_low'].iloc[i-self.lookback:i].dropna()
            if len(recent_lows) > 0:
                min_low = recent_lows.min()
                if df['low'].iloc[i] < min_low - threshold:
                    df.loc[df.index[i], 'bullish_sweep'] = 1
            
            # Look for bearish sweeps: high extends above recent swing high
            recent_highs = df['swing_high'].iloc[i-self.lookback:i].dropna()
            if len(recent_highs) > 0:
                max_high = recent_highs.max()
                if df['high'].iloc[i] > max_high + threshold:
                    df.loc[df.index[i], 'bearish_sweep'] = 1
        
        return df
    
    def analyze_structure(self, df: pd.DataFrame) -> pd.DataFrame:
        """Complete structure analysis combining all methods"""
        df = df.copy()
        
        # Apply all analysis methods
        df = self.detect_bos(df)
        df = self.detect_mss(df)
        df = self.detect_displacement(df)
        df = self.detect_liquidity_sweep(df)
        
        return df
    
    def check_fvg_touch(self, current_price: float, fvg: Dict) -> bool:
        """Check if current price is touching a FVG zone"""
        if fvg['type'] == 'bullish':
            return fvg['bottom'] <= current_price <= fvg['top']
        else:  # bearish
            return fvg['bottom'] <= current_price <= fvg['top']
    
    def get_htf_bias(self, df: pd.DataFrame) -> str:
        """Determine higher timeframe bias based on recent structure"""
        df_analyzed = self.analyze_structure(df)
        
        # Look at last 20 candles for bias
        recent_df = df_analyzed.tail(20)
        
        bullish_bos_count = recent_df['bullish_bos'].sum()
        bearish_bos_count = recent_df['bearish_bos'].sum()
        
        if bullish_bos_count > bearish_bos_count:
            return 'bullish'
        elif bearish_bos_count > bullish_bos_count:
            return 'bearish'
        else:
            return 'neutral' 