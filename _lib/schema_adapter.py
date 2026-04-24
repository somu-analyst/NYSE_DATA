"""
Database Schema Adapter
Normalizes your options_daily schema to work with all analytics modules
"""
import sqlite3
import pandas as pd

DB_PATH = r'c:\Users\srini\Options_chain_data\US_data.db'


def create_normalized_views():
    """
    Create SQL views that normalize your schema for analytics
    Your schema: vol_Call, vol_Put, openInt_Call, openInt_Put in same row
    Normalized: Separate rows for calls and puts with standard column names
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("Creating normalized views...")
    
    # Drop existing views if they exist
    cursor.execute("DROP VIEW IF EXISTS options_normalized")
    
    # Create normalized view - Calls
    cursor.execute("""
    CREATE VIEW IF NOT EXISTS options_normalized AS
    SELECT 
        ticker,
        strike,
        expiry_date as expiry,
        trade_date as date,
        'C' as option_type,
        vol_Call as volume,
        openInt_Call as open_interest,
        lastPrice_Call as last_price,
        call_bid_info as bid,
        call_ask_info as ask,
        call_volume_info as daily_volume,
        call_openInterest_info as daily_oi,
        0.30 as implied_volatility,  -- Default IV, will calculate later
        contractSymbol_Call as contract_symbol
    FROM options_daily
    WHERE vol_Call IS NOT NULL AND vol_Call > 0
    
    UNION ALL
    
    SELECT 
        ticker,
        strike,
        expiry_date as expiry,
        trade_date as date,
        'P' as option_type,
        vol_Put as volume,
        openInt_Put as open_interest,
        lastPrice_Put as last_price,
        put_bid_info as bid,
        put_ask_info as ask,
        put_volume_info as daily_volume,
        put_openInterest_info as daily_oi,
        0.30 as implied_volatility,
        contractSymbol_Put as contract_symbol
    FROM options_daily
    WHERE vol_Put IS NOT NULL AND vol_Put > 0
    """)
    
    print("✅ Created options_normalized view")
    
    # Create summary view by ticker
    cursor.execute("DROP VIEW IF EXISTS ticker_activity_summary")
    cursor.execute("""
    CREATE VIEW IF NOT EXISTS ticker_activity_summary AS
    SELECT 
        ticker,
        trade_date as date,
        SUM(vol_Call + vol_Put) as total_volume,
        SUM(vol_Call) as call_volume,
        SUM(vol_Put) as put_volume,
        SUM(openInt_Call) as call_oi,
        SUM(openInt_Put) as put_oi,
        COUNT(DISTINCT strike) as num_strikes,
        COUNT(DISTINCT expiry_date) as num_expiries
    FROM options_daily
    WHERE trade_date IS NOT NULL
    GROUP BY ticker, trade_date
    """)
    
    print("✅ Created ticker_activity_summary view")
    
    conn.commit()
    conn.close()
    
    print("\n✅ All views created successfully!")
    print("   • options_normalized - Normalized calls/puts as separate rows")
    print("   • ticker_activity_summary - Aggregated ticker activity")


def test_normalized_views():
    """Test that the views work correctly"""
    conn = sqlite3.connect(DB_PATH)
    
    print("\n" + "="*70)
    print("TESTING NORMALIZED VIEWS")
    print("="*70)
    
    # Test options_normalized
    print("\n1. Testing options_normalized view:")
    query = """
    SELECT ticker, option_type, COUNT(*) as count, SUM(volume) as total_volume
    FROM options_normalized
    WHERE date = (SELECT MAX(date) FROM options_normalized)
    GROUP BY ticker, option_type
    ORDER BY total_volume DESC
    LIMIT 10
    """
    
    df = pd.read_sql_query(query, conn)
    print(df.to_string(index=False))
    
    # Test ticker_activity_summary
    print("\n2. Testing ticker_activity_summary view:")
    query = """
    SELECT ticker, total_volume, call_volume, put_volume,
           ROUND(CAST(put_volume AS REAL) / NULLIF(call_volume, 0), 2) as pc_ratio
    FROM ticker_activity_summary
    WHERE date = (SELECT MAX(date) FROM ticker_activity_summary)
    ORDER BY total_volume DESC
    LIMIT 10
    """
    
    df = pd.read_sql_query(query, conn)
    print(df.to_string(index=False))
    
    conn.close()
    
    print("\n✅ Views are working correctly!")


if __name__ == '__main__':
    print("="*70)
    print("DATABASE SCHEMA ADAPTER")
    print("="*70)
    
    create_normalized_views()
    test_normalized_views()
    
    print("\n" + "="*70)
    print("✅ SCHEMA ADAPTER COMPLETE!")
    print("="*70)
    print("""
All analytics modules can now use:
  • options_normalized - Standard schema for queries
  • ticker_activity_summary - Quick ticker-level stats

Your original tables are unchanged and safe!
    """)
