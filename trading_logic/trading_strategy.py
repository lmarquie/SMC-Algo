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
from models.direction import Direction
from models.side import Side


class FVGStrategy:
    def __init__(self, client: Client, risk_amount=0):
        self.analyzer = StructureAnalyzer(min_fvg_strength=MIN_FVG_STRENGTH)

        self.active_fvgs = []
        self.existing_fvg_times = []

        self.most_recent_setup = None
        self.active_setups = []
        self.last_analysis_time = None
        self.current_position = None
        self.fvg_count = 0
        self.bullish_fvg_touch = 0
        self.bearish_fvg_touch = 0
        self.previous_fvg_times = []
        self.risk_amount = risk_amount

        self.client = client
        self.exchange = client.exchange

    def clear_setups(self):
        self.active_setups = []


    def update_fvgs(self, df: pd.DataFrame):
        """Update and maintain active FVGs"""

        recent_fvgs = []
        fvg_candidates = self.analyzer.detect_fvg(df[-(MAX_FVG_LOOKBACK + 1):])
        
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
                    if current_low < fvg.top:
                        fvg.fill()
                else:  # bearish
                    # FVG is filled if price goes above the top
                    if current_high > fvg.bottom:
                        fvg.fill()
                
                if current_time - fvg.time > timedelta(minutes=MAX_FVG_LOOKBACK):
                    fvg.fill()

            if not fvg.filled:
                active_fvgs.append(fvg)

        self.active_fvgs = active_fvgs


    def is_ascending(self, series):
        for i in range(0, len(series)):
            if series[i] < series[i-1]:
                return False
        return True

    def is_descending(self, series):
        for i in range(0, len(series)):
            if series[i] > series[i-1]:
                return False
        return True


    def identify_trend(self, df, min_seq):
        swing_lows = df.loc[df['swing_low'].notna(), 'swing_low'].to_numpy()
        swing_highs = df.loc[df['swing_high'].notna(), 'swing_high'].to_numpy()
        #print(len(swing_lows), len(swing_highs))

        if len(swing_lows) < min_seq or len(swing_highs) < min_seq:
            print("Not enough swing points to identify trend")
            return None
        elif (self.is_ascending(swing_lows[-min_seq:])
                and self.is_ascending(swing_highs[-min_seq:])):
            return 'uptrend'
        elif (self.is_descending(swing_lows[-min_seq:])
              and self.is_descending(swing_highs[-min_seq:])):
            return 'downtrend'
        else:
            return None


    def find_setups(self, df: pd.DataFrame, htf_df: pd.DataFrame):
        self.update_fvgs(df)
        if len(self.active_fvgs) == 0:
            return

        last_fvg = self.active_fvgs[-1]
        if df['T'].iloc[-1] == last_fvg.time + timedelta(minutes=1):
            swing_lows = df.loc[df['swing_low'].notna(), 'swing_low'].to_numpy()
            swing_highs = df.loc[df['swing_high'].notna(), 'swing_high'].to_numpy()

            if (last_fvg.bullish
                and self.is_descending(swing_highs[(MIN_SEQUENTIAL_SWINGS + 1):-1])
                and len(swing_highs) >= 2
                and swing_highs[-1] >= swing_highs[-2]
                #and self.identify_trend(htf_df, HTF_SEQUENTIAL_MIN) == 'uptrend'
                ):
                mss_time = df.loc[df['swing_high'].notna(), 'T'].iloc[-1]
                self.active_setups = []
                self.add_setup(direction=Direction.LONG, fvg=last_fvg, mss_time=mss_time, df=df)

            elif (last_fvg.bearish
                and self.is_ascending(swing_lows[(MIN_SEQUENTIAL_SWINGS + 1):-1])
                and len(swing_lows) >= 2
                and swing_lows[-1] <= swing_lows[-2]
                #and self.identify_trend(htf_df, HTF_SEQUENTIAL_MIN) == 'downtrend'
                ):
                mss_time = df.loc[df['swing_low'].notna(), 'T'].iloc[-1]
                self.active_setups = []
                self.add_setup(direction=Direction.SHORT, fvg=last_fvg, mss_time=mss_time, df=df)


    def add_setup(self, direction: Direction, fvg: FVG, mss_time, df: pd.DataFrame):
        entry_price = fvg.midpoint

        if direction == Direction.LONG:
            stop_loss = entry_price - entry_price * MIN_STOP_DISTANCE_COIN
            maximum_allowed = min(fvg.bottom, df['low'].iloc[-1])
            initial_stop_loss = min(stop_loss, maximum_allowed)
        else:
            stop_loss = entry_price + entry_price * MIN_STOP_DISTANCE_COIN
            minimum_allowed = max(fvg.top, df['high'].iloc[-1])
            initial_stop_loss = max(stop_loss, minimum_allowed)

        stop_distance = abs(entry_price - initial_stop_loss)
        quantity = self.risk_amount / stop_distance


        setup = Setup(
            entry_price=entry_price,
            quantity=quantity,
            direction=direction,
            initial_stop_loss=initial_stop_loss,
            fvg=fvg,
            mss_time=mss_time,
            larger_trend='uptrend' if fvg.bullish else 'downtrend',
            trend_confidence=1.0,
        )
        self.active_setups.append(setup)
        self.most_recent_setup = setup
        self.client.place_limit_order(
            quantity=quantity,
            placement_time=df['T'].iloc[-1],
            side=Side.BUY if direction == Direction.LONG else Side.SELL,
            entry_price=entry_price,
        )


    def get_valid_swing_stop(self, type, df, current_candle):
        current_idx = len(df) - 1
        swing_idx = current_idx - SWING_LOOKBACK_FORWARD - 1

        # Check if we have enough data for the backward window
        if swing_idx < SWING_LOOKBACK_BACKWARD:
            return None

        swing_candle = df.iloc[swing_idx]
        swing_value = swing_candle['high'] if type == 'high' else swing_candle['low']

        # Check backward window: SWING_LOOKBACK_BACKWARD candles before swing
        for i in range(swing_idx - SWING_LOOKBACK_BACKWARD, swing_idx):
            if type == 'high' and df['high'].iloc[i] > swing_value:
                return None
            elif type == 'low' and df['low'].iloc[i] < swing_value:
                return None

        # Check forward window: SWING_LOOKBACK_FORWARD candles after swing (up to but not including current)
        for i in range(swing_idx + 1, swing_idx + SWING_LOOKBACK_FORWARD + 1):
            if type == 'high' and df['high'].iloc[i] > swing_value:
                return None
            elif type == 'low' and df['low'].iloc[i] < swing_value:
                return None

        # Verify swing is beyond current candle (provides stop loss protection)
        if type == 'high' and swing_value <= current_candle['high']:
            return None
        elif type == 'low' and swing_value >= current_candle['low']:
            return None

        return swing_value


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
            if (current_time - order['placement_time']).total_seconds() / 60 > MAX_ORDER_DURATION:
                self.client.cancel_order(order['id'])


    def update_trailing_stop(self, current_stop, position, df, candle):
        if position.direction == Direction.LONG:
            risk = max(0.0, position.entry_price - current_stop)
            if risk > 0:
                reward = candle['low'] - position.entry_price
                if reward < risk:
                    return None

            swing_point = self.get_valid_swing_stop('low', df, candle)
            if swing_point:
                new_stop = swing_point - STOP_LOSS_BUFFER
                if new_stop > position.get_last_stop():
                    return new_stop
        else:  # short
            risk = max(0.0, current_stop - position.entry_price)
            if risk > 0:
                reward = position.entry_price - candle['high']
                if reward < risk:
                    return None

            swing_point = self.get_valid_swing_stop('high', df, candle)
            if swing_point:
                new_stop = swing_point + STOP_LOSS_BUFFER
                if new_stop < position.get_last_stop():
                    return new_stop

        return None