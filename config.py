import os
from dotenv import load_dotenv

load_dotenv()

# Hyperliquid API Configuration
# Get these from: https://app.hyperliquid.xyz/API
HYPERLIQUID_API_KEY = ""  # API wallet's private key (from the API section)
HYPERLIQUID_ACCOUNT_ADDRESS = ""  # Your main wallet's public key (NOT the API wallet's public key)
HYPERLIQUID_SUBACCOUNT = "default"

# Trading Configuration
SYMBOLS = ["SOL", "ETH"]
TIMEFRAME = "1m"
HTF_TIMEFRAME = "15m"
POSITION_SIZE = 2000
MAX_LEVERAGE = {
    "SOL-USD": 20,
    "ETH-USD": 25
}

# Risk Management
RISK_PER_TRADE = 100  # Fixed $150 risk per trade

# Strategy Parameters (ACTUALLY USED)
BOS_LOOKBACK = 8  # Used in StructureAnalyzer
DISPLACEMENT_THRESHOLD = 0.3  # Used in displacement detection
STOP_LOSS_BUFFER = 0.003  # Used in stop loss calculations
TAKE_PROFIT_RATIO = 1.5  # Used in position sizing
TRAILING_CONFIRMATION_CANDLES = 2  # Fewer candles - move stop faster
STOP_LOSS_BUFFER = 0.005  # More generous buffer (was 0.003)  # Number of candles price must stay above/below swing before moving trailing stop 

