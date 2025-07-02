# SMC Algo Trading Bot

A sophisticated cryptocurrency trading bot built for Hyperliquid exchange, featuring Fair Value Gap (FVG) strategy with advanced risk management and multi-symbol support.

## 🚀 Features

### Core Trading Strategy
- **Fair Value Gap (FVG) Detection**: Identifies and trades on institutional order flow gaps
- **Multi-Timeframe Analysis**: Combines 1-minute and 15-minute data for trend confirmation
- **Structure Analysis**: Identifies swing highs/lows and market structure breaks
- **Pullback Detection**: Finds optimal entry points during trend continuations

### Risk Management
- **Fixed Dollar Risk**: $100 risk per trade (configurable)
- **Dynamic Position Sizing**: Adjusts position size based on stop loss distance
- **Leverage Constraints**: Symbol-specific leverage limits (AVAX: 10x, SOL: 20x, ETH: 25x)
- **Capital Protection**: Automatic position size reduction if leverage limits exceeded

### Advanced Stop Management
- **Trailing Stops**: Dynamic stops that follow price movement after R:R > 0.5:1
- **AVAX 3:1 RR Logic**: Moves stop to breakeven when AVAX reaches 3:1 risk-reward ratio
- **Swing-Based Trailing**: Uses market structure for optimal stop placement
- **Confirmation Candles**: Ensures stops only move after price confirms swing levels

### Multi-Symbol Support
- **Independent Trading**: Each symbol trades independently with separate position tracking
- **Symbol-Specific Logic**: AVAX gets special 3:1 RR treatment, others use standard trailing
- **Cooldown Management**: 5-candle cooldown after stop loss hits per symbol

### Real-Time Notifications
- **Telegram Integration**: Comprehensive notifications for all trading events
- **Live Updates**: Trade setups, executions, stop movements, and balance updates
- **Debug Logging**: Detailed console output for strategy analysis

## 📋 Requirements

### System Requirements
- Python 3.11+ (3.13 compatible)
- 2GB+ RAM
- Stable internet connection
- Google Cloud VM (recommended for 24/7 operation)

### Python Dependencies
```
hyperliquid-python-sdk==0.15.0
pandas==2.2.0
numpy==1.26.4
websockets==12.0
requests==2.31.0
python-dotenv==1.0.0
plotly==5.17.0
ta==0.10.2
matplotlib==3.8.3
```

## 🛠️ Installation

### 1. Clone the Repository
```bash
git clone https://github.com/your-username/SMC-Algo.git
cd SMC-Algo
```

### 2. Set Up Virtual Environment
```bash
python3.11 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 3. Install Dependencies
```bash
# For faster installation on cloud VMs
sudo apt update
sudo apt install -y build-essential python3-dev gcc g++ gfortran libopenblas-dev

# Install packages with pre-compiled wheels
pip install --only-binary=all --no-cache-dir numpy==1.26.4
pip install --only-binary=all --no-cache-dir pandas==2.2.0
pip install --only-binary=all --no-cache-dir matplotlib==3.8.3
pip install -r requirements.txt
```

### 4. Configure Environment
```bash
# Copy and edit the environment file
cp .env.example .env
nano .env
```

Add your configuration:
```env
# Hyperliquid API Configuration
HYPERLIQUID_API_KEY=your_api_key_here
HYPERLIQUID_SUBACCOUNT=default

# Telegram Bot Configuration (for notifications)
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

## ⚙️ Configuration

### Trading Parameters (`config.py`)
```python
# Trading Configuration
SYMBOLS = ["AVAX", "SOL", "ETH"]  # Symbols to trade
TIMEFRAME = "1m"                   # Low timeframe
HTF_TIMEFRAME = "15m"              # High timeframe
RISK_PER_TRADE = 100               # Dollar risk per trade

# Leverage Limits
MAX_LEVERAGE = {
    "SOL-USD": 20,
    "ETH-USD": 25,
    "AVAX": 10
}

# Strategy Parameters
BOS_LOOKBACK = 8                   # Structure analysis lookback
DISPLACEMENT_THRESHOLD = 0.3       # FVG displacement threshold
STOP_LOSS_BUFFER = 0.005          # Stop loss buffer
TRAILING_CONFIRMATION_CANDLES = 2  # Candles to confirm swing levels
```

## 🚀 Usage

### Paper Trading (Recommended for Testing)
```bash
# Start paper trading with debug output
python live_data_test.py
```

### Live Trading (Real Money)
```bash
# Start live trading (use with caution)
python main.py
```

### Backtesting
```bash
# Run backtest on historical data
python backtest_real_data.py
```

## 📊 Trading Modes

