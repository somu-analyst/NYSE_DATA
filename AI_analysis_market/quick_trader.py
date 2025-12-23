#!/usr/bin/env python
# coding: utf-8

# In[1]:


#!pip install yfinance yahooquery pandas numpy pytz streamlit apscheduler ta python-telegram-bot


# In[ ]:


import os, datetime, pandas as pd, yfinance as yf
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Bot

# --- USER CONFIGURATION ---

# Add your tickers here (examples: AAPL, MSFT, SPY, QQQ)
TICKERS = ["AAPL", "MSFT", "GOOG", "TSLA", "NVDA", "NDAQ", "SPY", "QQQ"]

# Get your API token from BotFather and your chat ID as per instructions above
TELEGRAM_BOT_TOKEN = "8407478799:AAG1GbQOeUVC-SJmZS0YmXiYyAZRWrdqUWE"
TELEGRAM_CHAT_ID = 6206450586   # PASTE your chat_id here (integer, no quotes)

DATA_DIR = r"C:\Users\srini\Options_chain_data\AI_analysis_market"
os.makedirs(DATA_DIR, exist_ok=True)

def fetch_price_data(ticker, days=5):
    df = yf.download(ticker, period=f"{days}d")
    df.to_csv(f"{DATA_DIR}/{ticker}_price.csv")
    return df

def fetch_options_data(ticker):
    try:
        tk = yf.Ticker(ticker)
        if not hasattr(tk, "options"):
            return None
        rows = []
        for expiry in tk.options:
            chain = tk.option_chain(expiry)
            calls = chain.calls
            puts = chain.puts
            calls["type"] = "call"
            puts["type"] = "put"
            df = pd.concat([calls, puts])
            df["expiry"] = expiry
            rows.append(df)
        options = pd.concat(rows)
        options.to_csv(f"{DATA_DIR}/{ticker}_options.csv")
        return options
    except Exception:
        return None

def generate_signals(df):
    if df is None or len(df) < 2:
        return ""
    prev = df.iloc[-2]
    last = df.iloc[-1]
    up = (last['Close'] - prev['Close']) / prev['Close']
    if up > 0.03:
        return f"Price breakout up {up:.2%}!"
    if up < -0.03:
        return f"Price breakdown {up:.2%}!"
    return ""

def send_telegram_alert(msg):
    if not msg:
        return
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)

def main_run():
    for t in TICKERS:
        price = fetch_price_data(t)
        options = fetch_options_data(t)
        sig = generate_signals(price)
        if sig:
            send_telegram_alert(f"{t}: {sig}")

if __name__ == "__main__":
    print("Starting trading bot system.")
    print("You can leave this open. It will check every 15 minutes for new signals.")
    sched = BackgroundScheduler()
    sched.add_job(main_run, 'interval', minutes=15)
    sched.start()
    try:
        while True:
            pass
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown()

