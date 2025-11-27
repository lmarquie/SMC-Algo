import pandas as pd
from typing import Dict, List, Optional
from trading_logic.structure_analysis import StructureAnalyzer
import logging
from config import *
from datetime import datetime, timedelta
from helpers.telegram_setup import send_telegram_message
import math
from config import MIN_FVG_STRENGTH

from models.fvg import FVG
from models.setup import Setup
from client.client import Client
from exchange.exchange import Exchange


class FVGStrategy:
    def __init__(self, client: Client, risk_amount=0):
        self.analyzer = StructureAnalyzer(min_fvg_strength=MIN_FVG_STRENGTH)

        self.active_fvgs = []
        self.existing_fvg_times = []

        self.last_order_placed = None
        self.active_setups = []
        self.last_analysis_time = None
        self.current_position = None
        self.fvg_count = 0
        self.bullish_fvg_touch = 0
        self.bearish_fvg_touch = 0
        self.previous_fvg_times = []
        self.risk_amount = risk_amount

        self.max_fvg_to_indicator_dist = 8
        self.max_entry_indicator_dist = 8

        self.fvg_lookback = self.max_fvg_to_indicator_dist
        self.client = client
        self.exchange = client.exchange


    def clear_setups(self):
        self.active_setups = []
        self.active_fvgs = []


    def update_active_setups(self, df):
        # Create a new list without expired setups to avoid modifying list while iterating
        current_time = df['T'].iloc[-1]
        self.active_setups = [
            setup for setup in self.active_setups 
            if current_time - setup.indicator_time <= timedelta(minutes=self.max_entry_indicator_dist)
        ]


    def update_fvgs(self, df: pd.DataFrame):
        """Update and maintain active FVGs"""

        recent_fvgs = []
        fvg_candidates = self.analyzer.detect_fvg(df[-(self.fvg_lookback + 1):])
        
        for fvg_candidate in fvg_candidates:
            fvg = FVG(
                type=fvg_candidate['type'],
                time=fvg_candidate['time'],
                top=fvg_candidate['top'],
                bottom=fvg_candidate['bottom'],
                strength=fvg_candidate['strength'],
            )
            recent_fvgs.append(fvg)

        new_fvgs = [fvg for fvg in recent_fvgs if not fvg.time in self.existing_fvg_times]
        self.fvg_count += len(new_fvgs)
        self.existing_fvg_times += [fvg.time for fvg in new_fvgs]
        current_low = df['low'].iloc[-1]
        current_high = df['high'].iloc[-1]
        current_time = df['T'].iloc[-1]

        # Filter out old FVGs and mark filled ones
        active_fvgs = []
        for fvg in self.active_fvgs + new_fvgs:
            if not fvg.filled:
                if fvg.bullish:
                    # FVG is filled if price goes below the bottom
                    if current_low < fvg.midpoint:
                        fvg.fill()
                else:  # bearish
                    # FVG is filled if price goes above the top
                    if current_high > fvg.midpoint:
                        fvg.fill()
                
                if current_time - fvg.time > timedelta(minutes=self.fvg_lookback):
                    fvg.fill()

            if not fvg.filled:
                active_fvgs.append(fvg)

        self.active_fvgs = active_fvgs


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
        #print(" - Checking entry conditions...")
        if len(df) < 20:
            return None

        # Step 1: Identify larger trend
        larger_trend = self.identify_larger_trend(htf_df)

        # Make trend confidence requirement stricter
        if larger_trend['confidence'] < MIN_LARGER_TREND_CONFIDENCE:
            #print(f"(check_entry_conditions) trend confidence too low ({larger_trend['confidence'] * 100:.2f}%) - skipping entry.")
            return None
        
        last_close = df['close'].iloc[-1]
        last_high = df['high'].iloc[-1]
        last_low = df['low'].iloc[-1]

        if larger_trend['trend'] == 'uptrend':
            self._add_bullish_setups(df, last_close=last_close, larger_trend=larger_trend)

        elif larger_trend['trend'] == 'downtrend':
            self._add_bearish_setups(df, last_close=last_close, larger_trend=larger_trend)

        return None


    def _add_bullish_setups(self, df, last_close, larger_trend):
        bullish_fvgs = sorted([fvg for fvg in self.active_fvgs if fvg.bullish], key=lambda fvg: fvg.time)
        last_low = df['low'].iloc[-1]

        df_analyzed = self.analyzer.analyze_structure(df)
        present_bullish_movement = df_analyzed["bullish_bos"].iloc[-1] or df_analyzed["bullish_mss"].iloc[-1]

        if present_bullish_movement or not REQUIRE_SETUP_INDICATORS:
            for fvg in bullish_fvgs:
                time_since_fvg = int((df['T'].iloc[-1] - fvg.time).total_seconds() / 60)

                recent_df = df_analyzed[-time_since_fvg:]
                has_bearish_reversal = (
                        recent_df["bearish_bos"].sum() > 0 or
                        recent_df["bearish_mss"].sum() > 0
                )

                if not (has_bearish_reversal and REVERSAL_CONSTRAINT_ENABLED):
                    # Check if setup already exists for this FVG
                    fvg_already_has_setup = any(
                        setup.fvg.time == fvg.time and setup.long
                        for setup in self.active_setups
                    )
                    
                    if not fvg_already_has_setup:
                        stop_loss = fvg.bottom - STOP_LOSS_BUFFER

                        # Find the nearest swing low for structure-based stop
                        nearest_swing_low, _ = self._find_nearest_swing(type='low', df_analyzed=df_analyzed, current_price=last_close)
                        if nearest_swing_low:
                            swing_stop = nearest_swing_low - STOP_LOSS_BUFFER
                            # Use the LOWER of the two stops (FVG-based or structure-based)
                            stop_loss = min(stop_loss, swing_stop)

                        # TEMP SOLUTION
                        entry_price = fvg.midpoint
                        stop_distance = entry_price - stop_loss
                        if stop_distance < entry_price * MIN_STOP_DISTANCE_COIN:
                            stop_distance = entry_price * MIN_STOP_DISTANCE_COIN
                        stop_loss = entry_price - stop_distance
                        quantity = self.risk_amount / stop_distance

                        existing_entry_prices = [setup.entry_price for setup in self.active_setups]
                        if entry_price in existing_entry_prices:
                            continue

                        setup = Setup(
                            entry_price=entry_price,
                            quantity=quantity,
                            direction='long',
                            stop_loss=stop_loss,
                            fvg=fvg,
                            indicator_type='bos' if df_analyzed["bullish_bos"].iloc[-1] else 'mss',
                            indicator_time = df['T'].iloc[-1],
                            larger_trend=larger_trend['trend'],
                            trend_confidence=larger_trend['confidence'],
                        )
                        self.active_setups.append(setup)
                        self.last_order_placed = setup
                        self.client.place_limit_order(
                            quantity=quantity,
                            placement_time=df['T'].iloc[-1],
                            side="buy",
                            entry_price=entry_price,
                        )


    def _add_bearish_setups(self, df, last_close, larger_trend):
        bearish_fvgs = sorted([fvg for fvg in self.active_fvgs if fvg.bearish], key=lambda fvg: fvg.time)
        df_analyzed = self.analyzer.analyze_structure(df)
        present_bearish_movement = df_analyzed["bearish_bos"].iloc[-1] or df_analyzed["bearish_mss"].iloc[-1]
        last_high = df['high'].iloc[-1]

        if present_bearish_movement or not REQUIRE_SETUP_INDICATORS:
            for fvg in bearish_fvgs:
                time_since_fvg = int((df['T'].iloc[-1] - fvg.time).total_seconds() / 60)

                recent_df = df_analyzed[-time_since_fvg:]
                has_bullish_reversal = (
                        recent_df["bullish_bos"].sum() > 0 or
                        recent_df["bullish_mss"].sum() > 0
                )

                if not (has_bullish_reversal and REVERSAL_CONSTRAINT_ENABLED):
                    # Check if setup already exists for this FVG
                    fvg_already_has_setup = any(
                        setup.fvg.time == fvg.time and setup.short
                        for setup in self.active_setups
                    )
                    
                    if not fvg_already_has_setup:
                        stop_loss = fvg.top + STOP_LOSS_BUFFER

                        # Find the nearest swing high for structure-based stop
                        nearest_swing_high, _ = self._find_nearest_swing(type='high', df_analyzed=df_analyzed, current_price=last_close)
                        if nearest_swing_high:
                            swing_stop = nearest_swing_high + STOP_LOSS_BUFFER
                            # Use the HIGHER of the two stops (FVG-based or structure-based)
                            stop_loss = max(stop_loss, swing_stop)

                        # TEMP SOLUTION
                        entry_price = fvg.midpoint
                        stop_distance = stop_loss - entry_price
                        if stop_distance < entry_price * MIN_STOP_DISTANCE_COIN:
                            stop_distance = entry_price * MIN_STOP_DISTANCE_COIN
                        stop_loss = entry_price + stop_distance
                        quantity = self.risk_amount / stop_distance

                        existing_entry_prices = [setup.entry_price for setup in self.active_setups]
                        if entry_price in existing_entry_prices:
                            continue
                        
                        setup = Setup(
                            entry_price=entry_price,
                            quantity=quantity,
                            direction='short',
                            stop_loss=stop_loss,
                            fvg=fvg,
                            indicator_type='bos' if df_analyzed["bearish_bos"].iloc[-1] else 'mss',
                            indicator_time = df['T'].iloc[-1],
                            larger_trend=larger_trend['trend'],
                            trend_confidence=larger_trend['confidence'],
                        )
                        self.active_setups.append(setup)
                        self.last_order_placed = setup
                        self.client.place_limit_order(
                            quantity=quantity,
                            placement_time=df['T'].iloc[-1],
                            side="sell",
                            entry_price=entry_price,
                        )


    def _get_valid_swing_stop(self, type, df, current_candle):
        current_idx = len(df) - 1
        swing_candle = df.iloc[-(SWING_LOOKBACK_FORWARD + 1)]
        swing_idx = len(df) - (SWING_LOOKBACK_FORWARD + 1)

        for i in list(range(swing_idx - SWING_LOOKBACK_BACKWARD, swing_idx)) + list(range(swing_idx + 1, current_idx + 1)):
            if (type == 'high' and df['high'].iloc[i] > swing_candle['high']) or \
                    (type == 'low' and df['low'].iloc[i] < swing_candle['low']):
                return None

        if type == 'high' and swing_candle['high'] <= current_candle['high']:
            return None
        elif type == 'low' and swing_candle['low'] >= current_candle['low']:
            return None

        return swing_candle[type]

    def _find_nearest_swing(self, type, df_analyzed, current_price):
        recent_df = df_analyzed.tail(50)
        swing_col = f'swing_{type}'
        swing_points = recent_df[recent_df[swing_col].notna()][swing_col]

        if type == 'high':
            valid_swings = swing_points[swing_points > current_price]
        else:
            valid_swings = swing_points[swing_points < current_price]

        if len(valid_swings) == 0:
            return None, None

        best_swing = valid_swings.min() if type == 'high' else valid_swings.max()
        best_swing_time = recent_df.loc[recent_df[swing_col] == best_swing]['T'].iloc[-1]
        return best_swing, best_swing_time


    def cancel_lagging_orders(self, current_time):
        orders = self.exchange.get_limit_orders()
        for order in orders:
            if (current_time - order['placement_time']).total_seconds() / 60 > MAX_INDICATOR_ENTRY_DIST:
                self.client.cancel_order(order['id'])


    def update_trailing_stop(self, current_stop, position, df, candle):
        if position.long:
            risk = max(0.0, position.entry_price - current_stop)
            if risk > 0:
                reward = candle['low'] - position.entry_price
                if reward < risk:
                    return None
        else:  # short
            risk = max(0.0, current_stop - position.entry_price)
            if risk > 0:
                reward = position.entry_price - candle['high']
                if reward < risk:
                    return None

        if position.long:
            swing_point = self._get_valid_swing_stop('low', df, candle)
            if swing_point:
                new_stop = swing_point - STOP_LOSS_BUFFER
                if new_stop > position.get_last_stop():
                    return new_stop

        else:  # short
            swing_point = self._get_valid_swing_stop('high', df, candle)
            if swing_point:
                new_stop = swing_point + STOP_LOSS_BUFFER
                if new_stop < position.get_last_stop():
                    return new_stop

        return None