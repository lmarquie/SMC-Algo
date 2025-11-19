from models.fvg import FVG

class Setup:
    def __init__(
            self, entry_price, quantity, direction, stop_loss, 
            fvg: FVG, indicator_type, indicator_time, larger_trend,
            trend_confidence,
        ):
        self.entry_price = entry_price
        self.quantity = quantity,
        self.direction = direction
        self.stop_loss = stop_loss
        self.fvg = fvg
        self.indicator_type = indicator_type
        self.indicator_time = indicator_time
        self.larger_trend = larger_trend
        self.trend_confidence = trend_confidence
        self.oid = None
        self.expired = False

        self.long = (direction == 'long')
        self.short = (direction == 'short')