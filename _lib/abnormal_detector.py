"""
Abnormal Movement Detector
Monitors options and stock prices for unusual activity that might affect open positions.
"""
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime
import yfinance as yf

DB_PATH = r"C:\Users\srini\Options_chain_data\US_data.db"

def detect_price_spike(ticker, threshold_pct=5.0):
    """
    Detect abnormal price spikes in underlying stock.
    
    Args:
        ticker: Stock symbol
        threshold_pct: Percentage change threshold (default 5%)
    
    Returns:
        dict with movement details or None
    """
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period='5d')
        
        if len(hist) < 2:
            return None
        
        current_price = hist['Close'].iloc[-1]
        prev_price = hist['Close'].iloc[-2]
        change_pct = ((current_price - prev_price) / prev_price) * 100
        
        if abs(change_pct) >= threshold_pct:
            severity = 'CRITICAL' if abs(change_pct) >= 10 else 'HIGH' if abs(change_pct) >= 7 else 'MEDIUM'
            
            return {
                'ticker': ticker,
                'movement_type': 'price_spike',
                'severity': severity,
                'price_change_pct': change_pct,
                'current_price': current_price,
                'prev_price': prev_price
            }
        
        return None
    
    except Exception as e:
        print(f"Error detecting price spike for {ticker}: {e}")
        return None


def detect_volume_surge(ticker, threshold_multiplier=3.0):
    """
    Detect abnormal volume surges.
    
    Args:
        ticker: Stock symbol
        threshold_multiplier: Volume vs average threshold (default 3x)
    
    Returns:
        dict with movement details or None
    """
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period='20d')
        
        if len(hist) < 10:
            return None
        
        current_volume = hist['Volume'].iloc[-1]
        avg_volume = hist['Volume'].iloc[:-1].mean()
        volume_ratio = current_volume / avg_volume
        
        if volume_ratio >= threshold_multiplier:
            severity = 'CRITICAL' if volume_ratio >= 5 else 'HIGH' if volume_ratio >= 4 else 'MEDIUM'
            
            return {
                'ticker': ticker,
                'movement_type': 'volume_surge',
                'severity': severity,
                'volume_vs_avg': volume_ratio,
                'current_volume': int(current_volume),
                'avg_volume': int(avg_volume)
            }
        
        return None
    
    except Exception as e:
        print(f"Error detecting volume surge for {ticker}: {e}")
        return None


def detect_iv_changes(ticker, strike, expiry, option_type, threshold_pct=20.0):
    """
    Detect abnormal IV changes (IV crush or spike).
    
    Args:
        ticker: Stock symbol
        strike: Strike price
        expiry: Expiry date
        option_type: 'CALL' or 'PUT'
        threshold_pct: IV change threshold (default 20%)
    
    Returns:
        dict with movement details or None
    """
    try:
        from options_tracker import get_current_option_data
        
        # Get current IV
        current_data = get_current_option_data(ticker, strike, expiry, option_type)
        if not current_data:
            return None
        
        current_iv = current_data['iv']
        
        # Get historical IV from snapshots
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
        SELECT iv FROM position_snapshots
        WHERE snapshot_date >= date('now', '-5 days')
        ORDER BY snapshot_date DESC, snapshot_time DESC
        LIMIT 5
        """)
        
        historical_ivs = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        if not historical_ivs:
            return None
        
        avg_iv = np.mean(historical_ivs)
        iv_change_pct = ((current_iv - avg_iv) / avg_iv) * 100
        
        if abs(iv_change_pct) >= threshold_pct:
            movement_type = 'iv_spike' if iv_change_pct > 0 else 'iv_crush'
            severity = 'CRITICAL' if abs(iv_change_pct) >= 40 else 'HIGH' if abs(iv_change_pct) >= 30 else 'MEDIUM'
            
            return {
                'ticker': ticker,
                'movement_type': movement_type,
                'severity': severity,
                'iv_change_pct': iv_change_pct,
                'current_iv': current_iv,
                'avg_iv': avg_iv
            }
        
        return None
    
    except Exception as e:
        print(f"Error detecting IV changes for {ticker}: {e}")
        return None


def monitor_open_positions():
    """
    Monitor all open positions for abnormal movements.
    
    Returns:
        list of detected abnormal movements
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get all open positions
    cursor.execute("""
    SELECT DISTINCT ticker, option_type, strike, expiry, trade_id
    FROM trades
    WHERE status = 'OPEN'
    """)
    
    open_positions = cursor.fetchall()
    conn.close()
    
    if not open_positions:
        print("No open positions to monitor")
        return []
    
    print(f"Monitoring {len(open_positions)} open positions for abnormal movements...")
    
    movements = []
    tickers_checked = set()
    
    for ticker, option_type, strike, expiry, trade_id in open_positions:
        # Check underlying price movements (once per ticker)
        if ticker not in tickers_checked:
            tickers_checked.add(ticker)
            
            # Price spike
            price_movement = detect_price_spike(ticker, threshold_pct=5.0)
            if price_movement:
                price_movement['affected_trades'] = str([trade_id])
                movements.append(price_movement)
                print(f"  ⚠️ {ticker}: Price spike {price_movement['price_change_pct']:+.2f}%")
            
            # Volume surge
            volume_movement = detect_volume_surge(ticker, threshold_multiplier=3.0)
            if volume_movement:
                volume_movement['affected_trades'] = str([trade_id])
                movements.append(volume_movement)
                print(f"  ⚠️ {ticker}: Volume {volume_movement['volume_ratio']:.1f}x average")
        
        # Check IV changes for each option
        iv_movement = detect_iv_changes(ticker, strike, expiry, option_type, threshold_pct=20.0)
        if iv_movement:
            iv_movement['affected_trades'] = str([trade_id])
            movements.append(iv_movement)
            print(f"  ⚠️ {ticker} {option_type} ${strike}: IV {iv_movement['movement_type']} {iv_movement['iv_change_pct']:+.2f}%")
    
    return movements


