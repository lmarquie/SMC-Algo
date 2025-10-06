import pandas as pd
from typing import Dict, List, Optional
from trading_logic.structure_analysis import StructureAnalyzer
import logging
from config import *
from datetime import datetime, timedelta
from helpers.telegram_setup import send_telegram_message
import math
from config import MIN_FVG_STRENGTH


class FVGStrategy:
    def __init__(self, risk_amount=0):
        self.analyzer = StructureAnalyzer(min_fvg_strength=MIN_FVG_STRENGTH)
        self.active_fvgs = []
        self.active_setups = []
        self.last_analysis_time = None
        self.current_position = None
        self.fvg_count = 0
        self.bullish_fvg_touch = 0
        self.bearish_fvg_touch = 0
        self.previous_fvg_times = []
        self.risk_amount = risk_amount

        self.max_fvg_to_indicator_dist = 20
        self.max_entry_indicator_dist = 20

        self.fvg_lookback = self.max_fvg_to_indicator_dist + self.max_entry_indicator_dist
        self.existing_fvg_times = []


    def update_active_setups(self, df):
        # Create a new list without expired setups to avoid modifying list while iterating
        current_time = df['T'].iloc[-1]
        self.active_setups = [
            setup for setup in self.active_setups 
            if current_time - setup['indicator_time'] <= timedelta(minutes=self.max_entry_indicator_dist)
        ]


    def update_fvgs(self, df: pd.DataFrame) -> List[Dict]:
        """Update and maintain active FVGs"""
        recent_fvgs = self.analyzer.detect_fvg(
            df.tail(self.fvg_lookback + 1))  # plus 1 to show candle before first possible fvg
        new_fvgs = [fvg for fvg in recent_fvgs if not fvg['time'] in self.existing_fvg_times]
        self.fvg_count += len(new_fvgs)
        self.existing_fvg_times += [fvg['time'] for fvg in new_fvgs]
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
        print(" - Checking entry conditions...")
        if len(df) < 20:
            return None

        # Step 1: Identify larger trend
        larger_trend = self.identify_larger_trend(htf_df)

        # Make trend confidence requirement stricter
        if larger_trend['confidence'] < MIN_LARGER_TREND_CONFIDENCE:
            print(f"⚠️ Trend confidence is too low ({larger_trend['confidence'] * 100:.2f}%) - skipping entry.")
            return None

        # Step 3: Look for reversal of the pullback
        last_close = df['close'].iloc[-1]
        last_high = df['high'].iloc[-1]
        last_low = df['low'].iloc[-1]
        active_fvgs = self.update_fvgs(df)

        if larger_trend['trend'] == 'uptrend':
            self._add_bullish_setups(df, active_fvgs=active_fvgs, last_close=last_close, last_low=last_low, larger_trend=larger_trend)

        elif larger_trend['trend'] == 'downtrend':
            self._add_bearish_setups(df, active_fvgs=active_fvgs, last_close=last_close, last_high=last_high, larger_trend=larger_trend)

        return None

    def _add_bullish_setups(self, df, active_fvgs, last_close, last_low, larger_trend):
        bullish_fvgs = [fvg for fvg in active_fvgs if fvg['type'] == 'bullish']

        df_analyzed = self.analyzer.analyze_structure(df)
        present_bullish_movement = df_analyzed["bullish_bos"].iloc[-1] or df_analyzed["bullish_mss"].iloc[-1]

        if present_bullish_movement or not REQUIRE_SETUP_INDICATORS:
            for fvg in bullish_fvgs:
                time_since_fvg = int((df['T'].iloc[-1] - fvg['time']).total_seconds() / 60)

                recent_df = df_analyzed.tail(time_since_fvg)
                has_bearish_reversal = (
                        recent_df["bearish_bos"].sum() > 0 or
                        recent_df["bearish_mss"].sum() > 0
                )

                if last_close > fvg["top"] and not (has_bearish_reversal and REVERSAL_CONSTRAINT_ENABLED):
                    # Check if setup already exists for this FVG
                    fvg_already_has_setup = any(
                        setup['fvg']['time'] == fvg['time'] and setup['direction'] == 'long'
                        for setup in self.active_setups
                    )
                    
                    if not fvg_already_has_setup:
                        stop_loss = fvg['bottom'] - STOP_LOSS_BUFFER

                        # Find nearest swing low for structure-based stop
                        nearest_swing_low = self._find_nearest_swing_low(df_analyzed, last_low)
                        if nearest_swing_low:
                            swing_stop = nearest_swing_low - STOP_LOSS_BUFFER
                            # Use the LOWER of the two stops (FVG-based or structure-based)
                            stop_loss = min(stop_loss, swing_stop)

                        # TEMP SOLUTION
                        entry_price = (fvg['top'] + fvg['bottom']) / 2
                        stop_distance = entry_price * MIN_STOP_DISTANCE_COIN
                        stop_loss = entry_price - stop_distance
                        quantity = self.risk_amount / stop_distance

                        existing_entry_prices = [setup['entry_price'] for setup in self.active_setups]
                        if entry_price in existing_entry_prices:
                            continue

                        self.active_setups.append({
                            'entry_price': entry_price,
                            'quantity': quantity,
                            'direction': 'long',
                            'stop_loss': stop_loss,
                            'fvg': fvg,
                            'indicator_type': 'bos' if df_analyzed["bullish_bos"].iloc[-1] else 'mss',
                            'indicator_time': df['T'].iloc[-1],
                            'larger_trend': larger_trend['trend'],
                            'trend_confidence': larger_trend['confidence'],
                            'oid': None,
                            'filled': False
                        })


    def _add_bearish_setups(self, df, active_fvgs, last_close, last_high, larger_trend):
        bearish_fvgs = [fvg for fvg in active_fvgs if fvg['type'] == 'bearish']
        df_analyzed = self.analyzer.analyze_structure(df)
        present_bearish_movement = df_analyzed["bearish_bos"].iloc[-1] or df_analyzed["bearish_mss"].iloc[-1]

        if present_bearish_movement or not REQUIRE_SETUP_INDICATORS:
            for fvg in bearish_fvgs:
                time_since_fvg = int((df['T'].iloc[-1] - fvg['time']).total_seconds() / 60)

                recent_df = df_analyzed.tail(time_since_fvg)
                has_bullish_reversal = (
                        recent_df["bullish_bos"].sum() > 0 or
                        recent_df["bullish_mss"].sum() > 0
                )

                if last_close < fvg["bottom"] and not (has_bullish_reversal and REVERSAL_CONSTRAINT_ENABLED):
                    # Check if setup already exists for this FVG
                    fvg_already_has_setup = any(
                        setup['fvg']['time'] == fvg['time'] and setup['direction'] == 'short'
                        for setup in self.active_setups
                    )
                    
                    if not fvg_already_has_setup:
                        stop_loss = fvg['top'] + STOP_LOSS_BUFFER

                        # Find nearest swing high for structure-based stop
                        nearest_swing_high = self._find_nearest_swing_high(df_analyzed, last_high)
                        if nearest_swing_high:
                            swing_stop = nearest_swing_high + STOP_LOSS_BUFFER
                            # Use the HIGHER of the two stops (FVG-based or structure-based)
                            stop_loss = max(stop_loss, swing_stop)

                        # TEMP SOLUTION
                        entry_price = (fvg['top'] + fvg['bottom']) / 2
                        stop_distance = entry_price * MIN_STOP_DISTANCE_COIN
                        stop_loss = entry_price + stop_distance
                        quantity = self.risk_amount / stop_distance

                        existing_entry_prices = [setup['entry_price'] for setup in self.active_setups]
                        if entry_price in existing_entry_prices:
                            continue

                        self.active_setups.append({
                            'entry_price': entry_price,
                            'quantity': quantity,
                            'direction': 'short',
                            'stop_loss': stop_loss,
                            'fvg': fvg,
                            'indicator_time': df['T'].iloc[-1],
                            'indicator_type': 'bos' if df_analyzed["bearish_bos"].iloc[-1] else 'mss',
                            'larger_trend': larger_trend['trend'],
                            'trend_confidence': larger_trend['confidence'],
                            'oid': None,
                            'filled': False,
                        })


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


    def update_trailing_stop(self, current_price, df: pd.DataFrame, position: Dict, telegram=False) -> bool:
        """Update stop only on new swing structure, but only after R:R >= 1:1."""
        if not position:
            return False

        # Cancel if RR is not high enough
        if position['direction'] == 'long':
            risk = max(0.0, position['entry_price'] - position['stop_loss'])
            if risk > 0:
                reward = current_price - position['entry_price']
                if reward < risk:
                    return False
        else: # short
            risk = max(0.0, position['stop_loss'] - position['entry_price'])
            if risk > 0:
                reward = position['entry_price'] - current_price
                if reward < risk:
                    return False


        # Store original static stop if not already stored
        if 'original_stop_loss' not in position:
            position['original_stop_loss'] = position['stop_loss']

        # Now update trailing stop based on structure
        df_analyzed = self.analyzer.analyze_structure(df)
        updated = False

        if position['direction'] == 'long':
            # Find all swing lows since last stop update
            recent_swings = df_analyzed.tail(50)
            swing_lows = recent_swings[recent_swings['swing_low'].notna()]
            if not swing_lows.empty:
                # Find the HIGHEST swing low (most favorable for longs)
                best_swing_low = swing_lows['swing_low'].max()
                # Tighter stop distance - closer to swing low for faster trailing
                new_stop = best_swing_low - STOP_LOSS_BUFFER
                # Only move stop up (more favorable) - NEVER move down for longs
                if new_stop > position['stop_loss'] and abs(new_stop - current_price) > MIN_STOP_DISTANCE_COIN:
                    old_stop = position['stop_loss']
                    position['stop_loss'] = new_stop
                    position['last_stop_update_idx'] = swing_lows.index[-1]
                    updated = True
                    print(f"📈 TRAILING STOP TRIGGERED! ${old_stop:.4f} → ${new_stop:.4f} (swing low: ${best_swing_low:.4f}")

                    if telegram:
                        telegram_message = "===== TRAILING STOP UPDATED =====\n"
                        telegram_message += f"Direction: Long\n"

                        unrealized_pnl = (position['stop_loss'] - position['entry_price']) * position['quantity'] - position['entry_fees']

                        telegram_message += f"Stop loss: ${old_stop:.4f} → ${position['stop_loss']:.4f} (swing high: ${best_swing_low:.4f}\n"
                        telegram_message += f"Unrealized P&L: ${unrealized_pnl:.4f} ({(unrealized_pnl / self.risk_amount) * 100}%)\n"

                        send_telegram_message(telegram_message)
                        print(telegram_message)

                        # Debug prints only when candles_after_swing exists
                        print("Swing lows:", swing_lows)
                        print("Best swing low:", best_swing_low)
                        print("New stop:", new_stop)

        else:  # short
            # Find all swing highs since last stop update
            recent_swings = df_analyzed.tail(50)
            swing_highs = recent_swings[recent_swings['swing_high'].notna()]
            if not swing_highs.empty:
                best_swing_high = swing_highs['swing_high'].min()
                new_stop = best_swing_high + STOP_LOSS_BUFFER

                if new_stop < position['stop_loss'] and abs(new_stop - current_price) > MIN_STOP_DISTANCE_COIN:

                    old_stop = position['stop_loss']
                    position['stop_loss'] = new_stop
                    position['last_stop_update_idx'] = swing_highs.index[-1]
                    updated = True
                    print(f"📉 TRAILING STOP TRIGGERED! ${old_stop:.4f} → ${new_stop:.4f} (swing high: ${best_swing_high:.4f}")

                    if telegram:
                        telegram_message = "===== TRAILING STOP UPDATED =====\n"
                        telegram_message += f"Direction: Short\n"

                        unrealized_pnl = (position['entry_price'] - position['stop_loss']) * position['quantity'] - position['entry_fees']

                        telegram_message += f"Stop loss: ${old_stop:.4f} → ${position['stop_loss']:.4f} (swing high: ${best_swing_high:.4f}"
                        telegram_message += f"Unrealized P&L: ${unrealized_pnl:.4f} ({(unrealized_pnl / self.risk_amount) * 100}%)\n"

                        send_telegram_message(telegram_message)
                        print(telegram_message)

                print("Swing highs:", swing_highs)
                print("Best swing high:", best_swing_high)
                print("New stop:", new_stop)

        print("Current stop:", position['stop_loss'])

        return updated


