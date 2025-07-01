import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import logging
from backtest_real_data import RealDataBacktester
from config import *
import matplotlib.pyplot as plt
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px

class SequentialBacktester:
    def __init__(self):
        self.config = {
            'HYPERLIQUID_API_KEY': HYPERLIQUID_API_KEY,
            'HYPERLIQUID_SUBACCOUNT': HYPERLIQUID_SUBACCOUNT,
            'SYMBOLS': SYMBOLS,
            'TIMEFRAME': TIMEFRAME,
            'HTF_TIMEFRAME': HTF_TIMEFRAME,
            'POSITION_SIZE': POSITION_SIZE,
            'BOS_LOOKBACK': BOS_LOOKBACK,
            'DISPLACEMENT_THRESHOLD': DISPLACEMENT_THRESHOLD,
            'STOP_LOSS_BUFFER': STOP_LOSS_BUFFER,
            'TAKE_PROFIT_RATIO': TAKE_PROFIT_RATIO,
            'RISK_PER_TRADE': RISK_PER_TRADE
        }
        
        # Setup logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
        
        # Results storage
        self.all_trades = []
        self.all_equity_curves = []
        self.session_results = []
        
    async def run_sequential_backtest(self, symbols: list = None, total_days: int = 7, session_days: int = 3):
        """Run multiple sequential backtests to simulate longer periods"""
        if symbols is None:
            symbols = self.config['SYMBOLS']
        
        self.logger.info(f"🚀 Starting sequential backtest for {len(symbols)} symbols")
        self.logger.info(f"Symbols: {', '.join(symbols)}")
        self.logger.info(f"Total days: {total_days}, Each session: {session_days} days (~{session_days * 24 * 60} minutes)")
        
        # Calculate number of sessions needed
        num_sessions = (total_days + session_days - 1) // session_days  # Ceiling division
        
        self.logger.info(f"Will run {num_sessions} sessions per symbol to cover {total_days} days")
        
        # Initialize cumulative balance
        cumulative_balance = 10000  # Starting balance
        session_start_balance = cumulative_balance
        
        # Run backtests for each symbol
        for symbol in symbols:
            self.logger.info(f"\n{'='*80}")
            self.logger.info(f"📊 TRADING {symbol}")
            self.logger.info(f"{'='*80}")
            
            for session in range(num_sessions):
                self.logger.info(f"\n{'='*60}")
                self.logger.info(f"📊 {symbol} - SESSION {session + 1}/{num_sessions}")
                self.logger.info(f"{'='*60}")
                
                # Create backtester for this session
                session_config = self.config.copy()
                session_config['INITIAL_BALANCE'] = session_start_balance
                
                backtester = RealDataBacktester(session_config)
                
                try:
                    # Run backtest for this session with different time periods
                    # Each session gets data from a different time period to avoid duplicates
                    trades, equity_curve = await backtester.run_backtest_with_time_offset(symbol, session_days, session)
                    
                    if trades:
                        # Adjust trade timestamps to be sequential and add symbol info
                        time_offset = session * session_days * 24 * 60 * 60  # seconds
                        for trade in trades:
                            trade['entry_time'] = trade['entry_time'] + timedelta(seconds=time_offset)
                            trade['exit_time'] = trade['exit_time'] + timedelta(seconds=time_offset)
                            trade['session'] = session + 1
                            trade['symbol'] = symbol  # Add symbol to trade
                        
                        # Adjust equity curve timestamps and add symbol info
                        for point in equity_curve:
                            point['timestamp'] = point['timestamp'] + timedelta(seconds=time_offset)
                            point['session'] = session + 1
                            point['symbol'] = symbol  # Add symbol to equity curve
                        
                        # Store results
                        self.all_trades.extend(trades)
                        self.all_equity_curves.extend(equity_curve)
                        
                        # Calculate session performance
                        session_pnl = sum(trade['pnl_dollar'] for trade in trades)
                        session_start_balance += session_pnl
                        
                        session_result = {
                            'symbol': symbol,
                            'session': session + 1,
                            'trades': len(trades),
                            'pnl': session_pnl,
                            'start_balance': session_start_balance - session_pnl,
                            'end_balance': session_start_balance,
                            'win_rate': len([t for t in trades if t['pnl_dollar'] > 0]) / len(trades) if trades else 0
                        }
                        self.session_results.append(session_result)
                        
                        self.logger.info(f"{symbol} Session {session + 1} Results:")
                        self.logger.info(f"  Trades: {len(trades)}")
                        self.logger.info(f"  P&L: ${session_pnl:.2f}")
                        self.logger.info(f"  Win Rate: {session_result['win_rate']:.1%}")
                        self.logger.info(f"  End Balance: ${session_start_balance:.2f}")
                        
                    else:
                        self.logger.info(f"{symbol} Session {session + 1}: No trades")
                        session_result = {
                            'symbol': symbol,
                            'session': session + 1,
                            'trades': 0,
                            'pnl': 0,
                            'start_balance': session_start_balance,
                            'end_balance': session_start_balance,
                            'win_rate': 0
                        }
                        self.session_results.append(session_result)
                    
                    # Small delay between sessions
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    self.logger.error(f"Error in {symbol} session {session + 1}: {e}")
                    continue
        
        # Calculate overall performance
        self.calculate_overall_performance()
        
        # Plot results
        self.plot_sequential_results()
        
        return self.all_trades, self.all_equity_curves
    
    def calculate_overall_performance(self):
        """Calculate overall performance metrics"""
        if not self.all_trades:
            self.logger.warning("No trades to analyze")
            return
        
        # Basic metrics
        total_trades = len(self.all_trades)
        winning_trades = len([t for t in self.all_trades if t['pnl_dollar'] > 0])
        losing_trades = total_trades - winning_trades
        
        total_pnl = sum(trade['pnl_dollar'] for trade in self.all_trades)
        total_return = (total_pnl / 10000) * 100  # Starting balance was 10k
        
        win_rate = winning_trades / total_trades if total_trades > 0 else 0
        
        # Calculate average win/loss
        wins = [t['pnl_dollar'] for t in self.all_trades if t['pnl_dollar'] > 0]
        losses = [t['pnl_dollar'] for t in self.all_trades if t['pnl_dollar'] < 0]
        
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        
        # Calculate profit factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # Calculate max drawdown
        balances = [10000]  # Starting balance
        for trade in self.all_trades:
            balances.append(balances[-1] + trade['pnl_dollar'])
        
        peak = balances[0]
        max_drawdown = 0
        for balance in balances:
            if balance > peak:
                peak = balance
            drawdown = (peak - balance) / peak * 100
            max_drawdown = max(max_drawdown, drawdown)
        
        # Print results
        print("\n" + "="*70)
        print("🏁 SEQUENTIAL BACKTEST RESULTS")
        print("="*70)
        print(f"Total Sessions: {len(self.session_results)}")
        print(f"Total Trades: {total_trades}")
        print(f"Total P&L: ${total_pnl:.2f}")
        print(f"Total Return: {total_return:.2f}%")
        print(f"Win Rate: {win_rate:.1%}")
        print(f"Average Win: ${avg_win:.2f}")
        print(f"Average Loss: ${avg_loss:.2f}")
        print(f"Profit Factor: {profit_factor:.2f}")
        print(f"Max Drawdown: {max_drawdown:.2f}%")
        
        # Session breakdown by symbol
        print(f"\n📊 SESSION BREAKDOWN BY SYMBOL:")
        symbols = list(set([result['symbol'] for result in self.session_results]))
        for symbol in symbols:
            symbol_results = [r for r in self.session_results if r['symbol'] == symbol]
            symbol_trades = sum(r['trades'] for r in symbol_results)
            symbol_pnl = sum(r['pnl'] for r in symbol_results)
            symbol_win_rate = sum(r['trades'] * r['win_rate'] for r in symbol_results) / symbol_trades if symbol_trades > 0 else 0
            print(f"  {symbol}: {symbol_trades} trades, ${symbol_pnl:.2f} P&L, {symbol_win_rate:.1%} win rate")
        
        print(f"\n📊 DETAILED SESSION BREAKDOWN:")
        for result in self.session_results:
            print(f"  {result['symbol']} Session {result['session']}: {result['trades']} trades, ${result['pnl']:.2f} P&L, {result['win_rate']:.1%} win rate")
        
        # Store overall results
        self.overall_results = {
            'total_trades': total_trades,
            'total_pnl': total_pnl,
            'total_return': total_return,
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'max_drawdown': max_drawdown,
            'sessions': len(self.session_results)
        }
    
    def plot_sequential_results(self):
        """Create comprehensive plots of the sequential backtest results"""
        if not self.all_equity_curves:
            self.logger.warning("No equity curve data to plot")
            return
        
        # Create DataFrame for plotting
        df = pd.DataFrame(self.all_equity_curves)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # Create subplots
        fig = make_subplots(
            rows=3, cols=1,
            subplot_titles=('Equity Curve', 'Trade P&L Distribution', 'Session Performance'),
            vertical_spacing=0.1,
            row_heights=[0.5, 0.25, 0.25]
        )
        
        # 1. Equity Curve
        fig.add_trace(
            go.Scatter(
                x=df['timestamp'],
                y=df['equity'],
                mode='lines',
                name='Portfolio Value',
                line=dict(color='blue', width=2)
            ),
            row=1, col=1
        )
        
        # Add session markers
        for session in range(1, len(self.session_results) + 1):
            session_data = df[df['session'] == session]
            if not session_data.empty:
                fig.add_trace(
                    go.Scatter(
                        x=session_data['timestamp'],
                        y=session_data['equity'],
                        mode='markers',
                        name=f'Session {session}',
                        marker=dict(size=4),
                        showlegend=False
                    ),
                    row=1, col=1
                )
        
        # 2. Trade P&L Distribution
        if self.all_trades:
            trade_pnls = [trade['pnl_dollar'] for trade in self.all_trades]
            fig.add_trace(
                go.Histogram(
                    x=trade_pnls,
                    nbinsx=20,
                    name='Trade P&L Distribution',
                    marker_color='green' if sum(trade_pnls) > 0 else 'red'
                ),
                row=2, col=1
            )
        
        # 3. Session Performance
        if self.session_results:
            sessions = [r['session'] for r in self.session_results]
            session_pnls = [r['pnl'] for r in self.session_results]
            colors = ['green' if pnl > 0 else 'red' for pnl in session_pnls]
            
            fig.add_trace(
                go.Bar(
                    x=sessions,
                    y=session_pnls,
                    name='Session P&L',
                    marker_color=colors
                ),
                row=3, col=1
            )
        
        # Update layout
        fig.update_layout(
            title=f'Sequential Backtest Results - {len(self.all_trades)} Trades Over {len(self.session_results)} Sessions',
            height=800,
            showlegend=True
        )
        
        # Save plot
        filename = f'sequential_backtest_results_{datetime.now().strftime("%Y%m%d_%H%M%S")}.html'
        fig.write_html(filename)
        self.logger.info(f"📊 Results saved to: {filename}")
        
        return filename

async def main():
    """Main function to run sequential backtest"""
    backtester = SequentialBacktester()
    
    try:
        # Run sequential backtest for 7 days total, 3 days per session
        await backtester.run_sequential_backtest(
            symbols=["SOL", "ETH", "XRP"],
            total_days=7,
            session_days=3
        )
    except Exception as e:
        logging.error(f"Main error: {e}")

if __name__ == "__main__":
    print("🚀 Multi-Symbol Sequential Backtest")
    print("This will run backtests on SOL, ETH, and XRP.")
    print("Each session: 3 days (~4,320 minutes)")
    print("Total period: 7 days (last 7 days of available data)")
    print("Make sure you have set up your Hyperliquid API key in the .env file.")
    print()
    
    asyncio.run(main()) 