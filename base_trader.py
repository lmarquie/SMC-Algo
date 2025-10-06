from config import *
from typing import Dict
from trading_logic.trading_strategy import FVGStrategy, LiveFVGStrategy
import numpy as np
from helpers.telegram_setup import send_telegram_message
import pandas as pd
import os
import shutil
from helpers.telegram_setup import send_telegram_image
import matplotlib.pyplot as plt
from datetime import datetime
import ccxt
from credentials import *
from models.position import Position


class BaseTrader:
    def __init__(self, symbol, balance, telegram=False):
        self.symbol = symbol
        self.risk_amount = RISK_PER_TRADE  # Use config value
        self.strategy = FVGStrategy(risk_amount=self.risk_amount)
        self.current_position = None
        self.iteration = 0

        self.last_close = None
        self.trade_cooldown = 20

        self.symbol = symbol
        self.trades = []
        self.current_position = None
        self.current_balance = balance
        self.last_position_close_time = None
        self.telegram = telegram

        self.htf_lookback = 50
        self.ltf_lookback = 100

        if os.path.exists('trades'):
            shutil.rmtree("trades")
        os.makedirs("trades", exist_ok=True)
        os.makedirs("trades/wins", exist_ok=True)
        os.makedirs("trades/losses", exist_ok=True)


    def show_final_results(self, trades, test_type):
        final_message = ""

        final_message += "\n" + "=" * 70
        final_message += f"\n🏁 {test_type.upper()} RESULTS\n"
        final_message += "=" * 70

        initial_balance = 10000
        total_trades = len(trades)

        final_message += f"\nSymbol Traded: {self.symbol}\n"
        final_message += f"Initial Balance: ${initial_balance:,.2f}\n"
        final_message += f"Total Trades: {total_trades}\n"

        if trades:
            symbol_pnl = sum(trade['pnl_dollar'] for trade in trades)
            symbol_trades = len(trades)
            symbol_wins = len([t for t in trades if t['pnl_dollar'] > 0])
            symbol_win_rate = (symbol_wins / symbol_trades) * 100 if symbol_trades > 0 else 0

            final_message += f"{self.symbol} Results:\n"
            final_message += f"  Trades: {symbol_trades}\n"
            final_message += f"  P&L: ${symbol_pnl:.2f}\n"
            final_message += f"  Win Rate: {symbol_win_rate:.1f}%\n"

            # Calculate overall performance
            total_pnl = sum(trade['pnl_dollar'] for trade in trades)
            final_balance = initial_balance + total_pnl
            total_return = (total_pnl / initial_balance) * 100

            winning_trades = [t for t in trades if t['pnl_dollar'] > 0]
            losing_trades = [t for t in trades if t['pnl_dollar'] < 0]

            long_trades = [t for t in trades if t['direction'] == 'long']
            short_trades = [t for t in trades if t['direction'] == 'short']

            win_rate = (len(winning_trades) / total_trades) * 100
            avg_win = np.mean([t['pnl_dollar'] for t in winning_trades]) if winning_trades else 0
            avg_loss = np.mean([t['pnl_dollar'] for t in losing_trades]) if losing_trades else 0

            total_wins = sum([t['pnl_dollar'] for t in winning_trades])
            total_losses = abs(sum([t['pnl_dollar'] for t in losing_trades]))
            profit_factor = total_wins / total_losses if total_losses > 0 else 0

            avg_rr = avg_win / self.risk_amount

            final_message += f"Final Balance: ${final_balance:,.2f}\n"
            final_message += f"Total P&L: ${total_pnl:.2f}\n"
            final_message += f"Total Return: {total_return:.2f}%\n"
            final_message += f"Winning Trades: {len(winning_trades)}\n"
            final_message += f"Losing Trades: {len(losing_trades)}\n"
            final_message += f"Win Rate: {win_rate:.2f}%\n"
            final_message += f"Average Win: ${avg_win:.2f}\n"
            final_message += f"Average Loss: ${avg_loss:.2f}\n"
            final_message += f"Profit Factor: {profit_factor:.2f}\n"
            final_message += f"Average R:R: {avg_rr:.2f}\n"  # Add average R:R

            final_message += f"Long Trades Taken: {len(long_trades)} / {len(trades)}\n"
            final_message += f"Short Trades Taken: {len(short_trades)} / {len(trades)}\n"
        else:
            final_message += "\nNo trades executed\n"
            final_message += "Final Balance: $10,000.00\n"
            final_message += "Total P&L: $0.00\n"
            final_message += "Total Return: 0.00%\n"

        final_message += "=" * 70
        final_message += f"\nFVGs: {self.strategy.fvg_count}"

        print(final_message)
        if self.telegram:
            send_telegram_message(final_message)


    def process_new_candle(self, ltf_data, htf_data, timestamp):
        print(f"Running iteration: time {timestamp}, balance: ${self.current_balance:.2f}")
        if self.current_position:
            print("In position currently")
            print(self.current_position.trade_df)
            self.current_position.trade_df = pd.concat([self.current_position.trade_df, ltf_data.tail(1)], ignore_index=True)
            current_price = ltf_data['close'].iloc[-1]
            self.strategy.update_trailing_stop(current_price=current_price, df=ltf_data, position=self.current_position, telegram=self.telegram)
            # Only in non live-version, otherwise training stop handled in real_live_trade.py
            return

        self.strategy.check_entry_conditions(ltf_data, htf_data)
        self.strategy.update_active_setups(ltf_data)


    def create_open_order(self, setup, trade_df, timestamp):
        risk_amount = self.risk_amount
        min_dist_percent = MIN_STOP_DISTANCE_COIN  # Use the config value

        entry_price = setup['fvg'].midpoint
        original_stop_distance = abs(entry_price - setup['stop_loss'])
        min_stop_distance = entry_price * min_dist_percent
        
        # Enforce the minimum stop distance
        if original_stop_distance < min_stop_distance:
            print(f"MINIMUM STOP DISTANCE ENFORCED: {original_stop_distance:.4f} → {min_stop_distance:.4f}")
            stop_distance = min_stop_distance
        else:
            stop_distance = original_stop_distance

        position = Position(
            symbol=self.symbol,
            risk_amount=risk_amount,
            stop_distance=stop_distance,
            entry_time=timestamp,
            direction=setup['direction'],
            trade_df=trade_df,
            fvg=setup['fvg'],
            indicator_type=setup['indicator_type'],
            indicator_time=setup['indicator_time'],
            larger_trend=setup['larger_trend'],
            trend_confidence=setup['trend_confidence'],
        )

        return position


    def check_position_opened(self, current_high, current_low):
        sorted_setups = sorted(
            self.strategy.active_setups,
            key = lambda setup: setup['fvg'].top,
            reverse=True,
        )

        sorted_setups = [setup for setup in sorted_setups if not setup['fvg'].filled]

        for setup in sorted_setups:
            fvg_midpoint = setup['fvg'].midpoint
            if current_high >= fvg_midpoint >= current_low:
                return setup
        return None


    def check_position_closed(self, current_price):
        if self.current_position.long:
            if current_price <= self.current_position.stop_loss:
                return True
        elif self.current_position.short:
            if current_price >= self.current_position.stop_loss:
                return True

        return False


    def handle_positions(self, ltf_data, current_price, current_open=None, current_high=None, current_low=None, current_time=None, trade_config="backtest"):
        if not self.current_position:
            position = self.check_position_opened(current_high, current_low)
            if position:
                print("POSITION FOUND")
                if self.telegram:
                    send_telegram_message(f"New position found at {current_time}")
                self.current_position = self.handle_position_open(position, current_time, ltf_data)
        else:
            if self.check_position_closed(current_price):
                if trade_config == "livetest":
                    last_candle = [{
                        'T': current_time,
                        'open': current_open,
                        'high': current_price,
                        'low': current_low,
                        'close': current_price,
                    }]
                    last_candle = pd.DataFrame(last_candle)
                    self.current_position.trade_df  = pd.concat([self.current_position.trade_df, last_candle], ignore_index=True)

                self.handle_position_close(current_time)


    def handle_position_open(self, setup, timestamp, ltf_data):
        print("OPEN ORDER BEING CALLED")
        time_since_fvg = int((timestamp - setup['fvg'].time).total_seconds() / 60)
        trade_df = ltf_data.tail(time_since_fvg + 20).reset_index(drop=True)

        position = self.create_open_order(setup, trade_df, timestamp)

        telegram_text = ""
        if self.symbol:
            telegram_text += f"===== New {self.symbol} Position Opened =====\n"
        else:
            telegram_text += "===== New Position Opened =====\n"
        telegram_text += f"Symbol: {self.symbol if self.symbol else 'N/A'}\n"
        telegram_text += f"Direction: {position.direction}\n"
        telegram_text += f"Entry price: ${position.entry_price:.4f}\n"
        telegram_text += f"Stop loss: ${position.stop_loss:.4f}\n"
        telegram_text += f"Position quantity: {position.quantity:.4f}\n"

        telegram_text += f"Full exposure: ${position.full_exposure:.2f}\n"
        telegram_text += f"Margin required: ${position.margin:.2f}\n"
        telegram_text += f"Entry Fees: ${position.entry_fees:.2f}\n"

        if self.telegram:
            send_telegram_message(telegram_text)
        print(telegram_text)
        self.strategy.active_setups = []

        return position


    def handle_position_close(self, timestamp):
        # Calculate dollar P&L
        exit_price = self.current_position.stop_loss
        price_diff = abs(exit_price - self.current_position.entry_price)
        pnl_dollar = self.current_position.quantity * price_diff

        if self.current_position.long and exit_price < self.current_position.entry_price:
            pnl_dollar *= -1
        elif self.current_position.short and exit_price > self.current_position.entry_price:
            pnl_dollar *= -1

        final_exposure = exit_price * self.current_position.quantity
        exit_fees = final_exposure * 0.00015
        total_fees = self.current_position.entry_fees + exit_fees
        pnl_dollar -= total_fees


        # Debug P&L calculation
        print(f"P&L Debug: Entry: ${self.current_position.entry_price:.4f}, Exit: ${exit_price:.4f}")
        print(f"P&L Debug: Price diff: ${price_diff:.4f}, Position size: {self.current_position.quantity:.4f}")
        print(f"P&L Debug: Raw P&L: ${pnl_dollar:.2f}")

        # Update balance
        self.current_balance += pnl_dollar

        # Record trade
        trade = {
            'entry_time': self.current_position.entry_time,
            'exit_time': timestamp,
            'direction': self.current_position.direction,
            'entry_price': self.current_position.entry_price,
            'exit_price': exit_price,
            'entry_fees': self.current_position.entry_fees,
            'exit_fees': exit_fees,
            'margin': self.current_position.margin,
            'total_fees': total_fees,
            'quantity': self.current_position.quantity,
            'indicator_time': self.current_position.indicator_time,
            'indicator_type': self.current_position.indicator_type,
            'fvg': self.current_position.fvg,
            'pnl_dollar': pnl_dollar,
        }
        self.trades.append(trade)
        print(f"Position closed: {pnl_dollar:.2f}")

        telegram_text = ""
        if self.symbol:
            telegram_text += f"===== {self.symbol} Position Closed =====\n"
        else:
            telegram_text += "===== Position Closed =====\n"
        telegram_text += f"Symbol: {self.symbol if self.symbol else 'N/A'}\n"
        telegram_text += f"Direction: {self.current_position.direction}\n"
        telegram_text += f"Exit price: ${trade['exit_price']:.4f}\n"
        telegram_text += f"Total time in trade: {trade['exit_time'] - trade['entry_time']}\n"
        telegram_text += f"P&L: ${pnl_dollar:.2f}\n"
        telegram_text += f"Total fees: ${trade['total_fees']:.2f}\n"
        telegram_text += f"Total trades taken: {len(self.trades)}\n"
        telegram_text += f"Current balance: ${self.current_balance:.2f}\n"

        if self.telegram:
            send_telegram_message(telegram_text)
        print(telegram_text)

        # Reset position
        self.current_position.create_candle_chart(exit_time=timestamp, pnl_dollar=pnl_dollar, id=len(self.trades), telegram=self.telegram)
        self.current_position = None
        self.last_position_close_time = timestamp


