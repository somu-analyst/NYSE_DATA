"""
Enhanced Market News Aggregator
Gets news from multiple premium sources with links
"""

import requests
from datetime import datetime, timedelta
import yfinance as yf

def get_google_finance_news(limit=5):
    """Get market news from Google Finance RSS"""
    try:
        # Google Finance RSS feed
        url = "https://news.google.com/rss/search?q=stock+market+when:1d&hl=en-US&gl=US&ceid=US:en"
        
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(response.content)
            
            news = []
            for item in root.findall('.//item')[:limit]:
                title = item.find('title').text if item.find('title') is not None else ""
                link = item.find('link').text if item.find('link') is not None else ""
                pub_date = item.find('pubDate').text if item.find('pubDate') is not None else ""
                
                # Shorten title
                if len(title) > 80:
                    title = title[:77] + "..."
                
                news.append({
                    'headline': title,
                    'url': link,
                    'source': 'Google News',
                    'datetime': pub_date
                })
            
            return news
    except Exception as e:
        print(f"Google News error: {e}")
        return []

def get_yahoo_finance_news(limit=5):
    """Get news from Yahoo Finance"""
    try:
        # Use yfinance to get market news
        ticker = yf.Ticker("^GSPC")  # S&P 500
        news = ticker.news[:limit] if hasattr(ticker, 'news') else []
        
        formatted_news = []
        for article in news:
            title = article.get('title', '')
            if len(title) > 80:
                title = title[:77] + "..."
            
            formatted_news.append({
                'headline': title,
                'url': article.get('link', ''),
                'source': 'Yahoo Finance',
                'datetime': datetime.fromtimestamp(article.get('providerPublishTime', 0)).strftime('%H:%M')
            })
        
        return formatted_news
    except Exception as e:
        print(f"Yahoo News error: {e}")
        return []

def get_marketwatch_rss(limit=5):
    """Get MarketWatch RSS feed"""
    try:
        url = "https://feeds.marketwatch.com/marketwatch/topstories/"
        
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(response.content)
            
            news = []
            for item in root.findall('.//item')[:limit]:
                title = item.find('title').text if item.find('title') is not None else ""
                link = item.find('link').text if item.find('link') is not None else ""
                
                if len(title) > 80:
                    title = title[:77] + "..."
                
                news.append({
                    'headline': title,
                    'url': link,
                    'source': 'MarketWatch',
                    'datetime': ''
                })
            
            return news
    except Exception as e:
        print(f"MarketWatch error: {e}")
        return []

def get_aggregated_news(limit=5):
    """Get news from multiple sources"""
    all_news = []
    
    # Try multiple sources
    all_news.extend(get_yahoo_finance_news(3))
    all_news.extend(get_google_finance_news(3))
    all_news.extend(get_marketwatch_rss(2))
    
    # Remove duplicates by headline similarity
    unique_news = []
    seen_titles = set()
    
    for article in all_news:
        # First 30 chars as duplicate check
        title_key = article['headline'][:30].lower()
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            unique_news.append(article)
    
    return unique_news[:limit]

