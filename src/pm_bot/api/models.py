"""Pydantic models for Kalshi API responses."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# --- Enums ---

class MarketStatus(str, Enum):
    """Kalshi market lifecycle statuses (API response values)."""
    INITIALIZED = "initialized"
    INACTIVE = "inactive"
    ACTIVE = "active"
    CLOSED = "closed"
    DETERMINED = "determined"
    DISPUTED = "disputed"
    AMENDED = "amended"
    FINALIZED = "finalized"
    # Legacy values (filter/older API)
    OPEN = "open"
    SETTLED = "settled"


class OrderType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"


class OrderStatus(str, Enum):
    RESTING = "resting"
    CANCELED = "canceled"
    EXECUTED = "executed"
    PENDING = "pending"


class Side(str, Enum):
    YES = "yes"
    NO = "no"


class Action(str, Enum):
    BUY = "buy"
    SELL = "sell"


# --- Market models ---

class Market(BaseModel):
    ticker: str
    title: str
    subtitle: str = ""
    status: MarketStatus = MarketStatus.OPEN
    yes_bid: int = 0
    yes_ask: int = 0
    no_bid: int = 0
    no_ask: int = 0
    last_price: int = 0
    volume: int = 0
    open_interest: int = 0
    event_ticker: str = ""
    category: str = ""
    close_time: datetime | None = None
    expiration_time: datetime | None = None
    result: str = ""

    model_config = {"extra": "allow"}


class MarketsResponse(BaseModel):
    markets: list[Market] = []
    cursor: str = ""


class Event(BaseModel):
    event_ticker: str
    title: str
    category: str = ""
    markets: list[Market] = []

    model_config = {"extra": "allow"}


class EventsResponse(BaseModel):
    events: list[Event] = []
    cursor: str = ""


# --- Orderbook models ---

class OrderBookLevel(BaseModel):
    price: int
    quantity: int = Field(alias="quantity", default=0)


class OrderBook(BaseModel):
    yes: list[OrderBookLevel] = []
    no: list[OrderBookLevel] = []

    @property
    def best_yes_bid(self) -> int | None:
        return self.yes[0].price if self.yes else None

    @property
    def best_yes_ask(self) -> int | None:
        return (100 - self.no[0].price) if self.no else None

    @property
    def mid_price(self) -> float | None:
        bid = self.best_yes_bid
        ask = self.best_yes_ask
        if bid is not None and ask is not None:
            return (bid + ask) / 2.0
        return None

    @property
    def spread(self) -> int | None:
        bid = self.best_yes_bid
        ask = self.best_yes_ask
        if bid is not None and ask is not None:
            return ask - bid
        return None


class OrderBookResponse(BaseModel):
    orderbook: OrderBook


# --- Order models ---

class OrderRequest(BaseModel):
    ticker: str
    action: Action
    side: Side
    count: int
    type: OrderType
    yes_price: int | None = None
    no_price: int | None = None
    client_order_id: str

    model_config = {"extra": "allow"}


class Order(BaseModel):
    order_id: str = ""
    ticker: str = ""
    action: Action = Action.BUY
    side: Side = Side.YES
    type: OrderType = OrderType.LIMIT
    status: OrderStatus = OrderStatus.PENDING
    yes_price: int = 0
    no_price: int = 0
    remaining_count: int = 0
    queue_position: int | None = None
    created_time: datetime | None = None
    client_order_id: str = ""

    model_config = {"extra": "allow"}


class OrderResponse(BaseModel):
    order: Order


class OrdersResponse(BaseModel):
    orders: list[Order] = []
    cursor: str = ""


# --- Position models ---

class Position(BaseModel):
    market_ticker: str = ""
    position_cost: int = 0
    realized_pnl: int = 0
    fees_paid: int = 0
    quantity: int = 0
    side: str = ""

    model_config = {"extra": "allow"}

    @property
    def position_cost_dollars(self) -> float:
        return self.position_cost / 10_000

    @property
    def realized_pnl_dollars(self) -> float:
        return self.realized_pnl / 10_000

    @property
    def fees_paid_dollars(self) -> float:
        return self.fees_paid / 10_000


class PositionsResponse(BaseModel):
    market_positions: list[Position] = []
    cursor: str = ""


# --- Fill models ---

class Fill(BaseModel):
    trade_id: str = ""
    order_id: str = ""
    ticker: str = ""
    action: Action = Action.BUY
    side: Side = Side.YES
    count: int = 0
    yes_price: int = 0
    no_price: int = 0
    created_time: datetime | None = None

    model_config = {"extra": "allow"}


class FillsResponse(BaseModel):
    fills: list[Fill] = []
    cursor: str = ""


# --- Balance ---

class Balance(BaseModel):
    balance: int = 0

    model_config = {"extra": "allow"}

    @property
    def balance_dollars(self) -> float:
        return self.balance / 100