class LiveBaseTrader(BaseTrader):
    def __init__(self, symbol, telegram=True):
        self.dex = ccxt.hyperliquid({
            "walletAddress": HYPERLIQUID_ACCOUNT_ADDRESS,
            "privateKey": HYPERLIQUID_API_KEY,
        })
        balance = self.retrieve_balance()
        print(balance)

        super().__init__(symbol, balance, telegram)

        self.risk_amount = LIVE_RISK_AMOUNT
        self.strategy = LiveFVGStrategy(self.cancel_order_by_id, self.risk_amount)


    def retrieve_balance(self):
        try:
            result = self.dex.fetch_balance()
            return float(result['info']['marginSummary']['accountValue'])
        except Exception as e:
            print(f"Error retrieving balance: {e}")
            return 0


    def raise_alarm(self, error):
        send_telegram_message("Error in main loop, investigate immediately!")
        send_telegram_message(error)


    def cancel_order_by_id(self, order_id):
        try:
            current_open_orders = self.dex.fetch_open_orders(symbol=self.symbol)
            current_order_ids = [order['id'] for order in current_open_orders]
            if order_id in current_order_ids:
                result = self.dex.cancel_order(order_id, symbol=self.symbol)
                print(result)
        except Exception as e:
            print(f"Error cancelling order by id {order_id}: {e}")
            send_telegram_message(f"Error cancelling order by id {order_id}: {e}")


    def process_new_candle(self, ltf_data, htf_data, timestamp):
        print(f"Running iteration: time {timestamp}, balance: ${self.current_balance:.2f}")
        if self.current_position:
            print("In position currently")
            print(self.current_position.trade_df)
            self.current_position.trade_df = pd.concat([self.current_position.trade_df, ltf_data.tail(1)], ignore_index=True)
            return

        self.strategy.check_entry_conditions(ltf_data, htf_data)
        self.strategy.update_active_setups(ltf_data)


    def handle_position_close(self, timestamp, position):
        # Calculate dollar P&L
        exit_price = position.stop_loss
        price_diff = abs(exit_price - position.entry_price)
        pnl_dollar = position.quantity * price_diff

        if position.long and exit_price < position.entry_price:
            pnl_dollar *= -1
        elif position.short and exit_price > position.entry_price:
            pnl_dollar *= -1

        final_exposure = exit_price * position.quantity
        exit_fees = final_exposure * 0.00045
        total_fees = position.entry_fees + exit_fees
        pnl_dollar -= total_fees


        # Debug P&L calculation
        print(f"P&L Debug: Entry: ${position.entry_price:.4f}, Exit: ${exit_price:.4f}")
        print(f"P&L Debug: Price diff: ${price_diff:.4f}, Position size: {position.quantity:.4f}")
        print(f"P&L Debug: Raw P&L: ${pnl_dollar:.2f}")

        # Update balance
        self.current_balance = self.retrieve_balance()

        # Record trade
        trade = {
            'entry_time': position.entry_time,
            'exit_time': timestamp,
            'direction': position.direction,
            'entry_price': position.entry_price,
            'exit_price': exit_price,
            'entry_fees': position.entry_fees,
            'exit_fees': exit_fees,
            'margin': position.margin,
            'total_fees': total_fees,
            'quantity': position.quantity,
            'indicator_time': position.indicator_time,
            'indicator_type': position.indicator_type,
            'fvg': position.fvg,
            'pnl_dollar': pnl_dollar,
        }
        self.trades.append(trade)
        print(f"Position closed: {pnl_dollar:.2f}")

        telegram_text = ""
        if self.symbol:
            telegram_text += f"===== {self.symbol} Position Closed =====\n"
        else:
            telegram_text += "===== Position Closed =====\n"
        telegram_text += f"Symbol: {self.symbol if self.symbol else 'N/A'}\n"
        telegram_text += f"Direction: {position.direction}\n"
        telegram_text += f"Exit price: ${trade['exit_price']:.4f}\n"
        telegram_text += f"Total time in trade: {trade['exit_time'] - trade['entry_time']}\n"
        telegram_text += f"P&L: ${pnl_dollar:.2f}\n"
        telegram_text += f"Total fees: ${trade['total_fees']:.2f}\n"
        telegram_text += f"Total trades taken: {len(self.trades)}\n"
        telegram_text += f"Current balance: ${self.current_balance:.2f}\n"

        if self.telegram:
            send_telegram_message(telegram_text)
        print(telegram_text)

        # Reset position
        self.last_position_close_time = timestamp

        position.create_candle_chart(exit_time=timestamp, pnl_dollar=pnl_dollar, id=len(self.trades), telegram=self.telegram)