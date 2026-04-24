"""
ORGANIZED Market Report - Same Data, Better Format
Tables, charts, and clear sections!
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '_lib'))
import sqlite3
from datetime import datetime
import pandas as pd
from telegram_rich_formatter import send_telegram_message
from market_news_aggregator import get_market_snapshot
from market_news_enhanced import get_aggregated_news
from options_flow_detector import OptionsFlowDetector
import requests

# Database & Credentials
DB_PATH = r"C:\Users\srini\Options_chain_data\US_data.db"
BOT_TOKEN_FILE = "us_bot_token.txt"
CHAT_ID_FILE = "us_chat_id.txt"

if os.path.exists(BOT_TOKEN_FILE) and os.path.exists(CHAT_ID_FILE):
    with open(BOT_TOKEN_FILE) as f:
        BOT_TOKEN = f.read().strip()
    with open(CHAT_ID_FILE) as f:
        CHAT_ID = f.read().strip()
else:
    print("❌ Credentials not found")
    exit(1)

def format_market_tables():
    """Format market data as clean tables"""
    try:
        snapshot = get_market_snapshot()
        if not snapshot:
            return "_Market data unavailable_\n"
        
        report = "📊 *MARKET OVERVIEW*\n\n"
        
        # INDICES TABLE with country flags
        report += "*Indices*\n"
        report += "```\n"
        report += "Index         Price   Change   \n"
        report += "───────────  ──────  ───────  ─\n"
        indices = [
            ('S&P 500', '🇺🇸 SPX'),
            ('Dow Jones', '🇺🇸 DOW'),
            ('Nasdaq', '🇺🇸 NDX'),
            ('Russell 2000', '🇺🇸 RUT'),
            ('VIX (Fear Index)', '🇺🇸 VIX'),
            ('Nifty 50', '🇮🇳 NIFTY'),
            ('Sensex', '🇮🇳 SENSEX')
        ]
        
        for name, symbol in indices:
            if name in snapshot:
                d = snapshot[name]
                emoji = "🟢" if d['direction'] == 'up' else "🔴"
                price_str = f"{d['price']:.2f}".rjust(6)
                change_str = f"{d['change_pct']:+.2f}%".rjust(7)
                report += f"{symbol.ljust(11)}  {price_str}  {change_str}  {emoji}\n"
        
        report += "```\n"
        
        # COMMODITIES TABLE with emojis
        report += "*Commodities*\n"
        report += "```\n"
        report += "Commodity      Price   Change   \n"
        report += "──────────  ────────  ───────  ─\n"
        commodities = [
            ('Gold', '🥇 GOLD'),
            ('Silver', '🥈 SLVR'),
            ('Crude Oil (WTI)', '🛢️ OIL'),
            ('Natural Gas', '🔥 GAS')
        ]
        
        for name, symbol in commodities:
            if name in snapshot:
                d = snapshot[name]
                emoji = "🟢" if d['direction'] == 'up' else "🔴"
                
                # Format price with $ aligned
                price_str = f"${d['price']:7.2f}"
                change_str = f"{d['change_pct']:+6.2f}%"
                
                # Add intensity indicator
                if abs(d['change_pct']) > 3:
                    intensity = "⚡"
                elif abs(d['change_pct']) > 1.5:
                    intensity = "•"
                else:
                    intensity = " "
                
                report += f"{symbol.ljust(10)}  {price_str}  {change_str}  {emoji}{intensity}\n"
        
        report += "```\n"
        
        # CURRENCIES TABLE with flags and sentiment
        report += "*Currencies*\n"
        report += "```\n"
        report += "Pair           Price  Change  \n"
        report += "──────────  ────────  ──────  ──────\n"
        currencies = [
            ('Dollar Index', '💵 DXY'),
            ('EUR/USD', '🇪🇺 EUR/USD'),
            ('GBP/USD', '🇬🇧 GBP/USD'),
            ('USD/JPY', '🇯🇵 USD/JPY'),
            ('USD/INR', '🇮🇳 USD/INR'),
            ('USD/CAD', '🇨🇦 USD/CAD')
        ]
        
        for name, symbol in currencies:
            if name in snapshot:
                d = snapshot[name]
                
                # Determine sentiment
                if abs(d['change_pct']) > 0.5:
                    if d['direction'] == 'up':
                        signal = "🟢 BUL"
                    else:
                        signal = "🔴 BER"
                elif abs(d['change_pct']) > 0.2:
                    signal = "🟡 NEU"
                else:
                    signal = "⚪ FLT"
                
                price_str = f"{d['price']:8.4f}"
                change_str = f"{d['change_pct']:+5.2f}%"
                report += f"{symbol.ljust(10)}  {price_str}  {change_str}  {signal}\n"
        
        report += "```\n"
        
        # CRYPTO TABLE
        report += "*Crypto*\n"
        report += "```\n"
        report += "Crypto      Price   Change   \n"
        report += "────────  ────────  ───────  ─\n"
        cryptos = [
            ('Bitcoin', 'BTC'),
            ('Ethereum', 'ETH')
        ]
        
        for name, symbol in cryptos:
            if name in snapshot:
                d = snapshot[name]
                emoji = "🟢" if d['direction'] == 'up' else "🔴"
                price_str = f"${d['price']:,.0f}".rjust(8)
                change_str = f"{d['change_pct']:+.2f}%".rjust(7)
                report += f"{symbol.ljust(8)}  {price_str}  {change_str}  {emoji}\n"
        
        report += "```\n"
        
        return report
        
    except Exception as e:
        print(f"Market table error: {e}")
        return "_Market data unavailable_\n"

def format_news_with_links(limit=5):
    """Get news with clickable links"""
    try:
        news = get_aggregated_news(limit)
        
        if not news:
            return ""
        
        report = "📰 *TOP NEWS*\n\n"
        
        for i, article in enumerate(news, 1):
            headline = article['headline']
            url = article['url']
            source = article['source']
            
            # Create clickable link (Telegram format)
            report += f"{i}. [{headline}]({url})\n"
            report += f"   _{source}_\n\n"
        
        return report
        
    except Exception as e:
        print(f"News error: {e}")
        return ""

def get_reddit_stock_analysis():
    """Get REAL stocks from Reddit with analysis"""
    try:
        # Real tickers to track
        real_tickers = {
            'SPY', 'QQQ', 'TSLA', 'NVDA', 'AAPL', 'MSFT', 'AMZN', 'GOOGL', 
            'META', 'AMD', 'PLTR', 'SOFI', 'MSTR', 'COIN', 'GME', 'AMC',
            'NFLX', 'DIS', 'BABA', 'NIO', 'RIVN', 'INTC', 'PYPL', 'SQ',
            'SHOP', 'ROKU', 'SNAP', 'UBER', 'LYFT', 'ABNB', 'HOOD'
        }
        
        ticker_data = {}
        
        # Scan WSB + r/stocks
        for subreddit in ['wallstreetbets', 'stocks']:
            url = f"https://www.reddit.com/r/{subreddit}/hot.json"
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(url, headers=headers, params={'limit': 25}, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                for post in data.get('data', {}).get('children', []):
                    post_data = post.get('data', {})
                    text = f"{post_data.get('title', '')} {post_data.get('selftext', '')}".upper()
                    score = post_data.get('score', 0)
                    title = post_data.get('title', '')[:60]
                    
                    # Check for real tickers
                    for ticker in real_tickers:
                        if ticker in text:
                            if ticker not in ticker_data:
                                ticker_data[ticker] = {
                                    'mentions': 0,
                                    'max_score': 0,
                                    'top_post': '',
                                    'sentiment': 'NEUTRAL'
                                }
                            
                            ticker_data[ticker]['mentions'] += 1
                            if score > ticker_data[ticker]['max_score']:
                                ticker_data[ticker]['max_score'] = score
                                ticker_data[ticker]['top_post'] = title
                            
                            # Simple sentiment
                            if any(word in text for word in ['MOON', 'CALLS', 'BUY', 'BULL']):
                                ticker_data[ticker]['sentiment'] = 'BULLISH'
                            elif any(word in text for word in ['PUTS', 'SELL', 'BEAR', 'SHORT']):
                                ticker_data[ticker]['sentiment'] = 'BEARISH'
        
        # Get top 10 by mentions
        sorted_tickers = sorted(ticker_data.items(), key=lambda x: x[1]['mentions'], reverse=True)[:10]
        
        if not sorted_tickers:
            return ""
        
        report = "📱 *REDDIT TRENDING* (Real Stocks Only)\n\n"
        report += "```\n"
        report += "Ticker  Mentions  Sentiment   Top Post\n"
        report += "──────  ────────  ──────────  ───────────────\n"
        
        for ticker, data in sorted_tickers:
            mentions_str = str(data['mentions']).rjust(8)
            
            # Sentiment emoji
            if data['sentiment'] == 'BULLISH':
                sent = "🟢 BULL"
            elif data['sentiment'] == 'BEARISH':
                sent = "🔴 BEAR"
            else:
                sent = "🟡 NEUT"
            
            post_short = data['top_post'][:22] + "..." if len(data['top_post']) > 22 else data['top_post'].ljust(25)
            
            report += f"{ticker.ljust(6)}  {mentions_str}  {sent.ljust(10)}  {post_short}\n"
        
        report += "```\n"
        
        return report
        
    except Exception as e:
        print(f"Reddit error: {e}")
        return ""

def get_options_flow_table():
    """Get options flow as organized table with strategy"""
    try:
        conn = sqlite3.connect(DB_PATH)
        
        # Get latest date (properly sorted by YYYY-MM-DD)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT trade_date_now FROM options_change 
            ORDER BY substr(trade_date_now, 7, 4) || '-' || substr(trade_date_now, 1, 2) || '-' || substr(trade_date_now, 4, 2) DESC 
            LIMIT 1
        """)
        latest_date = cursor.fetchone()[0]
        
        if not latest_date:
            return ""
        
        # Top 10 by total volume
        query = """
        SELECT 
            ticker,
            SUM(vol_Call_now) as call_vol,
            SUM(vol_Put_now) as put_vol,
            SUM(change_OI_Call) as call_oi,
            SUM(change_OI_Put) as put_oi,
            (SUM(vol_Call_now) + SUM(vol_Put_now)) as total_vol
        FROM options_change
        WHERE trade_date_now = ?
        GROUP BY ticker
        ORDER BY total_vol DESC
        LIMIT 10
        """
        
        df = pd.read_sql(query, conn, params=[latest_date])
        conn.close()
        
        if df.empty:
            return ""
        
        # Convert date to MM-DD-YYYY format
        from datetime import datetime
        try:
            date_obj = datetime.strptime(latest_date, '%m-%d-%Y')
            display_date = date_obj.strftime('%m-%d-%Y')
        except:
            display_date = latest_date
        
        report = f"⚡ *OPTIONS FLOW* ({display_date})\n\n"
        report += "```\n"
        report += "Tick   Calls  Puts P/C OI_Ch Signal\n"
        report += "────  ──────  ──── ─── ───── ──────\n"
        
        for _, row in df.iterrows():
            ticker = row['ticker']
            calls = int(row['call_vol']) if pd.notna(row['call_vol']) else 0
            puts = int(row['put_vol']) if pd.notna(row['put_vol']) else 0
            call_oi = int(row['call_oi']) if pd.notna(row['call_oi']) else 0
            put_oi = int(row['put_oi']) if pd.notna(row['put_oi']) else 0
            
            # Put/Call ratio
            pcr = puts / calls if calls > 0 else 0
            
            # Signal
            if calls > puts * 1.5 and call_oi > 0:
                signal = "🟢 BUL"
            elif puts > calls * 1.5 and put_oi > 0:
                signal = "🔴 BER"
            elif calls > puts * 1.2:
                signal = "🟡 LN+"
            elif puts > calls * 1.2:
                signal = "🟡 LN-"
            else:
                signal = "⚪ NEU"
            
            calls_k = f"{calls//1000}K".rjust(6)
            puts_k = f"{puts//1000}K".rjust(4)
            pcr_str = f"{pcr:.1f}".rjust(3)
            oi_str = f"{call_oi - put_oi:+,}"[:5].rjust(5)
            
            report += f"{ticker.ljust(4)}  {calls_k}  {puts_k} {pcr_str} {oi_str} {signal}\n"
        
        report += "```\n"
        
        return report
        
    except Exception as e:
        print(f"Options flow error: {e}")
        return ""

