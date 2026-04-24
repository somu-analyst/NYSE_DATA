"""
News and Earnings Integration
Pulls news, earnings, dividends, and major events from Finnhub
Tracks impact on portfolio positions
"""
import requests
import sqlite3
from datetime import datetime, timedelta
import pandas as pd

DB_PATH = r"C:\Users\srini\Options_chain_data\US_data.db"

# Finnhub API setup
def get_finnhub_api_key():
    """Load Finnhub API key from file"""
    try:
        with open(r'C:\Users\srini\Options_chain_data\NYSE_DATA\finn_api_key.txt', 'r') as f:
            return f.read().strip()
    except:
        return None


FINNHUB_API_KEY = get_finnhub_api_key()
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"


def get_earnings_dates(ticker, limit=10):
    """
    Get upcoming and past earnings dates for a ticker
    """
    if not FINNHUB_API_KEY:
        return {'error': 'Finnhub API key not found'}
    
    try:
        url = f"{FINNHUB_BASE_URL}/calendar/earnings"
        params = {
            'symbol': ticker,
            'token': FINNHUB_API_KEY
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if 'earningsCalendar' in data:
            earnings = []
            for event in data['earningsCalendar'][:limit]:
                earnings.append({
                    'date': event.get('date'),
                    'epsEstimate': event.get('epsEstimate'),
                    'epsActual': event.get('epsActual'),
                    'revenueEstimate': event.get('revenueEstimate'),
                    'revenueActual': event.get('revenueActual'),
                    'quarter': event.get('quarter'),
                    'year': event.get('year'),
                    'hour': event.get('hour'),
                    'quarter_end': event.get('quarterEnd')
                })
            return earnings
        else:
            return []
    except Exception as e:
        print(f"Error fetching earnings: {e}")
        return []


def get_company_news(ticker, days_back=30):
    """
    Get recent company news
    """
    if not FINNHUB_API_KEY:
        return {'error': 'Finnhub API key not found'}
    
    try:
        from_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
        to_date = datetime.now().strftime('%Y-%m-%d')
        
        url = f"{FINNHUB_BASE_URL}/company-news"
        params = {
            'symbol': ticker,
            'from': from_date,
            'to': to_date,
            'token': FINNHUB_API_KEY
        }
        
        response = requests.get(url, params=params, timeout=10)
        news = response.json()
        
        # Limit to recent 20
        return news[:20] if isinstance(news, list) else []
    except Exception as e:
        print(f"Error fetching news: {e}")
        return []


def get_economic_calendar(from_date=None, to_date=None):
    """
    Get major economic events that could impact markets
    """
    if not FINNHUB_API_KEY:
        return {'error': 'Finnhub API key not found'}
    
    try:
        if not from_date:
            from_date = datetime.now().strftime('%Y-%m-%d')
        if not to_date:
            to_date = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        
        url = f"{FINNHUB_BASE_URL}/economic_calendar"
        params = {
            'from': from_date,
            'to': to_date,
            'token': FINNHUB_API_KEY
        }
        
        response = requests.get(url, params=params, timeout=10)
        events = response.json()
        
        if 'data' in events:
            return events['data']
        else:
            return []
    except Exception as e:
        print(f"Error fetching economic calendar: {e}")
        return []


def analyze_news_sentiment(title, summary):
    """
    Simple sentiment analysis on news headlines/summaries
    Returns: 'positive', 'negative', 'neutral'
    """
    positive_words = ['surge', 'gain', 'jump', 'rally', 'strong', 'beat', 'upgrade', 
                     'bullish', 'upbeat', 'profit', 'success', 'growth', 'rise']
    negative_words = ['drop', 'fall', 'crash', 'loss', 'decline', 'miss', 'downgrade',
                     'bearish', 'weak', 'loss', 'fail', 'crisis', 'plunge', 'slump']
    
    text = f"{title} {summary}".lower()
    
    positive_count = sum(1 for word in positive_words if word in text)
    negative_count = sum(1 for word in negative_words if word in text)
    
    if positive_count > negative_count:
        return 'positive'
    elif negative_count > positive_count:
        return 'negative'
    else:
        return 'neutral'


def store_earnings_event(ticker, event_date, eps_est, eps_actual, announce_time='after_hours'):
    """
    Store earnings event in database
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
        INSERT OR REPLACE INTO events (
            ticker, event_type, event_date, event_time, title, description,
            eps_estimate, eps_actual, impact_expected, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ticker, 'earnings', event_date, announce_time,
            f"{ticker} Earnings Report",
            f"EPS Est: {eps_est}, Actual: {eps_actual}",
            eps_est, eps_actual, 'high', 'finnhub'
        ))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error storing earnings event: {e}")
        return False


def store_news(ticker, headline, summary, url, source='finnhub'):
    """
    Store news item in database
    """
    try:
        sentiment = analyze_news_sentiment(headline, summary)
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
        INSERT INTO news_feed (
            ticker, headline, summary, source, url, published_date, sentiment
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            ticker, headline, summary, source, url,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'), sentiment
        ))
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error storing news: {e}")
        return False


def sync_ticker_news(ticker, days_back=30):
    """
    Sync all news for a specific ticker to database
    
    Returns: Count of news items added
    """
    try:
        news_list = get_company_news(ticker, days_back)
        count = 0
        
        for news_item in news_list:
            if store_news(
                ticker=ticker,
                headline=news_item.get('headline', ''),
                summary=news_item.get('summary', ''),
                url=news_item.get('url', ''),
                source=news_item.get('source', 'finnhub')
            ):
                count += 1
        
        return count
    except Exception as e:
        print(f"Error syncing news: {e}")
        return 0


def sync_ticker_earnings(ticker):
    """
    Sync earnings dates for a ticker to database
    
    Returns: Count of earnings events added
    """
    try:
        earnings_list = get_earnings_dates(ticker)
        count = 0
        
        for earning in earnings_list:
            if earning.get('date'):
                if store_earnings_event(
                    ticker=ticker,
                    event_date=earning['date'],
                    eps_est=earning.get('epsEstimate'),
                    eps_actual=earning.get('epsActual')
                ):
                    count += 1
        
        return count
    except Exception as e:
        print(f"Error syncing earnings: {e}")
        return 0


def get_upcoming_events(days_ahead=30):
    """
    Get all upcoming events affecting positions
    
    Returns: DataFrame of upcoming events
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        
        tomorrow = datetime.now().strftime('%Y-%m-%d')
        end_date = (datetime.now() + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
        
        query = """
        SELECT * FROM events
        WHERE event_date >= ? AND event_date <= ?
        ORDER BY event_date ASC
        """
        
        df = pd.read_sql_query(query, conn, params=[tomorrow, end_date])
        conn.close()
        
        return df if not df.empty else pd.DataFrame()
    except Exception as e:
        print(f"Error getting upcoming events: {e}")
        return pd.DataFrame()


def get_impact_on_position(ticker, event_type='earnings'):
    """
    Estimate volatility impact on open positions for this ticker
    
    Returns: Impact scenario (low/medium/high volatility expected)
    """
    try:
        if event_type == 'earnings':
            earnings = get_earnings_dates(ticker, limit=1)
            if earnings:
                next_earning = earnings[0]
                return {
                    'ticker': ticker,
                    'event_type': 'earnings',
                    'event_date': next_earning.get('date'),
                    'volatility_expected': 'high',
                    'impact_magnitude': 'high',
                    'recommendation': 'Consider tightening stop loss or taking profits before earnings'
                }
        
        return {
            'ticker': ticker,
            'event_type': event_type,
            'volatility_expected': 'normal',
            'impact_magnitude': 'normal'
        }
    except Exception as e:
        print(f"Error analyzing impact: {e}")
        return {'error': str(e)}


def get_ticker_watchlist_data(tickers):
    """
    Get latest news and events for a list of tickers
    
    Returns: Dict with news, earnings, and events for each ticker
    """
    watchlist_data = {}
    
    for ticker in tickers:
        try:
            watchlist_data[ticker] = {
                'recent_news': get_company_news(ticker, days_back=7)[:5],
                'next_earnings': get_earnings_dates(ticker, limit=1),
                'impact_forecast': get_impact_on_position(ticker)
            }
        except Exception as e:
            watchlist_data[ticker] = {'error': str(e)}
    
    return watchlist_data


if __name__ == '__main__':
    print("News & Earnings Integration - Functions Available:")
    print("=" * 60)
    print()
    print("Key Functions:")
    print("  get_earnings_dates(ticker) - Upcoming/past earnings")
    print("  get_company_news(ticker, days_back=30) - Recent news")
    print("  get_economic_calendar(from_date, to_date) - Major economic events")
    print("  analyze_news_sentiment(title, summary) - Sentiment classification")
    print("  store_earnings_event(ticker, event_date, eps_est, eps_actual)")
    print("  store_news(ticker, headline, summary, url)")
    print("  sync_ticker_news(ticker) - Sync all news to database")
    print("  sync_ticker_earnings(ticker) - Sync earnings to database")
    print("  get_upcoming_events(days_ahead=30) - Future events affecting positions")
    print("  get_impact_on_position(ticker, event_type)")
    print()
    print(f"Finnhub API Key Status: {'✅ Loaded' if FINNHUB_API_KEY else '❌ Not found'}")
    print()
