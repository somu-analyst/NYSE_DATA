"""
Portfolio Analytics Module
Calculates XIRR, allocation, sector breakdown, benchmark comparison
Generates performance reports and comparison against index
"""
import sqlite3
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from scipy.optimize import newton
import yfinance as yf

DB_PATH = r"C:\Users\srini\Options_chain_data\US_data.db"


def calculate_xirr(cash_flows):
    """
    Calculate XIRR (Internal Rate of Return) for portfolio
    
    Args:
        cash_flows: List of tuples (date_str, amount)
                   Negative = investment, Positive = withdrawal/gain
    
    Returns: XIRR percent
    """
    try:
        # Convert dates to years from first date
        dates = sorted([datetime.strptime(cf[0], '%Y-%m-%d') for cf in cash_flows])
        
        if not dates:
            return 0
        
        first_date = dates[0]
        
        # Create arrays for NPV calculation
        days_from_start = [(datetime.strptime(cf[0], '%Y-%m-%d') - first_date).days for cf in cash_flows]
        cf_values = [cf[1] for cf in cash_flows]
        
        # Total portfolio value for final cash flow
        total_value = sum([cf[1] for cf in cash_flows if cf[1] > 0])
        
        # NPV function
        def npv(rate):
            return sum([cf_values[i] / ((1 + rate) ** (days_from_start[i] / 365)) 
                       for i in range(len(cash_flows))])
        
        try:
            xirr = newton(npv, 0.1)
            return xirr * 100
        except:
            return 0
    except Exception as e:
        print(f"Error calculating XIRR: {e}")
        return 0


def get_portfolio_cash_flows(account_id=None, start_date=None):
    """
    Get all deposits, withdrawals, and gains for XIRR calculation
    
    Returns: List of (date, amount) tuples
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get account opening and closings
        query = """
        SELECT opening_date, initial_balance FROM accounts WHERE is_active = 1
        """
        if account_id:
            query += f" AND account_id = {account_id}"
        
        cursor.execute(query)
        accounts = cursor.fetchall()
        
        cash_flows = []
        
        # Add initial deposits
        for opening_date, initial_balance in accounts:
            if opening_date:
                cash_flows.append((opening_date, -initial_balance))
        
        # Add realized gains/losses
        cursor.execute("""
        SELECT exit_date, SUM(pnl) 
        FROM trades 
        WHERE status = 'CLOSED' AND exit_date IS NOT NULL
        GROUP BY exit_date
        """)
        
        for exit_date, total_pnl in cursor.fetchall():
            if exit_date and total_pnl:
                cash_flows.append((exit_date, total_pnl))
        
        conn.close()
        
        return sorted(cash_flows)
    except Exception as e:
        print(f"Error getting cash flows: {e}")
        return []


def get_portfolio_allocation():
    """
    Get current portfolio allocation by sector/asset type
    
    Returns: Dict with percentages
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        
        # Get all open positions
        query = """
        SELECT 
            ticker, 
            SUM(entry_price * quantity) as position_value,
            COUNT(*) as trade_count
        FROM trades 
        WHERE status = 'OPEN'
        GROUP BY ticker
        """
        
        df = pd.read_sql_query(query, conn)
        
        if df.empty:
            return {'total_value': 0, 'allocation': {}}
        
        total_value = df['position_value'].sum()
        
        allocation = {}
        for _, row in df.iterrows():
            allocation[row['ticker']] = {
                'value': row['position_value'],
                'percentage': (row['position_value'] / total_value * 100) if total_value > 0 else 0,
                'trades': row['trade_count']
            }
        
        conn.close()
        
        return {
            'total_value': total_value,
            'allocation': allocation,
            'num_positions': len(allocation)
        }
    except Exception as e:
        print(f"Error getting allocation: {e}")
        return {'total_value': 0, 'allocation': {}}


