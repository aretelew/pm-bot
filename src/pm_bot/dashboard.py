"""Streamlit dashboard for monitoring the trading bot."""

from __future__ import annotations

import asyncio

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine

from pm_bot.config import get_settings
from pm_bot.data.models import (
    MarketRecord,
    OrderRecord,
    PriceRecord,
    StrategySignalRecord,
)


def run_async(coro):
    loop = asyncio.new_event_loop()
    return loop.run_until_complete(coro)


@st.cache_resource
def get_engine():
    settings = get_settings()
    return create_async_engine(settings.db_url, echo=False)


async def _fetch_records(model, limit=200, order_col=None):
    engine = get_engine()
    async with AsyncSession(engine) as session:
        stmt = select(model)
        if order_col is not None:
            stmt = stmt.order_by(order_col.desc())
        stmt = stmt.limit(limit)
        result = await session.exec(stmt)
        return list(result.all())


def fetch_records(model, limit=200, order_col=None):
    return run_async(_fetch_records(model, limit, order_col))


# --- Page config ---
st.set_page_config(page_title="PM-Bot Dashboard", layout="wide")
st.title("PM-Bot Trading Dashboard")

tab_overview, tab_markets, tab_orders, tab_signals, tab_prices = st.tabs(
    ["Overview", "Markets", "Orders", "Signals", "Prices"]
)

# --- Overview ---
with tab_overview:
    col1, col2, col3, col4 = st.columns(4)

    orders = fetch_records(OrderRecord, limit=10000, order_col=OrderRecord.created_at)
    signals = fetch_records(StrategySignalRecord, limit=10000, order_col=StrategySignalRecord.created_at)
    markets = fetch_records(MarketRecord, limit=500, order_col=MarketRecord.fetched_at)

    col1.metric("Total Orders", len(orders))
    col2.metric("Total Signals", len(signals))
    col3.metric("Signals Executed", sum(1 for s in signals if s.executed))
    col4.metric("Markets Tracked", len(set(m.ticker for m in markets)))

    if orders:
        df_orders = pd.DataFrame([{
            "time": o.created_at,
            "ticker": o.ticker,
            "action": o.action,
            "side": o.side,
            "status": o.status,
            "strategy": o.strategy,
        } for o in orders])
        if not df_orders.empty and "time" in df_orders.columns:
            df_orders["time"] = pd.to_datetime(df_orders["time"])
            daily = df_orders.set_index("time").resample("h").size()
            st.subheader("Orders per Hour")
            st.bar_chart(daily)

# --- Markets ---
with tab_markets:
    st.subheader("Latest Market Snapshots")
    if markets:
        df_m = pd.DataFrame([{
            "Ticker": m.ticker,
            "Title": m.title[:60],
            "Last Price": m.last_price,
            "Yes Bid": m.yes_bid,
            "Yes Ask": m.yes_ask,
            "Volume": m.volume,
            "Fetched At": m.fetched_at,
        } for m in markets[:100]])
        st.dataframe(df_m, use_container_width=True)
    else:
        st.info("No market data yet. Run the scanner to collect data.")

# --- Orders ---
with tab_orders:
    st.subheader("Order Log")
    if orders:
        df_o = pd.DataFrame([{
            "Time": o.created_at,
            "Order ID": o.order_id[:12],
            "Ticker": o.ticker,
            "Action": o.action,
            "Side": o.side,
            "Type": o.order_type,
            "Price": o.yes_price or o.no_price,
            "Qty": o.count,
            "Status": o.status,
            "Strategy": o.strategy,
            "Reason": o.reason[:50] if o.reason else "",
        } for o in orders[:200]])
        st.dataframe(df_o, use_container_width=True)
    else:
        st.info("No orders yet.")

# --- Signals ---
with tab_signals:
    st.subheader("Strategy Signals")
    if signals:
        df_s = pd.DataFrame([{
            "Time": s.created_at,
            "Strategy": s.strategy,
            "Ticker": s.ticker,
            "Side": s.side,
            "Price": s.price,
            "Qty": s.quantity,
            "Confidence": f"{s.confidence:.2f}",
            "Executed": s.executed,
            "Reason": s.reason[:60] if s.reason else "",
        } for s in signals[:200]])
        st.dataframe(df_s, use_container_width=True)

        strategy_counts = df_s["Strategy"].value_counts()
        fig = go.Figure(data=[go.Pie(labels=strategy_counts.index, values=strategy_counts.values)])
        fig.update_layout(title="Signals by Strategy")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No signals yet.")

# --- Prices ---
with tab_prices:
    st.subheader("Price History")
    prices = fetch_records(PriceRecord, limit=5000, order_col=PriceRecord.captured_at)
    if prices:
        tickers = sorted(set(p.ticker for p in prices))
        selected = st.selectbox("Select ticker", tickers)
        filtered = [p for p in prices if p.ticker == selected]
        if filtered:
            df_p = pd.DataFrame([{
                "Time": p.captured_at,
                "Price": p.yes_price,
                "Volume": p.volume,
                "Source": p.source,
            } for p in filtered])
            df_p["Time"] = pd.to_datetime(df_p["Time"])
            df_p = df_p.sort_values("Time")

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df_p["Time"], y=df_p["Price"], mode="lines+markers", name="Yes Price"))
            fig.update_layout(title=f"Price: {selected}", xaxis_title="Time", yaxis_title="Price (cents)")
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No price data yet. Run the scanner or stream first.")
