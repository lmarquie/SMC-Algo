from models.fvg import FVG
from models.direction import Direction

class Setup:
    def __init__(
            self, entry_price, quantity, direction: Direction, initial_stop_loss,
            fvg: FVG, mss_time, larger_trend,
            trend_confidence,
        ):
        self.entry_price = entry_price
        self.quantity = quantity,
        self.direction = direction
        self.initial_stop_loss = initial_stop_loss
        self.fvg = fvg
        self.mss_time = mss_time
        self.larger_trend = larger_trend
        self.trend_confidence = trend_confidence
        self.oid = None
        self.expired = False

        self.long = direction == Direction.LONG
        self.short = direction == Direction.SHORT