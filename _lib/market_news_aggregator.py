"""
Comprehensive Market News & Data Aggregator
Tracks: Stocks, Indices, Commodities (Gold, Oil), Forex, Crypto
Sources: Finnhub API, Yahoo Finance, Economic Calendar
"""

import requests
import yfinance as yf
from datetime import datetime, timedelta
import sqlite3

DB_PATH = r"C:\Users\srini\Options_chain_data\US_data.db"


def get_finnhub_api_key():
    """Load Finnhub API key"""
    try:
        with open(r'C:\Users\srini\Options_chain_data\NYSE_DATA\finn_api_key.txt', 'r') as f:
            return f.read().strip()
    except:
        return None


FINNHUB_API_KEY = get_finnhub_api_key()
FINNHUB_BASE = "https://finnhub.io/api/v1"


# ===================================
# LIVE MARKET DATA
# ===================================

def get_market_snapshot():
    """
    Get current prices for major indices, commodities, currencies
    Returns dict with live data
    """
    snapshot = {}
    
    try:
        # Define symbols
        symbols = {
            # Major Indices
            '^GSPC': 'S&P 500',
            '^DJI': 'Dow Jones',
            '^IXIC': 'Nasdaq',
            '^RUT': 'Russell 2000',
            '^VIX': 'VIX (Fear Index)',
            '^NSEI': 'Nifty 50',  # India
            '^BSESN': 'Sensex',   # India
            
            # Commodities
            'GC=F': 'Gold',
            'CL=F': 'Crude Oil (WTI)',
            'BZ=F': 'Brent Oil',
            'SI=F': 'Silver',
            'NG=F': 'Natural Gas',
            'HG=F': 'Copper',
            
            # Currencies (vs USD)
            'EURUSD=X': 'EUR/USD',
            'JPY=X': 'USD/JPY',
            'GBPUSD=X': 'GBP/USD',
            'INR=X': 'USD/INR',
            'CAD=X': 'USD/CAD',
            'DX-Y.NYB': 'Dollar Index',
            
            # Crypto
            'BTC-USD': 'Bitcoin',
            'ETH-USD': 'Ethereum',
            
            # Treasury
            '^TNX': '10Y Treasury Yield',
            '^TYX': '30Y Treasury Yield'
        }
        
        for symbol, name in symbols.items():
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period='2d')
                
                if len(hist) >= 2:
                    current = hist['Close'].iloc[-1]
                    previous = hist['Close'].iloc[-2]
                    change = current - previous
                    change_pct = (change / previous) * 100
                    
                    snapshot[name] = {
                        'symbol': symbol,
                        'price': current,
                        'change': change,
                        'change_pct': change_pct,
                        'direction': 'up' if change > 0 else 'down'
                    }
            except Exception as e:
                print(f"Error fetching {name}: {e}")
                continue
        
        return snapshot
        
    except Exception as e:
        print(f"Error getting market snapshot: {e}")
        return {}


# ===================================
# NEWS AGGREGATION
# ===================================

def get_general_market_news(limit=10):
    """Get general market news from Finnhub"""
    if not FINNHUB_API_KEY:
        return []
    
    try:
        url = f"{FINNHUB_BASE}/news"
        params = {
            'category': 'general',
            'token': FINNHUB_API_KEY
        }
        
        response = requests.get(url, params=params, timeout=10)
        news = response.json()
        
        return news[:limit] if isinstance(news, list) else []
        
    except Exception as e:
        print(f"Error fetching market news: {e}")
        return []


def get_forex_news(limit=5):
    """Get forex/currency news"""
    if not FINNHUB_API_KEY:
        return []
    
    try:
        url = f"{FINNHUB_BASE}/news"
        params = {
            'category': 'forex',
            'token': FINNHUB_API_KEY
        }
        
        response = requests.get(url, params=params, timeout=10)
        news = response.json()
        
        return news[:limit] if isinstance(news, list) else []
        
    except Exception as e:
        print(f"Error fetching forex news: {e}")
        return []


def get_crypto_news(limit=5):
    """Get cryptocurrency news"""
    if not FINNHUB_API_KEY:
        return []
    
    try:
        url = f"{FINNHUB_BASE}/news"
        params = {
            'category': 'crypto',
            'token': FINNHUB_API_KEY
        }
        
        response = requests.get(url, params=params, timeout=10)
        news = response.json()
        
        return news[:limit] if isinstance(news, list) else []
        
    except Exception as e:
        print(f"Error fetching crypto news: {e}")
        return []


