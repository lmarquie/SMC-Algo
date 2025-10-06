class FVG:
    def __init__(self, type, time, top, bottom, strength):
        self.type = type
        self.time = time
        self.top = top
        self.bottom = bottom
        self.strength = strength
        self.filled = False
        self.midpoint = (top + bottom) / 2
        self.bullish = (type == 'bullish')
        self.bearish = (type == 'bearish')

    def fill(self):
        self.filled = True