def get_sector_allocation():
    """
    Get allocation by sector (Technology, Healthcare, Financials, etc.)
    
    Returns: Dict with sector percentages
    """
    try:
        # Simplified sector mapping - in production use proper APIs
        sector_map = {
            'AAPL': 'Technology', 'MSFT': 'Technology', 'NVDA': 'Technology', 'META': 'Technology',
            'GOOGL': 'Technology', 'AMZN': 'Consumer', 'TSLA': 'Consumer',
            'JPM': 'Financials', 'BAC': 'Financials', 'GS': 'Financials',
            'JNJ': 'Healthcare', 'PFE': 'Healthcare', 'ABBV': 'Healthcare',
            'XOM': 'Energy', 'CVX': 'Energy',
            'BA': 'Industrials', 'CAT': 'Industrials',
            'SPY': 'Broad Market', 'QQQ': 'Technology'
        }
        
        allocation = get_portfolio_allocation()
        
        sector_totals = {}
        total_value = allocation['total_value']
        
        for ticker, details in allocation['allocation'].items():
            sector = sector_map.get(ticker, 'Other')
            if sector not in sector_totals:
                sector_totals[sector] = 0
            sector_totals[sector] += details['value']
        
        # Convert to percentages
        sector_pct = {
            sector: (value / total_value * 100) if total_value > 0 else 0
            for sector, value in sector_totals.items()
        }
        
        return sector_pct
    except Exception as e:
        print(f"Error getting sector allocation: {e}")
        return {}


def compare_against_benchmark(benchmark_ticker='SPY', days_back=252):
    """
    Compare portfolio performance against benchmark (SPY, QQQ, etc.)
    
    Returns: Performance comparison metrics
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        
        # Get portfolio return
        query = """
        SELECT 
            SUM(pnl) as total_gain,
            COUNT(*) as total_trades,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades
        FROM trades
        """
        
        cursor = conn.cursor()
        cursor.execute(query)
        stats = cursor.fetchone()
        total_gain = stats[0] or 0
        total_trades = stats[1] or 0
        winning_trades = stats[2] or 0
        
        # Get benchmark performance
        benchmark = yf.Ticker(benchmark_ticker)
        hist = benchmark.history(period=f'{days_back}d')
        
        if hist.empty:
            return {'error': 'Benchmark data unavailable'}
        
        start_price = hist['Close'].iloc[0]
        end_price = hist['Close'].iloc[-1]
        benchmark_return = ((end_price - start_price) / start_price * 100)
        
        # Calculate portfolio metrics
        portfolio_value = 10000  # Assume starting value
        portfolio_return = (total_gain / portfolio_value * 100) if portfolio_value > 0 else 0
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        conn.close()
        
        return {
            'benchmark': benchmark_ticker,
            'days_measured': days_back,
            'portfolio_return_pct': round(portfolio_return, 2),
            'benchmark_return_pct': round(benchmark_return, 2),
            'outperformance_pct': round(portfolio_return - benchmark_return, 2),
            'portfolio_win_rate': round(win_rate, 2),
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'total_gain_usd': total_gain,
            'benchmark_closing_price': end_price,
            'benchmark_starting_price': start_price
        }
    except Exception as e:
        print(f"Error comparing benchmarks: {e}")
        return {'error': str(e)}


def calculate_sharpe_ratio(returns_list, risk_free_rate=0.02):
    """
    Calculate Sharpe Ratio for portfolio
    
    Args:
        returns_list: List of periodic returns (as decimals)
        risk_free_rate: Annual risk-free rate (default 2%)
    
    Returns: Sharpe ratio
    """
    try:
        returns_arr = np.array(returns_list)
        excess_returns = returns_arr - (risk_free_rate / 252)  # Daily risk-free rate
        
        if len(excess_returns) == 0:
            return 0
        
        sharpe = np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252)
        return round(sharpe, 2)
    except:
        return 0


def calculate_max_drawdown(equity_curve):
    """
    Calculate maximum drawdown from equity curve
    
    Args:
        equity_curve: List of portfolio values over time
    
    Returns: Max drawdown percentage
    """
    try:
        if not equity_curve or len(equity_curve) < 2:
            return 0
        
        arr = np.array(equity_curve)
        running_max = np.maximum.accumulate(arr)
        drawdowns = (arr - running_max) / running_max
        max_dd = np.min(drawdowns)
        
        return round(max_dd * 100, 2)
    except:
        return 0


def get_performance_by_strategy():
    """
    Get performance breakdown by trading strategy
    
    Returns: Strategy performance summary
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        
        query = """
        SELECT 
            strategy,
            COUNT(*) as num_trades,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
            SUM(pnl) as total_pnl,
            AVG(pnl) as avg_pnl,
            MAX(pnl) as max_win,
            MIN(pnl) as max_loss
        FROM trades
        WHERE status = 'CLOSED'
        GROUP BY strategy
        """
        
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        if df.empty:
            return {}
        
        results = {}
        for _, row in df.iterrows():
            strategy = row['strategy'] or 'Unknown'
            total = row['num_trades']
            wins = row['wins'] or 0
            
            results[strategy] = {
                'num_trades': total,
                'winning_trades': wins,
                'losing_trades': row['losses'] or 0,
                'win_rate': round((wins / total * 100), 2) if total > 0 else 0,
                'total_pnl': row['total_pnl'] or 0,
                'avg_pnl': row['avg_pnl'] or 0,
                'max_win': row['max_win'] or 0,
                'max_loss': row['max_loss'] or 0
            }
        
        return results
    except Exception as e:
        print(f"Error getting strategy performance: {e}")
        return {}


