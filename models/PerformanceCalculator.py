import numpy as np
from datetime import datetime

class PerformanceCalculator:
    def __init__(self, starting_balance):
        self.winning_trades = []
        self.losing_trades = []
        self.starting_balance = starting_balance
        self.balance = starting_balance
        self.trades = []
        self.trade_stop_amts = []


    def add_trade(self, trade, fees):
        self.trades.append(trade)
        if trade.pnl > 0:
            self.winning_trades.append(trade)
        else:
            self.losing_trades.append(trade)
        self.balance += trade.pnl - fees
        trade.pnl -= fees
        self.trade_stop_amts.append(len(trade.stop_losses))


    def log_performance(self):
        pnl = self.balance - self.starting_balance
        print(f"Initial Balance: ${self.starting_balance:.2f}")
        print(f"Final Balance: ${self.balance:.2f}")
        print(f"PNL: ${pnl:.2f}")

        average_win = np.mean([winning_trade.pnl for winning_trade in self.winning_trades])
        average_loss = np.mean([losing_trade.pnl for losing_trade in self.losing_trades])
        average_pnl = np.mean([trade.pnl for trade in self.trades])

        print(f"Average Win: ${average_win:.2f}")
        print(f"Average Loss: ${average_loss:.2f}")
        print(f"Average PNL: ${average_pnl:.2f}")

        print(f"Average stop losses placed per trade: {np.mean(self.trade_stop_amts):.2f}")

        total_trades = len(self.winning_trades) + len(self.losing_trades)
        print(f"Total Trades: {total_trades}")
        if total_trades > 0:
            win_ratio = len(self.winning_trades) / total_trades * 100
            print(f"Win Rate: {win_ratio:.2f}%")

        total_return = (self.balance / self.starting_balance - 1) * 100
        print(f"Total Return: {total_return:.2f}%\n")

        print("Results by Month:")

        # Group trades by month/year
        monthly_returns = {}
        all_trades = self.winning_trades + self.losing_trades

        for trade in all_trades:
            key = (trade.year, trade.month)
            if key not in monthly_returns:
                monthly_returns[key] = 0
            monthly_returns[key] += trade.pnl

        if monthly_returns:
            returns_list = list(monthly_returns.values())
            avg_monthly_return = np.mean(returns_list)
            std_monthly_return = np.std(returns_list)

            best_month_key = max(monthly_returns, key=monthly_returns.get)
            best_month_return = monthly_returns[best_month_key]

            worst_month_key = min(monthly_returns, key=monthly_returns.get)
            worst_month_return = monthly_returns[worst_month_key]

            # Print each month's return (sorted chronologically)
            month_order = {
                'January': 1, 'February': 2, 'March': 3, 'April': 4,
                'May': 5, 'June': 6, 'July': 7, 'August': 8,
                'September': 9, 'October': 10, 'November': 11, 'December': 12
            }
            sorted_months = sorted(monthly_returns.items(), key=lambda x: (x[0][0], month_order.get(x[0][1], 0)))
            for (year, month), pnl in sorted_months:
                print(f"{month} {year}: ${pnl:.2f}")

            print(f"\nAverage monthly return: ${avg_monthly_return:.2f}, std ${std_monthly_return:.2f}")
            print(f"Best month: {best_month_key[1]} {best_month_key[0]} (${best_month_return:.2f})")
            print(f"Worst month: {worst_month_key[1]} {worst_month_key[0]} (${worst_month_return:.2f})")


