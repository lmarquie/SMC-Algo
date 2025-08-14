import asyncio
from backtest_trader import BacktestTrader
import numpy as np
import matplotlib.pyplot as plt
import os
import shutil
from helpers.fetch_data import fetch_hyperliquid_data, fetch_binance_data
import math

async def run_real_data_backtest(symbol, method):
    """Run the real data backtest for all 3 cryptocurrencies"""
    if method == "binance":
        data = await fetch_binance_data(symbol)
        data = data.iloc[:200_000]
    elif method == "hyperliquid":
        data = await fetch_hyperliquid_data(symbol)
    else:
        raise ValueError("Invalid method")

    data_length = len(data)

    trades = []
    backtester = BacktestTrader(symbol)
    trades += await backtester.run_backtest(data)

    print("\n" + "=" * 70)
    print("🏁 BACKTEST RESULTS")
    print("=" * 70)

    initial_balance = 10000
    total_trades = len(trades)

    print(f"Symbol Traded: {symbol}")
    print(f"Initial Balance: ${initial_balance:,.2f}")
    print(f"Total Trades: {total_trades}")

    if trades:
        symbol_pnl = sum(trade['pnl_dollar'] for trade in trades)
        symbol_trades = len(trades)
        symbol_wins = len([t for t in trades if t['pnl_dollar'] > 0])
        symbol_win_rate = (symbol_wins / symbol_trades) * 100 if symbol_trades > 0 else 0

        print(f"{symbol} Results:")
        print(f"  Trades: {symbol_trades}")
        print(f"  P&L: ${symbol_pnl:.2f}")
        print(f"  Win Rate: {symbol_win_rate:.1f}%")

        # Calculate overall performance
        total_pnl = sum(trade['pnl_dollar'] for trade in trades)
        final_balance = initial_balance + total_pnl
        total_return = (total_pnl / initial_balance) * 100

        winning_trades = [t for t in trades if t['pnl_dollar'] > 0]
        losing_trades = [t for t in trades if t['pnl_dollar'] < 0]

        win_rate = (len(winning_trades) / total_trades) * 100
        avg_win = np.mean([t['pnl_dollar'] for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t['pnl_dollar'] for t in losing_trades]) if losing_trades else 0

        total_wins = sum([t['pnl_dollar'] for t in winning_trades])
        total_losses = abs(sum([t['pnl_dollar'] for t in losing_trades]))
        profit_factor = total_wins / total_losses if total_losses > 0 else 0

        avg_rr = avg_win / 150

        print(f"Final Balance: ${final_balance:,.2f}")
        print(f"Total P&L: ${total_pnl:.2f}")
        print(f"Total Return: {total_return:.2f}%")
        print(f"Winning Trades: {len(winning_trades)}")
        print(f"Losing Trades: {len(losing_trades)}")
        print(f"Win Rate: {win_rate:.2f}%")
        print(f"Average Win: ${avg_win:.2f}")
        print(f"Average Loss: ${avg_loss:.2f}")
        print(f"Profit Factor: {profit_factor:.2f}")
        print(f"Average R:R: {avg_rr:.2f}")  # Add average R:R

        print(f"Long Trades Taken: {backtester.long_count} / {len(trades)}")
        print(f"Short Trades Taken: {backtester.short_count} / {len(trades)}")

        plt.plot(backtester.plot_indices, backtester.plot_opens, color="blue", alpha=0.5, linewidth=1)

        for trade in winning_trades:
            entry_idx = trade["entry_idx"]
            exit_idx = trade["exit_idx"]

            plt.plot([entry_idx, exit_idx],  # x-coordinates of the two points
                     [trade["entry_price"], trade["exit_price"]],  # y-coordinates of the two points
                     color='green', linestyle='--', linewidth=5)

        for trade in losing_trades:
            entry_idx = trade["entry_idx"]
            exit_idx = trade["exit_idx"]

            plt.plot([entry_idx, exit_idx],  # x-coordinates of the two points
                     [trade["entry_price"], trade["exit_price"]],  # y-coordinates of the two points
                     color='red', linestyle='--', linewidth=5)

        plt.savefig("backtest.png")
        plt.close()

        if os.path.exists('trades'):
            shutil.rmtree("trades")
        os.makedirs("trades", exist_ok=True)
        os.makedirs("trades/wins", exist_ok=True)
        os.makedirs("trades/losses", exist_ok=True)
        for i, trade in enumerate(trades):
            entry_idx = trade['entry_idx']
            exit_idx = trade['exit_idx']

            plt.figure(figsize=(15, 8))
            plt.margins(x=0.1)
            plt.tight_layout()
            fig, ax = plt.subplots()
            print(f"Trade {i + 1}, entry {entry_idx}, fvg index {trade['fvg']['start_idx']}")

            for idx in range(trade['fvg']['start_idx'] - 2, min(exit_idx+9, data_length - 1)):
                candle = data.iloc[idx]
                boxplot_data = [[candle["low"], candle["open"], candle["close"], candle["high"]]]

                if idx == entry_idx:
                    boxprops = {'facecolor': 'green', 'alpha': 1}
                elif idx == exit_idx:
                    boxprops = {'facecolor': 'red', 'alpha': 1}
                elif idx == data.index.get_loc(trade['fvg']['start_idx']):
                    boxprops = {'facecolor': 'orange', 'alpha': 1}
                elif idx in trade['mss']:
                    boxprops = {'facecolor': 'yellow', 'alpha': 1}
                elif idx in trade['bos']:
                    boxprops = {'facecolor': 'blue', 'alpha': 1}
                elif idx > entry_idx and idx < exit_idx:
                    if candle["close"] >= candle["open"]:
                        boxprops = {'facecolor': 'green', 'alpha': 0.4}
                    else:
                        boxprops = {'facecolor': 'red', 'alpha': 0.4}
                else:
                    boxprops = {'facecolor': 'white', 'alpha': 1.0}

                ax.boxplot(
                    boxplot_data,
                    positions=[(idx-entry_idx+1) * 3],
                    widths=2,
                    showfliers=False,
                    manage_ticks=True,
                    medianprops={'linewidth': 0},
                    boxprops=boxprops,
                    patch_artist=True,
                )

            trade_direction = trade['direction'].upper()
            trade_result = "WIN" if trade['pnl_dollar'] > 0 else "LOSS"
            plt.title(f"Trade #{entry_idx}: {trade_direction} - {trade_result} (${trade['pnl_dollar']:.2f})")

            ax.tick_params(axis='both', labelsize=6)
            plt.tight_layout()

            if trade in winning_trades:
                plt.savefig(f"trades/wins/trade_{i+1}.png", dpi=600, bbox_inches="tight")
            else:
                plt.savefig(f"trades/losses/trade_{i+1}.png", dpi=600, bbox_inches="tight")

            plt.close("all")

    else:
        print("No trades executed")
        print("Final Balance: $10,000.00")
        print("Total P&L: $0.00")
        print("Total Return: 0.00%")

    print("=" * 70)
    print(f"FVGs: {backtester.strategy.fvg_count}")


SYMBOL = "SOL"
METHOD = "binance"
if __name__ == "__main__":
    asyncio.run(run_real_data_backtest(SYMBOL, METHOD))