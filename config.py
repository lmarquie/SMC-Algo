# Constants to use used by backtesting and live trading
HYPERLIQUID_SUBACCOUNT = "default"

# Trading Configuration
SYMBOLS = [
    "SOL", "ETH", "BTC", "AVAX",  # Your current ones
    "DOGE", "ADA", "LINK", "LTC", # Major alts
    "BNB", "XRP", "NEAR", "TON"   # Additional liquid coins
]
TIMEFRAME = "1m"
TIMEFRAME_INT = 1
HTF_TIMEFRAME = "15m"
HTF_TIMEFRAME_INT = 15
POSITION_SIZE = 2000
MAX_LEVERAGE = {
    "SOL": 20,
    "ETH": 25,
    "BTC": 40,
    "AVAX": 10,
}

# Risk Management
RISK_PER_TRADE = 150  # Fixed $100 risk per trade

# Strategy Parameters (ACTUALLY USED)
BOS_LOOKBACK = 8  # Used in StructureAnalyzer
DISPLACEMENT_THRESHOLD = 0.3  # Used in displacement detection
STOP_LOSS_BUFFER = 0.005  # Used in stop loss calculations
TAKE_PROFIT_RATIO = 1.5  # Used in position sizing
TRAILING_CONFIRMATION_CANDLES = 2  # Fewer candles - move stop faster
MIN_STOP_DISTANCE_COIN = 0.0015  # 0.15% minimum stop distance and FVG size as percentage of coin value