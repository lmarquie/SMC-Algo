import pandas as pd
from helpers.hyperliquid_client import HyperliquidClient
from helpers.telegram_setup import send_telegram_message, is_stop_requested, send_telegram_image
from config import *
from credentials import *
from datetime import datetime, timedelta
import asyncio
from base_trader import LiveBaseTrader
import ssl
import certifi
import matplotlib.pyplot as plt
import os
import shutil
import ccxt
import numpy as np
import traceback

class LiveTrader(LiveBaseTrader):
    def __init__(self, symbol):
        super().__init__(symbol, telegram=True)

        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

        self.working_candle = None
        self.full_data = pd.DataFrame()
        self.ltf_data = pd.DataFrame()
        self.htf_data = pd.DataFrame()

        self.current_price = 0

        try:
            result = self.dex.set_leverage(leverage=MAX_LEVERAGE[self.symbol], symbol=symbol)
            if result['status'] != 'ok':
                print(f"Error setting leverage: {result}")
        except Exception as e:
            print(f"Error setting leverage: {e}")


    async def fetch_initial_data(self):
        try:
            ltf_data = self.dex.fetch_ohlcv(self.symbol, timeframe='1m', limit=self.ltf_lookback)
            htf_data = self.dex.fetch_ohlcv(self.symbol, timeframe='15m',
                                            limit=self.ltf_lookback)

            ltf_list = []
            htf_list = []

            for index in range(len(ltf_data) - self.ltf_lookback - 1, len(ltf_data) - 1):
                ltf_list.append({
                    "T": datetime.fromtimestamp(ltf_data[index][0] / 1000),
                    "open": ltf_data[index][1],
                    "high": ltf_data[index][2],
                    "low": ltf_data[index][3],
                    "close": ltf_data[index][4],
                    "volume": ltf_data[index][5] if len(ltf_data[index]) > 5 else 0,
                })
            for index in range(len(htf_data) - self.htf_lookback - 1, len(htf_data) - 1):
                htf_list.append({
                    "T": datetime.fromtimestamp(htf_data[index][0] / 1000),
                    "open": htf_data[index][1],
                    "high": htf_data[index][2],
                    "low": htf_data[index][3],
                    "close": htf_data[index][4],
                    "volume": htf_data[index][5] if len(htf_data[index]) > 5 else 0,
                })

            ltf_df = pd.DataFrame(ltf_list)
            htf_df = pd.DataFrame(htf_list)

            return ltf_df, htf_df

        except Exception as e:
            print(f"🔴 FULL ERROR in fetch_initial_data: {e}")
            import traceback
            traceback.print_exc()
            return None, None


    def place_order(self, setup):
        ticker = self.dex.fetch_ticker(self.symbol)
        current_price = ticker['last']
        best_ask = ticker['ask']

        if setup['direction'] == 'long':
            # Safety checks
            if current_price <= setup['entry_price']:
                print("Entry price is too high, skipping order")
                print(f"Entry price {setup['entry_price']}, current price {current_price}")
                setup['filled'] = True
                return

            # Place order
            try:
                print(f"Quantity: {setup['quantity']}")
                print(f"Required Margin: {(setup['quantity'] * setup['entry_price']) / MAX_LEVERAGE[self.symbol]}")
                result = self.dex.create_order(
                    symbol=self.symbol,
                    type='limit',
                    side='buy',
                    amount=setup['quantity'],
                    price=setup['entry_price'],
                )
                setup['oid'] = result['id']

            except Exception as e:
                print(f"Error placing order: {e}")
                self.raise_alarm(f"Error placing order: {e}")

        else:
            # Safety checks
            if current_price >= setup['entry_price']:
                print("Entry price is too low, skipping order")
                print(f"Entry price {setup['entry_price']}, current price {current_price}")
                setup['filled'] = True
                return

            # Place order
            try:
                print(f"Quantity: {setup['quantity']}")
                print(f"Required Margin: {(setup['quantity'] * setup['entry_price']) / MAX_LEVERAGE[self.symbol]}")
                result = self.dex.create_order(
                    symbol=self.symbol,
                    type='limit',
                    side='sell',
                    amount=setup['quantity'],
                    price=setup['entry_price'],
                )
                setup['oid'] = result['id']

            except Exception as e:
                print(f"Error placing order: {e}")
                self.raise_alarm(f"Error placing order: {e}")


    def update_position_entered(self):
        if not self.current_position:
            positions = self.dex.fetch_positions()
            if len(positions) > 1:
                print(f"Multiple positions found, closing all: {positions}")
                self.raise_alarm("Multiple positions found, closing all")
                self.close_all_open_positions()
            elif len(positions) == 1:
                print(f"Current position: {positions[0]}")
                ccxt_pos = positions[0]
                
                # Create position object from actual Hyperliquid position
                # Calculate stop loss based on fixed risk amount, not percentage
                entry_price = ccxt_pos['entryPrice']
                quantity = abs(ccxt_pos['contracts'])
                
                # Calculate stop distance based on fixed risk amount
                stop_distance = self.risk_amount / quantity
                
                if ccxt_pos['side'] == 'long':
                    stop_loss = entry_price - stop_distance
                else:  # short
                    stop_loss = entry_price + stop_distance
                
                position = {
                    'direction': 'long' if ccxt_pos['side'] == 'long' else 'short',
                    'entry_price': entry_price,
                    'quantity': quantity,
                    'stop_loss': stop_loss,
                    'entry_time': datetime.now(),
                    'fvg': None,
                    'indicator_type': 'manual',
                    'indicator_time': datetime.now(),
                    'larger_trend': 'neutral',
                    'trend_confidence': 0.1,
                    'oid': None,
                    'filled': True,
                    'entry_fees': 0,
                    'margin': 0,
                    'full_exposure': entry_price * quantity,
                }
                
                self.current_position = position
                
                # Send position notification
                telegram_text = f"===== POSITION DETECTED =====\n"
                telegram_text += f"Symbol: {self.symbol}\n"
                telegram_text += f"Direction: {position['direction']}\n"
                telegram_text += f"Entry price: ${position['entry_price']:.4f}\n"
                telegram_text += f"Quantity: {position['quantity']:.4f}\n"
                telegram_text += f"Full exposure: ${position['full_exposure']:.2f}\n"
                telegram_text += f"Stop loss: ${position['stop_loss']:.4f}\n"
                
                if self.telegram:
                    send_telegram_message(telegram_text)
                print(telegram_text)

                # Cancel all open orders, then place a REAL stop loss
                self.cancel_all_open_orders()
                self.place_stop_loss_order(
                    position_direction=position['direction'],
                    stop_price=position['stop_loss'],
                    quantity=position['quantity'],
                )
        if self.current_position:
            positions = self.dex.fetch_positions()
            if len(positions) == 0:
                print("Position closed")
                self.current_position = None
            elif len(positions) == 1 and self.current_position:
                print(f"Current position: {self.current_position}")
            elif len(positions) > 1:
                print(f"Multiple positions found, closing all: {positions}")
                self.raise_alarm("Multiple positions found, closing all")
                self.close_all_open_positions()
            else:
                print("Unexpected error, position not logged correctly, closing all orders and positions")
                self.raise_alarm("Unexpected error, position not logged correctly, closing all orders and positions")
                self.cancel_all_open_orders()
                self.close_all_open_positions()




    def find_matching_position(self, ccxt_position):
        try:
            valid_setups = [setup for setup in self.strategy.active_setups if setup['oid']]
            if ccxt_position['side'] == 'long':
                long_setups = [setup for setup in valid_setups if setup['direction'] == 'long']
                if not long_setups:
                    print("No long setups found for matching position")
                    return None
                highest_long_setup = max(long_setups, key=lambda setup: setup['entry_price'])
                return highest_long_setup

            else:
                short_setups = [setup for setup in valid_setups if setup['direction'] == 'short']
                if not short_setups:
                    print("No short setups found for matching position")
                    return None
                lowest_short_setup = min(short_setups, key=lambda setup: setup['entry_price'])
                return lowest_short_setup
        except Exception as e:
            print(f"Error finding matching position: {e}")
            self.raise_alarm(f"Error finding matching position: {e}")
            return None


    def place_stop_loss_order(self, position_direction, stop_price, quantity):
        try:
            print(f"Placing stop loss order for {position_direction} at {stop_price}")
            if position_direction == 'long':
                side = 'sell'
                slippage_price = stop_price + 1
            else:
                side = 'buy'
                slippage_price = stop_price - 1


            result = self.dex.create_order(
                symbol=self.symbol,
                type='market',
                side=side,
                amount=quantity,
                price=slippage_price,
                params={
                    'triggerPrice': stop_price,
                    'reduceOnly': True
                }
            )
            print(f"✅ STOP LOSS ORDER PLACED: {result}")
            
            # Send confirmation to Telegram
            if self.telegram:
                telegram_text = f"🛡️ STOP LOSS ORDER PLACED\n"
                telegram_text += f"Direction: {position_direction}\n"
                telegram_text += f"Stop Price: ${stop_price:.4f}\n"
                telegram_text += f"Quantity: {quantity:.4f}\n"
                telegram_text += f"Order ID: {result.get('id', 'N/A')}\n"
                send_telegram_message(telegram_text)
        except Exception as e:
            print(f"Error placing stop loss order: {e}")
            self.raise_alarm(f"Error placing stop loss order: {e}")


    def manage_position_stops(self):
        try:
            print("Managing current position")
            current_price = self.dex.fetch_ticker(self.symbol)['last']
            old_stop = self.current_position['stop_loss']
            self.strategy.update_trailing_stop(current_price=current_price, df=self.ltf_data, position=self.current_position, telegram=self.telegram)
            new_stop = self.current_position['stop_loss']

            if old_stop != new_stop:
                print(f"New stop loss: {new_stop}")
                self.place_stop_loss_order(
                    position_direction=self.current_position['direction'],
                    stop_price=self.current_position['stop_loss'],
                    quantity=self.current_position['quantity'],
                )
        except Exception as e:
            print(f"Error managing position stops: {e}")
            self.raise_alarm(f"Error managing position stops: {e}")


    def manage_orders(self):
        if len(self.strategy.active_setups) == 0:
            return

        current_orders = self.dex.fetch_open_orders()
        current_order_ids = [order['id'] for order in current_orders]

        best_setup = max(self.strategy.active_setups, key=lambda x: x['entry_price'])
        for setup in self.strategy.active_setups:
            if setup != best_setup:
                setup['oid'] = None

        if not best_setup['oid'] in current_order_ids:
            self.place_order(best_setup)

        updated_orders = self.dex.fetch_open_orders()
        orders_to_cancel = [order for order in updated_orders if order['id'] != best_setup['oid']]
        for order in orders_to_cancel:
            result = self.dex.cancel_order(order['id'], symbol=self.symbol)
            if result['status'] != 'success':
                self.raise_alarm(f"Error cancelling order (manage orders): {result}")


    def cancel_all_open_orders(self):
        orders = self.dex.fetch_open_orders()
        for order in orders:
            result = self.dex.cancel_order(order['id'], symbol=self.symbol)
            print(result)
            if result['status'] != 'success':
                self.raise_alarm(f"Error cancelling all open orders: {result}")


    def close_all_open_positions(self):
        positions = self.dex.fetch_positions()
        for position in positions:
            size = float(position['contracts'])
            current_price = self.dex.fetch_ticker(self.symbol)['last']
            order_side = position['side']
            try:
                result = self.dex.create_order(
                    symbol=self.symbol,
                    type='market',
                    side='sell' if order_side == 'long' else 'buy',
                    amount=size,
                    price=current_price,
                    params={'reduceOnly': True}
                )
                print(result)
            except Exception as e:
                print(f"Error closing position: {e}")
                self.raise_alarm(f"Error closing position: {e}")


    def handle_candle_data(self, ltf_candle, htf_candle, current_time):
        last_ltf_time = self.ltf_data['T'].iloc[-1]
        last_htf_time = self.htf_data['T'].iloc[-1]

        if htf_candle['T'] > last_htf_time:
            self.htf_data = pd.concat([self.htf_data, pd.DataFrame([htf_candle])], ignore_index=True)
            self.htf_data = self.htf_data.iloc[-self.htf_lookback:].reset_index(drop=True)
        if ltf_candle['T'] > last_ltf_time:
            self.ltf_data = pd.concat([self.ltf_data, pd.DataFrame([ltf_candle])], ignore_index=True)
            self.ltf_data = self.ltf_data.iloc[-self.ltf_lookback:].reset_index(drop=True)

            larger_trend = self.strategy.identify_larger_trend(self.htf_data)
            print("Processing new candle")
            print("======================")
            print(f"Larger trend: {larger_trend}")
            print(f"Active fvg count: {len(self.strategy.active_fvgs)}")
            print(f"Active fvgs: {self.strategy.active_fvgs}")
            print(f"Active setup count: {len(self.strategy.active_setups)}")
            print(f"Active setups: {self.strategy.active_setups}")

            print("======================")
            self.process_new_candle(ltf_data=self.ltf_data, htf_data=self.htf_data, timestamp=current_time)


    async def run_trading_loop(self, duration_minutes=None):
        """Run paper trading indefinitely or for specified duration"""

        if duration_minutes:
            message = f"🚀 Starting {self.symbol} paper trading for {duration_minutes} minutes...\n"
            print(message)
            start_time = datetime.now()
            end_time = start_time + timedelta(minutes=duration_minutes)
        else:
            message = f"🚀 Starting {self.symbol} paper trading INDEFINITELY...\n"
            print(message)
            print("Press Ctrl+C to stop the bot")
            end_time = None

        print(f"Trading symbol: {self.symbol}")
        message += f"Trading symbol: {self.symbol}\n"
        print(f"Risk per trade: ${self.risk_amount}")
        message += f"Risk per trade: {RISK_PER_TRADE}"

        send_telegram_message(message)
        self.ltf_data, self.htf_data = await self.fetch_initial_data()
        self.full_data = self.ltf_data

        while True:
            if end_time and datetime.now() >= end_time:
                break
            elif is_stop_requested():
                send_telegram_message("🛑 Bot stopped by user")
                break

            try:
                ltf_candle = self.dex.fetch_ohlcv(self.symbol, timeframe='1m', limit=2)
                htf_candle = self.dex.fetch_ohlcv(self.symbol, timeframe='15m', limit=2)

                current_high = ltf_candle[-1][2]
                current_low = ltf_candle[-1][3]
                current_time = datetime.fromtimestamp(ltf_candle[-1][0] / 1000)

                ltf_candle = {
                    'T': datetime.fromtimestamp(ltf_candle[0][0] / 1000),
                    'open': ltf_candle[0][1],
                    'high': ltf_candle[0][2],
                    'low': ltf_candle[0][3],
                    'close': ltf_candle[0][4],
                }
                htf_candle = {
                    'T': datetime.fromtimestamp(htf_candle[0][0] / 1000),
                    'open': htf_candle[0][1],
                    'high': htf_candle[0][2],
                    'low': htf_candle[0][3],
                    'close': htf_candle[0][4],
                }

                current_ticker = self.dex.fetch_ticker(self.symbol)
                self.current_price = current_ticker['last']
                self.handle_candle_data(ltf_candle, htf_candle, current_time)

                old_position = self.current_position
                self.update_position_entered() # Updates self.current_position if a position is entered on hyperliquid
                if self.current_position:
                    self.manage_position_stops()
                else:
                    self.manage_orders()

                if not self.current_position and old_position:
                    self.handle_position_close(datetime.now(), position=old_position)

                await asyncio.sleep(2)

            except Exception as e:
                print(f"🔴 FULL ERROR in main loop: {e}")
                traceback.print_exc()
                print(f"Attempting to continue in 15 seconds...")
                await asyncio.sleep(15)


        if self.current_position:
            self.handle_position_close(datetime.now(), self.current_position)

        self.show_final_results(self.trades, "live test")

trader = LiveTrader("SOL/USDC:USDC")
asyncio.run(trader.run_trading_loop(duration_minutes=60*24*7))  # 7 days
