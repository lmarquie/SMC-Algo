from client.simulatedClient import SimulatedClient
from exchange.simulatedExchange import SimulatedExchange
from candleManager.simulatedCandleManager import SimulatedCandleManager
from trading_logic.structure_analysis import StructureAnalyzer
from trading_logic.trading_strategy import FVGStrategy
from models.candle import Candle
from models.position import Position
from config import *
import shutil
import os

SYMBOL = 'SOL'
EXCHANGE = 'binance'
RISK_AMOUNT = 150
balance = 10_000

exchange = SimulatedExchange(balance=balance)
client = SimulatedClient(exchange=exchange)
candleManager = SimulatedCandleManager(EXCHANGE, SYMBOL)
strategy = FVGStrategy(client=client, risk_amount=RISK_AMOUNT)
analyzer = StructureAnalyzer(min_fvg_strength=0.0)

empty_candle = Candle(0,0,0,0)
tracked_position = None

shutil.rmtree('trades', ignore_errors=True)
os.makedirs('trades/wins', exist_ok=True)
os.makedirs('trades/losses', exist_ok=True)
num_trades = 0
for i in range(len(candleManager.future_data)):
    candleManager.process_candle(next_candle=empty_candle)
    current_candle = candleManager.ltf_data.iloc[-1]

    strategy.update_fvgs(df=candleManager.ltf_data)
    strategy.cancel_lagging_orders(current_candle['T'])

    found_positions = exchange.get_positions(high=current_candle['high'], low=current_candle['low'])
    if len(found_positions) == 1:
        found_position = found_positions[0]
        if not tracked_position:
            setup = strategy.last_order_placed
            print("(main) New position entered")
            client.place_stop_market_order(
                quantity=found_position['quantity'],
                placement_time=current_candle['T'],
                side="sell" if found_position['side'] == "buy" else "buy",
                trigger_price=setup.stop_loss,
            )

            tracked_position = Position(
                symbol=SYMBOL,
                risk_amount=RISK_AMOUNT,
                initial_stop_loss=setup.stop_loss,
                entry_time=candleManager.ltf_data['T'].iloc[-1],
                side=found_position['side'],
                trade_df=candleManager.ltf_data[-20:],
                fvg=setup.fvg,
                indicator_type=setup.indicator_type,
                indicator_time=setup.indicator_time,
                larger_trend=setup.larger_trend,
                trend_confidence=setup.trend_confidence,
            )
        else:
            print("(main) Adding candle to existing position")
            tracked_position.add_candle(current_candle)

            stop_side = 'buy' if found_position['side'] == 'long' else 'sell'
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
            tracked_position.create_candle_chart(
                exit_time=candleManager.ltf_data.iloc[-1]['T'],
                pnl_dollar=tracked_position.calculate_pnl(tracked_position.get_last_stop()),
                id=num_trades,
                telegram=False
            )
            balance += tracked_position.calculate_pnl(tracked_position.get_last_stop())
            tracked_position = None
            print(f"(main) Balance: ${balance:.2f}")

        if len(found_positions) > 1:
            client.force_close_all_positions()
            client.cancel_all_orders()
        else:
            strategy.check_entry_conditions(candleManager.ltf_data, candleManager.htf_data)

print("(main) Final balance: " + str(balance))



