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
    "SOL/USDC:USDC": 20,
    "ETH/USDC:USDC": 20,
    "BTC/USDC:USDC": 20,
}

# Risk Management
RISK_PER_TRADE = 150  # Fixed $100 risk per trade

# Strategy Parameters (ACTUALLY USED)
DISPLACEMENT_THRESHOLD = 0.3  # Used in displacement detection
STOP_LOSS_BUFFER = 0.0015  # Used in stop loss calculations
TAKE_PROFIT_RATIO = 1.5  # Used in position sizing
TRAILING_CONFIRMATION_CANDLES = 2  # Fewer candles - move stop faster
MIN_STOP_DISTANCE_COIN = 0.0015  # 0.15% minimum stop distance and FVG size as percentage of coin value
MIN_FVG_STRENGTH = 0.00_000 # 0.005 % minimum FVG strength
MIN_LARGER_TREND_CONFIDENCE = 0.00 # 0.75 is good

REVERSAL_CONSTRAINT_ENABLED = True
REQUIRE_SETUP_INDICATORS = True