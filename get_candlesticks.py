from polygon import RESTClient
import json
import time

from_date = "2025-01-01"
to_date = "2025-01-02"
data = []
month_days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

AVAX_TICKER = "X:AVAX-USD"
SOL_TICKER = "X:SOL-USD"
ETH_TICKER = "X:ETH-USD"
AVAX_OUTPUT = "market_data/recent_avax.json"
SOL_OUTPUT = "market_data/recent_sol.json"
ETH_OUTPUT = "market_data/recent_eth.json"

TICKER = SOL_TICKER
OUTPUT = SOL_OUTPUT

client = RESTClient(api_key="GfF6dGScJa3pOZXtXt12UdAJukKcTd6K")

# month formatted like 4_2025 - month number, underscore, year 4 digits
def get_month_data(months):
    sleep_time = 10
    for date in months:
        success = False
        while not success:
            if sleep_time > 80:
                print("Sleep time has grown too long. Aborting program")
                quit()
            try:
                month, year = date.split("_")
                month_str = month if int(month) >= 10 else f"0{month}"
                data.extend(client.get_aggs(
                    ticker=TICKER,
                    multiplier=1,
                    timespan="minute",
                    from_=f"{year}-{month_str}-01",
                    to=f"{year}-{month_str}-{month_days[int(month) - 1]}",
                    limit=50_000,
                ))
                print(f"Successfully appended month {date} to {TICKER}.")
                success = True
                sleep_time = 10
            except:
                print(f"Failed to append month {date}. Sleeping {sleep_time} seconds")
                time.sleep(sleep_time)
                sleep_time *= 2

months = [
    "1_2025",
    "2_2025",
    "3_2025",
    "4_2025",
    "5_2025",
    "6_2025",
]
get_month_data(months)

agg_list = []
for i, agg in enumerate(data):
    agg = agg.__dict__
    agg_dict = {
        "open": agg["open"],
        "close": agg["close"],
        "high": agg["high"],
        "low": agg["low"],
        "volume": agg["volume"],
        "T": agg["timestamp"],
        "local_num": i
    }
    agg_list.append(agg_dict)


with open(OUTPUT, 'w') as f:
    json.dump(agg_list, f, indent=4)

print(f"Retrieved {len(agg_list)} candles of {TICKER}")