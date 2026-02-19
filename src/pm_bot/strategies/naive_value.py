"""Naive value strategy: trade when market price diverges from a simple fair-value estimate.

The estimate is derived from the orderbook mid-price plus a configurable bias.
When the market's last traded price deviates from the mid by more than a threshold,
a signal is generated to trade toward the estimated fair value.
"""

from __future__ import annotations

from pm_bot.api.models import Action, Market, OrderBook, Side
from pm_bot.strategies.base import Signal, Strategy
from pm_bot.utils.logging import get_logger

log = get_logger("strategies.naive_value")


class NaiveValueStrategy(Strategy):
    """Buy when market appears underpriced vs mid, sell when overpriced."""

    name = "naive_value"

    def __init__(
        self,
        *,
        threshold_cents: int = 5,
        quantity: int = 1,
        min_spread: int = 2,
        max_spread: int = 30,
        min_volume: int = 10,
    ) -> None:
        self._threshold = threshold_cents
        self._quantity = quantity
        self._min_spread = min_spread
        self._max_spread = max_spread
        self._min_volume = min_volume

    def should_trade(self, market: Market) -> bool:
        return (
            market.volume >= self._min_volume
            and market.yes_bid > 0
            and market.yes_ask > 0
        )

    async def on_market_update(
        self,
        market: Market,
        orderbook: OrderBook,
    ) -> list[Signal]:
        mid = orderbook.mid_price
        spread = orderbook.spread
        if mid is None or spread is None:
            return []

        if spread < self._min_spread or spread > self._max_spread:
            return []

        last = market.last_price
        if last <= 0:
            return []

        deviation = last - mid
        signals: list[Signal] = []

        if deviation < -self._threshold:
            bid = orderbook.best_yes_bid
            if bid is None:
                return []
            signals.append(Signal(
                market_ticker=market.ticker,
                action=Action.BUY,
                side=Side.YES,
                price=bid + 1,
                quantity=self._quantity,
                confidence=min(abs(deviation) / 20.0, 1.0),
                reason=f"underpriced by {abs(deviation):.1f}c vs mid {mid:.1f}",
                strategy_name=self.name,
            ))
        elif deviation > self._threshold:
            ask = orderbook.best_yes_ask
            if ask is None:
                return []
            signals.append(Signal(
                market_ticker=market.ticker,
                action=Action.SELL,
                side=Side.YES,
                price=ask - 1,
                quantity=self._quantity,
                confidence=min(abs(deviation) / 20.0, 1.0),
                reason=f"overpriced by {abs(deviation):.1f}c vs mid {mid:.1f}",
                strategy_name=self.name,
            ))

        for s in signals:
            log.info(
                "signal_generated",
                strategy=self.name,
                ticker=s.market_ticker,
                action=s.action.value,
                price=s.price,
                confidence=f"{s.confidence:.2f}",
                reason=s.reason,
            )
        return signals
