import matplotlib.pyplot as plt
from helpers.telegram_setup import send_telegram_image
from config import *
import pandas as pd
import numpy as np

from models.fvg import FVG
from models.direction import Direction

class Position:
    def __init__(
            self,
            symbol,
            risk_amount,
            initial_stop_loss: float,
            entry_time,
            direction: Direction,
            trade_df,
            fvg: FVG,
            mss_time,
            entry_timestamp,
        ):
        self.symbol = symbol
        self.entry_time = entry_time
        self.direction = direction
        self.fvg = fvg
        self.fvg_idx = len(trade_df) - 1 - int((entry_time - fvg.time).total_seconds() / 60)
        if self.fvg_idx < 0:
            raise ValueError("(position) FVG too early: aborting program")
        self.mss_time = mss_time
        self.trade_df = trade_df
        self.entry_price = fvg.midpoint

        self.initial_stop_loss = initial_stop_loss
        stop_distance = abs(self.entry_price - initial_stop_loss)

        self.trade_df = trade_df
        self.quantity = risk_amount / stop_distance
        self.full_exposure = self.entry_price * self.quantity
        self.margin = self.full_exposure / MAX_LEVERAGE[symbol]
        self.entry_fees = self.full_exposure * 0.00015

        self.pnl = 0

        self.stop_losses = [{
            "swing_idx": 0,
            "placement_idx": 0,
            "value": initial_stop_loss
        }]

        self.month = entry_timestamp.strftime('%B')
        self.year = entry_timestamp.year


    def create_candle_chart(self, exit_time, pnl_dollar, id, telegram):
        self.pnl = pnl_dollar
        print("(create_candle_chart) Creating candle chart for trade")
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
            elif idx == self.fvg_idx:
                boxprops = {'facecolor': 'orange', 'alpha': 1}
            elif candle['T'] == self.mss_time:
                boxprops = {'facecolor': 'yellow', 'alpha': 1}
            elif self.entry_time < candle['T'] < exit_time:
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

        for stop_loss in self.stop_losses:
            swing_point = (stop_loss['swing_idx'] + 1) * 3
            placement_point = (stop_loss['placement_idx'] + 1) * 3
            value = stop_loss['value']
            end_point = (int(len(self.trade_df) + 1)) * 3
            plt.hlines(y=value, xmin=swing_point, xmax=end_point, color='black', linewidth=1, linestyle='--')
            plt.scatter(placement_point, value, s=5, color='black', marker='o')

        ax.set_xticks([])
        trade_direction = "LONG" if self.direction == Direction.LONG else "SHORT"
        trade_result = "WIN" if pnl_dollar > 0 else "LOSS"
        fig.suptitle(f"Trade #{self.entry_time}: {trade_direction} - {trade_result} (${pnl_dollar:.2f})")

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
        self.trade_df = pd.concat([self.trade_df, pd.DataFrame([candle])], ignore_index=True)

    def get_idx(self):
        return len(self.trade_df) - 1

    def add_stop_loss(self, swing_idx, placement_idx, value):
        self.stop_losses.append({
            "swing_idx": swing_idx,
            "placement_idx": placement_idx,
            "value": value
        })

    def calculate_pnl(self, price: float):
        if self.direction == Direction.LONG:
            return (price - self.entry_price) * self.quantity
        else:
            return (self.entry_price - price) * self.quantity

    # returns scalar value for the last stop loss placed
    def get_last_stop(self):
        if len(self.stop_losses) > 0:
            return self.stop_losses[-1]['value']
        else:
            return -1
