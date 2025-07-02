import asyncio
import logging
import time
from datetime import datetime
from typing import Dict, Optional

from config import *
from hyperliquid_client import HyperliquidClient
from trading_strategy import FVGStrategy
from structure_analysis import StructureAnalyzer
from notifications import send_telegram_message

class TradingBot:
    def __init__(self):
        # Initialize configuration
        self.config = {
            'HYPERLIQUID_API_KEY': HYPERLIQUID_API_KEY,
            'HYPERLIQUID_SUBACCOUNT': HYPERLIQUID_SUBACCOUNT,
            'SYMBOLS': SYMBOLS,  # Use all symbols for live trading
            'TIMEFRAME': TIMEFRAME,
            'HTF_TIMEFRAME': HTF_TIMEFRAME,
            'POSITION_SIZE': POSITION_SIZE,
            'BOS_LOOKBACK': BOS_LOOKBACK,
            'DISPLACEMENT_THRESHOLD': DISPLACEMENT_THRESHOLD,
            'STOP_LOSS_BUFFER': STOP_LOSS_BUFFER,
            'TAKE_PROFIT_RATIO': TAKE_PROFIT_RATIO,
            'RISK_PER_TRADE': RISK_PER_TRADE,
            'MAX_LEVERAGE': MAX_LEVERAGE  # Add leverage mapping from config
        }
        
        # Initialize components
        self.client = HyperliquidClient(
            api_key=self.config['HYPERLIQUID_API_KEY'],
            subaccount=self.config['HYPERLIQUID_SUBACCOUNT']
        )
        self.strategy = FVGStrategy(self.config)
        self.analyzer = StructureAnalyzer()
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('trading_bot.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Trading state
        self.current_position = None
        self.daily_pnl = 0.0
        self.trades_today = 0
        self.is_running = False
        
    async def start(self):
        """Start the trading bot"""
        self.logger.info("Starting FVG Trading Bot...")
        send_telegram_message(f"🚀 LIVE BOT STARTED: Trading {', '.join(self.config['SYMBOLS'])} | Risk: ${self.config['RISK_PER_TRADE']}")
        self.is_running = True
        
        try:
            # Check account status
            account_info = self.client.get_account_info()
            print("DEBUG: Raw account_info from API:", account_info)  # <-- Print the full API response

            # Print the "Available to Trade" value
            if account_info:
                available = account_info.get('withdrawable') or account_info.get('marginSummary', {}).get('accountValue')
                print(f"DEBUG: Available to Trade (from API): {available}")
            else:
                print("DEBUG: No account info returned from API")

            if not account_info:
                self.logger.error("Failed to get account info. Check API key.")
                return
            
            self.logger.info(f"Account info: {account_info}")
            
            # Main trading loop
            while self.is_running:
                await self.trading_cycle()
                await asyncio.sleep(3)  # 3 second interval
                
        except KeyboardInterrupt:
            self.logger.info("Bot stopped by user")
        except Exception as e:
            self.logger.error(f"Bot error: {e}")
        finally:
            await self.stop()
    
    async def trading_cycle(self):
        """Main trading cycle - analyze market and execute trades for all symbols"""
        try:
            # Process each symbol
            for symbol in self.config['SYMBOLS']:
                # Fetch market data for this symbol
                ltf_data = await self.client.get_ohlcv(
                    symbol=symbol,
                    timeframe=self.config['TIMEFRAME'],
                    limit=200
                )
                
                htf_data = await self.client.get_ohlcv(
                    symbol=symbol,
                    timeframe=self.config['HTF_TIMEFRAME'],
                    limit=100
                )
                
                if ltf_data.empty or htf_data.empty:
                    self.logger.warning(f"Failed to fetch market data for {symbol}")
                    continue
                
                current_price = self.client.get_current_price(symbol)
                if not current_price:
                    self.logger.warning(f"Failed to get current price for {symbol}")
                    continue
                
                # Check if we should exit current position for this symbol
                if self.current_position and self.current_position.get('symbol') == symbol:
                    await self.check_exit_conditions(ltf_data, current_price)
                
                # Check for new entry opportunities for this symbol
                if not self.current_position and self.can_trade():
                    await self.check_entry_conditions(symbol, ltf_data, htf_data, current_price)
                
                # Update position if exists for this symbol
                if self.current_position and self.current_position.get('symbol') == symbol:
                    self.strategy.update_position(self.current_position, current_price)
                    # Update trailing stop
                    self.strategy.update_trailing_stop(ltf_data, self.current_position)
                
                # Small delay between symbols
                await asyncio.sleep(1)
            
            # AVAX-specific stop loss move at 3:1 RR
            if self.current_position and self.current_position.get('symbol') == "AVAX":
                position = self.current_position
                initial_risk = abs(position['entry_price'] - position['stop_loss'])
                if position['direction'] == 'long':
                    current_profit = self.client.get_current_price("AVAX") - position['entry_price']
                else:
                    current_profit = position['entry_price'] - self.client.get_current_price("AVAX")
                rr_ratio = current_profit / initial_risk if initial_risk > 0 else 0

                # If RR >= 3, move stop loss to 1:1 RR
                if rr_ratio >= 3:
                    old_stop = position['stop_loss']
                    if position['direction'] == 'long':
                        new_stop = position['entry_price'] + initial_risk
                        if position['stop_loss'] < new_stop:
                            position['stop_loss'] = new_stop
                            self.logger.info(f"Moved AVAX stop loss to 1:1 RR (${new_stop:.4f}) after reaching 3:1 RR")
                            send_telegram_message(f"🎯 AVAX 3:1 RR TRIGGERED! Stop moved: ${old_stop:.4f} → ${new_stop:.4f} (1:1 RR)")
                    else:
                        new_stop = position['entry_price'] - initial_risk
                        if position['stop_loss'] > new_stop:
                            position['stop_loss'] = new_stop
                            self.logger.info(f"Moved AVAX stop loss to 1:1 RR (${new_stop:.4f}) after reaching 3:1 RR")
                            send_telegram_message(f"🎯 AVAX 3:1 RR TRIGGERED! Stop moved: ${old_stop:.4f} → ${new_stop:.4f} (1:1 RR)")
            
        except Exception as e:
            self.logger.error(f"Error in trading cycle: {e}")
    
    async def check_entry_conditions(self, symbol, ltf_data, htf_data, current_price):
        """Check for new trading opportunities for a specific symbol"""
        try:
            # Check strategy conditions with both LTF and HTF data
            setup = self.strategy.check_entry_conditions(ltf_data, htf_data)
            
            if setup:
                self.logger.info(f"Trend continuation setup detected for {symbol}: {setup}")
                self.strategy.log_setup(setup)
                send_telegram_message(f"🎯 TRADE SETUP: {symbol} {setup['direction'].upper()} at ${setup['entry_price']:.4f}")
                
                # Execute trade
                await self.execute_trade(symbol, setup, current_price)
                
        except Exception as e:
            self.logger.error(f"Error checking entry conditions for {symbol}: {e}")
    
    async def execute_trade(self, symbol: str, setup: Dict, current_price: float):
        """Execute a trade based on the setup for a specific symbol"""
        try:
            # Calculate position size based on risk
            risk_amount = abs(setup['entry_price'] - setup['stop_loss'])
            position_size, adjusted_stop = self.calculate_position_size(risk_amount, setup['entry_price'], setup, symbol)
            
            # Use adjusted stop if it was changed
            final_stop = adjusted_stop if adjusted_stop != setup['stop_loss'] else setup['stop_loss']
            
            # Get leverage for this symbol
            leverage = self.config['MAX_LEVERAGE'].get(symbol, 20)  # Default to 20x if not found
            
            # Place order
            order_result = self.client.place_order(
                symbol=symbol,
                side='buy' if setup['direction'] == 'long' else 'sell',
                size=position_size,
                order_type='market',
                price=setup['entry_price'],
                stop_loss=final_stop,
                leverage=leverage  # Use symbol-specific leverage
            )
            
            if 'error' not in order_result:
                # Update position tracking
                self.current_position = {
                    'symbol': symbol,
                    'direction': setup['direction'],
                    'entry_price': setup['entry_price'],
                    'stop_loss': final_stop,
                    'take_profit': setup['take_profit'],
                    'size': position_size,
                    'entry_time': datetime.now(),
                    'order_id': order_result.get('orderId'),
                    'reason': setup['reason']
                }
                
                self.trades_today += 1
                self.logger.info(f"Trade executed: {self.current_position}")
                
                # Send trade execution notification
                send_telegram_message(
                    f"📈 TRADE EXECUTED: {symbol} {setup['direction'].upper()} at ${setup['entry_price']:.4f} | Stop: ${final_stop:.4f} | Size: {position_size:.4f} | Leverage: {leverage}x"
                )
                
                # Place take profit order
                if setup['take_profit']:
                    tp_order = self.client.place_order(
                        symbol=symbol,
                        side='sell' if setup['direction'] == 'long' else 'buy',
                        size=position_size,
                        order_type='limit',
                        price=setup['take_profit']
                    )
                    self.logger.info(f"Take profit order placed for {symbol}: {tp_order}")
                    
            else:
                self.logger.error(f"Failed to place order: {order_result}")
                
        except Exception as e:
            self.logger.error(f"Error executing trade: {e}")
    
    async def check_exit_conditions(self, ltf_data, current_price):
        """Check if current position should be exited"""
        try:
            if not self.current_position:
                return
            direction = self.current_position['direction']
            stop_loss = self.current_position['stop_loss']
            # Only exit if stop loss is hit
            if direction == 'long' and current_price <= stop_loss:
                send_telegram_message(f"🛑 STOP LOSS HIT: {self.current_position['symbol']} LONG at ${stop_loss:.4f}")
                await self.close_position(stop_loss, "Stop Loss Hit")
            elif direction == 'short' and current_price >= stop_loss:
                send_telegram_message(f"🛑 STOP LOSS HIT: {self.current_position['symbol']} SHORT at ${stop_loss:.4f}")
                await self.close_position(stop_loss, "Stop Loss Hit")
        except Exception as e:
            self.logger.error(f"Error checking exit conditions: {e}")
    
    async def close_position(self, current_price: float, reason: str):
        """Close the current position"""
        try:
            if not self.current_position:
                return
            
            # Calculate P&L in dollars
            if self.current_position['direction'] == 'long':
                pnl_dollar = (current_price - self.current_position['entry_price']) * self.current_position['size']
            else:
                pnl_dollar = (self.current_position['entry_price'] - current_price) * self.current_position['size']
            
            # Update daily P&L
            self.daily_pnl += pnl_dollar
            
            # Get leverage for this symbol
            symbol = self.current_position['symbol']
            leverage = self.config['MAX_LEVERAGE'].get(symbol, 20)  # Default to 20x if not found
            
            # Close position
            close_order = self.client.place_order(
                symbol=symbol,
                side='sell' if self.current_position['direction'] == 'long' else 'buy',
                size=self.current_position['size'],
                order_type='market',
                leverage=leverage  # Use symbol-specific leverage
            )
            
            if 'error' not in close_order:
                self.logger.info(f"Position closed: {reason}")
                self.logger.info(f"P&L: ${pnl_dollar:.2f}, Daily P&L: ${self.daily_pnl:.2f}")
                
                # Send position close notification
                send_telegram_message(
                    f"📉 POSITION CLOSED: {symbol} {self.current_position['direction'].upper()} | Entry: ${self.current_position['entry_price']:.4f} | Exit: ${current_price:.4f} | P&L: ${pnl_dollar:.2f}"
                )
                
                # Send balance update
                send_telegram_message(
                    f"💰 BALANCE UPDATE: Daily P&L: ${self.daily_pnl:.2f} | Trades Today: {self.trades_today}"
                )
                
                # Reset position
                self.current_position = None
            else:
                self.logger.error(f"Failed to close position: {close_order}")
                
        except Exception as e:
            self.logger.error(f"Error closing position: {e}")
    
    def calculate_position_size(self, risk_amount: float, entry_price: float, setup: Dict, symbol: str) -> tuple:
        """Calculate position size based on risk management rules with capital and leverage constraints"""
        try:
            # Use fixed dollar risk instead of percentage
            max_risk_amount = self.config['RISK_PER_TRADE']  # $150
            
            # Calculate position size based on dollar risk
            # risk_amount is the price difference between entry and stop
            position_size = max_risk_amount / risk_amount
            
            # Calculate position value (size × entry price)
            position_value = position_size * entry_price
            
            # Get leverage for this symbol
            leverage = self.config['MAX_LEVERAGE'].get(symbol, 20)  # Default to 20x if not found
            
            # Capital constraints: $10,000 capital with leverage = max position value
            max_position_value = 10000 * leverage  # Dynamic based on symbol leverage
            
            # Check if position value exceeds maximum allowed
            if position_value > max_position_value:
                # First, try to double the stop loss distance to reduce position size
                original_stop = setup['stop_loss']
                direction = setup['direction']
                
                if direction == 'long':
                    # Double the distance from entry to stop (widen stop down)
                    new_stop_distance = (entry_price - original_stop) * 2
                    new_stop = entry_price - new_stop_distance
                else:  # short
                    # Double the distance from entry to stop (widen stop up)
                    new_stop_distance = (original_stop - entry_price) * 2
                    new_stop = entry_price + new_stop_distance
                
                # Recalculate position size with new stop
                new_risk_amount = abs(entry_price - new_stop)
                position_size = max_risk_amount / new_risk_amount
                position_value = position_size * entry_price
                
                # Check if this fits within capital constraints
                if position_value <= max_position_value:
                    self.logger.warning(f"Stop loss widened to fit capital constraints. New stop: ${new_stop:.4f} (was ${original_stop:.4f})")
                    # Cap at maximum position size from config
                    position_size = min(position_size, self.config['POSITION_SIZE'])
                    return position_size, new_stop
                else:
                    # If still too large, scale down position size as last resort
                    position_size = max_position_value / entry_price
                    actual_risk = position_size * new_risk_amount
                    self.logger.warning(f"Position size reduced due to capital constraints. Risk: ${actual_risk:.2f} instead of ${max_risk_amount}")
                    return position_size, new_stop
            
            # Cap at maximum position size from config
            position_size = min(position_size, self.config['POSITION_SIZE'])
            
            self.logger.info(f"Position size: {position_size:.4f} (Risk: ${max_risk_amount}, Leverage: {leverage}x)")
            return position_size, setup['stop_loss']
            
        except Exception as e:
            self.logger.error(f"Error calculating position size: {e}")
            return self.config['POSITION_SIZE'], setup['stop_loss']
    
    def can_trade(self) -> bool:
        """Check if we can place new trades based on risk management"""
        # Check daily loss limit (in dollars)
        if self.daily_pnl <= -1000:  # $1000 daily loss limit
            self.logger.warning(f"Daily loss limit reached: ${self.daily_pnl:.2f}")
            return False
        
        # Check maximum positions
        if self.current_position:
            return False
        
        # Check maximum trades per day (optional)
        if self.trades_today >= 10:  # Max 10 trades per day
            self.logger.warning("Maximum trades per day reached")
            return False
        
        return True
    
    async def stop(self):
        """Stop the trading bot"""
        self.logger.info("Stopping trading bot...")
        send_telegram_message(f"🛑 LIVE BOT STOPPED: Daily P&L: ${self.daily_pnl:.2f} | Trades: {self.trades_today}")
        self.is_running = False
        
        # Close any open positions
        if self.current_position:
            current_price = self.client.get_current_price(self.config['SYMBOL'])
            if current_price:
                await self.close_position(current_price, "Bot shutdown")
        
        # Close client connection
        self.client.close()
        
        self.logger.info("Trading bot stopped")

async def main():
    """Main function to run the trading bot"""
    bot = TradingBot()
    
    try:
        await bot.start()
    except Exception as e:
        logging.error(f"Main error: {e}")

if __name__ == "__main__":
    # Run the bot
    asyncio.run(main()) 