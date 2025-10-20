import pandas as pd
import numpy as np
from typing import Dict

class VolumeAnalyzer:
    def __init__(self, lookback_periods=20):
        self.lookback_periods = lookback_periods
    
    def calculate_volume_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate basic volume indicators for analysis"""
        df = df.copy()
        
        # Ensure volume column exists
        if 'volume' not in df.columns:
            # If no volume data, create dummy volume for compatibility
            df['volume'] = 1.0
        
        # Volume Moving Averages
        df['volume_sma_20'] = df['volume'].rolling(20).mean()
        df['volume_sma_50'] = df['volume'].rolling(50).mean()
        
        # Volume Rate of Change
        df['volume_roc'] = df['volume'].pct_change(periods=5)
        
        # Volume Surge Detection
        df['volume_surge'] = df['volume'] > (df['volume_sma_20'] * 1.2)
        
        return df
    
    def validate_volume_for_fvg(self, df: pd.DataFrame, fvg: Dict, direction: str) -> Dict:
        """Validate FVG with proper volume confirmation logic"""
        fvg_time = fvg['time']
        fvg_matches = df[df['T'] == fvg_time]
        
        if len(fvg_matches) == 0:
            return {'valid': False, 'reason': 'FVG time not found in data'}
        
        # Use iloc to get the position in the DataFrame, not the original index
        fvg_idx = fvg_matches.index[0]
        fvg_position = df.index.get_loc(fvg_idx)
        
        # Safety check for index bounds
        if fvg_position < 2 or fvg_position >= len(df) - 1:
            return {'valid': False, 'reason': 'FVG position out of bounds for analysis'}
        
        # Get the 3-candle sequence that formed the FVG (FVG is at position 1 of 3)
        fvg_formation_start = fvg_position - 1
        fvg_formation_end = fvg_position + 2
        fvg_formation_data = df.iloc[fvg_formation_start:fvg_formation_end]
        
        # Calculate volume metrics for FVG formation
        formation_volume = fvg_formation_data['volume'].sum()
        avg_volume_20 = df.iloc[max(0, fvg_position-20):fvg_position]['volume'].mean()
        volume_ratio = formation_volume / (avg_volume_20 * 3) if avg_volume_20 > 0 else 0
        
        # Volume confirmation: FVG formation should have above-average volume
        from config import VOLUME_SURGE_THRESHOLD
        volume_confirms_formation = volume_ratio > VOLUME_SURGE_THRESHOLD
        
        # Volume trend: Check if volume is increasing during formation
        first_candle_vol = fvg_formation_data['volume'].iloc[0]
        last_candle_vol = fvg_formation_data['volume'].iloc[-1]
        volume_increasing = last_candle_vol > first_candle_vol
        
        # For bullish FVG: volume should be increasing (buying pressure)
        # For bearish FVG: volume should be increasing (selling pressure)
        volume_trend_ok = volume_increasing
        
        # Overall validation: volume must confirm formation AND show proper trend
        valid = volume_confirms_formation and volume_trend_ok
        
        return {
            'valid': valid,
            'volume_ratio': volume_ratio,
            'volume_surge': volume_confirms_formation,
            'volume_trend': volume_trend_ok,
            'formation_volume': formation_volume,
            'avg_volume': avg_volume_20,
            'reason': 'Volume confirms FVG formation' if valid else 'Volume does not confirm FVG formation'
        }
    
    def get_volume_strength_score(self, df: pd.DataFrame, fvg: Dict) -> float:
        """Calculate volume strength score based on FVG formation volume"""
        fvg_time = fvg['time']
        fvg_matches = df[df['T'] == fvg_time]
        
        if len(fvg_matches) == 0:
            return 0.0
        
        # Use iloc to get the position in the DataFrame, not the original index
        fvg_idx = fvg_matches.index[0]
        fvg_position = df.index.get_loc(fvg_idx)
        
        # Safety check for index bounds
        if fvg_position < 2 or fvg_position >= len(df) - 1:
            return 0.0
        
        # Get the 3-candle sequence that formed the FVG
        fvg_formation_start = fvg_position - 1
        fvg_formation_end = fvg_position + 2
        fvg_formation_data = df.iloc[fvg_formation_start:fvg_formation_end]
        
        # Calculate volume strength based on formation
        formation_volume = fvg_formation_data['volume'].sum()
        avg_volume_20 = df.iloc[max(0, fvg_position-20):fvg_position]['volume'].mean()
        volume_ratio = formation_volume / (avg_volume_20 * 3) if avg_volume_20 > 0 else 0
        
        # Volume trend strength (how much volume increased during formation)
        first_candle_vol = fvg_formation_data['volume'].iloc[0]
        last_candle_vol = fvg_formation_data['volume'].iloc[-1]
        volume_trend_strength = (last_candle_vol - first_candle_vol) / first_candle_vol if first_candle_vol > 0 else 0
        
        # Combine volume ratio and trend strength
        # Higher volume ratio + positive trend = higher score
        base_score = min(1.0, volume_ratio / 2)  # Normalize volume ratio
        trend_bonus = max(0, min(0.5, volume_trend_strength))  # Add trend bonus (0-0.5)
        score = min(1.0, base_score + trend_bonus)
        
        return score