### Paper Trading (`live_data_test.py`)
- **Virtual Money**: $10,000 starting balance
- **Full Strategy**: All features including AVAX 3:1 RR logic
- **Debug Output**: Comprehensive logging and notifications
- **Multi-Symbol**: Trades all configured symbols independently
- **Perfect for Testing**: No real money risk

### Live Trading (`main.py`)
- **Real Money**: Uses actual Hyperliquid account
- **Same Strategy**: Identical logic to paper trading
- **Risk Management**: Daily loss limits and position constraints
- **Production Ready**: Built for 24/7 operation

### Backtesting (`backtest_real_data.py`)
- **Historical Analysis**: Tests strategy on past data
- **Performance Metrics**: Win rate, profit factor, drawdown analysis
- **Multiple Sessions**: Can test different time periods
- **Chart Generation**: Creates performance visualization

## 🔧 24/7 Operation

### Using Screen (Recommended)
```bash
# Install screen
sudo apt install screen

# Start bot in screen session
screen -S trading_bot
cd SMC-Algo
source .venv/bin/activate
python live_data_test.py

# Detach (keep running): Ctrl+A, then D
# Reattach later: screen -r trading_bot
```

### Using Systemd Service
```bash
# Create service file
sudo nano /etc/systemd/system/trading-bot.service

# Enable and start
sudo systemctl enable trading-bot
sudo systemctl start trading-bot

# Check status
sudo systemctl status trading-bot
```

## 📱 Notifications

The bot sends comprehensive Telegram notifications for:

- **Bot Events**: Startup, shutdown, errors
- **Trade Setups**: Entry opportunities detected
- **Trade Execution**: Position opens with details
- **Stop Management**: Trailing stop updates, AVAX 3:1 RR triggers
- **Trade Closes**: Position exits with P&L
- **Balance Updates**: Account balance after each trade
- **Periodic Updates**: Status every 20 cycles

## 📈 Strategy Details

### Entry Conditions
1. **Larger Trend**: Identify trend direction on 15-minute timeframe
2. **Pullback Detection**: Find retracements within the trend
3. **FVG Identification**: Locate fair value gaps for entry
4. **Structure Confirmation**: Verify market structure supports the trade
5. **Risk Calculation**: Determine position size based on stop distance

### Exit Conditions
1. **Stop Loss**: Hard stop at predetermined level
2. **Trailing Stop**: Dynamic stop that follows price movement
3. **AVAX Special**: Move to breakeven at 3:1 RR ratio
4. **Take Profit**: Optional target levels (if configured)

### Risk Management
- **Fixed Dollar Risk**: Always risk exactly $100 per trade
- **Leverage Limits**: Respect exchange leverage constraints
- **Position Sizing**: Automatic adjustment for capital protection
- **Cooldown Periods**: Prevent overtrading after losses

## 🔍 Debug and Monitoring

### Console Output
- **Cycle Updates**: Every 20 cycles with balance and P&L
- **Trade Details**: Entry/exit prices, position sizes, reasons
- **Stop Management**: Trailing stop movements and AVAX RR updates
- **Error Logging**: Detailed error messages for troubleshooting

### Log Files
- **Trading Log**: `trading_bot.log` for live trading
- **Console Output**: Real-time strategy execution details
- **Error Tracking**: Comprehensive error logging

## 🚨 Important Notes

### Risk Warning
- **Paper Trading**: Always test with paper trading first
- **Real Money**: Live trading involves real financial risk
- **API Security**: Keep your API keys secure and never share them
- **Monitoring**: Always monitor live trading bots

### Performance
- **Market Conditions**: Strategy performance varies with market conditions
- **Backtesting**: Past performance doesn't guarantee future results
- **Risk Management**: Always use proper risk management
- **Continuous Monitoring**: Regularly check bot performance

## 🛠️ Troubleshooting

### Common Issues
1. **API Connection**: Check API keys and internet connection
2. **Dependencies**: Ensure all packages are installed correctly
3. **Memory Issues**: Monitor RAM usage on cloud VMs
4. **Screen Sessions**: Use `screen -ls` to check for orphaned sessions

### Useful Commands
```bash
# Check if bot is running
ps aux | grep live_data_test.py

# Kill bot processes
pkill -f live_data_test.py

# Check screen sessions
screen -ls

# Kill screen sessions
screen -S trading_bot -X quit

# Update from GitHub
git pull origin main
pip install -r requirements.txt
```

## 📄 License

This project is for educational and personal use. Use at your own risk.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📞 Support

For issues and questions:
- Check the troubleshooting section
- Review console logs for error messages
- Test with paper trading first
- Monitor Telegram notifications for status updates

---

**Disclaimer**: This trading bot is for educational purposes. Trading cryptocurrencies involves substantial risk. Always test thoroughly with paper trading before using real money. Past performance does not guarantee future results. 
