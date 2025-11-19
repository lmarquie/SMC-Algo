import json
import pandas as pd
import ccxt
from datetime import datetime
from credentials import HYPERLIQUID_ACCOUNT_ADDRESS, HYPERLIQUID_API_KEY

def fetch_binance_data(symbol):
    with open(f'recent_{symbol.lower()}.json', 'r') as f:
        aggs_list = json.load(f)

    df = pd.DataFrame()

    # Create DataFrame with required columns
    df['open'] = [agg['open'] for agg in aggs_list]
    df['high'] = [agg['high'] for agg in aggs_list]
    df['low'] = [agg['low'] for agg in aggs_list]
    df['close'] = [agg['close'] for agg in aggs_list]
    df['T'] = [agg['T'] for agg in aggs_list]

    # Convert timestamp to datetime and set as index
    df['T'] = pd.to_datetime(df['T'], unit='ms')
    # df = df.tail(50_000)
    df = df.reset_index(drop=True)

    print(f"✅ Successfully loaded {len(df)} candles from JSON file for {symbol}")
    return df


def fetch_hyperliquid_data(symbol):
    # Fetch exactly 5000 candles
    target_candles = 5000

    dex = ccxt.hyperliquid({
        "walletAddress": HYPERLIQUID_ACCOUNT_ADDRESS,
        "privateKey": HYPERLIQUID_API_KEY,
    })

    # Fetch the most recent 5000 candles
    data = dex.fetch_ohlcv(symbol + '/USDC:USDC', timeframe='1m', limit=5000)

    data_list = []

    for index in range(0, 5000):
        data_list.append({
            "T": datetime.fromtimestamp(data[index][0] / 1000),
            "open": data[index][1],
            "high": data[index][2],
            "low": data[index][3],
            "close": data[index][4],
        })

    return pd.DataFrame(data_list)

