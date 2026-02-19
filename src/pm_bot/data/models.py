"""Database models for persisting market data and trades."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MarketRecord(SQLModel, table=True):
    __tablename__ = "markets"

    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(index=True)
    title: str = ""
    status: str = "open"
    event_ticker: str = ""
    category: str = ""
    yes_bid: int = 0
    yes_ask: int = 0
    no_bid: int = 0
    no_ask: int = 0
    last_price: int = 0
    volume: int = 0
    open_interest: int = 0
    close_time: datetime | None = None
    fetched_at: datetime = Field(default_factory=_utcnow)


class OrderBookSnapshot(SQLModel, table=True):
    __tablename__ = "orderbook_snapshots"

    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(index=True)
    best_yes_bid: int | None = None
    best_yes_ask: int | None = None
    mid_price: float | None = None
    spread: int | None = None
    yes_levels_json: str = "[]"
    no_levels_json: str = "[]"
    captured_at: datetime = Field(default_factory=_utcnow)


class TradeRecord(SQLModel, table=True):
    __tablename__ = "trades"

    id: int | None = Field(default=None, primary_key=True)
    trade_id: str = ""
    ticker: str = Field(index=True)
    action: str = ""
    side: str = ""
    count: int = 0
    yes_price: int = 0
    no_price: int = 0
    order_id: str = ""
    client_order_id: str = ""
    created_time: datetime | None = None
    recorded_at: datetime = Field(default_factory=_utcnow)


class PriceRecord(SQLModel, table=True):
    __tablename__ = "prices"

    id: int | None = Field(default=None, primary_key=True)
    ticker: str = Field(index=True)
    yes_price: int = 0
    volume: int = 0
    source: str = "ticker"
    captured_at: datetime = Field(default_factory=_utcnow)


class OrderRecord(SQLModel, table=True):
    __tablename__ = "order_log"

    id: int | None = Field(default=None, primary_key=True)
    order_id: str = Field(index=True, default="")
    client_order_id: str = ""
    ticker: str = Field(index=True, default="")
    action: str = ""
    side: str = ""
    order_type: str = ""
    yes_price: int = 0
    no_price: int = 0
    count: int = 0
    remaining_count: int = 0
    status: str = ""
    strategy: str = ""
    reason: str = ""
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class StrategySignalRecord(SQLModel, table=True):
    __tablename__ = "strategy_signals"

    id: int | None = Field(default=None, primary_key=True)
    strategy: str = ""
    ticker: str = Field(index=True, default="")
    side: str = ""
    price: int = 0
    quantity: int = 0
    confidence: float = 0.0
    reason: str = ""
    executed: bool = False
    created_at: datetime = Field(default_factory=_utcnow)
