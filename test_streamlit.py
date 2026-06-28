import streamlit as st
import pandas as pd
import yfinance as yf
import altair as alt


def _price_chart(df):
    """Line chart with a y-axis auto-fit to the data range (not zero-based),
    so small moves on a high price aren't flattened into a straight line."""
    long = df.reset_index().melt(id_vars=df.index.name or "index",
                                 var_name="series", value_name="price").dropna()
    xcol = df.index.name or "index"
    lo, hi = float(long["price"].min()), float(long["price"].max())
    pad = max((hi - lo) * 0.08, hi * 0.0005)
    return alt.Chart(long).mark_line().encode(
        x=alt.X(f"{xcol}:T", title=None),
        y=alt.Y("price:Q", title=None,
                scale=alt.Scale(domain=[lo - pad, hi + pad], zero=False, nice=False)),
        color="series:N",
        tooltip=[xcol, "series", alt.Tooltip("price:Q", format=",.2f")],
    )

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
            st.altair_chart(_price_chart(data[["Close"]]), use_container_width=True)

            # Moving averages
            ma = data[["Close"]].copy()
            ma["MA20"] = ma["Close"].rolling(20).mean()
            ma["MA50"] = ma["Close"].rolling(50).mean()

            st.subheader("Close with 20 & 50 day moving averages")
            st.altair_chart(_price_chart(ma), use_container_width=True)

            # Volume chart
            st.subheader("Volume")
            st.bar_chart(data["Volume"])