def get_trading_strategies():
    """Generate trading strategies with option prices and probabilities"""
    try:
        import yfinance as yf
        from datetime import datetime
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT trade_date_now FROM options_change 
            ORDER BY substr(trade_date_now, 7, 4) || '-' || substr(trade_date_now, 1, 2) || '-' || substr(trade_date_now, 4, 2) DESC 
            LIMIT 1
        """)
        latest_date = cursor.fetchone()[0]
        
        if not latest_date:
            return ""
        
        # Get top bullish setups
        query_bull = """
        SELECT ticker, SUM(vol_Call_now) as calls, SUM(vol_Put_now) as puts
        FROM options_change
        WHERE trade_date_now = ?
        GROUP BY ticker
        HAVING (calls + puts) > 10000
        ORDER BY (calls - puts) DESC
        LIMIT 3
        """
        
        df_bull = pd.read_sql(query_bull, conn, params=[latest_date])
        
        # Get top bearish setups
        query_bear = """
        SELECT ticker, SUM(vol_Call_now) as calls, SUM(vol_Put_now) as puts
        FROM options_change
        WHERE trade_date_now = ?
        GROUP BY ticker
        HAVING (calls + puts) > 10000
        ORDER BY (puts - calls) DESC
        LIMIT 3
        """
        
        df_bear = pd.read_sql(query_bear, conn, params=[latest_date])
        conn.close()
        
        report = "📋 *TRADE IDEAS*\n\n"
        report += "```\n"
        report += "Tick  Price  ATM  OTM  Cost  Max  Win%\n"
        report += "──── ──────  ─── ──── ───── ──── ────\n"
        
        # ========== BULLISH SETUPS ==========
        if not df_bull.empty:
            report += "🟢 BULLISH (Bull Call Spreads)\n"
            
            for _, row in df_bull.iterrows():
                ticker = row['ticker']
                try:
                    stock = yf.Ticker(ticker)
                    current_price = stock.history(period='1d')['Close'].iloc[-1]
                    
                    # Get options expiring in ~30 days
                    expirations = stock.options
                    if len(expirations) > 0:
                        # Find expiration ~30 days out
                        target_exp = None
                        for exp in expirations[:8]:
                            exp_date = datetime.strptime(exp, '%Y-%m-%d')
                            days_out = (exp_date - datetime.now()).days
                            if 20 <= days_out <= 40:
                                target_exp = exp
                                break
                        
                        if not target_exp:
                            target_exp = expirations[1] if len(expirations) > 1 else expirations[0]
                        
                        opt_chain = stock.option_chain(target_exp)
                        calls = opt_chain.calls
                        
                        # Find ATM and OTM strikes
                        calls['diff'] = abs(calls['strike'] - current_price)
                        atm_call = calls.iloc[calls['diff'].idxmin()]
                        
                        # OTM: ~5% above current
                        otm_strike = current_price * 1.05
                        calls['diff_otm'] = abs(calls['strike'] - otm_strike)
                        otm_call = calls.iloc[calls['diff_otm'].idxmin()]
                        
                        # Calculate spread cost (debit)
                        spread_cost = atm_call['lastPrice'] - otm_call['lastPrice']
                        spread_width = otm_call['strike'] - atm_call['strike']
                        max_profit = spread_width - spread_cost
                        
                        # Win probability (simplified: based on delta if available)
                        win_prob = 60 if spread_cost < spread_width * 0.4 else 50
                        
                        # Format for table
                        price_str = f"${current_price:.0f}".rjust(6)
                        atm_str = f"${atm_call['lastPrice']:.0f}".rjust(3)
                        otm_str = f"${otm_call['lastPrice']:.0f}".rjust(4)
                        cost_str = f"${spread_cost*100:.0f}".rjust(5)  # x100 for contract
                        max_str = f"${max_profit*100:.0f}".rjust(4)
                        prob_str = f"{win_prob}%".rjust(4)
                        
                        report += f"{ticker.ljust(4)} {price_str} {atm_str} {otm_str} {cost_str} {max_str} {prob_str}\n"
                    else:
                        report += f"{ticker.ljust(6)} No options data available\n"
                        
                except Exception:
                    pass  # Skip errors
            
            # Add separator between bullish and bearish
            if not df_bear.empty:
                report += "─────────────────────────────────────\n"
        
        # ========== BEARISH SETUPS ==========
        if not df_bear.empty:
            report += "🔴 BEARISH (Bear Put Spreads)\n"
            
            for _, row in df_bear.iterrows():
                ticker = row['ticker']
                try:
                    stock = yf.Ticker(ticker)
                    current_price = stock.history(period='1d')['Close'].iloc[-1]
                    
                    expirations = stock.options
                    if len(expirations) > 0:
                        # Find expiration ~30 days out
                        target_exp = None
                        for exp in expirations[:8]:
                            exp_date = datetime.strptime(exp, '%Y-%m-%d')
                            days_out = (exp_date - datetime.now()).days
                            if 20 <= days_out <= 40:
                                target_exp = exp
                                break
                        
                        if not target_exp:
                            target_exp = expirations[1] if len(expirations) > 1 else expirations[0]
                        
                        opt_chain = stock.option_chain(target_exp)
                        puts = opt_chain.puts
                        
                        # Find ATM and OTM puts
                        puts['diff'] = abs(puts['strike'] - current_price)
                        atm_put = puts.iloc[puts['diff'].idxmin()]
                        
                        # OTM put: ~5% below current
                        otm_strike = current_price * 0.95
                        puts['diff_otm'] = abs(puts['strike'] - otm_strike)
                        otm_put = puts.iloc[puts['diff_otm'].idxmin()]
                        
                        # Calculate spread cost
                        spread_cost = atm_put['lastPrice'] - otm_put['lastPrice']
                        spread_width = atm_put['strike'] - otm_put['strike']
                        max_profit = spread_width - spread_cost
                        
                        win_prob = 60 if spread_cost < spread_width * 0.4 else 50
                        
                        price_str = f"${current_price:.0f}".rjust(6)
                        atm_str = f"${atm_put['lastPrice']:.0f}".rjust(3)
                        otm_str = f"${otm_put['lastPrice']:.0f}".rjust(4)
                        cost_str = f"${spread_cost*100:.0f}".rjust(5)
                        max_str = f"${max_profit*100:.0f}".rjust(4)
                        prob_str = f"{win_prob}%".rjust(4)
                        
                        report += f"{ticker.ljust(4)} {price_str} {atm_str} {otm_str} {cost_str} {max_str} {prob_str}\n"
                    else:
                        report += f"{ticker.ljust(6)} No options data available\n"
                        
                except Exception:
                    pass  # Skip errors
        
        if not df_bull.empty or not df_bear.empty:
            report += "```\n"
            report += "_ATM=At-Money, OTM=Out-Money_\n"
            report += "_Cost=$ per contract, Max=Profit_\n\n"
        
        return report
        
    except Exception as e:
        print(f"Strategy error: {e}")
        return ""

def get_important_events():
    """Get upcoming and past important events with impact analysis"""
    try:
        from market_news_enhanced import get_economic_calendar_detailed
        events = get_economic_calendar_detailed()
        
        if not events:
            return ""
        
        report = "📅 *UPCOMING EVENTS*\n\n"
        report += "```\n"
        report += "Event           Date  Days Impact Risk\n"
        report += "─────────────  ─────  ──── ────── ──────────\n"
        
        for event in events[:8]:  # Show top 8 upcoming
            # Event name with icon (already has emoji)
            event_name = event['event'][:15].ljust(15)
            
            # Date (shorten format)
            date_str = event['date'][:5].rjust(5)
            
            # Days until
            days = event['days_until']
            if days == 0:
                days_str = "NOW".rjust(5)
            elif days == 1:
                days_str = "1d".rjust(5)
            else:
                days_str = f"{days}d".rjust(5)
            
            # Impact level
            impact = event['impact']
            if impact == 'HIGH':
                impact_str = "🔴 HI"
            elif impact == 'MEDIUM':
                impact_str = "🟡 MD"
            else:
                impact_str = "🟢 LO"
            
            # Risk/Consequence (shortened)
            category = event['category']
            if category == 'Fed Policy':
                risk = "Rate shock"
            elif category == 'Labor':
                risk = "Jobs risk"
            elif category == 'Inflation':
                risk = "CPI spike"
            elif category == 'Earnings':
                risk = "Miss=-5%"
            elif category == 'Growth':
                risk = "GDP weak"
            else:
                risk = "Vol up"
            
            report += f"{event_name} {date_str} {days_str} {impact_str} {risk}\n"
        
        report += "```\n\n"
        
        # ========== LAST WEEK EVENTS ==========
        from economic_data_tracker import get_recent_economic_releases
        
        recent_releases = get_recent_economic_releases()
        
        if recent_releases:
            report += "*📊 LAST WEEK EVENTS*\n\n"
            report += "```\n"
            report += "Event         Date Expct Actl Impact\n"
            report += "───────────  ───── ───── ──── ──────────\n"
            
            for release in recent_releases[:7]:  # Show last 7 events
                event_str = release['event'][:13].ljust(13)
                date_str = release['date'][:5].rjust(5)
                expect_str = release['expected'][:5].ljust(5)
                actual_str = release['actual'][:4].ljust(4)
                
                # Format impact with emoji
                if release['beat']:
                    impact_emoji = "🟢"
                else:
                    impact_emoji = "🔴"
                
                impact_str = f"{impact_emoji} {release['impact']}".ljust(14)
                
                report += f"{event_str} {date_str} {expect_str} {actual_str} {impact_str}\n"
            
            report += "```\n"
            report += "_Impact = S&P reaction · 🟢 Beat · 🔴 Miss_\n\n"
        
        return report
        
    except Exception as e:
        print(f"Calendar error: {e}")
        return ""

def get_unusual_option_flows():
    """Get unusual options flows for major stocks"""
    try:
        detector = OptionsFlowDetector(db_path="US_data-bk.db")
        
        # Check major stocks for unusual flows
        symbols = ['GOOG', 'MSFT', 'AAPL', 'TSLA', 'NVDA', 'SPY', 'QQQ']
        
        all_signals = []
        for symbol in symbols:
            signals = detector.get_all_signals(symbol, limit_per_rule=25)
            if len(signals) > 0:
                all_signals.append(signals)
        
        if not all_signals:
            return ""
        
        # Combine and sort by confidence
        df_combined = pd.concat(all_signals, ignore_index=True)
        df_combined = df_combined.sort_values('confidence', ascending=False).head(10)
        
        if df_combined.empty:
            return ""
        
        report = "⚡ *UNUSUAL OPTIONS FLOWS* (Institutional Signals)\n\n"
        report += "```\n"
        report += "Ticker  Signal         Confidence  Details\n"
        report += "──────  ─────────────  ──────────  ────────────────────\n"
        
        for _, row in df_combined.iterrows():
            ticker = str(row.get('trade_date_now', '')).ljust(6)[:6]
            signal_type = str(row.get('signal_type', 'UNKNOWN')).ljust(13)[:13]
            confidence_pct = str(int(row.get('confidence', 0)))
            confidence = f"{confidence_pct}%".ljust(10)
            
            # Build details based on signal type
            stype = row.get('signal_type')
            if stype == 'LIQUIDATION':
                oi_chg = row.get('pct_change_OI_Put', 0)
                details = f"OI {oi_chg:.0f}%"
            elif stype == 'ACCUMULATION':
                oi_chg = row.get('pct_change_OI_Call', 0)
                details = f"OI +{oi_chg:.0f}%"
            elif stype == 'CONVICTION':
                vol = int(row.get('vol_Call_now', 0))
                details = f"Vol:{vol//1000}K"
            elif stype == 'BULLISH_FLIP' or stype == 'BEARISH_FLIP':
                call_pct = row.get('pct_change_OI_Call', 0)
                put_pct = row.get('pct_change_OI_Put', 0)
                details = f"C:{call_pct:+.0f}% P:{put_pct:+.0f}%"
            else:
                details = "Monitoring"
            
            report += f"{ticker}  {signal_type}  {confidence} {details}\n"
        
        report += "```\n\n"
        
        return report
        
    except Exception as e:
        print(f"Flow detection error: {e}")
        return ""

def get_signal_performance_section():
    """Build current signals + previous signals performance with OHLC comparison"""
    try:
        conn = sqlite3.connect(DB_PATH)

        date_query = """
        SELECT DISTINCT trade_date_now as trade_date
        FROM options_change
        ORDER BY substr(trade_date_now, 7, 4) || '-' || substr(trade_date_now, 1, 2) || '-' || substr(trade_date_now, 4, 2) DESC
        LIMIT 5
        """
        available_dates = pd.read_sql(date_query, conn)
        if available_dates.empty or len(available_dates) < 2:
            conn.close()
            return ""

        current_signal_date = available_dates.iloc[0]['trade_date']
        prev_signal_date = available_dates.iloc[1]['trade_date']

        def get_signals_for_date(signal_date: str):
            query = """
            SELECT
                ticker,
                AVG(COALESCE(pct_change_OI_Call, 0)) as call_oi_pct,
                AVG(COALESCE(pct_change_OI_Put, 0)) as put_oi_pct
            FROM options_change
            WHERE trade_date_now = ?
            GROUP BY ticker
            """
            df = pd.read_sql(query, conn, params=[signal_date])
            if df.empty:
                return []

            signals = []
            for _, row in df.iterrows():
                ticker = row['ticker']
                call_oi = float(row['call_oi_pct']) if pd.notna(row['call_oi_pct']) else 0.0
                put_oi = float(row['put_oi_pct']) if pd.notna(row['put_oi_pct']) else 0.0

                if call_oi > put_oi * 2.5 and call_oi > 50:
                    signal = 'BUY'
                elif put_oi > call_oi * 2.5 and put_oi > 50:
                    signal = 'SELL'
                else:
                    continue

                signals.append({
                    'ticker': ticker,
                    'signal': signal,
                    'call_oi': call_oi,
                    'put_oi': put_oi,
                    'strength': abs(call_oi - put_oi)
                })

            signals.sort(key=lambda x: x['strength'], reverse=True)
            return signals[:10]

        prev_signals = get_signals_for_date(prev_signal_date)
        current_signals = get_signals_for_date(current_signal_date)

        next_for_prev_query = """
        SELECT DISTINCT trade_date as trade_date
        FROM stock_daily
        WHERE (substr(trade_date, 7, 4) || '-' || substr(trade_date, 1, 2) || '-' || substr(trade_date, 4, 2)) >
              (substr(?, 7, 4) || '-' || substr(?, 1, 2) || '-' || substr(?, 4, 2))
        ORDER BY substr(trade_date, 7, 4) || '-' || substr(trade_date, 1, 2) || '-' || substr(trade_date, 4, 2) ASC
        LIMIT 1
        """
        next_prev_df = pd.read_sql(next_for_prev_query, conn, params=[prev_signal_date, prev_signal_date, prev_signal_date])
        prev_validation_date = next_prev_df.iloc[0]['trade_date'] if not next_prev_df.empty else None

        next_for_current_df = pd.read_sql(next_for_prev_query, conn, params=[current_signal_date, current_signal_date, current_signal_date])
        current_validation_date = next_for_current_df.iloc[0]['trade_date'] if not next_for_current_df.empty else None

        report = "🎯 *SIGNALS & PERFORMANCE*\n\n"

        report += f"*Previous Signals* ({prev_signal_date})"
        if prev_validation_date:
            report += f" → {prev_validation_date} validation\n"
        else:
            report += "\n"

        if prev_signals and prev_validation_date:
            ohlc_prev = pd.read_sql(
                """
                SELECT ticker, open, high, low, close
                FROM stock_daily
                WHERE trade_date = ?
                """,
                conn,
                params=[prev_validation_date]
            )
            ohlc_map = {r['ticker']: r for _, r in ohlc_prev.iterrows()}

            report += "```\n"
            report += "Tick Sig C_OI P_OI Open  High   Low  Close Res   P&L%\n"
            report += "──── ─── ──── ──── ───── ───── ───── ───── ───── ─────\n"

            hit_count = 0
            eval_count = 0
            for sig in prev_signals:
                t = sig['ticker']
                if t not in ohlc_map:
                    continue

                o = float(ohlc_map[t]['open'])
                h = float(ohlc_map[t]['high'])
                l = float(ohlc_map[t]['low'])
                c = float(ohlc_map[t]['close'])

                if sig['signal'] == 'BUY':
                    if h > o:
                        res = 'HIT'
                        pnl = ((h - o) / o) * 100 if o else 0
                    else:
                        res = 'MISS'
                        pnl = ((c - o) / o) * 100 if o else 0
                else:
                    if l < o:
                        res = 'HIT'
                        pnl = ((o - l) / o) * 100 if o else 0
                    else:
                        res = 'MISS'
                        pnl = ((o - c) / o) * 100 if o else 0

                eval_count += 1
                if res == 'HIT':
                    hit_count += 1

                report += (
                    f"{t[:4].ljust(4)} {sig['signal'][:3].ljust(3)} "
                    f"{int(sig['call_oi']):>4}% {int(sig['put_oi']):>4}% "
                    f"{o:>5.1f} {h:>5.1f} {l:>5.1f} {c:>5.1f} {res.ljust(5)} {pnl:>+4.1f}\n"
                )

            report += "```\n"
            if eval_count > 0:
                win_rate = (hit_count / eval_count) * 100
                report += f"_Performance: {hit_count}/{eval_count} HIT ({win_rate:.0f}% win rate)_\n\n"
            else:
                report += "_No OHLC validation rows found._\n\n"
        else:
            report += "_No previous signals available for validation._\n\n"

        report += f"*Current Signals* ({current_signal_date})"
        if current_validation_date:
            report += f" → pending/partial {current_validation_date}\n"
        else:
            report += " → awaiting next trading day\n"

        if current_signals:
            report += "```\n"
            report += "Tick Sig C_OI P_OI Status\n"
            report += "──── ─── ──── ──── ─────────\n"
            for sig in current_signals:
                status = "PENDING"
                report += f"{sig['ticker'][:4].ljust(4)} {sig['signal'][:3].ljust(3)} {int(sig['call_oi']):>4}% {int(sig['put_oi']):>4}% {status}\n"
            report += "```\n\n"
        else:
            report += "_No current signals generated._\n\n"

        conn.close()
        return report

    except Exception as e:
        print(f"Signal performance section error: {e}")
        return ""

# BUILD COMPLETE ORGANIZED REPORT
print("="*60)
print("Generating Organized Market Report...")
print("="*60)

current_time = datetime.now().strftime('%b %d, %I:%M %p ET')

report = "🌍 *MARKET REPORT*\n"
report += f"🕐 {current_time}\n"
report += "━━━━━━━━━━━━━━━━━━━━\n\n"

print("  Building market tables...")
report += format_market_tables()

print("  Getting news with links...")
report += format_news_with_links(5)

print("  Checking important events...")
report += get_important_events()

print("  Scanning unusual options flows...")
report += get_unusual_option_flows()

print("  Building signals performance table...")
report += get_signal_performance_section()

print("  Scanning Reddit for real stocks...")
report += get_reddit_stock_analysis()

print("  Building options flow table...")
report += get_options_flow_table()

print("  Generating trade strategies...")
report += get_trading_strategies()

report += "\n_Updated hourly · Auto-generated_"

# Send
print("\nSending organized report...")
success = send_telegram_message(BOT_TOKEN, CHAT_ID, report)

if success:
    print("Organized report sent!")
    print(f"   Length: {len(report)} chars")
    print("   Format: Tables + Links + Strategies")
else:
    print("[FAIL] Send failed")

print("="*60)