def create_performance_report(period='ytd'):
    """
    Create comprehensive performance report
    
    Args:
        period: 'ytd', 'month', '3month', 'year', 'all'
    
    Returns: Detailed performance report
    """
    try:
        # Calculate period dates
        today = datetime.now()
        if period == 'ytd':
            start_date = datetime(today.year, 1, 1)
        elif period == 'month':
            start_date = today - timedelta(days=30)
        elif period == '3month':
            start_date = today - timedelta(days=90)
        elif period == 'year':
            start_date = today - timedelta(days=365)
        else:  # 'all'
            start_date = datetime(2000, 1, 1)
        
        start_str = start_date.strftime('%Y-%m-%d')
        
        conn = sqlite3.connect(DB_PATH)
        
        # Get trades in period
        query = f"""
        SELECT 
            COUNT(*) as total_trades,
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
            SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losing_trades,
            SUM(pnl) as total_pnl,
            AVG(pnl) as avg_pnl,
            MAX(pnl) as largest_win,
            MIN(pnl) as largest_loss,
            AVG(days_held) as avg_holding_days
        FROM trades
        WHERE status = 'CLOSED' AND exit_date >= '{start_str}'
        """
        
        df = pd.read_sql_query(query, conn)
        
        if df.empty or df.iloc[0]['total_trades'] is None:
            return {'period': period, 'trades': 0, 'message': 'No trades in period'}
        
        row = df.iloc[0]
        total = row['total_trades']
        wins = row['winning_trades'] or 0
        
        report = {
            'period': period,
            'start_date': start_str,
            'end_date': today.strftime('%Y-%m-%d'),
            'total_trades': total,
            'winning_trades': wins,
            'losing_trades': row['losing_trades'] or 0,
            'win_rate_pct': round((wins / total * 100), 2) if total > 0 else 0,
            'total_pnl': row['total_pnl'] or 0,
            'avg_pnl': row['avg_pnl'] or 0,
            'largest_win': row['largest_win'] or 0,
            'largest_loss': row['largest_loss'] or 0,
            'avg_holding_days': round(row['avg_holding_days'] or 0, 1),
            'strategy_breakdown': get_performance_by_strategy()
        }
        
        # Add benchmark comparison
        if period in ['ytd', 'year']:
            days = 365 if period == 'year' else (today - datetime(today.year, 1, 1)).days
            report['benchmark_comparison'] = compare_against_benchmark('SPY', days)
        
        conn.close()
        return report
    except Exception as e:
        print(f"Error creating performance report: {e}")
        return {'error': str(e)}


if __name__ == '__main__':
    print("Portfolio Analytics Module - Functions Available:")
    print("=" * 60)
    print()
    print("Key Functions:")
    print("  calculate_xirr(cash_flows) - Internal rate of return")
    print("  get_portfolio_allocation() - Asset allocation by ticker")
    print("  get_sector_allocation() - Allocation by sector")
    print("  compare_against_benchmark(benchmark_ticker, days_back)")
    print("  calculate_sharpe_ratio(returns_list)")
    print("  calculate_max_drawdown(equity_curve)")
    print("  get_performance_by_strategy() - Returns by strategy")
    print("  create_performance_report(period) - Complete performance report")
    print()
