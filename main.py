from client.simulatedClient import SimulatedClient
from exchange.simulatedExchange import SimulatedExchange
from candleManager.simulatedCandleManager import SimulatedCandleManager
from models.PerformanceCalculator import PerformanceCalculator
from trading_logic.structure_analysis import StructureAnalyzer
from trading_logic.trading_strategy import FVGStrategy

from config import *
import shutil
import os
from datetime import timedelta

from models.direction import Direction
from models.side import Side
from models.candle import Candle
from models.position import Position

SYMBOL = 'SOL'
EXCHANGE = 'binance'
RISK_AMOUNT = 150
balance = 10_000

exchange = SimulatedExchange(balance=balance)
client = SimulatedClient(exchange=exchange)
candleManager = SimulatedCandleManager(EXCHANGE, SYMBOL)
strategy = FVGStrategy(client=client, risk_amount=RISK_AMOUNT)
analyzer = StructureAnalyzer(min_fvg_strength=0.0)
performanceCalculator = PerformanceCalculator(starting_balance=balance)
last_position_close_time = None

empty_candle = Candle(0,0,0,0)
tracked_position = None

shutil.rmtree('trades', ignore_errors=True)
os.makedirs('trades/wins', exist_ok=True)
os.makedirs('trades/losses', exist_ok=True)
num_trades = 0
for i in range(len(candleManager.future_data)):
    candleManager.process_candle(next_candle=empty_candle)
    current_candle = candleManager.ltf_data.iloc[-1]
    strategy.cancel_lagging_orders(current_candle['T'])

    found_positions = exchange.get_positions(high=current_candle['high'], low=current_candle['low'])
    if len(found_positions) == 1:
        found_position = found_positions[0]
        if not tracked_position:
            setup = strategy.most_recent_setup
            if (
                found_position['side'] == Side.BUY and setup.direction == Direction.SHORT
                or found_position['side'] == Side.SELL and setup.direction == Direction.LONG
            ):
                raise Exception("Found position does not match last order side")

            print("(main) New position entered")
            client.place_stop_market_order(
                quantity=found_position['quantity'],
                placement_time=current_candle['T'],
                side=Side.SELL if found_position['side'] == Side.SELL else Side.BUY,
                trigger_price=setup.initial_stop_loss,
            )

            tracked_position = Position(
                symbol=SYMBOL,
                risk_amount=RISK_AMOUNT,
                initial_stop_loss=setup.initial_stop_loss,
                entry_time=candleManager.ltf_data['T'].iloc[-1],
                direction=Direction.LONG if found_position['side'] == Side.BUY else Direction.SHORT,
                trade_df=candleManager.ltf_data[-50:],
                fvg=setup.fvg,
                mss_time=setup.mss_time,
                entry_timestamp=candleManager.ltf_data['T'].iloc[-1],
            )
            strategy.clear_setups()
            # TEMPORARY
            client.cancel_all_entry_orders()

        elif current_candle['T'] >= tracked_position.entry_time + timedelta(minutes=SWING_LOOKBACK_BACKWARD + SWING_LOOKBACK_FORWARD):
            #print("(main) Adding candle to existing position")
            tracked_position.add_candle(current_candle)

            stop_side = Side.SELL if found_position['side'] == Side.BUY else Side.BUY
            stop_lookback = int((candleManager.ltf_data['T'].iloc[-1] - tracked_position.entry_time).total_seconds() / 60)
            trigger_price = strategy.update_trailing_stop(
                current_stop=tracked_position.get_last_stop(),
                position=tracked_position,
                df=candleManager.ltf_data,
                candle=current_candle
            )
            if trigger_price:
                client.place_stop_market_order(
                    quantity=found_position['quantity'],
                    placement_time=current_candle['T'],
                    side=stop_side,
                    trigger_price=trigger_price
                )
                swing_idx = len(tracked_position.trade_df) - 1 - SWING_LOOKBACK_FORWARD
                tracked_position.add_stop_loss(swing_idx, tracked_position.get_idx(), trigger_price)
    else:
        if tracked_position:
            tracked_position.add_candle(current_candle)
            num_trades += 1
            pnl = tracked_position.calculate_pnl(tracked_position.get_last_stop())
            tracked_position.create_candle_chart(
                exit_time=current_candle['T'],
                pnl_dollar=pnl,
                id=num_trades,
                telegram=False
            )
            last_position_close_time = current_candle['T']
            fees = 18
            balance += pnl - fees
            performanceCalculator.add_trade(tracked_position, fees)
            tracked_position = None
            print(f"(main) Balance: ${balance:.2f}")

        if len(found_positions) > 1:
            client.force_close_all_positions()
            client.cancel_all_orders()
        else:
            if (not last_position_close_time
                or current_candle['T'] >= last_position_close_time + timedelta(minutes=20)):
                strategy.find_setups(candleManager.ltf_data, candleManager.htf_data)

performanceCalculator.log_performance()



