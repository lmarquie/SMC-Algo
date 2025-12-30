from exchange.exchange import Exchange
from models.side import Side
from models.direction import Direction

class SimulatedExchange(Exchange):
    def __init__(self, balance):
        super().__init__(balance)
        self.stop_market_orders = []

    def get_balance(self):
        # returns: float for account balance
        return self.balance

    # returns True if position has exited, else False
    def check_position_exited(self, high: float, low: float):
        if self.current_position and len(self.stop_market_orders) == 1:
            stop_market_order = self.stop_market_orders[0]
            stop_side = stop_market_order['side']
            stop_trigger_price = stop_market_order['trigger_price']
            if stop_side == Side.BUY and high >= stop_trigger_price:
                return True
            elif stop_side == Side.SELL and low <= stop_trigger_price:
                return True
        return False


    def get_positions(self, high: float, low: float):
        # returns: dictionary of position-related values
        #          {{ quantity: float
        #            side: Side
        #            entry_price: float }}
        limit_orders = self.get_limit_orders()
        if self.current_position:
            if not self.check_position_exited(high, low):
                return [self.current_position]
            else:
                print("(get_positions) position exited")
                self.current_position = None
                return []
        elif len(limit_orders) == 0:
            return []
        else:
            sorted_orders = sorted(
                limit_orders,
                key = lambda limit_order: limit_order['entry_price'],
                reverse=True,
            )
            for order in sorted_orders:
                if high >= order['entry_price'] >= low:
                    self.current_position = {
                        'quantity': order['quantity'],
                        'side': "long" if order['side'] == "buy" else "short",
                        'entry_price': order['entry_price'],
                    }
            if self.current_position:
                return [self.current_position]
            else:
                return []


    def get_limit_orders(self):
        # returns: dictionary of orders
        #          {{ quantity: float
        #             placement_time: float
        #             side: string 'buy' or 'sell'
        #             entry_price: float
        #             id: int }}
        return self.limit_orders


    def get_stop_market_orders(self):
        return self.stop_market_orders


    def add_limit_order(self, limit_order):
        self.limit_orders.append(limit_order)


    def add_stop_market_order(self, stop_market_order):
        print("(add_stop_market_order) stop market order added")
        self.stop_market_orders = [stop_market_order]