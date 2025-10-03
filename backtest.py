import asyncio
from backtest_trader import BacktestTrader
import numpy as np
import matplotlib.pyplot as plt
import os
import shutil
from helpers.fetch_data import fetch_hyperliquid_data, fetch_binance_data
import math
from datetime import timedelta

async def run_real_data_backtest(symbol, method):
    """Run the real data backtest for all 3 cryptocurrencies"""
    if method == "binance":
        data = await fetch_binance_data(symbol)
        data = data.iloc[:200_000]
    elif method == "hyperliquid":
        data = await fetch_hyperliquid_data(symbol)

    else:
        raise ValueError("Invalid method")

    backtester = BacktestTrader(symbol)
    trades = await backtester.run_backtest(data)

    backtester.show_final_results(trades, "backtest")

SYMBOL = "SOL"
METHOD = "hyperliquid"
if __name__ == "__main__":
    asyncio.run(run_real_data_backtest(SYMBOL, METHOD))