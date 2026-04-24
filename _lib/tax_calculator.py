"""
Tax Calculation Module
Tracks capital gains, losses, wash sales, and tax liability
Supports short-term vs long-term capital gains, multiple account types
"""
import sqlite3
from datetime import datetime, timedelta
import pandas as pd

DB_PATH = r"C:\Users\srini\Options_chain_data\US_data.db"

def calculate_holding_period(entry_date_str, exit_date_str=None):
    """
    Calculate holding period and determine if long-term capital gain
    Long-term = > 365 days (or > 1 year)
    Entry: 'YYYY-MM-DD' format
    """
    try:
        entry_date = datetime.strptime(entry_date_str, '%Y-%m-%d')
        exit_date = datetime.strptime(exit_date_str, '%Y-%m-%d') if exit_date_str else datetime.now()
        
        holding_days = (exit_date - entry_date).days
        is_long_term = holding_days >= 365
        holding_period = 'long_term' if is_long_term else 'short_term'
        
        return {
            'holding_days': holding_days,
            'holding_period': holding_period,
            'is_long_term': is_long_term
        }
    except Exception as e:
        print(f"Error calculating holding period: {e}")
        return {'holding_days': 0, 'holding_period': 'unknown', 'is_long_term': False}


def detect_wash_sale(ticker, exit_date_str, days_window=30):
    """
    Detect wash sales: buying same security within 30 days before/after a loss
    Rule: Loss disallowed if same/substantially identical security bought/sold within ±30 days
    
    Returns list of wash sale violations
    """
    try:
        exit_date = datetime.strptime(exit_date_str, '%Y-%m-%d')
        window_start = exit_date - timedelta(days=days_window)
        window_end = exit_date + timedelta(days=days_window)
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Find trades in window
        cursor.execute("""
        SELECT trade_id, entry_date, exit_date, pnl 
        FROM trades 
        WHERE ticker = ? 
        AND entry_date >= ? 
        AND entry_date <= ?
        """, (ticker, window_start.strftime('%Y-%m-%d'), window_end.strftime('%Y-%m-%d')))
        
        trades = cursor.fetchall()
        conn.close()
        
        wash_sales = [t for t in trades if t[3] and t[3] < 0]  # Losses
        return wash_sales
    except Exception as e:
        print(f"Error detecting wash sales: {e}")
        return []


def create_tax_lot(trade_id, ticker, quantity, cost_per_share, entry_date, exit_date=None, exit_price=None):
    """
    Create a tax lot record with proper cost basis and holding period
    
    Args:
        trade_id: Reference to trade
        ticker: Stock ticker
        quantity: Number of shares
        cost_per_share: Entry price
        entry_date: 'YYYY-MM-DD'
        exit_date: 'YYYY-MM-DD' (if exited)
        exit_price: Exit price (if exited)
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        holding_info = calculate_holding_period(entry_date, exit_date)
        
        # Check for wash sales if there's a loss
        is_loss = exit_price and exit_price < cost_per_share
        wash_sales = detect_wash_sale(ticker, exit_date) if is_loss and exit_date else []
        
        exit_quantity = quantity if exit_price else None
        
        # Calculate realized gain/loss
        realized_gain = None
        if exit_price:
            realized_gain = (exit_price - cost_per_share) * quantity
        
        cursor.execute("""
        INSERT INTO tax_lots (
            trade_id, ticker, quantity, cost_per_share, acquisition_date,
            exit_quantity, exit_price, exit_date,
            holding_period, is_long_term, is_wash_sale
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_id, ticker, quantity, cost_per_share, entry_date,
            exit_quantity, exit_price, exit_date,
            holding_info['holding_period'],
            1 if holding_info['is_long_term'] else 0,
            1 if wash_sales else 0
        ))
        
        conn.commit()
        lot_id = cursor.lastrowid
        conn.close()
        
        return {
            'lot_id': lot_id,
            'holding_period': holding_info['holding_period'],
            'is_wash_sale': len(wash_sales) > 0,
            'wash_sale_count': len(wash_sales),
            'realized_gain': realized_gain
        }
    except Exception as e:
        print(f"Error creating tax lot: {e}")
        return None