def log_abnormal_movement(movement):
    """
    Log abnormal movement to database.
    
    Args:
        movement: dict with movement details
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
    INSERT INTO abnormal_movements (
        ticker, detected_date, detected_time, movement_type, severity,
        price_change_pct, volume_vs_avg, iv_change_pct,
        underlying_price, affected_trades
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        movement.get('ticker'),
        datetime.now().strftime('%Y-%m-%d'),
        datetime.now().strftime('%H:%M:%S'),
        movement.get('movement_type'),
        movement.get('severity'),
        movement.get('price_change_pct'),
        movement.get('volume_vs_avg'),
        movement.get('iv_change_pct'),
        movement.get('current_price'),
        movement.get('affected_trades')
    ))
    
    conn.commit()
    conn.close()


def suggest_action(movement, trade_id):
    """
    Suggest action based on abnormal movement.
    
    Args:
        movement: dict with movement details
        trade_id: Trade ID affected
    
    Returns:
        Suggested action: 'exit', 'hedge', 'adjust', 'monitor'
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get trade details
    cursor.execute("""
    SELECT option_type, entry_price, quantity, stop_loss_price, take_profit_price
    FROM trades WHERE trade_id = ?
    """, (trade_id,))
    
    trade = cursor.fetchone()
    conn.close()
    
    if not trade:
        return 'monitor'
    
    option_type, entry_price, quantity, stop_loss, take_profit = trade
    
    movement_type = movement.get('movement_type')
    severity = movement.get('severity')
    
    # Critical movements -> Exit or hedge
    if severity == 'CRITICAL':
        if movement_type == 'price_spike':
            price_change = movement.get('price_change_pct', 0)
            if option_type == 'CALL' and price_change > 0:
                return 'monitor'  # Profit situation
            elif option_type == 'PUT' and price_change < 0:
                return 'monitor'  # Profit situation
            else:
                return 'exit'  # Losing situation
        
        elif movement_type in ['iv_crush', 'iv_spike']:
            return 'adjust'  # Consider rolling or adjusting
        
        elif movement_type == 'volume_surge':
            return 'monitor'  # Just watch for now
    
    # High severity -> Adjust or monitor closely
    elif severity == 'HIGH':
        return 'adjust'
    
    # Medium severity -> Monitor
    else:
        return 'monitor'


def run_abnormal_movement_scan():
    """
    Full scan: detect, log, and suggest actions for abnormal movements.
    """
    print("=" * 60)
    print("ABNORMAL MOVEMENT SCAN")
    print("=" * 60)
    print()
    
    movements = monitor_open_positions()
    
    if not movements:
        print("✅ No abnormal movements detected")
        return
    
    print()
    print(f"⚠️ Detected {len(movements)} abnormal movements")
    print()
    
    for movement in movements:
        # Log to database
        log_abnormal_movement(movement)
        
        # Suggest actions for affected trades
        affected_trades = eval(movement.get('affected_trades', '[]'))
        for trade_id in affected_trades:
            action = suggest_action(movement, trade_id)
            
            print(f"Trade #{trade_id}:")
            print(f"  Movement: {movement.get('movement_type')} ({movement.get('severity')})")
            print(f"  Suggested Action: {action.upper()}")
            print()


def get_abnormal_movements(days_back=7, min_severity='LOW'):
    """
    Get recent abnormal movements from database
    
    Args:
        days_back: Number of days to look back (default 7)
        min_severity: Minimum severity level ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
    
    Returns:
        DataFrame with abnormal movements
    """
    import sqlite3
    
    conn = sqlite3.connect(DB_PATH)
    
    severity_map = {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3, 'CRITICAL': 4}
    severity_filter = severity_map.get(min_severity, 1)
    
    query = """
    SELECT 
        movement_id,
        ticker,
        movement_type,
        severity,
        detection_date,
        detection_time,
        metric_value,
        threshold_value,
        affected_trades,
        description
    FROM abnormal_movements
    WHERE julianday('now') - julianday(detection_date) <= ?
    ORDER BY detection_date DESC, detection_time DESC
    """
    
    df = pd.read_sql_query(query, conn, params=(days_back,))
    conn.close()
    
    # Filter by severity if needed
    if not df.empty:
        severity_order = {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3, 'CRITICAL': 4}
        df['severity_num'] = df['severity'].map(severity_order)
        df = df[df['severity_num'] >= severity_filter]
        df = df.drop('severity_num', axis=1)
    
    return df


if __name__ == '__main__':
    run_abnormal_movement_scan()
