# FVG Trading Bot - Trend Continuation Strategy

An automated trading bot that implements a **trend continuation strategy** using Fair Value Gaps (FVG) + Market Structure Shift (MSS) / Break of Structure (BOS) for cryptocurrency trading on Hyperliquid.

## Strategy Overview

This bot implements a sophisticated **trend continuation approach**:

### Core Concept
1. **Identify Larger Trend**: Determine the dominant trend direction using higher timeframe analysis (15m)
2. **Detect Pullbacks**: Find counter-trend moves (pullbacks/retracements) within the larger trend
3. **Enter on Reversals**: Trade the reversal of pullbacks to **continue the larger trend**
4. **Risk Management**: Position sizing, stop losses, and trend-based exits

### Strategy Flow
```
Larger Uptrend → Bearish Pullback → Bullish Reversal → Long Entry
Larger Downtrend → Bullish Pullback → Bearish Reversal → Short Entry
```

## Features

- ✅ **Multi-Timeframe Analysis**: HTF for trend, LTF for entries
- ✅ **Trend Identification**: Automatic detection of larger trend direction and strength
- ✅ **Pullback Detection**: Identifies counter-trend moves within the larger trend
- ✅ **Reversal Confirmation**: BOS/MSS + displacement + FVG for entry signals
- ✅ **Automated Trading**: Places and manages orders on Hyperliquid
- ✅ **Risk Management**: Position sizing, stop losses, daily loss limits
- ✅ **Backtesting**: Test strategy on historical data before going live
- ✅ **Visualization**: Interactive charts with trade markers
- ✅ **Comprehensive Logging**: Track all trades and performance

## Installation

1. **Clone the repository**:
```bash
git clone <repository-url>
cd fvg-trading-bot
```

2. **Install dependencies**:
```bash
pip install -r requirements.txt
```

3. **Set up environment variables**:
Create a `.env` file in the project root:
```env
HYPERLIQUID_API_KEY=your_api_key_here
HYPERLIQUID_SUBACCOUNT=default
```

## Configuration

Edit `config.py` to customize your trading parameters:

```python
# Trading Parameters
SYMBOL = "SOL"  # Trading pair
TIMEFRAME = "1m"  # Primary timeframe for entries
HTF_TIMEFRAME = "15m"  # Higher timeframe for trend identification
POSITION_SIZE = 1.0  # Position size in SOL

# Strategy Parameters
FVG_LOOKBACK = 20  # Candles to look back for FVG detection
BOS_LOOKBACK = 10  # Candles to look back for BOS detection
DISPLACEMENT_THRESHOLD = 0.6  # Minimum body/wick ratio for displacement
STOP_LOSS_BUFFER = 0.005  # Buffer below FVG for stop loss
TAKE_PROFIT_RATIO = 2.0  # Risk:Reward ratio

# Risk Management
MAX_DAILY_LOSS = 0.05  # 5% max daily loss
MAX_TRADE_RISK = 0.02  # 2% max risk per trade
```

## Usage

### 1. Backtesting (Recommended First Step)

Test the trend continuation strategy on historical data:

```bash
python backtest.py
```

This will:
- Generate realistic trend data with pullbacks
- Run the trend continuation strategy
- Display performance metrics
- Create an interactive chart (`trend_continuation_backtest.html`)

### 2. Live Trading

**⚠️ WARNING: Start with small amounts and test thoroughly!**

```bash
python main.py
```

The bot will:
- Connect to Hyperliquid
- Monitor market data in real-time
- Identify larger trends and pullbacks
- Execute trades on pullback reversals
- Log all activities to `trading_bot.log`

### 3. Monitoring

Check the logs for real-time updates:
```bash
tail -f trading_bot.log
```

## Strategy Logic

### Entry Conditions

**For Long Positions (Uptrend Continuation):**
1. **Larger Trend**: HTF shows clear uptrend (confidence > 60%)
2. **Pullback**: Recent bearish BOS/MSS detected (counter-trend move)
3. **Reversal**: Bullish BOS/MSS + displacement candle
4. **FVG Touch**: Price touches bullish FVG
5. **Optional**: Liquidity sweep of recent low

**For Short Positions (Downtrend Continuation):**
1. **Larger Trend**: HTF shows clear downtrend (confidence > 60%)
2. **Pullback**: Recent bullish BOS/MSS detected (counter-trend move)
3. **Reversal**: Bearish BOS/MSS + displacement candle
4. **FVG Touch**: Price touches bearish FVG
5. **Optional**: Liquidity sweep of recent high

