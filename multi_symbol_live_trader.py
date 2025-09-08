import pandas as pd
from helpers.hyperliquid_client import HyperliquidClient
from helpers.telegram_setup import send_telegram_message, is_stop_requested, send_telegram_image
from config import *
from credentials import *
from datetime import datetime, timedelta
import asyncio
from base_trader import BaseTrader
import ssl
import certifi
import matplotlib.pyplot as plt
import os
import shutil
import ccxt

class MultiSymbolLiveTrader:
    def __init__(self, symbols):
        self.symbols = symbols
        self.traders = {}
        self.client = HyperliquidClient(
            api_key=None,  # No API key needed for paper trading
            subaccount=HYPERLIQUID_SUBACCOUNT,
        )
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())
        
        # Initialize a trader for each symbol
        for symbol in symbols:
            self.traders[symbol] = BaseTrader(symbol, 10_000)
            self.traders[symbol].client = self.client
            self.traders[symbol].ssl_context = self.ssl_context
            self.traders[symbol].working_candle = None
            self.traders[symbol].full_data = pd.DataFrame()
            self.traders[symbol].ltf_data = pd.DataFrame()
            self.traders[symbol].htf_data = pd.DataFrame()
            self.traders[symbol].plot_times = []
            self.traders[symbol].plot_opens = []
            self.traders[symbol].current_price = 0
            self.traders[symbol].last_price = 0

    async def fetch_initial_data_for_symbol(self, symbol):
        """Fetch initial data for a specific symbol"""
        try:
            print(f"Fetching initial data for {symbol}...")
            
            # Use CCXT for consistency with live data
            dex = ccxt.hyperliquid({
                "walletAddress": HYPERLIQUID_ACCOUNT_ADDRESS,
                "privateKey": "",
            })
            
            # Fetch LTF data (1m)
            ltf_data = dex.fetch_ohlcv(symbol + '/USDC:USDC', timeframe='1m', limit=self.traders[symbol].ltf_lookback)
            ltf_data = pd.DataFrame(ltf_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            ltf_data['T'] = pd.to_datetime(ltf_data['timestamp'], unit='ms')
            ltf_data = ltf_data[["open", "high", "low", "close", "T"]].reset_index(drop=True)

            # Fetch HTF data (15m)
            htf_data = dex.fetch_ohlcv(symbol + '/USDC:USDC', timeframe='15m', limit=self.traders[symbol].htf_lookback)
            htf_data = pd.DataFrame(htf_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            htf_data['T'] = pd.to_datetime(htf_data['timestamp'], unit='ms')
            htf_data = htf_data[["open", "high", "low", "close", "T"]].reset_index(drop=True)

            if ltf_data.empty or htf_data.empty:
                print(f"Failed to fetch market data for {symbol}")
                return None, None

            return ltf_data, htf_data

        except Exception as e:
            print(f"🔴 Error fetching data for {symbol}: {e}")
            return None, None

    async def handle_candle_data_for_symbol(self, symbol, candle):
        """Handle candle data for a specific symbol"""
        trader = self.traders[symbol]
        
        if not trader.working_candle:
            trader.working_candle = candle
        elif candle['T'] == trader.working_candle['T']:
            trader.working_candle['high'] = candle['high']
            trader.working_candle['low'] = candle['low']
            trader.working_candle['close'] = candle['close']
        else:
            print(f"Adding ltf candle for {symbol}")
            trader.ltf_data = pd.concat([trader.ltf_data, pd.DataFrame([trader.working_candle])], ignore_index=True)
            trader.ltf_data = trader.ltf_data.iloc[-trader.ltf_lookback:].reset_index(drop=True)

            trader.full_data = pd.concat([trader.full_data, pd.DataFrame([trader.working_candle])], ignore_index=True)
            trader.plot_times.append(trader.working_candle['T'])
            trader.plot_opens.append(trader.working_candle['open'])
            trader.iteration = len(trader.full_data) - 1

            if not trader.htf_data.empty and trader.working_candle['T'] >= trader.htf_data['T'].iloc[-1] + timedelta(minutes=HTF_TIMEFRAME_INT):
                print(f"Adding htf candle for {symbol}")
                trader.htf_data = pd.concat([trader.htf_data, pd.DataFrame([trader.working_candle])], ignore_index=True)
                trader.htf_data = trader.htf_data.iloc[-trader.htf_lookback:].reset_index(drop=True)

            trader.working_candle = None

    def check_position_opened_for_symbol(self, symbol, current_price, last_price):
        """Check if position opened for a specific symbol"""
        trader = self.traders[symbol]
        sorted_setups = sorted(
            trader.strategy.active_setups,
            key=lambda setup: setup['fvg']['top'],
            reverse=True,
        )

        for setup in sorted_setups:
            fvg_midpoint = (setup['fvg']['top'] + setup['fvg']['bottom']) / 2
            if ((current_price >= fvg_midpoint >= last_price)
                or (current_price <= fvg_midpoint <= last_price)):
                return setup
        return None

    async def handle_price_data_for_symbol(self, symbol, current_price, last_price):
        """Handle price data for a specific symbol"""
        trader = self.traders[symbol]
        
        if not trader.current_position:
            position = self.check_position_opened_for_symbol(symbol, current_price, last_price)
            if position:
                print(f"🚀 {symbol} POSITION FOUND!")
                trader.handle_position_open(position, datetime.now(), telegram=True, symbol=symbol)
        else:
            if trader.check_position_closed(current_price):
                print(f"🔴 {symbol} POSITION CLOSED!")
                trader.handle_position_close(current_price, datetime.now(), telegram=True, symbol=symbol)

    async def run_multi_symbol_trading(self, duration_minutes=None):
        """Run multi-symbol paper trading"""
        
        if duration_minutes:
            message = f"🚀 Starting multi-symbol paper trading for {duration_minutes} minutes...\n"
            print(message)
            start_time = datetime.now()
            end_time = start_time + timedelta(minutes=duration_minutes)
        else:
            message = f"🚀 Starting multi-symbol paper trading INDEFINITELY...\n"
            print(message)
            print("Press Ctrl+C to stop the bot")
            end_time = None

        print(f"Trading symbols: {', '.join(self.symbols)}")
        message += f"Trading symbols: {', '.join(self.symbols)}\n"
        print(f"Risk per trade: ${RISK_PER_TRADE}")
        message += f"Risk per trade: ${RISK_PER_TRADE}"

        send_telegram_message(message)
        
        # Fetch initial data for all symbols
        for symbol in self.symbols:
            ltf_data, htf_data = await self.fetch_initial_data_for_symbol(symbol)
            if ltf_data is not None and htf_data is not None:
                self.traders[symbol].ltf_data = ltf_data
                self.traders[symbol].htf_data = htf_data
                self.traders[symbol].full_data = ltf_data
                print(f"✅ Initial data loaded for {symbol}")
            else:
                print(f"❌ Failed to load initial data for {symbol}")

        # Initialize CCXT for price fetching
        dex = ccxt.hyperliquid({
            "walletAddress": HYPERLIQUID_ACCOUNT_ADDRESS,
            "privateKey": "",  # No private key needed for paper trading
        })

        while True:
            if end_time and datetime.now() >= end_time:
                break
            elif is_stop_requested():
                send_telegram_message("🛑 Multi-symbol bot stopped by user")
                break

            try:
                # Process each symbol
                for symbol in self.symbols:
                    trader = self.traders[symbol]
                    
                    try:
                        # Get current price for this symbol
                        trader.last_price = trader.current_price
                        trader.current_price = dex.fetch_ticker(symbol + '/USDC:USDC')['last']

                        # Check for new candle formation
                        current_candle = dex.fetch_ohlcv(symbol + '/USDC:USDC', timeframe='1m', limit=1)
                        
                        # Check if we got valid data
                        if not current_candle or len(current_candle) == 0:
                            print(f"⚠️ No candle data for {symbol}, skipping...")
                            continue
                            
                        current_candle = {
                            'T': datetime.fromtimestamp(current_candle[0][0] / 1000),
                            'open': current_candle[0][1],
                            'high': current_candle[0][2],
                            'low': current_candle[0][3],
                            'close': current_candle[0][4],
                        }

                        # Check if we have a new candle
                        if (not hasattr(trader, 'last_candle_time') or 
                            current_candle['T'] != trader.last_candle_time):
                            
                            # New candle formed - update data and run strategy
                            if trader.last_position_close_time:
                                if datetime.now() - trader.last_position_close_time > timedelta(minutes=5):  # Match single-symbol cooldown
                                    continue
                            
                            await self.handle_candle_data_for_symbol(symbol, current_candle)
                            trader.last_candle_time = current_candle['T']
                        
                        # Always check for entries every 5 seconds
                        await self.handle_price_data_for_symbol(symbol, trader.current_price, trader.last_price)
                        
                        # Run strategy analysis every iteration (like single-symbol version)
                        if hasattr(trader, 'ltf_data') and not trader.ltf_data.empty and hasattr(trader, 'htf_data') and not trader.htf_data.empty:
                            # Run strategy analysis on current data
                            trader.process_new_candle(ltf_data=trader.ltf_data, htf_data=trader.htf_data, timestamp=datetime.now())
                            
                            # Debug: Show active setups for this symbol
                            if len(trader.strategy.active_setups) > 0:
                                print(f"🎯 {symbol}: {len(trader.strategy.active_setups)} active setups")
                                for i, setup in enumerate(trader.strategy.active_setups):
                                    print(f"   {symbol} Setup {i+1}: {setup['direction']} FVG at ${setup['fvg']['top']:.4f}-${setup['fvg']['bottom']:.4f}")
                    
                    except Exception as e:
                        print(f"🔴 Error processing {symbol}: {e}")
                        continue
                
                # Wait 5 seconds before next check (match single-symbol)
                await asyncio.sleep(5)

            except Exception as e:
                print(f"🔴 FULL ERROR in main loop: {e}")
                import traceback
                traceback.print_exc()
                print(f"Attempting to continue in 15 seconds...")
                await asyncio.sleep(15)

        # Close any remaining positions
        for symbol in self.symbols:
            trader = self.traders[symbol]
            if trader.current_position:
                final_price = trader.ltf_data['close'].iloc[-1]
                trader.handle_position_close(final_price, datetime.now(), telegram=True)

        # Send final results
        total_trades = sum(len(trader.trades) for trader in self.traders.values())
        total_pnl = sum(trader.current_balance - 10000 for trader in self.traders.values())
        
        final_message = f"🏁 Multi-Symbol Trading Complete\n"
        final_message += f"Total Trades: {total_trades}\n"
        final_message += f"Total P&L: ${total_pnl:.2f}\n\n"
        
        for symbol in self.symbols:
            trader = self.traders[symbol]
            final_message += f"{symbol}: {len(trader.trades)} trades, P&L: ${trader.current_balance - 10000:.2f}\n"
        
        send_telegram_message(final_message)

# Run the multi-symbol trader
if __name__ == "__main__":
    trader = MultiSymbolLiveTrader(SYMBOLS)
    asyncio.run(trader.run_multi_symbol_trading(duration_minutes=60*24))  # 24 hours