# Adds order cancellation to the regular strategy setup expiration
class LiveFVGStrategy(FVGStrategy):
    def __init__(self, cancel_order_by_id, risk_amount=0):
        super().__init__(risk_amount=risk_amount)
        self.cancel_order_by_id = cancel_order_by_id
        self.risk_amount = risk_amount


    def update_active_setups(self, df):
        # Create a new list without expired setups to avoid modifying list while iterating
        current_time = df['T'].iloc[-1]

        for setup in self.active_setups:
            if setup['oid'] is not None and current_time - setup['indicator_time'] > timedelta(minutes=self.max_entry_indicator_dist):
                self.cancel_order_by_id(setup['oid'])

        self.active_setups = [
            setup for setup in self.active_setups
            if current_time - setup['indicator_time'] <= timedelta(minutes=self.max_entry_indicator_dist)
        ]

        active_setups = []
        for setup in self.active_setups:
            if setup['oid'] is not None:
                fvg_midpoint = (setup['fvg']['top'] + setup['fvg']['bottom']) / 2
                if setup['direction'] == 'long' and df['low'].iloc[-1] <= fvg_midpoint:
                    self.cancel_order_by_id(setup['oid'])
                    continue
                elif setup['direction'] == 'short' and df['low'].iloc[-1] >= fvg_midpoint:
                    self.cancel_order_by_id(setup['oid'])
                    continue

            active_setups.append(setup)
        self.active_setups = active_setups
