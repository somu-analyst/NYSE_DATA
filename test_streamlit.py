import streamlit as st
import pandas as pd
import yfinance as yf

st.title("NYSE Stock Dashboard")

# ---- Inputs ----
ticker = st.text_input("Ticker symbol", value="AAPL")
col1, col2 = st.columns(2)
with col1:
    start = st.date_input("Start date")
with col2:
    end = st.date_input("End date")

if st.button("Load data"):
    if not ticker:
        st.error("Please enter a ticker symbol.")
    else:
        # Fetch sample data from Yahoo Finance
        data = yf.download(ticker, start=start, end=end)

        if data.empty:
            st.warning("No data returned. Check ticker and dates.")
        else:
            st.subheader(f"Price data for {ticker}")
            st.dataframe(data)

            # Close price chart
            st.subheader("Closing price")
            st.line_chart(data["Close"])

            # Moving averages
            ma = data[["Close"]].copy()
            ma["MA20"] = ma["Close"].rolling(20).mean()
            ma["MA50"] = ma["Close"].rolling(50).mean()

            st.subheader("Close with 20 & 50 day moving averages")
            st.line_chart(ma)

            # Volume chart
            st.subheader("Volume")
            st.bar_chart(data["Volume"])
