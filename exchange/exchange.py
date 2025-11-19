class Exchange:
    def __init__(self, balance):
        self.balance = balance
        self.current_position = None
        self.limit_orders = []

    def get_balance(self):
        # returns: float for account balance
        pass

    def get_positions(self, high: float, low: float):
        # returns: list of position-related values
        #          [{ quantity: float
        #             side: string 'long' or 'short'
        #             entry_price: float }]
        pass

    def get_limit_orders(self):
        # returns: list of limit orders
        #          [{ quantity: float
        #             placement_time: Timestamp
        #             side: string 'buy' or 'sell'
        #             entry_price: float }]
        pass

    def get_stop_market_orders(self):
        # returns: list of stop market orders
        #          [{ quantity: float
        #             placement_time: Timestamp
        #             side: string 'buy' or 'sell'
        #             trigger_price: float }]
        pass

    def add_limit_order(self, order):
        # adds order to the list of limit orders
        #  * only available in simulated exchange
        pass

    def add_stop_market_order(self, order):
        # adds order to the list of stop market orders
        #  * only available in simulated exchange
        pass