def get_economic_calendar(days_ahead=7):
    """Get upcoming economic events"""
    if not FINNHUB_API_KEY:
        return []
    
    try:
        from_date = datetime.now().strftime('%Y-%m-%d')
        to_date = (datetime.now() + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
        
        url = f"{FINNHUB_BASE}/calendar/economic"
        params = {
            'from': from_date,
            'to': to_date,
            'token': FINNHUB_API_KEY
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if 'economicCalendar' in data:
            return data['economicCalendar'][:10]
        return []
        
    except Exception as e:
        print(f"Error fetching economic calendar: {e}")
        return []


# ===================================
# TELEGRAM FORMATTING
# ===================================

def format_market_snapshot_telegram():
    """Format market snapshot for Telegram"""
    snapshot = get_market_snapshot()
    
    if not snapshot:
        return "📊 *Market Snapshot*\n\n_Data unavailable_\n"
    
    message = "📊 *Market Snapshot*\n\n"
    
    # Major Indices
    indices = ['S&P 500', 'Dow Jones', 'Nasdaq', 'Russell 2000', 'VIX (Fear Index)']
    message += "*Major Indices:*\n"
    for name in indices:
        if name in snapshot:
            data = snapshot[name]
            emoji = "🟢" if data['direction'] == 'up' else "🔴"
            message += f"{emoji} {name}: {data['price']:.2f} ({data['change_pct']:+.2f}%)\n"
    
    message += "\n*Commodities:*\n"
    commodities = ['Gold', 'Crude Oil (WTI)', 'Silver', 'Natural Gas']
    for name in commodities:
        if name in snapshot:
            data = snapshot[name]
            emoji = "🟢" if data['direction'] == 'up' else "🔴"
            message += f"{emoji} {name}: ${data['price']:.2f} ({data['change_pct']:+.2f}%)\n"
    
    message += "\n*Currencies:*\n"
    currencies = ['EUR/USD', 'GBP/USD', 'USD/JPY', 'Dollar Index']
    for name in currencies:
        if name in snapshot:
            data = snapshot[name]
            emoji = "🟢" if data['direction'] == 'up' else "🔴"
            message += f"{emoji} {name}: {data['price']:.4f} ({data['change_pct']:+.2f}%)\n"
    
    message += "\n*Crypto:*\n"
    crypto = ['Bitcoin', 'Ethereum']
    for name in crypto:
        if name in snapshot:
            data = snapshot[name]
            emoji = "🟢" if data['direction'] == 'up' else "🔴"
            price_str = f"${data['price']:,.0f}" if data['price'] > 100 else f"${data['price']:.2f}"
            message += f"{emoji} {name}: {price_str} ({data['change_pct']:+.2f}%)\n"
    
    message += "\n*Treasuries:*\n"
    treasuries = ['10Y Treasury Yield', '30Y Treasury Yield']
    for name in treasuries:
        if name in snapshot:
            data = snapshot[name]
            emoji = "🟢" if data['direction'] == 'up' else "🔴"
            message += f"{emoji} {name}: {data['price']:.2f}% ({data['change_pct']:+.2f}%)\n"
    
    message += "\n━━━━━━━━━━━━━━━━━━━━\n"
    return message


def format_market_news_telegram(limit=5):
    """Format market news for Telegram"""
    news = get_general_market_news(limit=limit)
    
    if not news:
        return "📰 *Market News*\n\n_No news available_\n"
    
    message = "📰 *Market News*\n\n"
    
    for i, article in enumerate(news, 1):
        headline = article.get('headline', 'No headline')
        summary = article.get('summary', '')
        source = article.get('source', 'Unknown')
        url = article.get('url', '')
        
        # Truncate headline if too long
        if len(headline) > 80:
            headline = headline[:77] + '...'
        
        message += f"*{i}. {headline}*\n"
        
        if summary and len(summary) > 0:
            # Truncate summary
            summary_short = summary[:120] + '...' if len(summary) > 120 else summary
            message += f"   {summary_short}\n"
        
        message += f"   _Source: {source}_\n\n"
    
    message += "━━━━━━━━━━━━━━━━━━━━\n"
    return message


def format_commodity_focus_telegram():
    """Special focus on Gold, Oil, and key commodities"""
    snapshot = get_market_snapshot()
    
    if not snapshot:
        return ""
    
    message = "🏆 *Commodity Watch*\n\n"
    
    commodities_detail = {
        'Gold': '🥇',
        'Crude Oil (WTI)': '🛢️',
        'Silver': '⚪',
        'Copper': '🔶',
        'Natural Gas': '🔥'
    }
    
    for name, emoji in commodities_detail.items():
        if name in snapshot:
            data = snapshot[name]
            direction_emoji = "🟢" if data['direction'] == 'up' else "🔴"
            
            # Add context
            if abs(data['change_pct']) > 3:
                intensity = "⚡ MAJOR MOVE"
            elif abs(data['change_pct']) > 1.5:
                intensity = "Notable"
            else:
                intensity = "Stable"
            
            message += f"{emoji} *{name}*\n"
            message += f"   Price: ${data['price']:.2f}\n"
            message += f"   Change: {direction_emoji} {data['change_pct']:+.2f}% ({intensity})\n\n"
    
    message += "━━━━━━━━━━━━━━━━━━━━\n"
    return message


def format_economic_calendar_telegram(days=3):
    """Format upcoming economic events"""
    events = get_economic_calendar(days_ahead=days)
    
    if not events:
        return "📅 *Economic Calendar*\n\n_No major events scheduled_\n"
    
    message = "📅 *Economic Calendar* (Next 3 Days)\n\n"
    
    for event in events[:8]:  # Top 8 events
        event_name = event.get('event', 'Unknown Event')
        country = event.get('country', 'US')
        date_str = event.get('time', '')
        impact = event.get('impact', 'medium')
        
        # Impact emoji
        if impact.lower() == 'high':
            impact_emoji = "🔴"
        elif impact.lower() == 'medium':
            impact_emoji = "🟡"
        else:
            impact_emoji = "🟢"
        
        # Format date
        try:
            event_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            date_display = event_date.strftime('%b %d, %H:%M')
        except:
            date_display = date_str
        
        message += f"{impact_emoji} *{event_name}* ({country})\n"
        message += f"   {date_display}\n\n"
    
    message += "━━━━━━━━━━━━━━━━━━━━\n"
    return message


def format_forex_focus_telegram():
    """Currency wars / major forex movements"""
    snapshot = get_market_snapshot()
    forex_news = get_forex_news(limit=3)
    
    message = "💱 *Currency Wars Watch*\n\n"
    
    # Dollar Index check
    if 'Dollar Index' in snapshot:
        dxy = snapshot['Dollar Index']
        direction = "strengthening" if dxy['direction'] == 'up' else "weakening"
        emoji = "🟢" if dxy['direction'] == 'up' else "🔴"
        message += f"{emoji} *US Dollar {direction}*\n"
        message += f"   DXY: {dxy['price']:.2f} ({dxy['change_pct']:+.2f}%)\n\n"
    
    # Major pairs
    pairs = ['EUR/USD', 'GBP/USD', 'USD/JPY']
    for pair in pairs:
        if pair in snapshot:
            data = snapshot[pair]
            emoji = "🟢" if data['direction'] == 'up' else "🔴"
            
            # Intensity
            if abs(data['change_pct']) > 0.5:
                status = "⚡ VOLATILE"
            else:
                status = "Steady"
            
            message += f"{emoji} {pair}: {data['price']:.4f} ({data['change_pct']:+.2f}%) - {status}\n"
    
    # Add top forex news if available
    if forex_news:
        message += "\n*Latest Forex News:*\n"
        for article in forex_news[:2]:
            headline = article.get('headline', '')[:60]
            message += f"• {headline}...\n"
    
    message += "\n━━━━━━━━━━━━━━━━━━━━\n"
    return message


def generate_complete_market_report():
    """
    Generate complete daily market report
    Combines: Snapshot, News, Commodities, Forex, Economic Calendar
    """
    report = "🌍 *Global Markets Daily Report*\n"
    report += f"📅 {datetime.now().strftime('%B %d, %Y - %H:%M ET')}\n"
    report += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # Market Snapshot
    report += format_market_snapshot_telegram()
    report += "\n"
    
    # Commodity Focus
    report += format_commodity_focus_telegram()
    report += "\n"
    
    # Currency Wars
    report += format_forex_focus_telegram()
    report += "\n"
    
    # Market News
    report += format_market_news_telegram(limit=5)
    report += "\n"
    
    # Economic Calendar
    report += format_economic_calendar_telegram(days=3)
    
    return report


# ===================================
# DATABASE STORAGE
# ===================================

def store_market_snapshot():
    """Store current market snapshot to database"""
    snapshot = get_market_snapshot()
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Create table if not exists
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT,
            snapshot_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        import json
        cursor.execute("""
        INSERT INTO market_snapshots (snapshot_date, snapshot_data)
        VALUES (?, ?)
        """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), json.dumps(snapshot)))
        
        conn.commit()
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error storing snapshot: {e}")
        return False


if __name__ == "__main__":
    print("🌍 Generating Global Markets Report...")
    print("=" * 60)
    
    report = generate_complete_market_report()
    print(report)
    
    print("\n" + "=" * 60)
    print("✅ Report generated!")
    print("\nThis can be sent to Telegram via telegram_rich_formatter.py")
