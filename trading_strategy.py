import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from structure_analysis import StructureAnalyzer
import logging

class FVGStrategy:
    def __init__(self, config: Dict):
        self.config = config
        self.analyzer = StructureAnalyzer(lookback=config.get('BOS_LOOKBACK', 10))
        self.active_fvgs = []
        self.last_analysis_time = None
        self.current_position = None
        
        # Setup logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
        
    def update_fvgs(self, df: pd.DataFrame) -> List[Dict]:
        """Update and maintain active FVGs"""
        new_fvgs = self.analyzer.detect_fvg(df)
        current_price = df['close'].iloc[-1]
        
        # Filter out old FVGs and mark filled ones
        active_fvgs = []
        for fvg in self.active_fvgs + new_fvgs:
            # Check if FVG is still valid (not too old)
            if fvg['end_idx'] >= len(df) - 50:  # Keep FVGs from last 50 candles
                # Check if FVG has been filled
                if not fvg['filled']:
                    if fvg['type'] == 'bullish':
                        # FVG is filled if price goes below the bottom
                        if current_price < fvg['bottom']:
                            fvg['filled'] = True
                    else:  # bearish
                        # FVG is filled if price goes above the top
                        if current_price > fvg['top']:
                            fvg['filled'] = True
                
                if not fvg['filled']:
                    active_fvgs.append(fvg)
        
        self.active_fvgs = active_fvgs
        return active_fvgs
    
    def identify_larger_trend(self, htf_df: pd.DataFrame) -> Dict:
        """Identify the larger trend direction and strength"""
        if len(htf_df) < 20:
            return {'trend': 'neutral', 'strength': 0, 'confidence': 0}
        
        htf_analyzed = self.analyzer.analyze_structure(htf_df)
        
        # Look at recent structure (last 20-30 candles)
        recent_df = htf_analyzed.tail(30)
        
        # Count bullish vs bearish structure
        bullish_bos_count = recent_df['bullish_bos'].sum()
        bearish_bos_count = recent_df['bearish_bos'].sum()
        bullish_mss_count = recent_df['bullish_mss'].sum()
        bearish_mss_count = recent_df['bearish_mss'].sum()
        
        # Calculate trend strength
        bullish_strength = bullish_bos_count + bullish_mss_count
        bearish_strength = bearish_bos_count + bearish_mss_count
        
        # Determine trend direction
        if bullish_strength > bearish_strength + 1:  # Reduced from +2 - more lenient
            trend = 'uptrend'
            strength = bullish_strength
            confidence = min(bullish_strength / (bullish_strength + bearish_strength), 1.0)
        elif bearish_strength > bullish_strength + 1:  # Reduced from +2 - more lenient
            trend = 'downtrend'
            strength = bearish_strength
            confidence = min(bearish_strength / (bullish_strength + bearish_strength), 1.0)
        else:
            trend = 'neutral'
            strength = max(bullish_strength, bearish_strength)
            confidence = 0.5
        
        return {
            'trend': trend,
            'strength': strength,
            'confidence': confidence,
            'bullish_strength': bullish_strength,
            'bearish_strength': bearish_strength
        }
    
    def detect_pullback(self, ltf_df: pd.DataFrame, larger_trend: str) -> Optional[Dict]:
        """Detect pullbacks/retracements against the larger trend"""
        if len(ltf_df) < 15:
            return None
        
        ltf_analyzed = self.analyzer.analyze_structure(ltf_df)
        recent_df = ltf_analyzed.tail(15)
        
        if larger_trend == 'uptrend':
            # Look for bearish pullback in uptrend
            bearish_bos_count = recent_df['bearish_bos'].sum()
            bearish_mss_count = recent_df['bearish_mss'].sum()
            
            if bearish_bos_count > 0 or bearish_mss_count > 0:
                # Check if we have a recent bearish structure
                return {
                    'type': 'bearish_pullback',
                    'strength': bearish_bos_count + bearish_mss_count,
                    'last_bearish_candle': recent_df[recent_df['bearish_bos'] == 1].index[-1] if bearish_bos_count > 0 else None
                }
        
        elif larger_trend == 'downtrend':
            # Look for bullish pullback in downtrend
            bullish_bos_count = recent_df['bullish_bos'].sum()
            bullish_mss_count = recent_df['bullish_mss'].sum()
            
            if bullish_bos_count > 0 or bullish_mss_count > 0:
                # Check if we have a recent bullish structure
                return {
                    'type': 'bullish_pullback',
                    'strength': bullish_bos_count + bullish_mss_count,
                    'last_bullish_candle': recent_df[recent_df['bullish_bos'] == 1].index[-1] if bullish_bos_count > 0 else None
                }
        
        return None
    
    def check_entry_conditions(self, df: pd.DataFrame, htf_df: pd.DataFrame) -> Optional[Dict]:
        """Check if entry conditions are met for the trend continuation strategy"""
        if len(df) < 20:
            return None
        
        # Step 1: Identify larger trend
        larger_trend = self.identify_larger_trend(htf_df)
        
        # Only trade if we have a clear trend with good confidence
        if larger_trend['confidence'] < 0.4:  # Reduced from 0.6 - more lenient
            return None
        
        # Step 2: Detect pullback against the larger trend
        pullback = self.detect_pullback(df, larger_trend['trend'])
        
        if not pullback:
            return None
        
        # Step 3: Look for reversal of the pullback
        current_price = df['close'].iloc[-1]
        active_fvgs = self.update_fvgs(df)
        
        if larger_trend['trend'] == 'uptrend' and pullback['type'] == 'bearish_pullback':
            # Look for bullish reversal to continue uptrend
            setup = self._check_bullish_reversal(df, active_fvgs, current_price, larger_trend, pullback)
            if setup:
                return setup
        
        elif larger_trend['trend'] == 'downtrend' and pullback['type'] == 'bullish_pullback':
            # Look for bearish reversal to continue downtrend
            setup = self._check_bearish_reversal(df, active_fvgs, current_price, larger_trend, pullback)
            if setup:
                return setup
        
        return None
    
    def _check_bullish_reversal(self, df: pd.DataFrame, fvgs: List[Dict], current_price: float, 
                               larger_trend: Dict, pullback: Dict) -> Optional[Dict]:
        """Check for bullish reversal to continue uptrend"""
        # Look for bullish FVGs that price is touching
        bullish_fvgs = [fvg for fvg in fvgs if fvg['type'] == 'bullish']
        
        for fvg in bullish_fvgs:
            if self.analyzer.check_fvg_touch(current_price, fvg):
                # Analyze the DataFrame to get structure columns
                df_analyzed = self.analyzer.analyze_structure(df)
                recent_df = df_analyzed.tail(8)  # Look at very recent candles
                
                # Look for bullish BOS or MSS (reversal signal)
                has_bullish_reversal = (
                    recent_df['bullish_bos'].sum() > 0 or 
                    recent_df['bullish_mss'].sum() > 0
                )
                
                # Look for displacement (strong reversal move)
                has_displacement = recent_df['displacement'].sum() > 0
                
                # Check for liquidity sweep (optional but preferred)
                has_sweep = recent_df['bullish_sweep'].sum() > 0
                
                # Entry conditions for trend continuation - more lenient
                if has_bullish_reversal:  # Removed has_displacement requirement
                    # Calculate entry levels
                    entry_price = current_price
                    stop_loss = fvg['bottom'] - self.config.get('STOP_LOSS_BUFFER', 0.005)
                    
                    # Find nearest swing low for structure-based stop
                    nearest_swing_low = self._find_nearest_swing_low(df_analyzed, current_price)
                    if nearest_swing_low:
                        swing_stop = nearest_swing_low - self.config.get('STOP_LOSS_BUFFER', 0.005)
                        # Use the LOWER of the two stops (FVG-based or structure-based)
                        stop_loss = min(stop_loss, swing_stop)
                    
                    return {
                        'direction': 'long',
                        'entry_price': entry_price,
                        'stop_loss': stop_loss,
                        'take_profit': None,  # No fixed TP, use trailing stop
                        'fvg': fvg,
                        'larger_trend': larger_trend['trend'],
                        'trend_confidence': larger_trend['confidence'],
                        'pullback_type': pullback['type'],
                        'reason': f'Uptrend continuation: Bearish pullback + Bullish reversal + FVG. Trend confidence: {larger_trend["confidence"]:.2f}'
                    }
        
        return None
    
    def _check_bearish_reversal(self, df: pd.DataFrame, fvgs: List[Dict], current_price: float, 
                               larger_trend: Dict, pullback: Dict) -> Optional[Dict]:
        """Check for bearish reversal to continue downtrend"""
        # Look for bearish FVGs that price is touching
        bearish_fvgs = [fvg for fvg in fvgs if fvg['type'] == 'bearish']
        
        for fvg in bearish_fvgs:
            if self.analyzer.check_fvg_touch(current_price, fvg):
                # Analyze the DataFrame to get structure columns
                df_analyzed = self.analyzer.analyze_structure(df)
                recent_df = df_analyzed.tail(8)  # Look at very recent candles
                
                # Look for bearish BOS or MSS (reversal signal)
                has_bearish_reversal = (
                    recent_df['bearish_bos'].sum() > 0 or 
                    recent_df['bearish_mss'].sum() > 0
                )
                
                # Look for displacement (strong reversal move)
                has_displacement = recent_df['displacement'].sum() > 0
                
                # Check for liquidity sweep (optional but preferred)
                has_sweep = recent_df['bearish_sweep'].sum() > 0
                
                # Entry conditions for trend continuation - more lenient
                if has_bearish_reversal:  # Removed has_displacement requirement
                    # Calculate entry levels
                    entry_price = current_price
                    stop_loss = fvg['top'] + self.config.get('STOP_LOSS_BUFFER', 0.005)
                    
                    # Find nearest swing high for structure-based stop
                    nearest_swing_high = self._find_nearest_swing_high(df_analyzed, current_price)
                    if nearest_swing_high:
                        swing_stop = nearest_swing_high + self.config.get('STOP_LOSS_BUFFER', 0.005)
                        # Use the HIGHER of the two stops (FVG-based or structure-based)
                        stop_loss = max(stop_loss, swing_stop)
                    
                    return {
                        'direction': 'short',
                        'entry_price': entry_price,
                        'stop_loss': stop_loss,
                        'take_profit': None,  # No fixed TP, use trailing stop
                        'fvg': fvg,
                        'larger_trend': larger_trend['trend'],
                        'trend_confidence': larger_trend['confidence'],
                        'pullback_type': pullback['type'],
                        'reason': f'Downtrend continuation: Bullish pullback + Bearish reversal + FVG. Trend confidence: {larger_trend["confidence"]:.2f}'
                    }
        
        return None
    
    def should_exit_position(self, df: pd.DataFrame, position: Dict) -> bool:
        """Check if current position should be exited"""
        if not position:
            return False
        
        current_price = df['close'].iloc[-1]
        
        # Check stop loss FIRST (before updating trailing stop)
        if position['direction'] == 'long':
            if current_price <= position['stop_loss']:
                return True
        else:  # short
            if current_price >= position['stop_loss']:
                return True
        
        # Only update trailing stop if we haven't been stopped out
        self.update_trailing_stop(df, position)
        
        # Check for trend reversal (exit if larger trend changes)
        if 'larger_trend' in position:
            htf_df = df  # In real implementation, this would be HTF data
            current_trend = self.identify_larger_trend(htf_df)
            
            # Exit if trend changes significantly
            if (position['larger_trend'] == 'uptrend' and current_trend['trend'] == 'downtrend') or \
               (position['larger_trend'] == 'downtrend' and current_trend['trend'] == 'uptrend'):
                return True
        
        # Check for opposing structure (optional - trailing stop)
        df_analyzed = self.analyzer.analyze_structure(df)
        recent_df = df_analyzed.tail(5)
        if position['direction'] == 'long':
            if recent_df['bearish_bos'].sum() > 0 or recent_df['bearish_mss'].sum() > 0:
                return True
        else:  # short
            if recent_df['bullish_bos'].sum() > 0 or recent_df['bullish_mss'].sum() > 0:
                return True
        
        return False
    
    def update_position(self, position: Dict, current_price: float):
        """Update position with current market data"""
        if not position:
            return
        
        # Update unrealized P&L
        if position['direction'] == 'long':
            position['unrealized_pnl'] = (current_price - position['entry_price']) / position['entry_price']
        else:  # short
            position['unrealized_pnl'] = (position['entry_price'] - current_price) / position['entry_price']
        
        # Update position age
        position['age'] = position.get('age', 0) + 1
    
    def log_setup(self, setup: Dict):
        """Log the trading setup for analysis"""
        self.logger.info(f"Trend continuation setup detected: {setup['direction']} at {setup['entry_price']}")
        self.logger.info(f"Larger trend: {setup['larger_trend']} (confidence: {setup['trend_confidence']:.2f})")
        self.logger.info(f"Pullback type: {setup['pullback_type']}")
        self.logger.info(f"Stop: {setup['stop_loss']}, Target: {setup['take_profit']}")
        self.logger.info(f"Reason: {setup['reason']}")
        self.logger.info(f"FVG: {setup['fvg']['type']} from {setup['fvg']['bottom']} to {setup['fvg']['top']}")
    
    def get_strategy_stats(self) -> Dict:
        """Get current strategy statistics"""
        return {
            'active_fvgs': len(self.active_fvgs),
            'current_position': self.current_position,
            'last_analysis_time': self.last_analysis_time
        }
    
    def _find_nearest_swing_low(self, df_analyzed: pd.DataFrame, current_price: float) -> Optional[float]:
        """Find the nearest swing low below current price for trailing stop"""
        # Get recent swing lows (last 50 candles)
        recent_df = df_analyzed.tail(50)
        swing_lows = recent_df[recent_df['swing_low'].notna()]['swing_low']
        
        if len(swing_lows) == 0:
            return None
        
        # Find swing lows below current price
        valid_lows = swing_lows[swing_lows < current_price]
        
        if len(valid_lows) == 0:
            return None
        
        # Return the highest swing low below current price (closest to price)
        return valid_lows.max()
    
    def _find_nearest_swing_high(self, df_analyzed: pd.DataFrame, current_price: float) -> Optional[float]:
        """Find the nearest swing high above current price for trailing stop"""
        # Get recent swing highs (last 50 candles)
        recent_df = df_analyzed.tail(50)
        swing_highs = recent_df[recent_df['swing_high'].notna()]['swing_high']
        
        if len(swing_highs) == 0:
            return None
        
        # Find swing highs above current price
        valid_highs = swing_highs[swing_highs > current_price]
        
        if len(valid_highs) == 0:
            return None
        
        # Return the lowest swing high above current price (closest to price)
        return valid_highs.min()
    
    def update_trailing_stop(self, df: pd.DataFrame, position: Dict) -> bool:
        """Update stop only on new swing structure, but only after R:R > 1:1."""
        if not position:
            return False
        
        current_price = df['close'].iloc[-1]
        
        # Calculate current risk:reward ratio
        if position['direction'] == 'long':
            current_profit = current_price - position['entry_price']
            current_risk = position['entry_price'] - position['stop_loss']
        else:  # short
            current_profit = position['entry_price'] - current_price
            current_risk = position['stop_loss'] - position['entry_price']
        
        # Only update trailing stop if R:R > 1:1 (profit > risk)
        if current_profit <= current_risk:
            return False  # Keep static stop until profitable
        
        # Store original static stop if not already stored
        if 'original_stop_loss' not in position:
            position['original_stop_loss'] = position['stop_loss']
            position['trailing_enabled'] = True
            self.logger.info(f"🎯 Trailing stop enabled - R:R now > 1:1. Profit: ${current_profit:.2f}, Risk: ${current_risk:.2f}")
        
        # Now update trailing stop based on structure
        df_analyzed = self.analyzer.analyze_structure(df)
        last_stop_update_idx = position.get('last_stop_update_idx', None)
        updated = False
        
        if position['direction'] == 'long':
            # Find all swing lows since last stop update
            recent_swings = df_analyzed.tail(50)
            swing_lows = recent_swings[recent_swings['swing_low'].notna()]
            if last_stop_update_idx is not None:
                swing_lows = swing_lows[swing_lows.index > last_stop_update_idx]
            if not swing_lows.empty:
                # Find the HIGHEST swing low (most favorable for longs)
                best_swing_low = swing_lows['swing_low'].max()
                new_stop = best_swing_low - self.config.get('STOP_LOSS_BUFFER', 0.005)
                
                # Only move stop up (more favorable) - NEVER move down for longs
                if new_stop > position['stop_loss']:
                    # Check if price has stayed above the swing low for required number of candles
                    confirmation_candles = self.config.get('TRAILING_CONFIRMATION_CANDLES', 3)
                    swing_low_idx = swing_lows.index[-1]
                    
                    # Get candles after the swing low
                    candles_after_swing = df_analyzed.loc[swing_low_idx:].tail(confirmation_candles + 1)  # +1 to include swing candle
                    
                    # Check if all candles after swing low have stayed above it
                    if len(candles_after_swing) >= confirmation_candles:
                        low_crossed = False
                        for _, candle in candles_after_swing.tail(confirmation_candles).iterrows():
                            if candle['low'] <= best_swing_low:
                                low_crossed = True
                                break
                        
                        if not low_crossed:
                            position['stop_loss'] = new_stop
                            position['last_stop_update_idx'] = swing_lows.index[-1]
                            updated = True
                            self.logger.info(f"📈 Trailing stop moved up to: ${new_stop:.4f} (swing low: ${best_swing_low:.4f}, confirmed after {confirmation_candles} candles)")
                        else:
                            self.logger.debug(f"📈 Trailing stop not updated - price crossed back below swing low ${best_swing_low:.4f}")
                    else:
                        self.logger.debug(f"📈 Trailing stop not updated - not enough candles after swing low (need {confirmation_candles})")
                else:
                    self.logger.debug(f"📈 Trailing stop not updated - new stop ${new_stop:.4f} <= current stop ${position['stop_loss']:.4f}")
        else:  # short
            # Find all swing highs since last stop update
            recent_swings = df_analyzed.tail(50)
            swing_highs = recent_swings[recent_swings['swing_high'].notna()]
            if last_stop_update_idx is not None:
                swing_highs = swing_highs[swing_highs.index > last_stop_update_idx]
            if not swing_highs.empty:
                # Find the LOWEST swing high (most favorable for shorts)
                best_swing_high = swing_highs['swing_high'].min()
                new_stop = best_swing_high + self.config.get('STOP_LOSS_BUFFER', 0.005)
                
                # Only move stop down (more favorable) - NEVER move up for shorts
                if new_stop < position['stop_loss']:
                    # Check if price has stayed below the swing high for required number of candles
                    confirmation_candles = self.config.get('TRAILING_CONFIRMATION_CANDLES', 3)
                    swing_high_idx = swing_highs.index[-1]
                    
                    # Get candles after the swing high
                    candles_after_swing = df_analyzed.loc[swing_high_idx:].tail(confirmation_candles + 1)  # +1 to include swing candle
                    
                    # Check if all candles after swing high have stayed below it
                    if len(candles_after_swing) >= confirmation_candles:
                        high_crossed = False
                        for _, candle in candles_after_swing.tail(confirmation_candles).iterrows():
                            if candle['high'] >= best_swing_high:
                                high_crossed = True
                                break
                        
                        if not high_crossed:
                            position['stop_loss'] = new_stop
                            position['last_stop_update_idx'] = swing_highs.index[-1]
                            updated = True
                            self.logger.info(f"📉 Trailing stop moved down to: ${new_stop:.4f} (swing high: ${best_swing_high:.4f}, confirmed after {confirmation_candles} candles)")
                        else:
                            self.logger.debug(f"📉 Trailing stop not updated - price crossed back above swing high ${best_swing_high:.4f}")
                    else:
                        self.logger.debug(f"📉 Trailing stop not updated - not enough candles after swing high (need {confirmation_candles})")
                else:
                    self.logger.debug(f"📉 Trailing stop not updated - new stop ${new_stop:.4f} >= current stop ${position['stop_loss']:.4f}")
        
        return updated 