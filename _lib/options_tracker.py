"""
Options Portfolio Tracker - Core Module
Handles trade entry, exit, Greeks calculation, and risk management.
"""
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime
import yfinance as yf
from scipy.stats import norm

DB_PATH = r"C:\Users\srini\Options_chain_data\US_data.db"

# ============================================================================
# GREEKS CALCULATION (Black-Scholes)
# ============================================================================

def calculate_greeks(S, K, T, r, sigma, option_type='call'):
    """
    Calculate option Greeks using Black-Scholes model.
    
    Args:
        S: Current stock price
        K: Strike price
        T: Time to expiration (years)
        r: Risk-free rate (e.g., 0.05 for 5%)
        sigma: Implied volatility (e.g., 0.25 for 25%)
        option_type: 'call' or 'put'
    
    Returns:
        dict with delta, gamma, theta, vega, rho
    """
    if T <= 0:
        # Expired option
        if option_type.lower() == 'call':
            return {
                'delta': 1.0 if S > K else 0.0,
                'gamma': 0.0,
                'theta': 0.0,
                'vega': 0.0,
                'rho': 0.0,
                'price': max(0, S - K)
            }
        else:
            return {
                'delta': -1.0 if S < K else 0.0,
                'gamma': 0.0,
                'theta': 0.0,
                'vega': 0.0,
                'rho': 0.0,
                'price': max(0, K - S)
            }
    
    # Standard Black-Scholes
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    
    if option_type.lower() == 'call':
        delta = norm.cdf(d1)
        price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:  # put
        delta = -norm.cdf(-d1)
        price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    
    # Common Greeks
    gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
    vega = S * norm.pdf(d1) * np.sqrt(T) / 100  # Per 1% change in IV
    theta_call = (-S * norm.pdf(d1) * sigma / (2 * np.sqrt(T)) - r * K * np.exp(-r * T) * norm.cdf(d2)) / 365
    theta_put = (-S * norm.pdf(d1) * sigma / (2 * np.sqrt(T)) + r * K * np.exp(-r * T) * norm.cdf(-d2)) / 365
    theta = theta_call if option_type.lower() == 'call' else theta_put
    
    rho_call = K * T * np.exp(-r * T) * norm.cdf(d2) / 100
    rho_put = -K * T * np.exp(-r * T) * norm.cdf(-d2) / 100
    rho = rho_call if option_type.lower() == 'call' else rho_put
    
    return {
        'delta': delta,
        'gamma': gamma,
        'theta': theta,
        'vega': vega,
        'rho': rho,
        'price': price
    }


def get_current_option_data(ticker, strike, expiry, option_type):
    """
    Get current option price and IV from yfinance.
    
    Returns:
        dict with price, bid, ask, volume, oi, iv
    """
    try:
        stock = yf.Ticker(ticker)
        chain = stock.option_chain(expiry)
        
        if option_type.upper() == 'CALL':
            options = chain.calls
        else:
            options = chain.puts
        
        option = options[options['strike'] == strike]
        
        if option.empty:
            return None
        
        return {
            'price': float(option['lastPrice'].iloc[0]),
            'bid': float(option['bid'].iloc[0]),
            'ask': float(option['ask'].iloc[0]),
            'volume': int(option['volume'].iloc[0]) if not pd.isna(option['volume'].iloc[0]) else 0,
            'open_interest': int(option['openInterest'].iloc[0]) if not pd.isna(option['openInterest'].iloc[0]) else 0,
            'iv': float(option['impliedVolatility'].iloc[0]) if 'impliedVolatility' in option.columns else 0.3
        }
    except Exception as e:
        print(f"Error getting option data: {e}")
        return None


# ============================================================================
# TRADE ENTRY
# ============================================================================

