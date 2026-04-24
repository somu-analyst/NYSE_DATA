"""
Abnormal Options Activity Detector
===================================
Detects unusual OI/Volume changes and infers buy/sell pressure using multi-factor heuristics.

Features:
- Z-score based anomaly detection for OI and volume changes
- Per-strike buy/sell inference using price action, IV, and flow metrics
- Expiry-level breakdown of activity
- Strategy recommendations based on detected patterns
"""

import sqlite3
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

DB_PATH = r'c:\Users\srini\Options_chain_data\US_data.db'

class AbnormalActivityDetector:
    """Detects unusual options activity and infers directional bias"""
    
    def __init__(self, db_path=DB_PATH, zscore_threshold=2.0):
        self.db_path = db_path
        self.zscore_threshold = zscore_threshold
        
    def get_latest_trade_date(self):
        """Get the most recent trade date from options_change"""
        conn = sqlite3.connect(self.db_path)
        query = """
            SELECT trade_date_now FROM options_change 
            ORDER BY substr(trade_date_now, 7, 4) || '-' || substr(trade_date_now, 1, 2) || '-' || substr(trade_date_now, 4, 2) DESC 
            LIMIT 1
        """
        latest = pd.read_sql(query, conn).iloc[0, 0]
        conn.close()
        return latest
    
    def load_oi_changes(self, trade_date=None):
        """Load OI/volume changes for specified date"""
        if trade_date is None:
            trade_date = self.get_latest_trade_date()
            
        conn = sqlite3.connect(self.db_path)
        query = f"""
        SELECT ticker, strike, expiry_date,
               change_OI_Call, pct_change_OI_Call,
               change_OI_Put, pct_change_OI_Put,
               change_vol_Call, pct_change_vol_Call,
               change_vol_Put, pct_change_vol_Put,
               openInt_Call_now, openInt_Put_now,
               vol_Call_now, vol_Put_now,
               lastPrice_Call_now, lastPrice_Put_now,
               call_open_now, call_high_now, call_low_now, call_close_now,
               put_open_now, put_high_now, put_low_now, put_close_now
        FROM options_change
        WHERE trade_date_now = '{trade_date}'
        """
        df = pd.read_sql(query, conn)
        conn.close()
        
        # Calculate days to expiry
        df['expiry_date'] = pd.to_datetime(df['expiry_date'], format='%m-%d-%Y', errors='coerce')
        trade_dt = pd.to_datetime(trade_date, format='%m-%d-%Y')
        df['days_to_expiry'] = (df['expiry_date'] - trade_dt).dt.days
        
        return df
    
    def detect_ticker_level_anomalies(self, df):
        """Detect tickers with unusual aggregate OI/volume"""
        # Aggregate per ticker
        agg = df.groupby('ticker').agg({
            'change_OI_Call': 'sum',
            'change_OI_Put': 'sum',
            'change_vol_Call': 'sum',
            'change_vol_Put': 'sum',
            'openInt_Call_now': 'sum',
            'openInt_Put_now': 'sum'
        }).reset_index()
        
        # Calculate z-scores
        for col in ['change_OI_Call', 'change_OI_Put', 'change_vol_Call', 'change_vol_Put']:
            mean = agg[col].mean()
            std = agg[col].std()
            if std > 0:
                agg[f'{col}_zscore'] = (agg[col] - mean) / std
            else:
                agg[f'{col}_zscore'] = 0
        
        # Find max z-score per ticker
        zscore_cols = [c for c in agg.columns if '_zscore' in c]
        agg['max_zscore'] = agg[zscore_cols].abs().max(axis=1)
        
        # Filter anomalies
        anomalies = agg[agg['max_zscore'] >= self.zscore_threshold].copy()
        anomalies = anomalies.sort_values('max_zscore', ascending=False)
        
        return anomalies
    
    def infer_buy_sell_pressure(self, row):
        """
        Infer if OI increase represents buying or selling using heuristics:
        
        CALL BUYING signals:
        - ΔOI_call > 0 AND volume >= ΔOI AND price increased (close > open)
        - ΔOI_call > 0 AND price at/above mid (close closer to high)
        
        CALL SELLING signals:
        - ΔOI_call > 0 AND price decreased (close < open)
        - ΔOI_call > 0 AND price at/below mid (close closer to low)
        
        Similar for puts
        """
        signals = {
            'call_signal': 'NEUTRAL',
            'call_confidence': 0,
            'put_signal': 'NEUTRAL', 
            'put_confidence': 0,
            'reasoning': []
        }
        
        # CALL ANALYSIS
        if row['change_OI_Call'] > 100:  # Meaningful OI increase
            call_vol_ratio = row['vol_Call_now'] / abs(row['change_OI_Call']) if row['change_OI_Call'] != 0 else 0
            
            # Price movement
            if pd.notna(row['call_close_now']) and pd.notna(row['call_open_now']):
                call_price_up = row['call_close_now'] > row['call_open_now']
                call_range = row['call_high_now'] - row['call_low_now'] if pd.notna(row['call_high_now']) and pd.notna(row['call_low_now']) else 0
                
                if call_range > 0:
                    # Where did it close in the range? (1 = at high, 0 = at low)
                    call_close_position = (row['call_close_now'] - row['call_low_now']) / call_range
                else:
                    call_close_position = 0.5
                
                # Strong BUY signals
                if call_price_up and call_vol_ratio >= 0.8 and call_close_position > 0.6:
                    signals['call_signal'] = 'STRONG BUY'
                    signals['call_confidence'] = 90
                    signals['reasoning'].append(f"Call OI +{row['change_OI_Call']:.0f}, price up, high volume, closed near highs")
                
                # Moderate BUY
                elif call_price_up and call_vol_ratio >= 0.5:
                    signals['call_signal'] = 'BUY'
                    signals['call_confidence'] = 70
                    signals['reasoning'].append(f"Call OI +{row['change_OI_Call']:.0f}, price up, decent volume")
                
                # SELL signal (writing)
                elif not call_price_up and call_close_position < 0.4:
                    signals['call_signal'] = 'SELLING/WRITING'
                    signals['call_confidence'] = 65
                    signals['reasoning'].append(f"Call OI +{row['change_OI_Call']:.0f}, price down, likely sellers")
                
                # Ambiguous
                else:
                    signals['call_signal'] = 'MIXED'
                    signals['call_confidence'] = 40
                    signals['reasoning'].append(f"Call OI +{row['change_OI_Call']:.0f}, unclear direction")
        
        elif row['change_OI_Call'] < -100:  # OI decrease
            signals['call_signal'] = 'CLOSING'
            signals['call_confidence'] = 80
            signals['reasoning'].append(f"Call OI {row['change_OI_Call']:.0f}, positions closing")
        
        # PUT ANALYSIS (similar logic)
        if row['change_OI_Put'] > 100:
            put_vol_ratio = row['vol_Put_now'] / abs(row['change_OI_Put']) if row['change_OI_Put'] != 0 else 0
            
            if pd.notna(row['put_close_now']) and pd.notna(row['put_open_now']):
                put_price_up = row['put_close_now'] > row['put_open_now']
                put_range = row['put_high_now'] - row['put_low_now'] if pd.notna(row['put_high_now']) and pd.notna(row['put_low_now']) else 0
                
                if put_range > 0:
                    put_close_position = (row['put_close_now'] - row['put_low_now']) / put_range
                else:
                    put_close_position = 0.5
                
                # Strong BUY (bearish positioning)
                if put_price_up and put_vol_ratio >= 0.8 and put_close_position > 0.6:
                    signals['put_signal'] = 'STRONG BUY'
                    signals['put_confidence'] = 90
                    signals['reasoning'].append(f"Put OI +{row['change_OI_Put']:.0f}, price up, high volume → BEARISH")
                
                # Moderate BUY
                elif put_price_up and put_vol_ratio >= 0.5:
                    signals['put_signal'] = 'BUY'
                    signals['put_confidence'] = 70
                    signals['reasoning'].append(f"Put OI +{row['change_OI_Put']:.0f}, price up → protective puts")
                
                # SELL signal
                elif not put_price_up and put_close_position < 0.4:
                    signals['put_signal'] = 'SELLING/WRITING'
                    signals['put_confidence'] = 65
                    signals['reasoning'].append(f"Put OI +{row['change_OI_Put']:.0f}, price down → put selling")
        
        elif row['change_OI_Put'] < -100:
            signals['put_signal'] = 'CLOSING'
            signals['put_confidence'] = 80
            signals['reasoning'].append(f"Put OI {row['change_OI_Put']:.0f}, hedges closing")
        
        return pd.Series(signals)
    
    def analyze_strike_level_activity(self, ticker, df):
        """Analyze specific strikes for a ticker"""
        ticker_df = df[df['ticker'] == ticker].copy()
        
        # Apply buy/sell inference
        inference = ticker_df.apply(self.infer_buy_sell_pressure, axis=1)
        ticker_df = pd.concat([ticker_df, inference], axis=1)
        
        # Sort by absolute OI change
        ticker_df['total_oi_change'] = abs(ticker_df['change_OI_Call']) + abs(ticker_df['change_OI_Put'])
        ticker_df = ticker_df.sort_values('total_oi_change', ascending=False)
        
        return ticker_df
    
    def get_expiry_breakdown(self, ticker, df):
        """Break down activity by expiry"""
        ticker_df = df[df['ticker'] == ticker].copy()
        
        expiry_agg = ticker_df.groupby('expiry_date').agg({
            'change_OI_Call': 'sum',
            'change_OI_Put': 'sum',
            'change_vol_Call': 'sum',
            'change_vol_Put': 'sum',
            'days_to_expiry': 'first'
        }).reset_index()
        
        expiry_agg = expiry_agg.sort_values('days_to_expiry')
        return expiry_agg
    
    def generate_strategy(self, ticker_analysis, expiry_breakdown):
        """Generate trading strategy based on detected activity"""
        strategies = []
        
        # Analyze top strikes
        top_strikes = ticker_analysis.head(5)
        
        for _, row in top_strikes.iterrows():
            if row['call_signal'] in ['STRONG BUY', 'BUY'] and row['call_confidence'] >= 70:
                if row['days_to_expiry'] <= 7:
                    strategies.append({
                        'strike': row['strike'],
                        'action': '🎯 BULLISH GAMMA PLAY',
                        'strategy': f"Heavy call buying at ${row['strike']:.0f} expiring in {row['days_to_expiry']} days",
                        'recommendation': f"Consider: Watch for breakout above ${row['strike']:.0f}. Possible gamma squeeze setup.",
                        'risk': 'Very short-dated, high risk/reward'
                    })
                else:
                    strategies.append({
                        'strike': row['strike'],
                        'action': '📈 BULLISH POSITIONING',
                        'strategy': f"Sustained call accumulation at ${row['strike']:.0f}",
                        'recommendation': f"Consider: LEAP calls or stock position. Target ${row['strike']:.0f}+",
                        'risk': 'Moderate - longer time frame'
                    })
            
            if row['put_signal'] in ['STRONG BUY', 'BUY'] and row['put_confidence'] >= 70:
                strategies.append({
                    'strike': row['strike'],
                    'action': '⚠️ BEARISH/HEDGING',
                    'strategy': f"Put accumulation at ${row['strike']:.0f}",
                    'recommendation': f"Consider: Protective puts or bearish spread. Support at ${row['strike']:.0f}",
                    'risk': 'Depends on current stock price'
                })
            
            if row['call_signal'] == 'SELLING/WRITING':
                strategies.append({
                    'strike': row['strike'],
                    'action': '💰 PREMIUM SELLING',
                    'strategy': f"Call writing at ${row['strike']:.0f}",
                    'recommendation': f"Sellers see resistance at ${row['strike']:.0f}. Consider iron condor or covered calls",
                    'risk': 'Defined risk with spreads'
                })
            
            if row['put_signal'] == 'CLOSING' and row['change_OI_Put'] < -1000:
                strategies.append({
                    'strike': row['strike'],
                    'action': '✅ RISK-OFF',
                    'strategy': f"Large put closing at ${row['strike']:.0f} ({row['change_OI_Put']:.0f})",
                    'recommendation': "Bullish signal - hedges being removed. Upside potential increased",
                    'risk': 'Monitor for reversal'
                })
        
        return strategies
    
    def detect_and_analyze(self, ticker=None, trade_date=None):
        """Main detection pipeline"""
        df = self.load_oi_changes(trade_date)
        
        if df.empty:
            return None, None, None
        
        # Ticker-level anomalies
        anomalies = self.detect_ticker_level_anomalies(df)
        
        # If specific ticker requested
        if ticker:
            ticker_analysis = self.analyze_strike_level_activity(ticker, df)
            expiry_breakdown = self.get_expiry_breakdown(ticker, df)
            strategies = self.generate_strategy(ticker_analysis, expiry_breakdown)
            return anomalies, ticker_analysis, expiry_breakdown, strategies
        
        return anomalies, df, None, None
    
    def format_alert_message(self, anomalies, ticker=None, ticker_analysis=None, 
                           expiry_breakdown=None, strategies=None):
        """Format comprehensive alert message"""
        lines = []
        lines.append("🔥 ABNORMAL OPTIONS ACTIVITY DETECTED 🔥")
        lines.append(f"📅 Date: {self.get_latest_trade_date()}")
        lines.append("=" * 50)
        
        # Top anomalies
        lines.append("\n📊 TOP UNUSUAL ACTIVITY:")
        for idx, row in anomalies.head(10).iterrows():
            pcr = row['openInt_Put_now'] / row['openInt_Call_now'] if row['openInt_Call_now'] > 0 else 0
            lines.append(f"\n{row['ticker']}:")
            lines.append(f"  Call OI: {row['change_OI_Call']:+,.0f} | Put OI: {row['change_OI_Put']:+,.0f}")
            lines.append(f"  Call Vol: {row['change_vol_Call']:+,.0f} | Put Vol: {row['change_vol_Put']:+,.0f}")
            lines.append(f"  PCR: {pcr:.2f} | Z-Score: {row['max_zscore']:.2f}")
        
        # Detailed ticker analysis if provided
        if ticker and ticker_analysis is not None:
            lines.append(f"\n\n🎯 DETAILED ANALYSIS: {ticker}")
            lines.append("=" * 50)
            
            # Top strikes
            lines.append("\n📍 TOP ACTIVE STRIKES:")
            for idx, row in ticker_analysis.head(5).iterrows():
                lines.append(f"\n${row['strike']:.0f} (Exp: {row['expiry_date'].strftime('%m/%d')}, {row['days_to_expiry']}d)")
                lines.append(f"  CALLS: {row['call_signal']} ({row['call_confidence']}% confidence)")
                lines.append(f"    ΔOI: {row['change_OI_Call']:+,.0f} | Vol: {row['vol_Call_now']:,.0f}")
                lines.append(f"  PUTS: {row['put_signal']} ({row['put_confidence']}% confidence)")
                lines.append(f"    ΔOI: {row['change_OI_Put']:+,.0f} | Vol: {row['vol_Put_now']:,.0f}")
                if row['reasoning']:
                    lines.append(f"  💡 {'; '.join(row['reasoning'])}")
            
            # Expiry breakdown
            if expiry_breakdown is not None:
                lines.append("\n\n📅 ACTIVITY BY EXPIRY:")
                for _, exp in expiry_breakdown.head(5).iterrows():
                    lines.append(f"{exp['expiry_date'].strftime('%m/%d')} ({exp['days_to_expiry']}d): " +
                               f"Calls {exp['change_OI_Call']:+,.0f}, Puts {exp['change_OI_Put']:+,.0f}")
            
            # Strategies
            if strategies:
                lines.append("\n\n💡 TRADING STRATEGIES:")
                for strat in strategies[:5]:
                    lines.append(f"\n{strat['action']} @ ${strat['strike']:.0f}")
                    lines.append(f"  {strat['strategy']}")
                    lines.append(f"  ➡️ {strat['recommendation']}")
                    lines.append(f"  ⚠️ Risk: {strat['risk']}")
        
        return "\n".join(lines)


def main():
    """Example usage"""
    detector = AbnormalActivityDetector(zscore_threshold=2.0)
    
    # Detect ticker-level anomalies
    print("Detecting unusual activity...")
    anomalies, all_data, _, _ = detector.detect_and_analyze()
    
    if anomalies is not None and not anomalies.empty:
        print(f"\nFound {len(anomalies)} tickers with unusual activity")
        
        # Detailed analysis for top ticker
        top_ticker = anomalies.iloc[0]['ticker']
        print(f"\nDetailed analysis for {top_ticker}:")
        
        anomalies, ticker_analysis, expiry_breakdown, strategies = detector.detect_and_analyze(
            ticker=top_ticker
        )
        
        # Format message
        message = detector.format_alert_message(
            anomalies, top_ticker, ticker_analysis, expiry_breakdown, strategies
        )
        print("\n" + message)
        
        return anomalies, ticker_analysis, expiry_breakdown, strategies
    else:
        print("No unusual activity detected")
        return None, None, None, None


if __name__ == "__main__":
    main()
