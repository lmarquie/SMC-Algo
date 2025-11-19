from exchange.exchange import Exchange

class Client:
    def __init__(self, exchange: Exchange):
        self.exchange = exchange

    def place_limit_order(self, quantity, placement_time, side, entry_price):
        # places limit order on exchange with specified details
        pass

    def place_stop_market_order(self, quantity, placement_time, side, trigger_price):
        # places a 'reduce-only' stop market order with specified details
        pass

    def cancel_order_by_id(self, id):
        # cancels order with specified id, returns None is order does not exist
        pass

    def cancel_all_orders(self):
        # cancels all orders
        pass

    def force_close_all_positions(self):
        # forces all positions to close
        pass

    def cancel_order(self, id):
        pass