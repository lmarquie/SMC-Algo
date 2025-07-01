import os
from dotenv import load_dotenv

load_dotenv()

# Hyperliquid API Configuration
# Get these from: https://app.hyperliquid.xyz/API
HYPERLIQUID_API_KEY = "0x8ecaef0cddc7e19ca0e4430a7a44942593cf73a0"  # API wallet's private key (from the API section)
HYPERLIQUID_ACCOUNT_ADDRESS = "0xD9A23C54539Fd6F9b51FcEe0F096f512f5adDB84"  # Your main wallet's public key (NOT the API wallet's public key)
HYPERLIQUID_SUBACCOUNT = "default"

# Trading Configuration
SYMBOLS = ["SOL", "ETH", "AVAX", "MATIC", "LINK"]  # Added MATIC and LINK
TIMEFRAME = "1m"
HTF_TIMEFRAME = "15m"
POSITION_SIZE = 2000

# Risk Management
RISK_PER_TRADE = 100  # Fixed $100 risk per trade
LEVERAGE = 50  # 50x leverage for larger position sizes

# Strategy Parameters (ACTUALLY USED)
BOS_LOOKBACK = 8  # Used in StructureAnalyzer
DISPLACEMENT_THRESHOLD = 0.3  # Used in displacement detection
STOP_LOSS_BUFFER = 0.003  # Used in stop loss calculations
TAKE_PROFIT_RATIO = 1.5  # Used in position sizing
TRAILING_CONFIRMATION_CANDLES = 2  # Fewer candles - move stop faster
STOP_LOSS_BUFFER = 0.005  # More generous buffer (was 0.003)  # Number of candles price must stay above/below swing before moving trailing stop 