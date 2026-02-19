"""Abstract base strategy and signal model."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pm_bot.api.models import Action, Market, OrderBook, Side


@dataclass
class Signal:
    """A trade signal emitted by a strategy."""

    market_ticker: str
    action: Action
    side: Side
    price: int
    quantity: int
    confidence: float
    reason: str
    strategy_name: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_buy(self) -> bool:
        return self.action == Action.BUY


class Strategy(ABC):
    """Base class for all trading strategies."""

    name: str = "base"

    @abstractmethod
    async def on_market_update(
        self,
        market: Market,
        orderbook: OrderBook,
    ) -> list[Signal]:
        """Evaluate a market update and return zero or more trade signals."""
        ...

    @abstractmethod
    def should_trade(self, market: Market) -> bool:
        """Return True if this strategy should consider this market."""
        ...

    def __repr__(self) -> str:
        return f"<Strategy: {self.name}>"
