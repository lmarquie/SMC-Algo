import requests
from datetime import datetime, timedelta


def fetch_dydx_minute_candles_month(ticker, year, month, limit=1000):
    """
    Download all 1-minute candles for a ticker from dYdX for a specific month.

    Args:
        ticker (str): Market ticker, e.g., 'SOL-USD'
        year (int): Four-digit year, e.g., 2025
        month (int): 1–12
        limit (int): Max candles per API request (dYdX may limit to 1,000)

    Returns:
        candles (list): List of candle dicts
    """
    base_url = "https://indexer.dydx.trade/v4/candles/perpetualMarkets"
    resolution = "1MIN"
    candles = []

    # Construct the first and last datetime of the target month (UTC)
    from_dt = datetime(year, month, 1)
    if month == 12:
        to_dt = datetime(year + 1, 1, 1)
    else:
        to_dt = datetime(year, month + 1, 1)
    curr_start = from_dt

    while curr_start < to_dt:
        curr_end = curr_start + timedelta(minutes=limit)
        if curr_end > to_dt:
            curr_end = to_dt
        params = {
            "resolution": resolution,
            "fromISO": curr_start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "toISO": curr_end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "limit": limit
        }
        url = f"{base_url}/{ticker}"
        resp = requests.get(url, params=params)
        if resp.status_code != 200:
            print(f"Failed at {curr_start}: {resp.status_code}")
            break
        batch = resp.json().get("candles", [])
        if not batch:
            break
        candles.extend(batch)
        print(f"Fetched {len(batch)} candles for {ticker} from {curr_start} to {curr_end}")
        curr_start = curr_end
    return candles


# Example usage: Fetch 1-min SOL-USD candles for July 2025
if __name__ == "__main__":
    ticker = "SOL-USD"
    year = 2025
    month = 7  # July
    candles = fetch_dydx_minute_candles_month(ticker, year, month)
    print(f"Retrieved {len(candles)} candles for {ticker} in {year}-{month:02d}")
    print(candles[:5])
    with open(f"recent_sol.json", "w") as f:
        f.write(data)
    # Do something with candles (save to CSV, analyze, etc.)