def enter_trade(ticker, option_type, strike, expiry, quantity, entry_price=None, 
                strategy='manual', signal_zscore=None, notes=''):
    """
    Enter a new option trade.
    
    Args:
        ticker: Stock symbol
        option_type: 'CALL' or 'PUT'
        strike: Strike price
        expiry: Expiry date (YYYY-MM-DD)
        quantity: Number of contracts
        entry_price: Entry price (if None, use current market price)
        strategy: Trading strategy name
        signal_zscore: Z-score from signal detection
        notes: Additional notes
    
    Returns:
        trade_id if successful, None otherwise
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Get current stock price
        stock = yf.Ticker(ticker)
        stock_price = stock.history(period='1d')['Close'].iloc[-1]
        
        # Get option data
        if entry_price is None:
            opt_data = get_current_option_data(ticker, strike, expiry, option_type)
            if opt_data is None:
                print(f"❌ Could not get option data for {ticker} {strike} {option_type}")
                return None
            entry_price = opt_data['price']
            iv = opt_data['iv']
        else:
            opt_data = get_current_option_data(ticker, strike, expiry, option_type)
            iv = opt_data['iv'] if opt_data else 0.3
        
        # Calculate days to expiry
        expiry_date = datetime.strptime(expiry, '%Y-%m-%d')
        days_to_expiry = (expiry_date - datetime.now()).days
        T = days_to_expiry / 365.0
        
        # Calculate Greeks
        greeks = calculate_greeks(stock_price, strike, T, 0.05, iv, option_type.lower())
        
        # Calculate entry cost
        entry_cost = entry_price * quantity * 100  # Options are per 100 shares
        
        # Get portfolio settings for risk management
        cursor.execute("SELECT setting_value FROM portfolio_settings WHERE setting_name = 'portfolio_value'")
        portfolio_value = float(cursor.fetchone()[0])
        
        cursor.execute("SELECT setting_value FROM portfolio_settings WHERE setting_name = 'stop_loss_pct'")
        stop_loss_pct = float(cursor.fetchone()[0])
        
        cursor.execute("SELECT setting_value FROM portfolio_settings WHERE setting_name = 'take_profit_pct'")
        take_profit_pct = float(cursor.fetchone()[0])
        
        # Calculate protection levels
        stop_loss_price = entry_price * (1 - stop_loss_pct / 100)
        take_profit_price = entry_price * (1 + take_profit_pct / 100)
        max_loss = (entry_price - stop_loss_price) * quantity * 100
        position_size_pct = (entry_cost / portfolio_value) * 100
        
        # Insert trade
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("""
        INSERT INTO trades (
            ticker, strategy, entry_date, entry_time, option_type, strike, expiry,
            entry_price, quantity, entry_cost, signal_zscore, signal_source,
            entry_delta, entry_gamma, entry_theta, entry_vega, entry_iv,
            current_delta, current_gamma, current_theta, current_vega, current_iv,
            stop_loss_price, take_profit_price, max_loss_amt, position_size_pct,
            status, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ticker, strategy, datetime.now().strftime('%Y-%m-%d'), datetime.now().strftime('%H:%M:%S'),
            option_type, strike, expiry, entry_price, quantity, entry_cost, signal_zscore, strategy,
            greeks['delta'], greeks['gamma'], greeks['theta'], greeks['vega'], iv,
            greeks['delta'], greeks['gamma'], greeks['theta'], greeks['vega'], iv,
            stop_loss_price, take_profit_price, max_loss, position_size_pct,
            'OPEN', notes, now, now
        ))
        
        trade_id = cursor.lastrowid
        
        # Create initial snapshot
        cursor.execute("""
        INSERT INTO position_snapshots (
            trade_id, snapshot_date, snapshot_time, underlying_price, option_price,
            bid, ask, volume, open_interest, delta, gamma, theta, vega, iv,
            unrealized_pnl, unrealized_pnl_pct, days_to_expiry
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_id, datetime.now().strftime('%Y-%m-%d'), datetime.now().strftime('%H:%M:%S'),
            stock_price, entry_price,
            opt_data['bid'] if opt_data else entry_price,
            opt_data['ask'] if opt_data else entry_price,
            opt_data['volume'] if opt_data else 0,
            opt_data['open_interest'] if opt_data else 0,
            greeks['delta'], greeks['gamma'], greeks['theta'], greeks['vega'], iv,
            0.0, 0.0, days_to_expiry
        ))
        
        conn.commit()
        
        print("✅ Trade entered successfully!")
        print(f"   Trade ID: {trade_id}")
        print(f"   {ticker} {option_type} ${strike} exp {expiry}")
        print(f"   Quantity: {quantity} contracts")
        print(f"   Entry Price: ${entry_price:.2f}")
        print(f"   Entry Cost: ${entry_cost:,.2f}")
        print(f"   Position Size: {position_size_pct:.2f}% of portfolio")
        print(f"   Stop Loss: ${stop_loss_price:.2f} (Max Loss: ${max_loss:,.2f})")
        print(f"   Take Profit: ${take_profit_price:.2f}")
        print(f"   Delta: {greeks['delta']:.3f}, Theta: {greeks['theta']:.3f}")
        
        return trade_id
    
    except Exception as e:
        print(f"❌ Error entering trade: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()


# ============================================================================
# TRADE EXIT
# ============================================================================

def exit_trade(trade_id, exit_price=None, exit_reason='manual', notes=''):
    """
    Exit an existing trade.
    
    Args:
        trade_id: Trade ID to exit
        exit_price: Exit price (if None, use current market price)
        exit_reason: Reason for exit
        notes: Additional notes
    
    Returns:
        True if successful, False otherwise
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Get trade details
        cursor.execute("""
        SELECT ticker, option_type, strike, expiry, quantity, entry_price, entry_date
        FROM trades WHERE trade_id = ? AND status = 'OPEN'
        """, (trade_id,))
        
        trade = cursor.fetchone()
        if not trade:
            print(f"❌ Trade {trade_id} not found or already closed")
            return False
        
        ticker, option_type, strike, expiry, quantity, entry_price, entry_date = trade
        
        # Get current option price
        if exit_price is None:
            opt_data = get_current_option_data(ticker, strike, expiry, option_type)
            if opt_data is None:
                print("⚠️ Could not get current option price, using last known price")
                # Use last snapshot price
                cursor.execute("""
                SELECT option_price FROM position_snapshots
                WHERE trade_id = ? ORDER BY snapshot_date DESC, snapshot_time DESC LIMIT 1
                """, (trade_id,))
                result = cursor.fetchone()
                exit_price = result[0] if result else entry_price
            else:
                exit_price = opt_data['price']
        
        # Calculate P&L
        pnl = (exit_price - entry_price) * quantity * 100
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100
        
        # Calculate days held
        entry_dt = datetime.strptime(entry_date, '%Y-%m-%d')
        days_held = (datetime.now() - entry_dt).days
        
        # Update trade
        cursor.execute("""
        UPDATE trades SET
            exit_date = ?,
            exit_time = ?,
            exit_price = ?,
            exit_reason = ?,
            pnl = ?,
            pnl_pct = ?,
            days_held = ?,
            status = 'CLOSED',
            notes = CASE WHEN notes = '' THEN ? ELSE notes || '; ' || ? END,
            updated_at = ?
        WHERE trade_id = ?
        """, (
            datetime.now().strftime('%Y-%m-%d'),
            datetime.now().strftime('%H:%M:%S'),
            exit_price,
            exit_reason,
            pnl,
            pnl_pct,
            days_held,
            notes, notes,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            trade_id
        ))
        
        conn.commit()
        
        print("✅ Trade exited successfully!")
        print(f"   Trade ID: {trade_id}")
        print(f"   {ticker} {option_type} ${strike}")
        print(f"   Entry: ${entry_price:.2f} → Exit: ${exit_price:.2f}")
        print(f"   P&L: ${pnl:,.2f} ({pnl_pct:+.2f}%)")
        print(f"   Days Held: {days_held}")
        print(f"   Reason: {exit_reason}")
        
        return True
    
    except Exception as e:
        print(f"❌ Error exiting trade: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


# ============================================================================
# POSITION MONITORING
# ============================================================================

def update_position_snapshots():
    """
    Update snapshots for all open positions.
    Should be run daily or intraday for active monitoring.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get all open positions
    cursor.execute("""
    SELECT trade_id, ticker, option_type, strike, expiry, quantity, entry_price
    FROM trades WHERE status = 'OPEN'
    """)
    
    open_trades = cursor.fetchall()
    
    if not open_trades:
        print("No open trades to update")
        return
    
    print(f"Updating {len(open_trades)} open positions...")
    
    for trade in open_trades:
        trade_id, ticker, option_type, strike, expiry, quantity, entry_price = trade
        
        try:
            # Get current stock price
            stock = yf.Ticker(ticker)
            stock_price = stock.history(period='1d')['Close'].iloc[-1]
            
            # Get current option data
            opt_data = get_current_option_data(ticker, strike, expiry, option_type)
            
            if opt_data is None:
                print(f"  ⚠️ Skipping {ticker} {option_type} ${strike} - no data")
                continue
            
            # Calculate days to expiry
            expiry_date = datetime.strptime(expiry, '%Y-%m-%d')
            days_to_expiry = (expiry_date - datetime.now()).days
            T = max(0.001, days_to_expiry / 365.0)
            
            # Calculate Greeks
            greeks = calculate_greeks(stock_price, strike, T, 0.05, opt_data['iv'], option_type.lower())
            
            # Calculate unrealized P&L
            current_value = opt_data['price'] * quantity * 100
            entry_cost = entry_price * quantity * 100
            unrealized_pnl = current_value - entry_cost
            unrealized_pnl_pct = (unrealized_pnl / entry_cost) * 100
            
            # Insert snapshot
            cursor.execute("""
            INSERT INTO position_snapshots (
                trade_id, snapshot_date, snapshot_time, underlying_price, option_price,
                bid, ask, volume, open_interest, delta, gamma, theta, vega, iv,
                unrealized_pnl, unrealized_pnl_pct, days_to_expiry
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_id, datetime.now().strftime('%Y-%m-%d'), datetime.now().strftime('%H:%M:%S'),
                stock_price, opt_data['price'], opt_data['bid'], opt_data['ask'],
                opt_data['volume'], opt_data['open_interest'],
                greeks['delta'], greeks['gamma'], greeks['theta'], greeks['vega'], opt_data['iv'],
                unrealized_pnl, unrealized_pnl_pct, days_to_expiry
            ))
            
            # Update current Greeks in trades table
            cursor.execute("""
            UPDATE trades SET
                current_delta = ?,
                current_gamma = ?,
                current_theta = ?,
                current_vega = ?,
                current_iv = ?,
                updated_at = ?
            WHERE trade_id = ?
            """, (
                greeks['delta'], greeks['gamma'], greeks['theta'], greeks['vega'], opt_data['iv'],
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                trade_id
            ))
            
            # Update max profit/drawdown
            cursor.execute("""
            UPDATE trades SET
                max_profit = CASE WHEN ? > COALESCE(max_profit, 0) THEN ? ELSE max_profit END,
                max_drawdown = CASE WHEN ? < COALESCE(max_drawdown, 0) THEN ? ELSE max_drawdown END
            WHERE trade_id = ?
            """, (unrealized_pnl, unrealized_pnl, unrealized_pnl, unrealized_pnl, trade_id))
            
            print(f"  ✅ {ticker} {option_type} ${strike}: ${opt_data['price']:.2f} (P&L: ${unrealized_pnl:+,.2f})")
        
        except Exception as e:
            print(f"  ❌ Error updating {ticker}: {e}")
            continue
    
    conn.commit()
    conn.close()
    print("✅ Position snapshots updated")


def check_exit_conditions():
    """
    Check if any open positions should be exited based on:
    - Stop loss hit
    - Take profit hit
    - Trailing stop
    - Expiry approaching
    - Abnormal movement detected
    
    Returns:
        list of (trade_id, exit_reason) tuples
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check auto-exit enabled
    cursor.execute("SELECT setting_value FROM portfolio_settings WHERE setting_name = 'auto_exit_enabled'")
    auto_exit = cursor.fetchone()[0].lower() == 'true'
    
    if not auto_exit:
        conn.close()
        return []
    
    exits = []
    
    # Get all open trades with latest snapshot
    cursor.execute("""
    SELECT t.trade_id, t.ticker, t.option_type, t.strike, t.entry_price,
           t.stop_loss_price, t.take_profit_price, t.trailing_stop_pct,
           s.option_price, s.unrealized_pnl_pct, s.days_to_expiry
    FROM trades t
    LEFT JOIN (
        SELECT trade_id, option_price, unrealized_pnl_pct, days_to_expiry
        FROM position_snapshots
        WHERE (trade_id, snapshot_date, snapshot_time) IN (
            SELECT trade_id, MAX(snapshot_date), MAX(snapshot_time)
            FROM position_snapshots
            GROUP BY trade_id
        )
    ) s ON t.trade_id = s.trade_id
    WHERE t.status = 'OPEN'
    """)
    
    open_trades = cursor.fetchall()
    
    for trade in open_trades:
        trade_id, ticker, option_type, strike, entry_price, stop_loss, take_profit, trailing_stop, current_price, pnl_pct, days_to_expiry = trade
        
        if current_price is None:
            continue
        
        # Check stop loss
        if stop_loss and current_price <= stop_loss:
            exits.append((trade_id, 'stop_loss', f"Price ${current_price:.2f} hit stop ${stop_loss:.2f}"))
        
        # Check take profit
        elif take_profit and current_price >= take_profit:
            exits.append((trade_id, 'profit_target', f"Price ${current_price:.2f} hit target ${take_profit:.2f}"))
        
        # Check expiry (exit 3 days before expiry to avoid pin risk)
        elif days_to_expiry is not None and days_to_expiry <= 3:
            exits.append((trade_id, 'expiry', f"Only {days_to_expiry} days to expiry"))
        
        # Check theta decay (close if theta > 10% of option value per day)
        # Could add more sophisticated checks here
    
    conn.close()
    return exits


# ============================================================================
# UTILITIES
# ============================================================================

def get_open_positions():
    """Get all open positions with current P&L"""
    conn = sqlite3.connect(DB_PATH)
    
    query = """
    SELECT t.trade_id, t.ticker, t.option_type, t.strike, t.expiry,
           t.quantity, t.entry_price, t.entry_date, t.strategy,
           t.stop_loss_price, t.take_profit_price,
           s.option_price as current_price,
           s.unrealized_pnl, s.unrealized_pnl_pct,
           s.delta, s.theta, s.iv,
           s.days_to_expiry
    FROM trades t
    LEFT JOIN (
        SELECT trade_id, option_price, unrealized_pnl, unrealized_pnl_pct,
               delta, theta, iv, days_to_expiry
        FROM position_snapshots
        WHERE (trade_id, snapshot_date, snapshot_time) IN (
            SELECT trade_id, MAX(snapshot_date), MAX(snapshot_time)
            FROM position_snapshots
            GROUP BY trade_id
        )
    ) s ON t.trade_id = s.trade_id
    WHERE t.status = 'OPEN'
    ORDER BY s.unrealized_pnl DESC
    """
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


def get_portfolio_summary():
    """Get overall portfolio metrics"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Total open positions
    cursor.execute("SELECT COUNT(*) FROM trades WHERE status = 'OPEN'")
    open_count = cursor.fetchone()[0]
    
    # Total unrealized P&L
    cursor.execute("""
    SELECT SUM(s.unrealized_pnl)
    FROM trades t
    JOIN (
        SELECT trade_id, unrealized_pnl
        FROM position_snapshots
        WHERE (trade_id, snapshot_date, snapshot_time) IN (
            SELECT trade_id, MAX(snapshot_date), MAX(snapshot_time)
            FROM position_snapshots
            GROUP BY trade_id
        )
    ) s ON t.trade_id = s.trade_id
    WHERE t.status = 'OPEN'
    """)
    unrealized_pnl = cursor.fetchone()[0] or 0
    
    # Total realized P&L
    cursor.execute("SELECT SUM(pnl) FROM trades WHERE status = 'CLOSED'")
    realized_pnl = cursor.fetchone()[0] or 0
    
    # Win rate
    cursor.execute("SELECT COUNT(*) FROM trades WHERE status = 'CLOSED' AND pnl > 0")
    winners = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM trades WHERE status = 'CLOSED'")
    total_closed = cursor.fetchone()[0]
    win_rate = (winners / total_closed * 100) if total_closed > 0 else 0
    
    # Portfolio delta
    cursor.execute("""
    SELECT SUM(s.delta * t.quantity)
    FROM trades t
    JOIN (
        SELECT trade_id, delta
        FROM position_snapshots
        WHERE (trade_id, snapshot_date, snapshot_time) IN (
            SELECT trade_id, MAX(snapshot_date), MAX(snapshot_time)
            FROM position_snapshots
            GROUP BY trade_id
        )
    ) s ON t.trade_id = s.trade_id
    WHERE t.status = 'OPEN'
    """)
    portfolio_delta = cursor.fetchone()[0] or 0
    
    conn.close()
    
    return {
        'open_positions': open_count,
        'unrealized_pnl': unrealized_pnl,
        'realized_pnl': realized_pnl,
        'total_pnl': unrealized_pnl + realized_pnl,
        'win_rate': win_rate,
        'portfolio_delta': portfolio_delta
    }


def get_closed_trades(days_back=None):
    """
    Get all closed trades with optional filter for recent trades
    
    Args:
        days_back: Optional int, only return trades closed in last N days
    
    Returns:
        DataFrame with closed trades details
    """
    conn = sqlite3.connect(DB_PATH)
    
    query = """
    SELECT 
        t.trade_id,
        t.ticker,
        t.option_type,
        t.strike,
        t.expiry,
        t.quantity,
        t.entry_price,
        t.entry_date,
        t.exit_price,
        t.exit_date,
        t.pnl,
        t.pnl_percent,
        t.exit_reason,
        t.hold_days,
        t.strategy,
        t.notes
    FROM trades t
    WHERE t.status = 'CLOSED'
    """
    
    if days_back:
        query += f" AND julianday('now') - julianday(t.exit_date) <= {days_back}"
    
    query += " ORDER BY t.exit_date DESC"
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


if __name__ == '__main__':
    print("Options Portfolio Tracker - Core Module")
    print("Import this module to use in your Streamlit app")
