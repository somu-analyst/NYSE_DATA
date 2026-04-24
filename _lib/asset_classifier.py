"""
Asset Type Classifier - Identify Stocks, ETFs, and Indexes
Similar to optioncharts.io categorization
"""
import sqlite3
import pandas as pd

DB_PATH = r'c:\Users\srini\Options_chain_data\US_data.db'

# Common ETFs
ETFS = {
    # Major Index ETFs
    'SPY', 'QQQ', 'IWM', 'DIA', 'VOO', 'VTI', 'VEA', 'VWO', 'EFA', 'EEM',
    
    # Sector ETFs
    'XLF', 'XLK', 'XLE', 'XLV', 'XLI', 'XLU', 'XLB', 'XLP', 'XLY', 'XLRE',
    'SMH', 'XBI', 'IBB', 'KRE', 'XHB', 'XRT', 'ITB', 'GDX', 'XOP', 'XME',
    
    # Popular Thematic ETFs
    'ARKK', 'ARKW', 'ARKG', 'ARKF', 'ARKQ',  # ARK Innovation
    'TAN', 'ICLN', 'LIT', 'REMX',  # Clean Energy / Battery
    'SOXX', 'VGT', 'IGV', 'HACK', 'FINX',  # Tech
    'JETS', 'XAR', 'PPA',  # Aerospace
    'MJ', 'YOLO', 'MSOS',  # Cannabis
    'TLT', 'IEF', 'SHY', 'AGG', 'BND', 'LQD',  # Bonds
    'GLD', 'SLV', 'USO', 'UGA', 'UNG',  # Commodities
    'HYG', 'JNK', 'EMB',  # High Yield
    
    # Leveraged ETFs
    'TQQQ', 'SQQQ', 'UPRO', 'SPXU', 'TNA', 'TZA', 'UVXY', 'SVXY',
    'SPXL', 'SPXS', 'TECL', 'TECS', 'FAS', 'FAZ', 'ERX', 'ERY',
    'LABU', 'LABD', 'NUGT', 'DUST', 'JNUG', 'JDST',
    
    # International
    'EWJ', 'FXI', 'EWZ', 'INDA', 'EWY', 'EWW', 'EWT', 'EWG', 'EWU',
    'MCHI', 'KWEB', 'ASHR', 'EWA', 'EWC', 'EZA',
    
    # Volatility
    'VXX', 'VIXY', 'SVXY', 'UVXY',
}

# Common Indexes
INDEXES = {
    'SPX', 'NDX', 'DJX', 'RUT', 'VIX', 'OEX', 'XEO', 'DJI', 'IXIC',
}


def setup_asset_classification_table():
    """Create table to store asset classifications"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS asset_classifications (
        ticker TEXT PRIMARY KEY,
        asset_type TEXT,  -- 'STOCK', 'ETF', 'INDEX'
        sector TEXT,      -- For stocks: Tech, Healthcare, etc.
        category TEXT,    -- For ETFs: Index, Sector, Leveraged, etc.
        last_updated DATE DEFAULT CURRENT_DATE
    )
    """)
    
    conn.commit()
    conn.close()
    print("✅ Created asset_classifications table")


