import requests
import time
import pandas as pd
from datetime import datetime
import json


def fetch_ohlc(symbol: str, month_year: str, interval="1m"):
    month, year = month_year.split("_")
    start_dt = datetime(int(year), int(month), 1)
    if int(month) == 12:
        end_dt = datetime(int(year) + 1, 1, 1)
    else:
        end_dt = datetime(int(year), int(month) + 1, 1)

    start_ts = int(start_dt.timestamp() * 1000)
    end_ts = int(end_dt.timestamp() * 1000)

    url = "https://api.binance.com/api/v3/klines"
    limit = 1000
    data = []
    current_ts = start_ts

    while current_ts < end_ts:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_ts,
            "endTime": end_ts,
            "limit": limit
        }
        response = requests.get(url, params=params)
        response.raise_for_status()
        klines = response.json()
        if not klines:
            break
        data.extend(klines)
        last_open_time = klines[-1][0]
        current_ts = last_open_time + 60 * 1000
        print(f"Fetched {len(data)} candles for {month_year}")
        time.sleep(0.05)

    # Prepare JSON output list with selected fields
    json_data = []
    for k in data:
        json_data.append({
            "T": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4])
        })

    return json_data


# Global constants
SYMBOL = "SOLUSDT"  # Correct symbol format for Binance
months = [
    "08_2025",
    #"03_2025",
    #"04_2025",
    #"05_2025",
]

# Fetch and save
data = []
for month in months:
    data.extend(fetch_ohlc(SYMBOL, month))
    print(f"Successfully added {month} to data.")

json_filename = f"recent_sol.json"
with open(json_filename, "w") as f:
    json.dump(data, f, indent=2)

print(f"Saved {len(data)} records to {json_filename}")
