"""
OPTIONS FLOW DETECTOR
Rule-based system for identifying institutional options repositioning

Detection Rules:
1. LIQUIDATION: Put OI collapse >30% in single day (profit-taking/unwind)
2. ACCUMULATION: Call OI growth >30% in single day (buying conviction)
3. CONVICTION: High volume + stable OI (retail following institutions)
4. PCR_FLIP: Extreme call/put ratio shifts (directional repositioning)
5. TERM_STRUCTURE: Weekly vs monthly coordination (macro hedging changes)
"""

import sqlite3
import pandas as pd
from datetime import datetime


class OptionsFlowDetector:
    """Detect institutional options positioning patterns"""
    
    def __init__(self, db_path="US_data-bk.db"):
        self.db_path = db_path
    
    def get_conn(self):
        """Get database connection"""
        return sqlite3.connect(self.db_path)
    
    def detect_liquidation(self, symbol, min_oi_change_pct=-30, min_prev_oi=50):
        """
        Detect put liquidation signals (profit-taking, unwind)
        
        Returns: DataFrame with liquidation events
        """
        with self.get_conn() as conn:
            df = pd.read_sql(
                """SELECT 
                     trade_date_now, expiry_date, strike,
                     openInt_Put_now, openInt_Put_prev, pct_change_OI_Put,
                     vol_Put_now, lastPrice_Put_now
                   FROM options_change
                   WHERE ticker = ? 
                   AND pct_change_OI_Put < ?
                   AND openInt_Put_prev > ?
                   ORDER BY pct_change_OI_Put ASC""",
                conn,
                params=(symbol, min_oi_change_pct, min_prev_oi)
            )
        
        if len(df) == 0:
            return pd.DataFrame()
        
        df['signal_type'] = 'LIQUIDATION'
        df['confidence'] = df['pct_change_OI_Put'].abs() * 0.8
        df['confidence'] = df['confidence'].clip(0, 100)
        
        return df[['trade_date_now', 'expiry_date', 'strike', 'openInt_Put_now', 
                   'openInt_Put_prev', 'pct_change_OI_Put', 'vol_Put_now', 
                   'signal_type', 'confidence']]
    
    def detect_accumulation(self, symbol, min_oi_change_pct=30, min_prev_oi=50):
        """
        Detect call accumulation signals (buying conviction, bullish positioning)
        
        Returns: DataFrame with accumulation events
        """
        with self.get_conn() as conn:
            df = pd.read_sql(
                """SELECT 
                     trade_date_now, expiry_date, strike,
                     openInt_Call_now, openInt_Call_prev, pct_change_OI_Call,
                     vol_Call_now, lastPrice_Call_now
                   FROM options_change
                   WHERE ticker = ? 
                   AND pct_change_OI_Call > ?
                   AND openInt_Call_prev > ?
                   ORDER BY pct_change_OI_Call DESC""",
                conn,
                params=(symbol, min_oi_change_pct, min_prev_oi)
            )
        
        if len(df) == 0:
            return pd.DataFrame()
        
        df['signal_type'] = 'ACCUMULATION'
        df['confidence'] = (df['pct_change_OI_Call'] / 100).clip(0, 1) * 100
        
        return df[['trade_date_now', 'expiry_date', 'strike', 'openInt_Call_now',
                   'openInt_Call_prev', 'pct_change_OI_Call', 'vol_Call_now',
                   'signal_type', 'confidence']]
    
    def detect_conviction(self, symbol, min_volume=1000, max_oi_change_pct=10, 
                         min_price=0.50, min_oi=500):
        """
        Detect conviction signals: high volume with stable OI
        Indicates institutional positioning without directional reversal
        
        Returns: DataFrame with conviction signals
        """
        with self.get_conn() as conn:
            df = pd.read_sql(
                """SELECT 
                     trade_date_now, expiry_date, strike,
                     openInt_Call_now, pct_change_OI_Call,
                     vol_Call_now, lastPrice_Call_now,
                     openInt_Put_now, vol_Put_now
                   FROM options_change
                   WHERE ticker = ?
                   AND (vol_Call_now > ? OR vol_Put_now > ?)
                   AND abs(pct_change_OI_Call) < ?
                   AND lastPrice_Call_now > ?
                   AND openInt_Call_now > ?
                   ORDER BY vol_Call_now DESC""",
                conn,
                params=(symbol, min_volume, min_volume, max_oi_change_pct, 
                       min_price, min_oi)
            )
        
        if len(df) == 0:
            return pd.DataFrame()
        
        df['signal_type'] = 'CONVICTION'
        df['confidence'] = 70  # High conviction default
        
        return df[['trade_date_now', 'expiry_date', 'strike', 'openInt_Call_now',
                   'vol_Call_now', 'vol_Put_now', 'signal_type', 'confidence']]
    
    def detect_pcr_flip(self, symbol, min_call_change=40, max_put_change=-40):
        """
        Detect PCR (Put/Call Ratio) structural shifts
        Indicates major directional repositioning
        
        Returns: DataFrame with PCR flip signals
        """
        with self.get_conn() as conn:
            df = pd.read_sql(
                """SELECT 
                     trade_date_now, expiry_date, strike,
                     openInt_Call_now, openInt_Put_now,
                     pct_change_OI_Call, pct_change_OI_Put
                   FROM options_change
                   WHERE ticker = ?
                   AND (pct_change_OI_Call > ? OR pct_change_OI_Put < ?)
                   ORDER BY abs(pct_change_OI_Call + pct_change_OI_Put) DESC""",
                conn,
                params=(symbol, min_call_change, max_put_change)
            )
        
        if len(df) == 0:
            return pd.DataFrame()
        
        # Determine flip direction
        def get_flip_type(row):
            if row['pct_change_OI_Call'] > 0 and row['pct_change_OI_Put'] < 0:
                return 'BULLISH_FLIP'
            elif row['pct_change_OI_Call'] < 0 and row['pct_change_OI_Put'] > 0:
                return 'BEARISH_FLIP'
            else:
                return 'NEUTRAL_FLIP'
        
        df['signal_type'] = df.apply(get_flip_type, axis=1)
        df['confidence'] = (abs(df['pct_change_OI_Call']) + abs(df['pct_change_OI_Put'])) / 2
        df['confidence'] = df['confidence'].clip(0, 100)
        
        return df[['trade_date_now', 'expiry_date', 'strike', 'openInt_Call_now',
                   'openInt_Put_now', 'pct_change_OI_Call', 'pct_change_OI_Put',
                   'signal_type', 'confidence']]
    
    def detect_term_structure(self, symbol, lookback_days=5):
        """
        Detect term structure changes: weekly vs monthly OI coordination shifts
        Indicates macro hedging strategy changes
        
        Returns: DataFrame with term structure signals
        """
        with self.get_conn() as conn:
            # Get recent changes grouped by expiration bucket
            df = pd.read_sql(
                """SELECT 
                     trade_date_now, expiry_date,
                     SUM(pct_change_OI_Call) as total_call_change,
                     SUM(pct_change_OI_Put) as total_put_change,
                     AVG(openInt_Call_now) as avg_call_oi,
                     AVG(openInt_Put_now) as avg_put_oi
                   FROM options_change
                   WHERE ticker = ?
                   GROUP BY trade_date_now, expiry_date
                   ORDER BY trade_date_now DESC, expiry_date""",
                conn,
                params=(symbol,)
            )
        
        if len(df) < 2:
            return pd.DataFrame()
        
        # Identify term buckets
        def get_bucket(exp_date):
            try:
                exp = datetime.strptime(exp_date, '%Y-%m-%d')
                today = datetime.strptime(df['trade_date_now'].iloc[0], '%d%b%Y') \
                    if len(df) > 0 else datetime.now()
                days_to_exp = (exp - today).days
                
                if days_to_exp <= 7:
                    return 'WEEKLY'
                elif days_to_exp <= 30:
                    return 'MONTHLY'
                else:
                    return 'LONG_DATED'
            except:
                return 'UNKNOWN'
        
        df['bucket'] = df['expiry_date'].apply(get_bucket)
        df['signal_type'] = 'TERM_STRUCTURE'
        df['confidence'] = 65
        
        return df[['trade_date_now', 'bucket', 'total_call_change', 'total_put_change',
                   'signal_type', 'confidence']]
    
    def get_all_signals(self, symbol, limit_per_rule=50):
        """Get all signals from all detection rules"""
        signals = []
        
        liq = self.detect_liquidation(symbol)
        if len(liq) > 0:
            signals.append(liq.head(limit_per_rule))
        
        accum = self.detect_accumulation(symbol)
        if len(accum) > 0:
            signals.append(accum.head(limit_per_rule))
        
        conv = self.detect_conviction(symbol)
        if len(conv) > 0:
            signals.append(conv.head(limit_per_rule))
        
        pcr = self.detect_pcr_flip(symbol)
        if len(pcr) > 0:
            signals.append(pcr.head(limit_per_rule))
        
        if signals:
            return pd.concat(signals, ignore_index=True)
        return pd.DataFrame()
    
    def get_latest_signals(self, symbol, count=10):
        """Get latest signals for today's report"""
        with self.get_conn() as conn:
            df_dates = pd.read_sql(
                "SELECT DISTINCT trade_date_now FROM options_change WHERE ticker = ? "
                "ORDER BY trade_date_now DESC LIMIT 1",
                conn,
                params=(symbol,)
            )
        
        if len(df_dates) == 0:
            return pd.DataFrame()
        
        latest_date = df_dates['trade_date_now'].iloc[0]
        
        all_signals = self.get_all_signals(symbol, limit_per_rule=100)
        
        if len(all_signals) == 0:
            return pd.DataFrame()
        
        latest = all_signals[all_signals['trade_date_now'] == latest_date]
        
        return latest.sort_values('confidence', ascending=False).head(count)
