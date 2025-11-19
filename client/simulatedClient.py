from client.client import Client
from exchange.simulatedExchange import SimulatedExchange

class SimulatedClient(Client):
    def __init__(self, exchange: SimulatedExchange):
        super().__init__(exchange)
        self.id_counter = 0

    def place_limit_order(self, quantity, placement_time, side, entry_price):
        self.exchange.add_limit_order({
            "quantity": quantity,
            "placement_time": placement_time,
            "side": side,
            "entry_price": entry_price,
            "id": self.id_counter,
        })
        self.id_counter += 1

    def place_stop_market_order(self, quantity, placement_time, side, trigger_price):
        self.exchange.add_stop_market_order({
            "quantity": quantity,
            "placement_time": placement_time,
            "side": side,
            "trigger_price": trigger_price,
            "id": self.id_counter,
        })
        self.id_counter += 1


    def cancel_order(self, id):
        orders = self.exchange.get_limit_orders()
        for index, order in enumerate(orders):
            if order['id'] == id:
                print(f"(cancel_order) Cancelling order {id}")
                self.exchange.limit_orders.pop(index)

