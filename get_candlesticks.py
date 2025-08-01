from polygon import RESTClient
import json

from_date = "2025-01-01"
to_date = "2025-01-02"

AVAX_TICKER = "X:AVAX-USD"
SOL_TICKER = "X:SOL-USD"
ETH_TICKER = "X:ETH-USD"
AVAX_OUTPUT = "market_data/recent_avax.json"
SOL_OUTPUT = "market_data/recent_sol.json"
ETH_OUTPUT = "market_data/recent_eth.json"

TICKER = AVAX_TICKER
OUTPUT = AVAX_OUTPUT

client = RESTClient(api_key="GfF6dGScJa3pOZXtXt12UdAJukKcTd6K")

data = client.get_aggs(
    ticker=TICKER,
    multiplier=1,
    timespan="minute",
    from_="2025-07-01",
    to="2025-07-30",
    limit=50_000,
)

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

print(f"Retrieved {len(agg_list)} candles")