def calculate_quarterly_taxes(year, quarter):
    """
    Calculate capital gains/losses for a specific quarter
    
    Args:
        year: Year (e.g., 2025)
        quarter: Q1, Q2, Q3, Q4
    
    Returns: Tax summary with short-term vs long-term breakdown
    """
    try:
        quarters = {
            'Q1': ('01-01', '03-31'),
            'Q2': ('04-01', '06-30'),
            'Q3': ('07-01', '09-30'),
            'Q4': ('10-01', '12-31')
        }
        
        if quarter not in quarters:
            return None
        
        start_month_day, end_month_day = quarters[quarter]
        start_date = f"{year}-{start_month_day}"
        end_date = f"{year}-{end_month_day}"
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get closed trades in period
        cursor.execute("""
        SELECT 
            ticker, 
            pnl,
            holding_period,
            exit_date,
            entry_date
        FROM (
            SELECT 
                t.ticker,
                (t.exit_price - t.entry_price) * t.quantity * 100 as pnl,
                CASE 
                    WHEN (julianday(t.exit_date) - julianday(t.entry_date)) >= 365 
                    THEN 'long_term' 
                    ELSE 'short_term' 
                END as holding_period,
                t.exit_date,
                t.entry_date
            FROM trades t
            WHERE t.status = 'CLOSED' 
            AND t.exit_date >= ? 
            AND t.exit_date <= ?
        )
        WHERE pnl IS NOT NULL
        """, (start_date, end_date))
        
        trades = cursor.fetchall()
        
        # Calculate totals
        short_term_gains = 0
        short_term_losses = 0
        long_term_gains = 0
        long_term_losses = 0
        short_term_count = 0
        long_term_count = 0
        
        for ticker, pnl, holding_period, exit_date, entry_date in trades:
            if pnl >= 0:
                if holding_period == 'short_term':
                    short_term_gains += pnl
                    short_term_count += 1
                else:
                    long_term_gains += pnl
                    long_term_count += 1
            else:
                if holding_period == 'short_term':
                    short_term_losses += abs(pnl)
                    short_term_count += 1
                else:
                    long_term_losses += abs(pnl)
                    long_term_count += 1
        
        # Store in database
        report_period = f"{quarter} {year}"
        cursor.execute("""
        INSERT OR REPLACE INTO tax_reports (
            report_period, report_date,
            short_term_gains, short_term_losses, short_term_trades,
            long_term_gains, long_term_losses, long_term_trades,
            total_gains, total_losses, net_capital_gain
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            report_period,
            datetime.now().strftime('%Y-%m-%d'),
            short_term_gains,
            short_term_losses,
            short_term_count,
            long_term_gains,
            long_term_losses,
            long_term_count,
            short_term_gains + long_term_gains,
            short_term_losses + long_term_losses,
            (short_term_gains - short_term_losses) + (long_term_gains - long_term_losses)
        ))
        
        conn.commit()
        conn.close()
        
        return {
            'period': report_period,
            'short_term_gains': short_term_gains,
            'short_term_losses': short_term_losses,
            'short_term_net': short_term_gains - short_term_losses,
            'short_term_trades': short_term_count,
            'long_term_gains': long_term_gains,
            'long_term_losses': long_term_losses,
            'long_term_net': long_term_gains - long_term_losses,
            'long_term_trades': long_term_count,
            'total_gains': short_term_gains + long_term_gains,
            'total_losses': short_term_losses + long_term_losses,
            'net_capital_gain': (short_term_gains - short_term_losses) + (long_term_gains - long_term_losses)
        }
    except Exception as e:
        print(f"Error calculating quarterly taxes: {e}")
        return None


def estimate_tax_liability(net_gain, account_type='normal', tax_bracket='24%'):
    """
    Estimate tax liability based on net capital gains
    
    Args:
        net_gain: Net capital gains/losses
        account_type: 'roth_ira', '401k', 'hsa' (tax-free), 'normal', 'margin' (taxable)
        tax_bracket: Taxpayer's expected tax bracket ('12%', '22%', '24%', '32%', '35%', '37%')
    
    Returns: Estimated tax liability
    """
    # Tax-advantaged accounts: $0 tax
    if account_type in ['roth_ira', '401k', 'hsa', 'traditional_ira']:
        return {
            'account_type': account_type,
            'estimated_liability': 0,
            'is_taxable': False,
            'reason': f'{account_type} account (tax-deferred/tax-free)'
        }
    
    # Taxable accounts
    bracket_rates = {
        '12%': 0.12,
        '22%': 0.22,
        '24%': 0.24,
        '32%': 0.32,
        '35%': 0.35,
        '37%': 0.37
    }
    
    tax_rate = bracket_rates.get(tax_bracket, 0.24)
    
    # Simplified: assume long-term capital gains at 15% preferential rate
    # For options (typically treated as short-term), use ordinary income rate
    estimated_tax = net_gain * tax_rate if net_gain > 0 else 0
    
    return {
        'account_type': account_type,
        'net_gain': net_gain,
        'estimated_tax_rate': tax_rate * 100,
        'estimated_liability': estimated_tax,
        'is_taxable': True,
        'note': 'Options may be subject to higher rates; consult tax advisor'
    }


def get_tax_summary(start_date=None, end_date=None, account_id=None):
    """
    Get comprehensive tax summary across specified period
    
    Args:
        start_date: 'YYYY-MM-DD' (default: start of year)
        end_date: 'YYYY-MM-DD' (default: today)
        account_id: Specific account or None for all
    """
    try:
        if not start_date:
            start_date = datetime(datetime.now().year, 1, 1).strftime('%Y-%m-%d')
        if not end_date:
            end_date = datetime.now().strftime('%Y-%m-%d')
        
        conn = sqlite3.connect(DB_PATH)
        
        # Get all closed trades in period
        query = """
        SELECT 
            t.trade_id,
            t.ticker,
            t.option_type,
            t.entry_date,
            t.exit_date,
            t.entry_price,
            t.exit_price,
            t.quantity,
            t.pnl,
            t.account_id,
            CASE 
                WHEN (julianday(t.exit_date) - julianday(t.entry_date)) >= 365 
                THEN 'long_term' 
                ELSE 'short_term' 
            END as holding_period
        FROM trades t
        WHERE t.status = 'CLOSED' 
        AND t.exit_date >= ? 
        AND t.exit_date <= ?
        """
        
        params = [start_date, end_date]
        
        if account_id:
            query += " AND t.account_id = ?"
            params.append(account_id)
        
        df = pd.read_sql_query(query, conn, params=params)
        
        if df.empty:
            return {
                'period': f'{start_date} to {end_date}',
                'trades_count': 0,
                'total_gains': 0,
                'total_losses': 0,
                'net_gain': 0,
                'short_term_count': 0,
                'long_term_count': 0
            }
        
        # Calculate totals
        df['is_gain'] = df['pnl'] >= 0
        df['is_long_term'] = df['holding_period'] == 'long_term'
        
        summary = {
            'period': f'{start_date} to {end_date}',
            'trades_count': len(df),
            'total_gains': df[df['is_gain']]['pnl'].sum(),
            'total_losses': abs(df[~df['is_gain']]['pnl'].sum()),
            'net_gain': df['pnl'].sum(),
            'short_term_gains': df[(~df['is_long_term']) & (df['is_gain'])]['pnl'].sum(),
            'short_term_losses': abs(df[(~df['is_long_term']) & (~df['is_gain'])]['pnl'].sum()),
            'short_term_count': len(df[~df['is_long_term']]),
            'long_term_gains': df[(df['is_long_term']) & (df['is_gain'])]['pnl'].sum(),
            'long_term_losses': abs(df[(df['is_long_term']) & (~df['is_gain'])]['pnl'].sum()),
            'long_term_count': len(df[df['is_long_term']]),
            'trades_by_ticker': df['ticker'].value_counts().to_dict()
        }
        
        conn.close()
        return summary
    except Exception as e:
        print(f"Error getting tax summary: {e}")
        return None


def get_account_types():
    """Return list of valid account types"""
    return {
        'cash': 'Standard cash account (no margin)',
        'margin': 'Margin account (can borrow)',
        'roth_ira': 'Roth IRA (tax-free growth)',
        '401k': '401(k) (employer-sponsored)',
        'traditional_ira': 'Traditional IRA (pre-tax contributions)',
        'hsa': 'Health Savings Account (triple tax advantage)'
    }


if __name__ == '__main__':
    print("Tax Calculation Module - Functions Available:")
    print("=" * 60)
    print()
    print("Key Functions:")
    print("  calculate_holding_period(entry_date, exit_date) - Holding period classification")
    print("  detect_wash_sale(ticker, exit_date) - Find wash sale violations")
    print("  create_tax_lot(trade_id, ticker, ...) - Create tax lot record")
    print("  calculate_quarterly_taxes(year, quarter) - Q1-Q4 tax summary")
    print("  estimate_tax_liability(net_gain, account_type, tax_bracket)")
    print("  get_tax_summary(start_date, end_date, account_id) - Full tax report")
    print()
    print("Account Types Supported:")
    for acc_type, description in get_account_types().items():
        print(f"  • {acc_type}: {description}")
    print()
