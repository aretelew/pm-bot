"""Signal-based strategy: trade when external data disagrees with market price.

This is a framework for plugging in external data sources (news, weather APIs,
polling aggregators, etc.) and comparing their implied probability with the
current market price.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pm_bot.api.models import Action, Market, OrderBook, Side
from pm_bot.strategies.base import Signal, Strategy
from pm_bot.utils.logging import get_logger

log = get_logger("strategies.signal")


@dataclass
class ExternalEstimate:
    """An external probability estimate for a market."""

    source: str
    probability: float  # 0.0 to 1.0
    confidence: float = 0.5  # how much we trust this source


class DataSource(ABC):
    """Base class for external data sources."""

    name: str = "base_source"

    @abstractmethod
    async def get_estimate(self, market: Market) -> ExternalEstimate | None:
        """Return a probability estimate for the given market, or None if not applicable."""
        ...


class StaticEstimateSource(DataSource):
    """Simple source that returns a fixed estimate (for testing)."""

    name = "static"

    def __init__(self, estimates: dict[str, float] | None = None) -> None:
        self._estimates = estimates or {}

    def set_estimate(self, ticker: str, probability: float) -> None:
        self._estimates[ticker] = probability

    async def get_estimate(self, market: Market) -> ExternalEstimate | None:
        prob = self._estimates.get(market.ticker)
        if prob is not None:
            return ExternalEstimate(source=self.name, probability=prob, confidence=0.6)
        return None


class SignalBasedStrategy(Strategy):
    """Trade when external signals disagree with market price by a threshold."""

    name = "signal_based"

    def __init__(
        self,
        *,
        sources: list[DataSource] | None = None,
        threshold_cents: int = 5,
        quantity: int = 1,
        min_confidence: float = 0.3,
    ) -> None:
        self._sources = sources or []
        self._threshold = threshold_cents
        self._quantity = quantity
        self._min_confidence = min_confidence

    def add_source(self, source: DataSource) -> None:
        self._sources.append(source)

    def should_trade(self, market: Market) -> bool:
        return market.last_price > 0

    async def on_market_update(
        self,
        market: Market,
        orderbook: OrderBook,
    ) -> list[Signal]:
        if not self._sources:
            return []

        estimates: list[ExternalEstimate] = []
        for source in self._sources:
            try:
                est = await source.get_estimate(market)
                if est is not None:
                    estimates.append(est)
            except Exception:
                log.exception("source_error", source=source.name, ticker=market.ticker)

        if not estimates:
            return []

        weighted_sum = sum(e.probability * e.confidence for e in estimates)
        weight_total = sum(e.confidence for e in estimates)
        if weight_total <= 0:
            return []

        fair_value_cents = int((weighted_sum / weight_total) * 100)
        market_price = market.last_price
        edge = fair_value_cents - market_price

        if abs(edge) < self._threshold:
            return []

        avg_confidence = weight_total / len(estimates)
        if avg_confidence < self._min_confidence:
            return []

        signals: list[Signal] = []

        if edge > 0:
            bid = orderbook.best_yes_bid
            price = (bid + 1) if bid is not None else market_price
            signals.append(Signal(
                market_ticker=market.ticker,
                action=Action.BUY,
                side=Side.YES,
                price=min(price, fair_value_cents - 1),
                quantity=self._quantity,
                confidence=min(abs(edge) / 20.0, 1.0),
                reason=(
                    f"external fair value {fair_value_cents}c vs market {market_price}c "
                    f"(edge={edge}c, sources={len(estimates)})"
                ),
                strategy_name=self.name,
            ))
        else:
            ask = orderbook.best_yes_ask
            price = (ask - 1) if ask is not None else market_price
            signals.append(Signal(
                market_ticker=market.ticker,
                action=Action.SELL,
                side=Side.YES,
                price=max(price, fair_value_cents + 1),
                quantity=self._quantity,
                confidence=min(abs(edge) / 20.0, 1.0),
                reason=(
                    f"external fair value {fair_value_cents}c vs market {market_price}c "
                    f"(edge={edge}c, sources={len(estimates)})"
                ),
                strategy_name=self.name,
            ))

        return signals
