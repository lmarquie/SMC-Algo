import json
import pandas as pd
from helpers.hyperliquid_client import HyperliquidClient

async def fetch_binance_data(symbol):
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


async def fetch_hyperliquid_data(symbol):
    # Fetch exactly 5000 candles
    target_candles = 5000

    client = HyperliquidClient(api_key="0xa90b4285bc34a56a8b102b71d18bd2a82f7e7b464965e5d3a9e064f4eb7ad4df")

    # Fetch the most recent 5000 candles
    df = await client.get_ohlcv(
        f"{symbol}",
        timeframe="1m",
        limit=target_candles,
    )

    # Ensure we have exactly 5000 candles (or as many as available)
    if len(df) > target_candles:
        df = df.tail(target_candles)

    df = df[["open", "high", "low", "close", "T"]]
    df['T'] = pd.to_datetime(df['T'], unit='ms')
    df = df.reset_index(drop=True)

    print(f"✅ Successfully fetched {len(df)} real candles for {symbol}")
    return df

