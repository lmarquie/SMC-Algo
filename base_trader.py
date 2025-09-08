from config import *
from typing import Dict
from trading_logic.trading_strategy import FVGStrategy
from trading_logic.structure_analysis import StructureAnalyzer
import numpy as np
from helpers.telegram_setup import send_telegram_message


class BaseTrader:
    def __init__(self, symbol, balance):
        self.symbol = symbol
        self.strategy = FVGStrategy()
        self.analyzer = StructureAnalyzer(min_fvg_strength=MIN_FVG_STRENGTH)
        self.current_position = None
        self.iteration = 0

        self.last_close = None
        self.trade_cooldown = 20

        self.symbol = symbol
        self.trades = []
        self.current_position = None
        self.current_balance = balance
        self.last_position_close_time = None

        self.htf_lookback = 24
        self.ltf_lookback = 100


    def process_new_candle(self, ltf_data, htf_data, timestamp, telegram=False):
        print(f"Running iteration: time {timestamp}, balance: ${self.current_balance:.2f}")
        if self.current_position:
            self.strategy.update_trailing_stop(df=ltf_data, position=self.current_position, telegram=telegram)
            return

        self.strategy.check_entry_conditions(ltf_data, htf_data)
        self.strategy.update_active_setups(ltf_data)


    def create_open_order(self, setup, timestamp):
        risk_amount = 150
        min_dist_percent = MIN_STOP_DISTANCE_COIN  # Use the config value

        entry_price = (setup['fvg']['top'] + setup['fvg']['bottom']) / 2
        original_stop_distance = abs(entry_price - setup['stop_loss'])
        min_stop_distance = entry_price * min_dist_percent
        
        # Enforce minimum stop distance
        if original_stop_distance < min_stop_distance:
            print(f"MINIMUM STOP DISTANCE ENFORCED: {original_stop_distance:.4f} → {min_stop_distance:.4f}")
            stop_distance = min_stop_distance
            
            # Recalculate stop loss based on new distance
            if setup['direction'] == 'long':
                stop_loss = entry_price - stop_distance
            else:  # short
                stop_loss = entry_price + stop_distance
        else:
            stop_distance = original_stop_distance
            stop_loss = setup['stop_loss']

        # Calculate quantity based on risk amount and stop distance
        quantity = risk_amount / stop_distance

        position = {
            'direction': setup['direction'],
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'quantity': quantity,
            'fvg': setup['fvg'],
            'indicator': setup['indicator'],
            'indicator_type': setup['indicator_type'],
            'entry_time': timestamp,
            'entry_idx': self.iteration,
        }

        print(f" === Position opened ===")
        print(f"Risk amount: ${risk_amount:.4f}, Quantity: {quantity:.4f}, Stop distance: ${stop_distance:.4f}")
        print(f"Stop loss: ${stop_loss:.4f}")
        if stop_distance > original_stop_distance:
            print(f"⚠️  Position size adjusted to maintain ${risk_amount} risk with minimum stop distance")

        return position


    def check_position_closed(self, current_price):
        if self.current_position['direction'] == 'long':
            if current_price <= self.current_position['stop_loss']:
                return True
        elif self.current_position['direction'] == 'short':
            if current_price >= self.current_position['stop_loss']:
                return True

        return False


    def handle_position_open(self, setup, timestamp, telegram=False, symbol=None):
        print("OPEN ORDER BEING CALLED")
        self.current_position = self.create_open_order(setup, timestamp)

        telegram_text = ""
        if symbol:
            telegram_text += f"===== New {symbol} Position Opened =====\n"
        else:
            telegram_text += "===== New Position Opened =====\n"
        telegram_text += f"Symbol: {symbol if symbol else 'N/A'}\n"
        telegram_text += f"Direction: {self.current_position['direction']}\n"
        telegram_text += f"Entry price: ${self.current_position['entry_price']:.4f}\n"
        telegram_text += f"Stop loss: ${self.current_position['stop_loss']:.4f}\n"
        telegram_text += f"Position quantity: {self.current_position['quantity']:.4f}\n"
        telegram_text += f"Full exposure: ${(self.current_position['entry_price'] * self.current_position['quantity']):.2f}\n"

        if telegram:
            send_telegram_message(telegram_text)
        print(telegram_text)
        self.strategy.active_setups = []


    def handle_position_close(self, current_price, timestamp, telegram=False, symbol=None):
        # Calculate dollar P&L
        exit_price = self.current_position['stop_loss']
        price_diff = abs(exit_price - self.current_position['entry_price'])
        pnl_dollar = self.current_position['quantity'] * price_diff

        if self.current_position['direction'] == 'long' and exit_price < self.current_position['entry_price']:
            pnl_dollar *= -1
        elif self.current_position['direction'] == 'short' and exit_price > self.current_position['entry_price']:
            pnl_dollar *= -1

        pnl_dollar -= 40
        # Debug P&L calculation
        print(f"P&L Debug: Entry: ${self.current_position['entry_price']:.4f}, Exit: ${exit_price:.4f}")
        print(f"P&L Debug: Price diff: ${price_diff:.4f}, Position size: {self.current_position['quantity']:.4f}")
        print(f"P&L Debug: Raw P&L: ${pnl_dollar:.2f}")

        # Update balance
        self.current_balance += pnl_dollar

        # Record trade
        trade = {
            'entry_time': self.current_position['entry_time'],
            'entry_idx': self.current_position['entry_idx'],
            'exit_time': timestamp,
            'exit_idx': self.iteration,
            'direction': self.current_position['direction'],
            'entry_price': self.current_position['entry_price'],
            'exit_price': exit_price,
            'quantity': self.current_position['quantity'],
            'indicator': self.current_position['indicator'],
            'indicator_type': self.current_position['indicator_type'],
            'fvg': self.current_position['fvg'],
            'pnl_dollar': pnl_dollar,
        }
        self.trades.append(trade)
        print(f"Position closed: {pnl_dollar:.2f}")

        telegram_text = ""
        if symbol:
            telegram_text += f"===== {symbol} Position Closed =====\n"
        else:
            telegram_text += "===== Position Closed =====\n"
        telegram_text += f"Symbol: {symbol if symbol else 'N/A'}\n"
        telegram_text += f"Direction: {self.current_position['direction']}\n"
        telegram_text += f"Exit price: ${trade['exit_price']:.4f}\n"
        telegram_text += f"Total time in trade: {trade['exit_time'] - trade['entry_time']}\n"
        telegram_text += f"P&L: ${pnl_dollar:.2f}\n"
        telegram_text += f"Total trades taken: {len(self.trades)}\n"
        telegram_text += f"Current balance: ${self.current_balance:.2f}\n"

        if telegram:
            send_telegram_message(telegram_text)
        print(telegram_text)

        # Reset position
        self.current_position = None
        self.last_position_close_time = timestamp