def get_economic_calendar_detailed():
    """Get upcoming economic events (next 14 days)"""
    calendar = []
    today = datetime.now()
    
    # ========================================
    # 1. FOMC MEETINGS (Federal Reserve)
    # ========================================
    fomc_dates_2026 = [
        datetime(2026, 1, 28),
        datetime(2026, 3, 18),
        datetime(2026, 4, 29),
        datetime(2026, 6, 17),
        datetime(2026, 7, 29),
        datetime(2026, 9, 16),
        datetime(2026, 11, 4),
        datetime(2026, 12, 16)
    ]
    
    for fomc_date in fomc_dates_2026:
        days_until = (fomc_date - today).days
        if 0 <= days_until <= 21:
            calendar.append({
                'event': '🏛️ FOMC Meeting',
                'date': fomc_date.strftime('%b %d'),
                'days_until': days_until,
                'impact': 'HIGH',
                'category': 'Fed Policy',
                'description': 'Interest rate decision & economic outlook'
            })
    
    # ========================================
    # 2. LABOR DATA RELEASES
    # ========================================
    
    # Jobs Report (First Friday of each month)
    current_month = today.replace(day=1)
    for i in range(3):  # Check current + next 2 months
        month_start = (current_month + timedelta(days=32*i)).replace(day=1)
        # Find first Friday
        days_ahead = (4 - month_start.weekday()) % 7  # Friday is 4
        if days_ahead == 0:
            days_ahead = 7  # If 1st is Friday, use next Friday
        first_friday = month_start + timedelta(days=days_ahead)
        
        days_until = (first_friday - today).days
        if 0 <= days_until <= 21:
            calendar.append({
                'event': '💼 Jobs Report',
                'date': first_friday.strftime('%b %d'),
                'days_until': days_until,
                'impact': 'HIGH',
                'category': 'Labor',
                'description': 'Non-Farm Payrolls & Unemployment Rate'
            })
    
    # Weekly Jobless Claims (Every Thursday)
    next_thursday = today + timedelta(days=(3 - today.weekday()) % 7)
    if next_thursday == today:
        next_thursday += timedelta(days=7)
    
    for i in range(3):  # Next 3 weeks
        claims_date = next_thursday + timedelta(days=7*i)
        days_until = (claims_date - today).days
        if 0 <= days_until <= 21:
            calendar.append({
                'event': '📊 Jobless Claims',
                'date': claims_date.strftime('%b %d'),
                'days_until': days_until,
                'impact': 'MEDIUM',
                'category': 'Labor',
                'description': 'Weekly unemployment claims'
            })
    
    # ========================================
    # 3. INFLATION DATA
    # ========================================
    
    # CPI (Consumer Price Index) - typically 10th-15th of month
    for i in range(3):
        month_start = (current_month + timedelta(days=32*i)).replace(day=1)
        cpi_date = month_start.replace(day=13)  # Usually around 13th
        
        days_until = (cpi_date - today).days
        if 0 <= days_until <= 21:
            calendar.append({
                'event': '📈 CPI Report',
                'date': cpi_date.strftime('%b %d'),
                'days_until': days_until,
                'impact': 'HIGH',
                'category': 'Inflation',
                'description': 'Consumer Price Index (Inflation data)'
            })
    
    # PPI (Producer Price Index) - usually day before CPI
    for i in range(3):
        month_start = (current_month + timedelta(days=32*i)).replace(day=1)
        ppi_date = month_start.replace(day=12)
        
        days_until = (ppi_date - today).days
        if 0 <= days_until <= 21:
            calendar.append({
                'event': '🏭 PPI Report',
                'date': ppi_date.strftime('%b %d'),
                'days_until': days_until,
                'impact': 'MEDIUM',
                'category': 'Inflation',
                'description': 'Producer Price Index'
            })
    
    # PCE (Fed's preferred inflation gauge) - end of month
    for i in range(3):
        month_start = (current_month + timedelta(days=32*i)).replace(day=1)
        # Last business day of month
        last_day = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        pce_date = last_day - timedelta(days=2)  # Usually 2 days before month end
        
        days_until = (pce_date - today).days
        if 0 <= days_until <= 21:
            calendar.append({
                'event': '💰 PCE Inflation',
                'date': pce_date.strftime('%b %d'),
                'days_until': days_until,
                'impact': 'HIGH',
                'category': 'Inflation',
                'description': "Fed's preferred inflation measure"
            })
    
    # ========================================
    # 4. GDP & ECONOMIC GROWTH
    # ========================================
    
    # GDP releases (quarterly - last week of Jan, Apr, Jul, Oct)
    gdp_months = [1, 4, 7, 10]
    for month in gdp_months:
        if month >= today.month:
            gdp_year = today.year
        else:
            gdp_year = today.year + 1
        
        # Last Thursday of the month (approximately)
        month_start = datetime(gdp_year, month, 1)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        last_day = next_month - timedelta(days=1)
        
        # Find last Thursday
        while last_day.weekday() != 3:  # Thursday is 3
            last_day -= timedelta(days=1)
        
        days_until = (last_day - today).days
        if 0 <= days_until <= 21:
            calendar.append({
                'event': '📊 GDP Report',
                'date': last_day.strftime('%b %d'),
                'days_until': days_until,
                'impact': 'HIGH',
                'category': 'Growth',
                'description': 'Gross Domestic Product (Economic growth)'
            })
    
    # ========================================
    # 5. RETAIL & CONSUMER DATA
    # ========================================
    
    # Retail Sales - mid-month
    for i in range(3):
        month_start = (current_month + timedelta(days=32*i)).replace(day=1)
        retail_date = month_start.replace(day=15)
        
        days_until = (retail_date - today).days
        if 0 <= days_until <= 21:
            calendar.append({
                'event': '🛍️ Retail Sales',
                'date': retail_date.strftime('%b %d'),
                'days_until': days_until,
                'impact': 'MEDIUM',
                'category': 'Consumer',
                'description': 'Monthly retail sales data'
            })
    
    # Consumer Confidence - last Tuesday of month
    for i in range(3):
        month_start = (current_month + timedelta(days=32*i)).replace(day=1)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        last_day = next_month - timedelta(days=1)
        
        # Find last Tuesday
        while last_day.weekday() != 1:  # Tuesday is 1
            last_day -= timedelta(days=1)
        
        days_until = (last_day - today).days
        if 0 <= days_until <= 21:
            calendar.append({
                'event': '😊 Consumer Confidence',
                'date': last_day.strftime('%b %d'),
                'days_until': days_until,
                'impact': 'MEDIUM',
                'category': 'Consumer',
                'description': 'Consumer sentiment index'
            })
    
    # ========================================
    # 6. MAJOR EARNINGS (Mega-cap tech)
    # ========================================
    
    # Earnings season timing (approximate)
    # Q1: Mid-April to early May
    # Q2: Mid-July to early August
    # Q3: Mid-October to early November
    # Q4: Late January to mid-February
    
    earnings_windows = {
        1: {'start': 20, 'end': 28, 'quarter': 'Q4'},  # Jan-Feb = Q4 results
        2: {'start': 10, 'end': 25, 'quarter': 'Q4'},  # Feb = Q4 tail
        4: {'start': 15, 'end': 30, 'quarter': 'Q1'},  # Apr = Q1 results
        5: {'start': 1, 'end': 10, 'quarter': 'Q1'},   # Early May = Q1 tail
        7: {'start': 15, 'end': 31, 'quarter': 'Q2'},  # Jul = Q2 results
        8: {'start': 1, 'end': 10, 'quarter': 'Q2'},   # Early Aug = Q2 tail
        10: {'start': 15, 'end': 31, 'quarter': 'Q3'}, # Oct = Q3 results
        11: {'start': 1, 'end': 10, 'quarter': 'Q3'},  # Early Nov = Q3 tail
    }
    
    if today.month in earnings_windows:
        window = earnings_windows[today.month]
        if window['start'] <= today.day <= window['end']:
            # We're in earnings season
            mega_caps = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA']
            
            # Approximate - earnings typically spread across 2-3 weeks
            for offset in [1, 3, 5, 7, 10, 14]:
                earnings_date = today + timedelta(days=offset)
                if offset <= 14:
                    calendar.append({
                        'event': '📊 Earnings Week',
                        'date': earnings_date.strftime('%b %d'),
                        'days_until': offset,
                        'impact': 'HIGH',
                        'category': 'Earnings',
                        'description': f'{window["quarter"]} earnings: Mega-cap tech reporting'
                    })
                    break  # Just show one earnings week marker
    
    # ========================================
    # 7. FED OFFICIALS SPEECHES
    # ========================================
    
    # Fed Chair typically speaks weekly
    # Add generic marker for Fed communications
    next_week = today + timedelta(days=7)
    calendar.append({
        'event': '🎤 Fed Communications',
        'date': next_week.strftime('%b %d'),
        'days_until': 7,
        'impact': 'MEDIUM',
        'category': 'Fed Policy',
        'description': 'Fed officials speeches & testimonies'
    })
    
    # ========================================
    # SORT & DEDUPLICATE
    # ========================================
    
    # Remove duplicates
    seen = set()
    unique_calendar = []
    for event in calendar:
        key = (event['event'], event['date'])
        if key not in seen:
            seen.add(key)
            unique_calendar.append(event)
    
    # Sort by days until
    unique_calendar.sort(key=lambda x: x['days_until'])
    
    return unique_calendar[:10]  # Return top 10 upcoming events

if __name__ == "__main__":
    print("Testing news sources...")
    news = get_aggregated_news(5)
    for n in news:
        print(f"  📰 {n['headline']}")
        print(f"     {n['url'][:50]}...")
    
    print("\nTesting calendar...")
    events = get_economic_calendar_detailed()
    for e in events:
        print(f"  📅 {e['event']} - {e['date']} ({e['days_until']} days)")