def populate_asset_classifications():
    """Populate asset classifications from known tickers"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get all unique tickers from options data
    cursor.execute("SELECT DISTINCT ticker FROM options_daily")
    all_tickers = [row[0] for row in cursor.fetchall()]
    
    classifications = []
    
    for ticker in all_tickers:
        if ticker in ETFS:
            # Determine ETF category
            if ticker in ['TQQQ', 'SQQQ', 'UPRO', 'SPXU', 'TNA', 'TZA', 'SPXL', 'SPXS', 
                         'TECL', 'TECS', 'FAS', 'FAZ', 'ERX', 'ERY', 'LABU', 'LABD']:
                category = 'Leveraged'
            elif ticker in ['SPY', 'QQQ', 'IWM', 'DIA', 'VOO', 'VTI']:
                category = 'Major Index'
            elif ticker in ['XLF', 'XLK', 'XLE', 'XLV', 'XLI', 'XLU', 'XLB', 'XLP', 'XLY', 'XLRE']:
                category = 'Sector'
            elif ticker in ['ARKK', 'ARKW', 'ARKG', 'ARKF', 'ARKQ']:
                category = 'Thematic - Innovation'
            elif ticker in ['GLD', 'SLV', 'USO']:
                category = 'Commodity'
            elif ticker in ['VXX', 'VIXY', 'SVXY', 'UVXY']:
                category = 'Volatility'
            else:
                category = 'Thematic'
            
            classifications.append((ticker, 'ETF', None, category))
            
        elif ticker in INDEXES:
            classifications.append((ticker, 'INDEX', None, 'Index'))
            
        else:
            # It's a stock - try to determine sector
            classifications.append((ticker, 'STOCK', None, None))
    
    # Insert/update classifications
    cursor.executemany("""
    INSERT OR REPLACE INTO asset_classifications (ticker, asset_type, sector, category)
    VALUES (?, ?, ?, ?)
    """, classifications)
    
    conn.commit()
    conn.close()
    
    print(f"✅ Classified {len(classifications)} tickers")
    
    # Print summary
    stocks = sum(1 for c in classifications if c[1] == 'STOCK')
    etfs = sum(1 for c in classifications if c[1] == 'ETF')
    indexes = sum(1 for c in classifications if c[1] == 'INDEX')
    
    print(f"   • Stocks: {stocks}")
    print(f"   • ETFs: {etfs}")
    print(f"   • Indexes: {indexes}")


def get_asset_type(ticker):
    """Get asset type for a ticker"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT asset_type FROM asset_classifications WHERE ticker = ?", (ticker,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return result[0]
    
    # Fallback classification
    if ticker in ETFS:
        return 'ETF'
    elif ticker in INDEXES:
        return 'INDEX'
    else:
        return 'STOCK'


def get_most_active_by_type(asset_type='STOCK', limit=20, min_volume=50000):
    """
    Get most active options by asset type
    
    Args:
        asset_type: 'STOCK', 'ETF', or 'INDEX'
        limit: Number of results
        min_volume: Minimum total volume
    
    Returns:
        DataFrame with most active options
    """
    conn = sqlite3.connect(DB_PATH)
    
    query = """
    SELECT 
        o.ticker,
        ac.asset_type,
        ac.category,
        SUM(o.volume) as total_volume,
        SUM(CASE WHEN o.option_type = 'C' THEN o.volume ELSE 0 END) as call_volume,
        SUM(CASE WHEN o.option_type = 'P' THEN o.volume ELSE 0 END) as put_volume,
        AVG(o.implied_volatility) as avg_iv
    FROM options_daily o
    LEFT JOIN asset_classifications ac ON o.ticker = ac.ticker
    WHERE o.date = (SELECT MAX(date) FROM options_daily)
        AND (ac.asset_type = ? OR (ac.asset_type IS NULL AND ? = 'STOCK'))
    GROUP BY o.ticker
    HAVING total_volume >= ?
    ORDER BY total_volume DESC
    LIMIT ?
    """
    
    df = pd.read_sql_query(query, conn, params=(asset_type, asset_type, min_volume, limit))
    conn.close()
    
    if not df.empty:
        # Calculate Put/Call ratio
        df['put_call_ratio'] = df['put_volume'] / df['call_volume'].replace(0, 1)
        
        # Convert IV to percentage
        df['iv_percent'] = df['avg_iv'] * 100
        
        # Add sentiment
        df['sentiment'] = df['put_call_ratio'].apply(lambda x: 
            'BEARISH' if x > 1.2 else 'BULLISH' if x < 0.8 else 'NEUTRAL'
        )
    
    return df


def get_all_asset_types_summary():
    """Get summary across all asset types"""
    conn = sqlite3.connect(DB_PATH)
    
    query = """
    SELECT 
        COALESCE(ac.asset_type, 'STOCK') as asset_type,
        COUNT(DISTINCT o.ticker) as num_tickers,
        SUM(o.volume) as total_volume,
        SUM(CASE WHEN o.option_type = 'C' THEN o.volume ELSE 0 END) as call_volume,
        SUM(CASE WHEN o.option_type = 'P' THEN o.volume ELSE 0 END) as put_volume
    FROM options_daily o
    LEFT JOIN asset_classifications ac ON o.ticker = ac.ticker
    WHERE o.date = (SELECT MAX(date) FROM options_daily)
    GROUP BY asset_type
    ORDER BY total_volume DESC
    """
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if not df.empty:
        df['put_call_ratio'] = df['put_volume'] / df['call_volume'].replace(0, 1)
        df['avg_volume_per_ticker'] = df['total_volume'] / df['num_tickers']
    
    return df


if __name__ == '__main__':
    print("="*70)
    print("ASSET TYPE CLASSIFIER - Stocks, ETFs, Indexes")
    print("="*70)
    
    # Setup
    print("\n1. Setting up database...")
    setup_asset_classification_table()
    
    print("\n2. Populating classifications...")
    populate_asset_classifications()
    
    # Summary
    print("\n" + "="*70)
    print("SUMMARY BY ASSET TYPE")
    print("="*70)
    
    summary = get_all_asset_types_summary()
    if not summary.empty:
        print(summary.to_string(index=False))
    
    # Most active stocks
    print("\n" + "="*70)
    print("MOST ACTIVE STOCK OPTIONS (Top 10)")
    print("="*70)
    
    stocks = get_most_active_by_type('STOCK', limit=10)
    if not stocks.empty:
        print(stocks[['ticker', 'total_volume', 'call_volume', 'put_volume', 
                     'put_call_ratio', 'sentiment']].to_string(index=False))
    
    # Most active ETFs
    print("\n" + "="*70)
    print("MOST ACTIVE ETF OPTIONS (Top 10)")
    print("="*70)
    
    etfs = get_most_active_by_type('ETF', limit=10, min_volume=10000)
    if not etfs.empty:
        print(etfs[['ticker', 'category', 'total_volume', 'call_volume', 'put_volume', 
                   'put_call_ratio', 'sentiment']].to_string(index=False))
    
    # Most active Indexes
    print("\n" + "="*70)
    print("MOST ACTIVE INDEX OPTIONS")
    print("="*70)
    
    indexes = get_most_active_by_type('INDEX', limit=10, min_volume=10000)
    if not indexes.empty:
        print(indexes[['ticker', 'total_volume', 'call_volume', 'put_volume', 
                      'put_call_ratio', 'sentiment']].to_string(index=False))
    else:
        print("No index options data available")
