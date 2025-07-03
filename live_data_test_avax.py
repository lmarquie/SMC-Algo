import asyncio
import pandas as pd
from datetime import datetime, timedelta
import logging
from trading_strategy import FVGStrategy
from hyperliquid_client import HyperliquidClient
from config import *
from notifications import send_telegram_message

class AVAXPaperTradingBot:
    def __init__(self):
        self.config = {
            'HYPERLIQUID_API_KEY': HYPERLIQUID_API_KEY,
            'HYPERLIQUID_SUBACCOUNT': HYPERLIQUID_SUBACCOUNT,
            'SYMBOLS': ["AVAX"],  # Only AVAX
            'TIMEFRAME': TIMEFRAME,
            'HTF_TIMEFRAME': HTF_TIMEFRAME,
            'POSITION_SIZE': POSITION_SIZE,
            'BOS_LOOKBACK': BOS_LOOKBACK,
            'DISPLACEMENT_THRESHOLD': DISPLACEMENT_THRESHOLD,
            'STOP_LOSS_BUFFER': STOP_LOSS_BUFFER,
            'TAKE_PROFIT_RATIO': TAKE_PROFIT_RATIO,
            'RISK_PER_TRADE': RISK_PER_TRADE,
            'MAX_LEVERAGE': MAX_LEVERAGE
        }
        
        self.client = HyperliquidClient(
            api_key=self.config['HYPERLIQUID_API_KEY'],
            subaccount=self.config['HYPERLIQUID_SUBACCOUNT']
        )
        self.strategy = FVGStrategy(self.config, send_notifications=False)
        
        # Paper trading state - single symbol
        self.paper_balance = 10000  # Starting with $10k
        self.current_position = None  # Single position tracking
        self.position_lock = False  # Lock to prevent race conditions
        self.trade_history = []
        self.total_trades = 0
        self.winning_trades = 0
        self.total_pnl = 0
        self.last_stop_idx = -10  # Track last stop loss exit index for cooldown
        
        # Stop monitoring state
        self.stop_monitoring_task = None
        self.stop_monitoring_active = False
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
    async def fetch_live_data(self):
        """Fetch live market data for AVAX"""
        try:
            self.logger.info(f"Fetching live data for AVAX...")
            
            # Fetch LTF data (1m) - increase to 1000 candles
            ltf_data = await self.client.get_ohlcv(
                symbol="AVAX",
                timeframe=self.config['TIMEFRAME'],
                limit=1000
            )
            
            # Fetch HTF data (15m) - increase to 500 candles
            htf_data = await self.client.get_ohlcv(
                symbol="AVAX",
                timeframe=self.config['HTF_TIMEFRAME'],
                limit=500
            )
            
            # Get current price
            current_price = self.client.get_current_price("AVAX")
            
            if ltf_data.empty or htf_data.empty:
                self.logger.error(f"Failed to fetch market data for AVAX")
                return None, None, None
            
            return ltf_data, htf_data, current_price
            
        except Exception as e:
            self.logger.error(f"Error fetching live data for AVAX: {e}")
            return None, None, None
    
    def calculate_position_size(self, entry_price, stop_loss, direction):
        """Calculate position size based on risk per trade with capital and leverage constraints"""
        risk_amount = abs(entry_price - stop_loss)
        position_size = self.config['RISK_PER_TRADE'] / risk_amount
        
        # Calculate position value (size × entry price)
        position_value = position_size * entry_price
        
        # Get leverage for AVAX
        leverage = self.config['MAX_LEVERAGE'].get("AVAX", 20)  # Default to 20x for AVAX
        
        # Capital constraints: $10,000 capital with leverage = max position value
        max_position_value = 10000 * leverage
        
        self.logger.info(f"DEBUG: Position calculation for AVAX:")
        self.logger.info(f"  Entry: ${entry_price:.4f}, Stop: ${stop_loss:.4f}")
        self.logger.info(f"  Risk amount: ${risk_amount:.4f}")
        self.logger.info(f"  Position size: {position_size:.4f}")
        self.logger.info(f"  Position value: ${position_value:.2f}")
        self.logger.info(f"  Leverage: {leverage}x")
        self.logger.info(f"  Max position value: ${max_position_value:.2f}")
        
        # Check if position value exceeds maximum allowed
        if position_value > max_position_value:
            original_stop = stop_loss
            if direction == 'long':
                new_stop_distance = (entry_price - original_stop) * 2
                new_stop = entry_price - new_stop_distance
            else:  # short
                new_stop_distance = (original_stop - entry_price) * 2
                new_stop = entry_price + new_stop_distance
            new_risk_amount = abs(entry_price - new_stop)
            position_size = self.config['RISK_PER_TRADE'] / new_risk_amount
            position_value = position_size * entry_price
            if position_value <= max_position_value:
                self.logger.warning(f"Stop loss widened to fit capital constraints. New stop: ${new_stop:.4f} (was ${original_stop:.4f})")
                return position_size, new_stop
            else:
                position_size = max_position_value / entry_price
                actual_risk = position_size * new_risk_amount
                self.logger.warning(f"Position size reduced due to capital constraints. Risk: ${actual_risk:.2f} instead of ${self.config['RISK_PER_TRADE']}")
                return position_size, new_stop
        
        # NEW LOGIC: If position size is too large (stop is too tight), widen stop so risk is $100
        max_position_size = max_position_value / entry_price
        if position_size > max_position_size:
            position_size = max_position_size
            if direction == 'long':
                stop_loss = entry_price - (self.config['RISK_PER_TRADE'] / position_size)
            else:
                stop_loss = entry_price + (self.config['RISK_PER_TRADE'] / position_size)
            self.logger.warning(f"Stop loss widened to ensure $100 risk. New stop: ${stop_loss:.4f}")
            return position_size, stop_loss
        
        return position_size, stop_loss

    # --- The rest of the code (open_paper_position, close_paper_position, check_position_exits, etc.) ---
    # Copy these methods from your BTC/ETH/SOL files, replacing BTC/ETH/SOL with AVAX in all logging, notifications, and symbol references.

    # For brevity, you can copy-paste the rest of the methods from your other live_data_test files,
    # making sure to replace all asset-specific references with AVAX.

async def main():
    """Main function to run AVAX paper trading"""
    bot = AVAXPaperTradingBot()
    try:
        await bot.run_paper_trading(duration_minutes=None)
    except Exception as e:
        logging.error(f"Main error: {e}")

if __name__ == "__main__":
    asyncio.run(main()) 