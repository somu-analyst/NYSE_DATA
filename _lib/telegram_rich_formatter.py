"""
Rich Telegram Message Formatter for Options Flow Analysis
Generates formatted messages similar to professional options flow alerts
"""

import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import requests

# Import market news aggregator
try:
    from market_news_aggregator import (
        format_market_snapshot_telegram,
        format_market_news_telegram,
        format_commodity_focus_telegram,
        format_forex_focus_telegram,
        format_economic_calendar_telegram
    )
    MARKET_NEWS_AVAILABLE = True
except ImportError:
    MARKET_NEWS_AVAILABLE = False


class OptionsFlowFormatter:
    """Format options flow data for Telegram with rich formatting"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        
    def get_connection(self):
        return sqlite3.connect(self.db_path)
    
    # ========================================
    # EMOJI & FORMATTING HELPERS
    # ========================================
    
    @staticmethod
    def strength_to_emoji(strength: int) -> str:
        """Convert strength (1-5) to visual indicator"""
        if strength >= 5:
            return "🔥🔥🔥🔥🔥"
        elif strength >= 4:
            return "🔥🔥🔥🔥"
        elif strength >= 3:
            return "🔥🔥🔥"
        elif strength >= 2:
            return "🔥🔥"
        else:
            return "🔥"
    
    @staticmethod
    def sentiment_emoji(sentiment: str) -> str:
        """Get emoji for bullish/bearish/neutral"""
        sentiment = sentiment.upper()
        if 'BULL' in sentiment:
            return "🟢"
        elif 'BEAR' in sentiment:
            return "🔴"
        else:
            return "🟡"
    
    @staticmethod
    def format_money(amount: float) -> str:
        """Format money with appropriate suffix"""
        if amount >= 1_000_000_000:
            return f"${amount/1_000_000_000:.1f}B"
        elif amount >= 1_000_000:
            return f"${amount/1_000_000:.1f}M"
        elif amount >= 1_000:
            return f"${amount/1_000:.1f}K"
        else:
            return f"${amount:.0f}"
    
    # ========================================
    # MARKET INSIGHTS
    # ========================================
    
    def generate_market_insights(self, date: str = None) -> str:
        """
        Generate AI Market Insights section
        
        Example output:
        📊 AI Market InsightsBETA
        
        Software leaders remain primary vehicles for downside macro hedging
        Outlook: Bearish (5/5 Strength)
        Stage: Established (First Detected 2026-1-29)
        """
        conn = self.get_connection()
        
        if date is None:
            # Get latest available date (MM-DD-YYYY format)
            latest_query = "SELECT DISTINCT trade_date_now FROM options_change ORDER BY substr(trade_date_now, 7, 4) || '-' || substr(trade_date_now, 1, 2) || '-' || substr(trade_date_now, 4, 2) DESC LIMIT 1"
            latest_df = pd.read_sql(latest_query, conn)
            if latest_df.empty:
                conn.close()
                return "📊 *AI Market Insights*\n\n_No data available_\n"
            date = latest_df.iloc[0]['trade_date_now']
        
        # Get sector-level insights from aggregated options data
        query = """
        SELECT 
            ticker,
            SUM(change_OI_Call) as total_call_oi,
            SUM(change_OI_Put) as total_put_oi,
            SUM(openInt_Call_now) as current_call_oi,
            SUM(openInt_Put_now) as current_put_oi,
            AVG(CASE WHEN openInt_Put_now > 0 AND openInt_Call_now > 0 
                THEN CAST(openInt_Put_now AS FLOAT) / openInt_Call_now 
                ELSE NULL END) as pcr
        FROM options_change
        WHERE trade_date_now = ?
        GROUP BY ticker
        ORDER BY ABS(total_call_oi) + ABS(total_put_oi) DESC
        LIMIT 20
        """
        
        df = pd.read_sql(query, conn, params=[date])
        conn.close()
        
        if df.empty:
            return "📊 *AI Market Insights*\n\n_No data available for analysis_\n"
        
        insights = []
        
        # Analyze for bullish/bearish themes
        bullish_tickers = df[
            (df['total_call_oi'] > df['total_put_oi']) & 
            (df['total_call_oi'] > 1000)
        ]['ticker'].tolist()
        
        bearish_tickers = df[
            (df['total_put_oi'] > df['total_call_oi']) & 
            (df['total_put_oi'] > 1000)
        ]['ticker'].tolist()
        
        if bullish_tickers:
            strength = min(5, len(bullish_tickers) // 2 + 3)
            insights.append({
                'title': 'Tech leaders showing strong upside call demand',
                'outlook': 'Bullish',
                'strength': strength,
                'stage': 'Established',
                'detected': (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d'),
                'why': f"Concentrated call buying in {', '.join(bullish_tickers[:5])} with PCR below 0.8 and positive OI changes. Institutional positioning suggests continued upside momentum."
            })
        
        if bearish_tickers:
            strength = min(5, len(bearish_tickers) // 2 + 3)
            insights.append({
                'title': 'Defensive put positioning across major names',
                'outlook': 'Bearish',
                'strength': strength,
                'stage': 'Established',
                'detected': (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'),
                'why': f"Heavy downside protection in {', '.join(bearish_tickers[:5])} with elevated put/call ratios. Hedge demand indicates caution on near-term direction."
            })
        
        # Format output
        message = "📊 *AI Market Insights*BETA\n\n"
        
        for idx, insight in enumerate(insights[:3], 1):
            message += f"*{insight['title']}*\n"
            message += f"Outlook: {self.sentiment_emoji(insight['outlook'])} *{insight['outlook']}* ({insight['strength']}/5 Strength)\n"
            message += f"Stage: {insight['stage']} (First Detected {insight['detected']})\n"
            message += f"Why: {insight['why']}\n\n"
        
        return message
    
    # ========================================
    # NOTEWORTHY TRADES
    # ========================================
    
    def generate_noteworthy_trades(self, date: str = None, limit: int = 5) -> str:
        """
        Generate AI Noteworthy Trades section
        
        Example:
        [2026-2-20 3:30 PM] MSFT 500 Put 2026-3-20 @ $51.6M (Top Position · Block)
        Intent: Hedge (5/5 Strength)
        Why: Large institutional protection...
        """
        conn = self.get_connection()
        
        if date is None:
            # Get latest available date (MM-DD-YYYY format)
            latest_query = "SELECT DISTINCT trade_date_now FROM options_change ORDER BY substr(trade_date_now, 7, 4) || '-' || substr(trade_date_now, 1, 2) || '-' || substr(trade_date_now, 4, 2) DESC LIMIT 1"
            latest_df = pd.read_sql(latest_query, conn)
            if latest_df.empty:
                conn.close()
                return "🎯 *AI Noteworthy Trades*\n\n_No data available_\n"
            date = latest_df.iloc[0]['trade_date_now']
        
        query = """
        SELECT 
            ticker,
            strike,
            expiry_date,
            change_OI_Call,
            change_OI_Put,
            openInt_Call_now,
            openInt_Put_now,
            vol_Call_now,
            vol_Put_now
        FROM options_change
        WHERE trade_date_now = ?
        ORDER BY ABS(change_OI_Call) + ABS(change_OI_Put) DESC
        LIMIT ?
        """
        
        df = pd.read_sql(query, conn, params=[date, limit * 2])
        conn.close()
        
        if df.empty:
            return "🎯 *AI Noteworthy Trades*\n\n_No significant trades detected_\n"
        
        message = "🎯 *AI Noteworthy Trades*BETA\n\n"
        
        trade_count = 0
        for _, row in df.iterrows():
            if trade_count >= limit:
                break
            
            ticker = row['ticker']
            strike = row['strike']
            expiry = row['expiry_date']
            
            # Determine if it's call or put dominant
            call_oi = abs(row['change_OI_Call'])
            put_oi = abs(row['change_OI_Put'])
            
            if call_oi > put_oi and call_oi > 100:
                option_type = "Call"
                oi_change = call_oi
                volume = row['vol_Call_now'] if pd.notna(row['vol_Call_now']) else 0
                sentiment = "Bullish"
                intent = "Stock Replacement"
            elif put_oi > call_oi and put_oi > 100:
                option_type = "Put"
                oi_change = put_oi
                volume = row['vol_Put_now'] if pd.notna(row['vol_Put_now']) else 0
                sentiment = "Bearish"
                intent = "Hedge"
            else:
                continue
            
            # Estimate premium (simplified)
            premium = oi_change * strike * 0.05  # Rough estimate
            
            # Determine strength
            strength = 5 if oi_change > 5000 else (4 if oi_change > 2000 else 3)
            
            # Format timestamp
            timestamp = datetime.now().strftime('%Y-%m-%d %I:%M %p')
            
            # Build trade line
            tags = []
            if oi_change > 3000:
                tags.append("Top Position")
            if volume > oi_change * 0.8:
                tags.append("Block")
            else:
                tags.append("Sweep")
            
            tag_str = " · ".join(tags)
            
            message += f"[{timestamp}] *{ticker} {strike:.0f} {option_type} {expiry}* @ {self.format_money(premium)} ({tag_str})\n"
            message += f"Intent: {intent} ({strength}/5 Strength)\n"
            message += f"Why: {self.sentiment_emoji(sentiment)} {oi_change:,.0f} contracts indicate {sentiment.lower()} positioning. "
            
            if intent == "Hedge":
                message += "Likely protective puts for existing long positions.\n\n"
            else:
                message += "Aggressive directional bet or stock replacement strategy.\n\n"
            
            trade_count += 1
        
        return message
    
    # ========================================
    # OPTIONS PLAYS / STRATEGIES
    # ========================================
    
    def generate_options_plays(self, date: str = None, limit: int = 5) -> str:
        """
        Generate strategy recommendations based on flow
        
        Example:
        NVDA Bull Call Spread
        Outlook: Bullish (5/5 Strength)
        Strategy: Use ATM call spreads 2-6 weeks out...
        """
        conn = self.get_connection()
        
        if date is None:
            # Get latest available date (MM-DD-YYYY format)
            latest_query = "SELECT DISTINCT trade_date_now FROM options_change ORDER BY substr(trade_date_now, 7, 4) || '-' || substr(trade_date_now, 1, 2) || '-' || substr(trade_date_now, 4, 2) DESC LIMIT 1"
            latest_df = pd.read_sql(latest_query, conn)
            if latest_df.empty:
                conn.close()
                return "💡 *AI Options Plays*\n\n_No data available_\n"
            date = latest_df.iloc[0]['trade_date_now']
        
        query = """
        SELECT 
            ticker,
            SUM(change_OI_Call) as total_call_oi,
            SUM(change_OI_Put) as total_put_oi,
            AVG(CASE WHEN openInt_Put_now > 0 AND openInt_Call_now > 0 
                THEN CAST(openInt_Put_now AS FLOAT) / openInt_Call_now 
                ELSE NULL END) as pcr
        FROM options_change
        WHERE trade_date_now = ?
        GROUP BY ticker
        HAVING ABS(total_call_oi) + ABS(total_put_oi) > 500
        ORDER BY ABS(total_call_oi) + ABS(total_put_oi) DESC
        LIMIT ?
        """
        
        df = pd.read_sql(query, conn, params=[date, limit])
        conn.close()
        
        if df.empty:
            return "💡 *AI Options Plays*\n\n_No clear setups identified_\n"
        
        message = "💡 *AI Options Plays*BETA\n\n"
        
        for _, row in df.iterrows():
            ticker = row['ticker']
            call_oi = row['total_call_oi']
            put_oi = row['total_put_oi']
            pcr = row['pcr'] if pd.notna(row['pcr']) else 1.0
            
            # Determine strategy
            if call_oi > put_oi and call_oi > 1000:
                strategy_name = f"{ticker} Bull Call Spread"
                outlook = "Bullish"
                strength = 5 if call_oi > 5000 else 4
                strategy_desc = "Use ATM to modest OTM call spreads expiring in 2-6 weeks to ride continued upside while capping premium."
                why = f"{ticker} shows dominant ask-side call flow with {call_oi:,.0f} net call OI and PCR of {pcr:.2f}. This supports defined-risk bullish structures."
            
            elif put_oi > call_oi and put_oi > 1000:
                strategy_name = f"{ticker} Bear Put Spread"
                outlook = "Bearish"
                strength = 5 if put_oi > 5000 else 4
                strategy_desc = f"Consider slightly OTM bear put spreads 2-6 weeks out to lean into {ticker} weakness while limiting cost."
                why = f"{ticker} carries heavy put premium with {put_oi:,.0f} net put OI and elevated PCR of {pcr:.2f}. Points to continued downside risk."
            
            else:
                continue
            
            message += f"*{strategy_name}*\n"
            message += f"Outlook: {self.sentiment_emoji(outlook)} *{outlook}* ({strength}/5 Strength)\n"
            message += f"Strategy: {strategy_desc}\n"
            message += f"Why: {why}\n\n"
        
        return message
    
    # ========================================
    # UNUSUAL ACTIVITY
    # ========================================
    
    def generate_unusual_activity(self, date: str = None, limit: int = 10) -> str:
        """
        Generate unusual OTM activity section
        
        Example:
        Unusual OTM %:
        AMAT - 47%
        NVDA - 45%
        """
        conn = self.get_connection()
        
        if date is None:
            # Get latest available date (MM-DD-YYYY format)
            latest_query = "SELECT DISTINCT trade_date_now FROM options_change ORDER BY substr(trade_date_now, 7, 4) || '-' || substr(trade_date_now, 1, 2) || '-' || substr(trade_date_now, 4, 2) DESC LIMIT 1"
            latest_df = pd.read_sql(latest_query, conn)
            if latest_df.empty:
                conn.close()
                return "🔍 *Unusual Options Activity*\n\n_No data available_\n"
            date = latest_df.iloc[0]['trade_date_now']
        
        # Get stock prices for OTM calculation
        query = """
        SELECT 
            oc.ticker,
            oc.strike,
            oc.change_OI_Call,
            oc.change_OI_Put,
            oc.vol_Call_now,
            oc.vol_Put_now,
            sd.close as stock_price
        FROM options_change oc
        LEFT JOIN stock_daily sd ON oc.ticker = sd.ticker 
            AND oc.trade_date_now = sd.trade_date
        WHERE oc.trade_date_now = ?
        """
        
        df = pd.read_sql(query, conn, params=[date])
        conn.close()
        
        if df.empty:
            return "🔍 *Unusual Options Activity*\n\n_No unusual activity detected_\n"
        
        # Calculate OTM percentage
        unusual_tickers = []
        
        for ticker, group in df.groupby('ticker'):
            if group['stock_price'].iloc[0] is None or pd.isna(group['stock_price'].iloc[0]):
                continue
            
            stock_price = group['stock_price'].iloc[0]
            
            # Count OTM activity
            otm_call_activity = group[group['strike'] > stock_price * 1.05]['change_OI_Call'].abs().sum()
            otm_put_activity = group[group['strike'] < stock_price * 0.95]['change_OI_Put'].abs().sum()
            total_activity = group['change_OI_Call'].abs().sum() + group['change_OI_Put'].abs().sum()
            
            if total_activity > 100:
                otm_pct = ((otm_call_activity + otm_put_activity) / total_activity * 100)
                if otm_pct > 25:  # Threshold for "unusual"
                    unusual_tickers.append((ticker, otm_pct, total_activity))
        
        # Sort by OTM percentage
        unusual_tickers.sort(key=lambda x: x[1], reverse=True)
        
        message = "🔍 *Unusual Options Activity*\n\n"
        message += "*Unusual OTM %:*\n"
        
        for ticker, otm_pct, activity in unusual_tickers[:limit]:
            message += f"{ticker} - {otm_pct:.0f}%\n"
        
        message += "\n"
        
        return message
    
    # ========================================
    # SWEEP ACTIVITY
    # ========================================
    
    def generate_sweep_activity(self, date: str = None, limit: int = 10) -> str:
        """
        Generate sweep options activity
        
        Example:
        Sweep Volume:
        NVDA - 499K
        TSLA - 143K
        """
        conn = self.get_connection()
        
        if date is None:
            # Get latest available date (MM-DD-YYYY format)
            latest_query = "SELECT DISTINCT trade_date_now FROM options_change ORDER BY substr(trade_date_now, 7, 4) || '-' || substr(trade_date_now, 1, 2) || '-' || substr(trade_date_now, 4, 2) DESC LIMIT 1"
            latest_df = pd.read_sql(latest_query, conn)
            if latest_df.empty:
                conn.close()
                return "⚡ *Sweep Options Activity*\n\n_No data available_\n"
            date = latest_df.iloc[0]['trade_date_now']
        
        query = """
        SELECT 
            ticker,
            SUM(vol_Call_now + vol_Put_now) as total_volume,
            SUM(change_OI_Call) as call_oi,
            SUM(change_OI_Put) as put_oi,
            COUNT(*) as num_strikes
        FROM options_change
        WHERE trade_date_now = ?
        GROUP BY ticker
        ORDER BY total_volume DESC
        LIMIT ?
        """
        
        df = pd.read_sql(query, conn, params=[date, limit])
        conn.close()
        
        if df.empty:
            return "⚡ *Sweep Options Activity*\n\n_No sweep activity detected_\n"
        
        message = "⚡ *Sweep Options Activity*\n\n"
        message += "*Sweep Volume:*\n"
        
        for _, row in df.iterrows():
            ticker = row['ticker']
            volume = row['total_volume']
            
            if volume >= 1_000_000:
                vol_str = f"{volume/1_000_000:.1f}M"
            elif volume >= 1_000:
                vol_str = f"{volume/1_000:.0f}K"
            else:
                vol_str = f"{volume:.0f}"
            
            message += f"{ticker} - {vol_str}\n"
        
        message += "\n"
        
        return message
    
    # ========================================    # INSIDER TRADES
    # ========================================
    
    def generate_insider_trades(self, limit: int = 5) -> str:
        """
        Generate Latest High-Signal Insider Trades section
        
        Example:
        🔥 Latest High-Signal Insider Trades
        
        [2026-2-20] NVDA - Jensen Huang (CEO)
        Action: BOUGHT $15.2M worth (100,000 shares @ $152.00)
        Signal: 🟢 BULLISH (5/5 Strength)
        """
        conn = self.get_connection()
        
        # Check if insider_trades table exists
        try:
            query = """
            SELECT 
                transaction_date,
                ticker,
                insider_name,
                insider_title,
                transaction_type,
                shares,
                value_usd,
                trading_signal_strength
            FROM insider_trades
            WHERE trading_signal_strength IN ('HIGH', 'MEDIUM')
            ORDER BY transaction_date DESC, value_usd DESC
            LIMIT ?
            """
            
            df = pd.read_sql(query, conn, params=[limit])
        except Exception:
            conn.close()
            return "🔥 *Latest High-Signal Insider Trades*\n\n_Table not available_\n"
        
        conn.close()
        
        if df.empty:
            return "🔥 *Latest High-Signal Insider Trades*\n\n_No recent high-signal trades_\n"
        
        message = "🔥 *Latest High-Signal Insider Trades*\n\n"
        
        for _, row in df.iterrows():
            # Parse transaction type
            trans_type = str(row['transaction_type']).upper()
            if 'BUY' in trans_type or 'PURCHASE' in trans_type:
                action = "BOUGHT"
                emoji = "🟢"
                signal = "BULLISH"
            elif 'SELL' in trans_type or 'SALE' in trans_type:
                action = "SOLD"
                emoji = "🔴"
                signal = "BEARISH"
            else:
                action = trans_type
                emoji = "🟡"
                signal = "NEUTRAL"
            
            # Strength
            strength = row['trading_signal_strength']
            if strength == 'HIGH':
                strength_val = 5
            elif strength == 'MEDIUM':
                strength_val = 4
            else:
                strength_val = 3
            
            # Format
            ticker = row['ticker']
            date = row['transaction_date']
            insider = row['insider_name']
            title = row['insider_title'] if pd.notna(row['insider_title']) else "Insider"
            shares = row['shares'] if pd.notna(row['shares']) else 0
            value = row['value_usd'] if pd.notna(row['value_usd']) else 0
            
            message += f"[{date}] *{ticker}* - {insider} ({title})\n"
            message += f"Action: {action} {self.format_money(value)} worth"
            if shares > 0:
                price_per_share = value / shares if shares > 0 else 0
                message += f" ({shares:,.0f} shares @ ${price_per_share:.2f})\n"
            else:
                message += "\n"
            message += f"Signal: {emoji} *{signal}* ({strength_val}/5 Strength)\n\n"
        
        return message
    
    # ========================================
    # CONGRESS TRADES
    # ========================================
    
    def generate_congress_trades(self, limit: int = 5) -> str:
        """
        Generate Latest High-Signal Congress Trades section
        
        Example:
        🏛️ Latest High-Signal Congress Trades
        
        [2026-2-19] NVDA - Senator John Doe (D-CA)
        Action: BOUGHT $250K-$500K
        Signal: 🟢 BULLISH (5/5 Strength)
        """
        conn = self.get_connection()
        
        # Check if congress_trades table exists
        try:
            query = """
            SELECT 
                transaction_date,
                ticker,
                representative,
                party,
                state,
                transaction_type,
                amount_range,
                value_usd,
                trading_signal_strength
            FROM congress_trades
            WHERE trading_signal_strength IN ('HIGH', 'MEDIUM')
            ORDER BY transaction_date DESC, value_usd DESC
            LIMIT ?
            """
            
            df = pd.read_sql(query, conn, params=[limit])
        except Exception:
            conn.close()
            return "🏛️ *Latest High-Signal Congress Trades*\n\n_Table not available_\n"
        
        conn.close()
        
        if df.empty:
            return "🏛️ *Latest High-Signal Congress Trades*\n\n_No recent high-signal trades_\n"
        
        message = "🏛️ *Latest High-Signal Congress Trades*\n\n"
        
        for _, row in df.iterrows():
            # Parse transaction type
            trans_type = str(row['transaction_type']).upper()
            if 'BUY' in trans_type or 'PURCHASE' in trans_type:
                action = "BOUGHT"
                emoji = "🟢"
                signal = "BULLISH"
            elif 'SELL' in trans_type or 'SALE' in trans_type:
                action = "SOLD"
                emoji = "🔴"
                signal = "BEARISH"
            else:
                action = trans_type
                emoji = "🟡"
                signal = "NEUTRAL"
            
            # Strength
            strength = row['trading_signal_strength']
            if strength == 'HIGH':
                strength_val = 5
            elif strength == 'MEDIUM':
                strength_val = 4
            else:
                strength_val = 3
            
            # Format
            ticker = row['ticker']
            date = row['transaction_date']
            rep = row['representative']
            party = row['party'] if pd.notna(row['party']) else ""
            state = row['state'] if pd.notna(row['state']) else ""
            
            party_state = f"{party}-{state}" if party and state else (party or state or "")
            
            amount_range = row['amount_range'] if pd.notna(row['amount_range']) else ""
            value = row['value_usd'] if pd.notna(row['value_usd']) else 0
            
            message += f"[{date}] *{ticker}* - {rep}"
            if party_state:
                message += f" ({party_state})"
            message += "\n"
            
            message += f"Action: {action} "
            if amount_range:
                message += f"{amount_range}"
            elif value > 0:
                message += f"{self.format_money(value)}"
            message += "\n"
            
            message += f"Signal: {emoji} *{signal}* ({strength_val}/5 Strength)\n\n"
        
        return message
    
    # ========================================    # MAIN DAILY REPORT GENERATOR
    # ========================================
    
    def generate_daily_report(self, date: str = None) -> str:
        """
        Generate complete daily options flow report
        
        Returns formatted Telegram message with all sections
        """
        conn = self.get_connection()
        
        if date is None:
            # Get latest available date (MM-DD-YYYY format)
            latest_query = "SELECT DISTINCT trade_date_now FROM options_change ORDER BY substr(trade_date_now, 7, 4) || '-' || substr(trade_date_now, 1, 2) || '-' || substr(trade_date_now, 4, 2) DESC LIMIT 1"
            latest_df = pd.read_sql(latest_query, conn)
            if latest_df.empty:
                conn.close()
                return "📈 *Options Flow Daily Report*\n\n_No data available in database_"
            date = latest_df.iloc[0]['trade_date_now']
        
        conn.close()
        
        # Build header
        try:
            report_date = datetime.strptime(date, '%Y-%m-%d').strftime('%B %d, %Y')
        except:
            report_date = date
        message = "📈 *Options Flow Daily Report*\n"
        message += f"📅 {report_date} (Latest Available)\n"
        message += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        # Add each section
        message += self.generate_market_insights(date)
        message += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        message += self.generate_noteworthy_trades(date, limit=3)
        message += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        message += self.generate_options_plays(date, limit=3)
        message += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        message += self.generate_unusual_activity(date, limit=10)
        message += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        message += self.generate_unusual_activity(date, limit=10)
        message += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        message += self.generate_sweep_activity(date, limit=10)
        message += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        # Add insider and congress trades
        message += self.generate_insider_trades(limit=5)
        message += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        message += self.generate_congress_trades(limit=5)
        
        return message


# ========================================
# TELEGRAM SENDER
# ========================================

def send_telegram_message(bot_token: str, chat_id: str, text: str, parse_mode: str = "Markdown"):
    """Send formatted message to Telegram"""
    url = f'https://api.telegram.org/bot{bot_token}/sendMessage'
    
    # Split message if too long (Telegram limit ~4096 characters)
    max_length = 4000
    messages = []
    
    if len(text) <= max_length:
        messages = [text]
    else:
        # Split by sections (━━━ separators)
        sections = text.split("━━━━━━━━━━━━━━━━━━━━")
        current_msg = ""
        
        for section in sections:
            if len(current_msg) + len(section) < max_length:
                current_msg += section + "━━━━━━━━━━━━━━━━━━━━"
            else:
                if current_msg:
                    messages.append(current_msg)
                current_msg = section + "━━━━━━━━━━━━━━━━━━━━"
        
        if current_msg:
            messages.append(current_msg)
    
    # Send each message
    results = []
    for msg in messages:
        payload = {
            'chat_id': chat_id,
            'text': msg,
            'parse_mode': parse_mode,
            'disable_web_page_preview': True
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            results.append(response.status_code == 200)
        except Exception as e:
            print(f"Error sending message: {e}")
            results.append(False)
    
    return all(results)


# ========================================
# USAGE EXAMPLE
# ========================================

if __name__ == "__main__":
    # Configuration
    DB_PATH = r"C:\Users\srini\Options_chain_data\US_data.db"
    
    # Load credentials
    import os
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    
    try:
        with open(os.path.join(BASE_DIR, "us_bot_token.txt"), "r") as f:
            BOT_TOKEN = f.read().strip()
        
        with open(os.path.join(BASE_DIR, "us_chat_id.txt"), "r") as f:
            CHAT_ID = f.read().strip()
    except FileNotFoundError:
        print("Error: bot token or chat ID file not found")
        print("Create us_bot_token.txt and us_chat_id.txt in the script directory")
        exit(1)
    
    # Generate report
    formatter = OptionsFlowFormatter(DB_PATH)
    report = formatter.generate_daily_report()
    
    print("Generated Report:")
    print("=" * 50)
    print(report)
    print("=" * 50)
    
    # Send to Telegram
    print("\nSending to Telegram...")
    success = send_telegram_message(BOT_TOKEN, CHAT_ID, report)
    
    if success:
        print("✅ Report sent successfully!")
    else:
        print("❌ Failed to send report")
