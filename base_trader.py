from config import *
from typing import Dict
from trading_logic.trading_strategy import FVGStrategy
from trading_logic.structure_analysis import StructureAnalyzer
import numpy as np


class BaseTrader:
    def __init__(self, symbol, _open_position, _close_position, balance):
        self.symbol = symbol
        self._open_position = _open_position
        self._close_position = _close_position
        self.strategy = FVGStrategy()
        self.analyzer = StructureAnalyzer()
        self.current_position = None
        self.iteration = 0

        self.last_close = None
        self.trade_cooldown = 20

        self.symbol = symbol
        self.trades = []
        self.current_position = None
        self.current_balance = balance

    def single_iteration(self, ltf_data, htf_data, current_time, telegram=False):
        current_candle = ltf_data.iloc[-1]
        current_price = current_candle['close']
        current_low = current_candle['low']
        current_high = current_candle['high']
        print(f"Running iteration: time {current_time}, balance: ${self.current_balance:.2f}")

        self.strategy.update_trade_setups(ltf_data)

        if self.current_position:
            stop_loss = self.current_position['stop_loss']
            direction = self.current_position['direction']

            # Update trailing stop BEFORE checking stop loss
            self.strategy.update_trailing_stop(df=ltf_data, position=self.current_position, telegram=telegram)

            # Check if stop loss is hit
            if direction == 'long' and current_low <= stop_loss:
                self._close_position(stop_loss, current_time)
                self.last_close = current_candle['T']
            elif direction == 'short' and current_high >= stop_loss:
                self._close_position(stop_loss, current_time)
                self.last_close = current_candle['T']

        # Check for new entry if no position
        else:
            self.strategy.check_entry_conditions(ltf_data, htf_data)
            self.execute_valid_orders(current_price, current_time)


    def execute_valid_orders(self, current_price, current_time):
        for setup in self.strategy.trade_setups:
            if self.analyzer.check_fvg_touch(current_price, setup["fvg"]):
                setup["entry_price"] = current_price
                self._open_position(setup, current_price, current_time)
                return


    def create_open_order(self, setup: Dict, current_price: float, timestamp):
        """Open a new position"""

        # Calculate position size
        risk_amount = abs(setup['entry_price'] - setup['stop_loss'])
        min_stop_distance = setup['entry_price'] * 0.0015
        if risk_amount < min_stop_distance:
            print("RISK AMOUNT ADJUSTED TO MIN STOP DISTANCE")
        risk_amount = max(risk_amount, min_stop_distance)

        position_size, stop_loss = self._calculate_position_size(risk_amount, setup['entry_price'], setup, self.symbol)

        # Calculate expected loss if stop is hit
        final_risk_amount = abs(setup['entry_price'] - stop_loss) / position_size
        expected_loss = position_size * final_risk_amount

        position = {
            'direction': setup['direction'],
            'entry_price': setup['entry_price'],
            'stop_loss': stop_loss,
            'take_profit': setup.get('take_profit'),
            'size': position_size,
            'fvg': setup['fvg'],
            'bos': setup['bos'],
            'mss': setup['mss'],
            'entry_time': timestamp,
            'entry_idx': self.iteration,
            'reason': setup['reason'],
            'symbol': self.symbol,
        }

        print(f"Position opened: {self.current_position}")
        print(
                f"Risk amount: ${risk_amount:.4f}, Position size: {position_size:.4f}, Expected loss if stopped: ${expected_loss:.2f}")

        return position


    def _calculate_position_size(self, risk_amount: float, entry_price: float, setup: Dict,
                                 symbol: str = "SOL-USD") -> tuple:
        """Calculate position size based on risk management rules with capital and leverage constraints"""
        dollar_risk = 150  # $150 fixed risk

        # Position size = Target Risk / Price Risk per Unit
        # This guarantees we risk exactly $150
        position_size = dollar_risk / risk_amount
        # Calculate position value (size × entry price)
        position_value = position_size * entry_price

        # Get leverage for this symbol
        leverage = MAX_LEVERAGE[symbol]

        # Capital constraints: $10,000 capital with leverage = max position value
        max_position_value = 10000 * leverage  # Dynamic based on symbol leverage

        # Check if position value exceeds maximum allowed
        if position_value > max_position_value:
            # First, try to double the stop loss distance to reduce position size
            original_stop = setup['stop_loss']
            direction = setup['direction']

            if direction == 'long':
                # Double the distance from entry to stop (widen stop down)
                new_stop_distance = (entry_price - original_stop) * 2
                new_stop = entry_price - new_stop_distance
            else:  # short
                # Double the distance from entry to stop (widen stop up)
                new_stop_distance = (original_stop - entry_price) * 2
                new_stop = entry_price + new_stop_distance

            # Recalculate position size with new stop
            new_risk_amount = abs(entry_price - new_stop)
            position_size = dollar_risk / new_risk_amount
            position_value = position_size * entry_price

            # Check if this fits within capital constraints
            if position_value <= max_position_value:
                print(f"Stop loss widened to fit capital constraints. New stop: ${new_stop:.4f} (was ${original_stop:.4f})")
                return position_size, new_stop
            else:
                # If still too large, scale down position size as last resort
                position_size = max_position_value / entry_price
                actual_risk = position_size * new_risk_amount
                print(f"Position size reduced due to capital constraints. Risk: ${actual_risk:.2f} instead of $150")
                return position_size, new_stop

        return position_size, setup['stop_loss']