### Exit Conditions

- **Stop Loss**: Below FVG bottom (long) or above FVG top (short)
- **Take Profit**: 2:1 risk:reward ratio (configurable)
- **Trend Reversal**: Exit if larger trend changes direction
- **Structure Exit**: Opposing BOS/MSS detected
- **Daily Loss Limit**: 5% maximum daily loss

### Trend Identification

The bot identifies trends by analyzing:
- **BOS Count**: Number of bullish vs bearish breaks of structure
- **MSS Count**: Number of market structure shifts
- **Confidence Score**: Ratio of dominant structure to total structure
- **Minimum Threshold**: Requires clear majority (confidence > 60%)

## File Structure

```
fvg-trading-bot/
├── main.py                 # Main trading bot
├── backtest.py            # Backtesting module
├── trading_strategy.py    # Trend continuation strategy
├── structure_analysis.py  # Technical analysis
├── hyperliquid_client.py  # API client
├── config.py              # Configuration
├── requirements.txt       # Dependencies
├── README.md             # This file
└── .env                  # Environment variables (create this)
```

## API Setup

### Hyperliquid API

1. Create an account on [Hyperliquid](https://hyperliquid.xyz)
2. Generate API keys in your account settings
3. Add your API key to the `.env` file

**Note**: The current implementation uses a simplified API structure. You may need to adjust the API endpoints based on Hyperliquid's actual documentation.

## Risk Disclaimer

⚠️ **Trading cryptocurrencies involves substantial risk of loss. This bot is for educational purposes only.**

- Start with small amounts
- Test thoroughly in backtest mode first
- Monitor the bot closely when running live
- Never risk more than you can afford to lose
- Past performance does not guarantee future results

## Customization

### Adjusting Trend Sensitivity

Modify the trend identification parameters in `trading_strategy.py`:

```python
# In identify_larger_trend method
if bullish_strength > bearish_strength + 2:  # Adjust this threshold
    trend = 'uptrend'
```

### Modifying Pullback Detection

Edit the `detect_pullback` method to change how pullbacks are identified:

```python
# Adjust lookback period for pullback detection
recent_df = ltf_analyzed.tail(15)  # Change this number
```

### Changing Entry Conditions

Modify the reversal detection logic in `_check_bullish_reversal` and `_check_bearish_reversal` methods.

## Troubleshooting

### Common Issues

1. **No Trades Executing**: 
   - Check if trend confidence is high enough (> 60%)
   - Verify pullback detection is working
   - Ensure FVG detection is functioning

2. **Poor Performance**: 
   - Adjust trend identification sensitivity
   - Modify pullback detection parameters
   - Test different timeframes

3. **API Connection Errors**: Check your API key and internet connection

### Debug Mode

Enable debug logging by modifying the logging level in `main.py`:

```python
logging.basicConfig(level=logging.DEBUG)
```

## Performance Metrics

The backtester provides comprehensive metrics:

- **Total Return**: Overall percentage gain/loss
- **Win Rate**: Percentage of profitable trades
- **Profit Factor**: Ratio of gross profit to gross loss
- **Max Drawdown**: Largest peak-to-trough decline
- **Average Win/Loss**: Average profit/loss per trade
- **Trend Accuracy**: How well the bot identifies trends

## Support

For questions or issues:

1. Check the logs for error messages
2. Verify your configuration settings
3. Test with the backtester first
4. Start with small position sizes

## License

This project is for educational purposes. Use at your own risk.

---

**Remember**: Always test thoroughly before using real money, and never risk more than you can afford to lose. 

## Step-by-step Fix

### 1. **Install Python 3.11**
If you have Homebrew:
```bash
brew install python@3.11
```
Or with pyenv:
```bash
brew install pyenv
pyenv install 3.11.8
pyenv local 3.11.8
```

### 2. **Create a Virtual Environment**
```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

### 3. **Install Requirements**
```bash
pip install -r requirements.txt
```

### 4. **Run Your Script**
```bash
python backtest.py
```
or
```bash
python main.py
```

---

**Why?**
- `pip` will always use the Python version of the current environment.
- If you use the system Python 3.13, you will keep getting errors because the packages do not support it yet.

---

If you want, I can walk you through these steps one by one, or help you troubleshoot any part of the process! Just let me know where you get stuck or what error you see next. 