import matplotlib.pyplot as plt
from helpers.telegram_setup import send_telegram_image

class Position:
    def __init__(self, symbol, risk_amount, stop_distance, entry_time, direction, trade_df, fvg, indicator_type, indicator_time, larger_trend, trend_confidence):
        self.symbol = symbol
        self.entry_time = entry_time
        self.direction = direction
        self.fvg = fvg
        self.indicator_type = indicator_type
        self.indicator_time = indicator_time
        self.larger_trend = larger_trend
        self.trend_confidence = trend_confidence
        self.trade_df = trade_df

        self.long = direction == 'long'
        self.short = direction == 'short'
        self.entry_price = fvg.midpoint

        if self.long:
            stop_loss = self.entry_price - stop_distance
        else: # short
            stop_loss = self.entry_price + stop_distance

        self.stop_loss = stop_loss
        self.trade_df = trade_df
        self.quantity = risk_amount / stop_distance
        self.full_exposure = self.entry_price * self.quantity
        self.margin = self.full_exposure / MAX_LEVERAGE[symbol]
        self.entry_fees = self.full_exposure * 0.00015

        self.mss = indicator_type == 'mss'
        self.bos = indicator_type == 'bos'

        self.setup_volumes = {}


    def create_candle_chart(self, exit_time, pnl_dollar, id, telegram):

        plt.figure(figsize=(18, 6))
        plt.margins(x=0.1)
        plt.margins(y=0.1)
        plt.tight_layout()
        fig, ax = plt.subplots()
        print(f"Trade {id}, entry {self.entry_time}, fvg index {self.fvg.time}")

        for idx in range(0, len(self.trade_df)):
            candle = self.trade_df.iloc[idx]
            boxplot_data = [[candle["low"], candle["open"], candle["close"], candle["high"]]]

            if candle['T'] == self.entry_time:
                boxprops = {'facecolor': 'green', 'alpha': 1}
            elif idx == len(self.trade_df) - 1:
                boxprops = {'facecolor': 'red', 'alpha': 1}
            elif candle['T'] == self.fvg.time:
                boxprops = {'facecolor': 'orange', 'alpha': 1}
            elif candle['T'] == self.indicator_time:
                color = 'yellow' if self.mss else 'blue'
                boxprops = {'facecolor': color, 'alpha': 1}
            elif candle['T'] > self.entry_time and candle['T'] < exit_time:
                if candle["close"] >= candle["open"]:
                    boxprops = {'facecolor': 'green', 'alpha': 0.4}
                else:
                    boxprops = {'facecolor': 'red', 'alpha': 0.4}
            else:
                boxprops = {'facecolor': 'white', 'alpha': 1.0}

            ax.boxplot(
                boxplot_data,
                positions=[(idx + 1) * 3],
                widths=2,
                showfliers=False,
                manage_ticks=True,
                medianprops={'linewidth': 0},
                boxprops=boxprops,
                patch_artist=True,
            )

        ax.set_xticks([])
        trade_direction = self.direction.upper()
        trade_result = "WIN" if pnl_dollar > 0 else "LOSS"
        fig.suptitle(f"Trade #{self.entry_time}: {trade_direction} - {trade_result} (${pnl_dollar:.2f})")

        vol_subtitle = ""
        for lookback, total_volume in self.setup_volumes.items():
            vol_subtitle += f"{lookback} min volume: {total_volume:.4f}.    "
        ax.set_title(vol_subtitle, fontsize=12, color="gray")
        


        ax.tick_params(axis='both', labelsize=6)
        plt.tight_layout()

        if trade_result == "WIN":
            plt.savefig(f"trades/wins/trade_{id}.png", dpi=400, bbox_inches="tight")
            plt.close("all")
            if telegram:
                send_telegram_image(f"trades/wins/trade_{id}.png")
        else:
            plt.savefig(f"trades/losses/trade_{id}.png", dpi=400, bbox_inches="tight")
            plt.close("all")
            if telegram:
                send_telegram_image(f"trades/losses/trade_{id}.png")

    def add_candle(self, candle):
        self.trade_df = pd.concat([self.trade_df, candle], ignore_index=True)

    def add_setup_volume(self, lookback, total_volume):
        self.setup_volumes[f'volume_{lookback}'] = total_volume