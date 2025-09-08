import pandas as pd
from typing import Dict, List, Optional
from trading_logic.structure_analysis import StructureAnalyzer
import logging
from config import *
from datetime import datetime, timedelta
from helpers.telegram_setup import send_telegram_message
import math


class FVGStrategy:
    def __init__(self):
        self.analyzer = StructureAnalyzer()
        self.active_fvgs = []
        self.active_setups = []
        self.last_analysis_time = None
        self.current_position = None
        self.fvg_count = 0
        self.bullish_fvg_touch = 0
        self.bearish_fvg_touch = 0

        self.max_fvg_to_indicator_dist = 20
        self.max_entry_indicator_dist = 20

        self.fvg_lookback = self.max_fvg_to_indicator_dist + self.max_entry_indicator_dist
        self.existing_fvg_idxs = []

        # Setup logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)


    def _enforce_minimum_stop_distance(self, entry_price: float, stop_loss: float, direction: str) -> float:
        """
        Enforce minimum stop distance as a percentage of the coin's value
        Returns the adjusted stop loss that maintains minimum distance
        """
        min_distance = entry_price * MIN_STOP_DISTANCE_COIN
        
        if direction == 'long':
            # For long positions, stop loss should be below entry price
            max_stop_loss = entry_price - min_distance
            return max(stop_loss, max_stop_loss)
        else:  # short
            # For short positions, stop loss should be above entry price
            min_stop_loss = entry_price + min_distance
            return min(stop_loss, min_stop_loss)


    def update_active_setups(self, df):
        # Create a new list without expired setups to avoid modifying list while iterating
        current_time = df['T'].iloc[-1]
        self.active_setups = [
            setup for setup in self.active_setups 
            if current_time - setup['pitch_time'] <= timedelta(minutes=self.max_entry_indicator_dist)
        ]


    def update_fvgs(self, df: pd.DataFrame) -> List[Dict]:
        """Update and maintain active FVGs"""

        current_idx = df.index[-1]
        recent_fvgs = self.analyzer.detect_fvg(
            df.tail(self.fvg_lookback + 1))  # plus 1 to show candle before first possible fvg
        new_fvgs = [fvg for fvg in recent_fvgs if not fvg['start_idx'] in self.existing_fvg_idxs]
        self.fvg_count += len(new_fvgs)
        self.existing_fvg_idxs += [fvg['start_idx'] for fvg in new_fvgs]
        current_low = df['low'].iloc[-1]
        current_high = df['high'].iloc[-1]

        # Filter out old FVGs and mark filled ones
        active_fvgs = []
        for fvg in self.active_fvgs + new_fvgs:
            if not fvg['filled']:
                if fvg['type'] == 'bullish':
                    # FVG is filled if price goes below the bottom
                    if current_low < fvg['bottom']:
                        fvg['filled'] = True
                else:  # bearish
                    # FVG is filled if price goes above the top
                    if current_high > fvg['top']:
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

        # Count bullish vs bearish structure
        bullish_bos_count = htf_analyzed['bullish_bos'].sum()
        bearish_bos_count = htf_analyzed['bearish_bos'].sum()
        bullish_mss_count = htf_analyzed['bullish_mss'].sum()
        bearish_mss_count = htf_analyzed['bearish_mss'].sum()

        # Weighted trend strength: BOS gets 2x weight
        bullish_strength = 2 * bullish_bos_count + bullish_mss_count
        bearish_strength = 2 * bearish_bos_count + bearish_mss_count

        # Determine trend direction
        if bullish_strength > bearish_strength:
            trend = 'uptrend'
            strength = bullish_strength
            confidence = bullish_strength / (bullish_strength + bearish_strength)
        elif bearish_strength > bullish_strength:
            trend = 'downtrend'
            strength = bearish_strength
            confidence = bearish_strength / (bullish_strength + bearish_strength)
        else:  ### REMOVE NEUTRAL
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


    def check_entry_conditions(self, df: pd.DataFrame, htf_df: pd.DataFrame) -> Optional[Dict]:
        """Check if entry conditions are met for the trend continuation strategy"""
        if len(df) < 20:
            return None

        # Step 1: Identify larger trend
        larger_trend = self.identify_larger_trend(htf_df)

        # Make trend confidence requirement stricter
        if larger_trend['confidence'] < 0.55:
            return None

        # Step 3: Look for reversal of the pullback
        current_price = df['close'].iloc[-1]
        active_fvgs = self.update_fvgs(df)

        if larger_trend['trend'] == 'uptrend':
            self._add_bullish_setups(df, active_fvgs, current_price, larger_trend)

        elif larger_trend['trend'] == 'downtrend':
            self._add_bearish_setups(df, active_fvgs, current_price, larger_trend)

        return None

    def _add_bullish_setups(self, df, active_fvgs, current_price, larger_trend):
        bullish_fvgs = [fvg for fvg in active_fvgs if fvg['type'] == 'bullish']

        df_analyzed = self.analyzer.analyze_structure(df)
        present_bullish_movement = df_analyzed["bullish_bos"].iloc[-1] or df_analyzed["bullish_mss"].iloc[-1]
        if present_bullish_movement:
            for fvg in bullish_fvgs:
                recent_df = df_analyzed.tail(df.index[-1] - fvg['start_idx'])
                has_bearish_reversal = (
                        recent_df["bearish_bos"].sum() > 0 or
                        recent_df["bearish_mss"].sum() > 0
                )

                if current_price > fvg["top"] and not has_bearish_reversal:
                    # Check if setup already exists for this FVG
                    fvg_already_has_setup = any(
                        setup['fvg']['start_idx'] == fvg['start_idx'] and setup['direction'] == 'long'
                        for setup in self.active_setups
                    )
                    
                    if not fvg_already_has_setup:
                        stop_loss = fvg['bottom'] - STOP_LOSS_BUFFER

                        # Find nearest swing low for structure-based stop
                        nearest_swing_low = self._find_nearest_swing_low(df_analyzed, current_price)
                        if nearest_swing_low:
                            swing_stop = nearest_swing_low - STOP_LOSS_BUFFER
                            # Use the LOWER of the two stops (FVG-based or structure-based)
                            stop_loss = min(stop_loss, swing_stop)

                        # Enforce minimum stop distance
                        stop_loss = self._enforce_minimum_stop_distance(current_price, stop_loss, 'long')

                        self.active_setups.append({
                            'direction': 'long',
                            'stop_loss': stop_loss,
                            'fvg': fvg,
                            'indicator': df.index[-1],
                            'indicator_type': 'bos' if df_analyzed["bullish_bos"].iloc[-1] else 'mss',
                            'pitch_time': df['T'].iloc[-1],
                            'larger_trend': larger_trend['trend'],
                            'trend_confidence': larger_trend['confidence'],
                        })


    def _add_bearish_setups(self, df, active_fvgs, current_price, larger_trend):
        bearish_fvgs = [fvg for fvg in active_fvgs if fvg['type'] == 'bearish']
        df_analyzed = self.analyzer.analyze_structure(df)
        present_bearish_movement = df_analyzed["bearish_bos"].iloc[-1] or df_analyzed["bearish_mss"].iloc[-1]

        if present_bearish_movement:
            for fvg in bearish_fvgs:
                recent_df = df_analyzed.tail(df.index[-1] - fvg['start_idx'])
                has_bullish_reversal = (
                        recent_df["bullish_bos"].sum() > 0 or
                        recent_df["bullish_mss"].sum() > 0
                )

                if current_price < fvg["bottom"] and not has_bullish_reversal:
                    # Check if setup already exists for this FVG
                    fvg_already_has_setup = any(
                        setup['fvg']['start_idx'] == fvg['start_idx'] and setup['direction'] == 'short'
                        for setup in self.active_setups
                    )
                    
                    if not fvg_already_has_setup:
                        stop_loss = fvg['top'] + STOP_LOSS_BUFFER

                        # Find nearest swing high for structure-based stop
                        nearest_swing_high = self._find_nearest_swing_high(df_analyzed, current_price)
                        if nearest_swing_high:
                            swing_stop = nearest_swing_high + STOP_LOSS_BUFFER
                            # Use the HIGHER of the two stops (FVG-based or structure-based)
                            stop_loss = max(stop_loss, swing_stop)

                        # Enforce minimum stop distance
                        stop_loss = self._enforce_minimum_stop_distance(current_price, stop_loss, 'short')

                        self.active_setups.append({
                            'direction': 'short',
                            'stop_loss': stop_loss,
                            'fvg': fvg,
                            'indicator': df.index[-1],
                            'indicator_type': 'bos' if df_analyzed["bearish_bos"].iloc[-1] else 'mss',
                            'pitch_time': df['T'].iloc[-1],
                            'larger_trend': larger_trend['trend'],
                            'trend_confidence': larger_trend['confidence'],
                        })


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


    def update_trailing_stop(self, df: pd.DataFrame, position: Dict, telegram=False) -> bool:
        """Update stop only on new swing structure, but only after R:R >= 1:1."""
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

        # Only update trailing stop if R:R >= 1.0 (start trailing at 1:1 RR)
        if current_profit < current_risk:
            # Debug: Show when trailing is not yet enabled
            if 'trailing_enabled' not in position:
                rr_ratio = current_profit / current_risk if current_risk > 0 else 0
                self.logger.debug(
                    f"⏳ Trailing not yet enabled - R:R = {rr_ratio:.2f} (need >= 1.0). Profit: ${current_profit:.4f}, Risk: ${current_risk:.4f}")
            return False  # Keep static stop until 1:1 RR is reached

        # Store original static stop if not already stored
        if 'original_stop_loss' not in position:
            position['original_stop_loss'] = position['stop_loss']
            position['trailing_enabled'] = True
            rr_ratio = current_profit / current_risk if current_risk > 0 else 0
            print(f"🎯 TRAILING STOP ENABLED! R:R = {rr_ratio:.2f} >= 1.0. Profit: ${current_profit:.4f}, Risk: ${current_risk:.4f}")

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
                # Tighter stop distance - closer to swing low for faster trailing
                new_stop = best_swing_low - STOP_LOSS_BUFFER
                # Only move stop up (more favorable) - NEVER move down for longs
                if new_stop > position['stop_loss']:
                    # Check if price has stayed above the swing low for required number of candles (2 before + 2 after)
                    confirmation_candles = 2  # 2 candles before + 2 candles after
                    swing_low_idx = swing_lows.index[-1]

                    # Get 2 candles before the swing low
                    candles_before_swing = df_analyzed.loc[:swing_low_idx].tail(
                        confirmation_candles + 1)  # +1 to include swing candle
                    # Get 2 candles after the swing low
                    candles_after_swing = df_analyzed.loc[swing_low_idx:].tail(
                        confirmation_candles + 1)  # +1 to include swing candle

                    # Check if we have enough candles on both sides
                    if len(candles_before_swing) >= confirmation_candles and len(
                            candles_after_swing) >= confirmation_candles:
                        # Check if 2 candles before swing low were above it
                        before_low_crossed = False
                        for _, candle in candles_before_swing.head(confirmation_candles).iterrows():
                            if candle['low'] <= best_swing_low:
                                before_low_crossed = True
                                break

                        # Check if 2 candles after swing low stayed above it
                        after_low_crossed = False
                        for _, candle in candles_after_swing.tail(confirmation_candles).iterrows():
                            if candle['low'] <= best_swing_low:
                                after_low_crossed = True
                                break

                        # Only update if both sides confirm (2 before + 2 after)
                        if not before_low_crossed and not after_low_crossed:
                            # Enforce minimum stop distance before updating
                            new_stop = self._enforce_minimum_stop_distance(position['entry_price'], new_stop, 'long')
                            
                            if position['stop_loss'] != new_stop:
                                old_stop = position['stop_loss']
                                position['stop_loss'] = new_stop
                                position['last_stop_update_idx'] = swing_lows.index[-1]
                                updated = True
                                print(f"📈 TRAILING STOP TRIGGERED! ${old_stop:.4f} → ${new_stop:.4f} (swing low: ${best_swing_low:.4f}, confirmed after {confirmation_candles} candles)")

                                if telegram:
                                    telegram_message = "===== TRAILING STOP UPDATED =====\n"
                                    telegram_message += f"Direction: Long\n"
                                    telegram_message += f"Stop loss: ${position['original_stop_loss']:.4f} → ${position['stop_loss']:.4f} (swing low: ${best_swing_low:.4f}, confirmed after {confirmation_candles} candles)\n"
                                    telegram_message += f"Unrealized P&L: ${(current_price - position['entry_price']):.4f} ({(current_price - position['entry_price']) / position['entry_price'] * 100:.2f}%)\n"

                                    send_telegram_message(telegram_message)

                        # Debug prints only when candles_after_swing exists
                        print("Swing lows:", swing_lows)
                        print("Best swing low:", best_swing_low)
                        print("New stop:", new_stop)
                        print("Lows of confirmation candles:", candles_after_swing['low'].tolist())

        else:  # short
            # Find all swing highs since last stop update
            recent_swings = df_analyzed.tail(50)
            swing_highs = recent_swings[recent_swings['swing_high'].notna()]
            if last_stop_update_idx is not None:
                swing_highs = swing_highs[swing_highs.index > last_stop_update_idx]
            if not swing_highs.empty:
                # Find the LOWEST swing high (most favorable for shorts)
                best_swing_high = swing_highs['swing_high'].min()
                # Tighter stop distance - closer to swing high for faster trailing
                new_stop = best_swing_high + STOP_LOSS_BUFFER
                # Only move stop down (more favorable) - NEVER move up for shorts
                if new_stop < position['stop_loss']:
                    # Check if price has stayed below the swing high for required number of candles (2 before + 2 after)
                    confirmation_candles = 2  # 2 candles before + 2 candles after
                    swing_high_idx = swing_highs.index[-1]

                    # Get 2 candles before the swing high
                    candles_before_swing = df_analyzed.loc[:swing_high_idx].tail(
                        confirmation_candles + 1)  # +1 to include swing candle
                    # Get 2 candles after the swing high
                    candles_after_swing = df_analyzed.loc[swing_high_idx:].tail(
                        confirmation_candles + 1)  # +1 to include swing candle

                    # Check if we have enough candles on both sides
                    if len(candles_before_swing) >= confirmation_candles and len(
                            candles_after_swing) >= confirmation_candles:
                        # Check if 2 candles before swing high were below it
                        before_high_crossed = False
                        for _, candle in candles_before_swing.head(confirmation_candles).iterrows():
                            if candle['high'] >= best_swing_high:
                                before_high_crossed = True
                                break

                        # Check if 2 candles after swing high stayed below it
                        after_high_crossed = False
                        for _, candle in candles_after_swing.tail(confirmation_candles).iterrows():
                            if candle['high'] >= best_swing_high:
                                after_high_crossed = True
                                break

                        # Only update if both sides confirm (2 before + 2 after)
                        if not before_high_crossed and not after_high_crossed:
                            # Enforce minimum stop distance before updating
                            new_stop = self._enforce_minimum_stop_distance(position['entry_price'], new_stop, 'short')
                            
                            if position['stop_loss'] != new_stop:
                                position['stop_loss'] = new_stop
                                position['last_stop_update_idx'] = swing_highs.index[-1]
                                updated = True
                                old_stop = position['stop_loss']
                                print(f"📉 TRAILING STOP TRIGGERED! ${old_stop:.4f} → ${new_stop:.4f} (swing high: ${best_swing_high:.4f}, confirmed after {confirmation_candles} candles)")

                                if telegram:
                                    telegram_message = "===== TRAILING STOP UPDATED =====\n"
                                    telegram_message += f"Direction: Short\n"
                                    telegram_message += f"Stop loss: ${position['original_stop_loss']:.4f} → ${position['stop_loss']:.4f} (swing high: ${best_swing_high:.4f}, confirmed after {confirmation_candles} candles)\n"
                                    telegram_message += f"Unrealized P&L: ${(current_price - position['entry_price']):.4f} ({(current_price - position['entry_price']) / position['entry_price'] * 100:.2f}%)\n"

                                    send_telegram_message(telegram_message)

                        # Debug prints only when candles_after_swing exists
                        print("Swing highs:", swing_highs)
                        print("Best swing high:", best_swing_high)
                        print("New stop:", new_stop)
                        print("Highs of confirmation candles:", candles_after_swing['high'].tolist())

        print("Current stop:", position['stop_loss'])

        return updated