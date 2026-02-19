"""Cross-market arbitrage: finds inconsistencies between related Kalshi markets.

Examples of exploitable inconsistencies:
- "GDP > 3%" priced higher than "GDP > 2%" (monotonicity violation)
- A set of mutually exclusive outcomes whose prices sum to more or less than 100
"""

from __future__ import annotations

import re
from collections import defaultdict

from pm_bot.api.models import Action, Market, OrderBook, Side
from pm_bot.strategies.base import Signal, Strategy
from pm_bot.utils.logging import get_logger

log = get_logger("strategies.arbitrage")


def _extract_threshold(title: str) -> float | None:
    """Try to extract a numeric threshold from a market title like 'GDP growth above 3.0%'."""
    match = re.search(r"(above|below|over|under|>=?|<=?)\s*([\d.]+)", title, re.IGNORECASE)
    if match:
        try:
            return float(match.group(2))
        except ValueError:
            return None
    return None


class CrossMarketArbStrategy(Strategy):
    """Detect and exploit price inconsistencies across related markets."""

    name = "arbitrage"

    def __init__(
        self,
        *,
        min_edge_cents: int = 3,
        quantity: int = 1,
    ) -> None:
        self._min_edge = min_edge_cents
        self._quantity = quantity
        self._event_markets: dict[str, list[Market]] = defaultdict(list)

    def should_trade(self, market: Market) -> bool:
        return bool(market.event_ticker)

    def register_markets(self, markets: list[Market]) -> None:
        """Group markets by their parent event for cross-comparison."""
        self._event_markets.clear()
        for m in markets:
            if m.event_ticker:
                self._event_markets[m.event_ticker].append(m)

    async def on_market_update(
        self,
        market: Market,
        orderbook: OrderBook,
    ) -> list[Signal]:
        signals: list[Signal] = []

        event_ticker = market.event_ticker
        related = self._event_markets.get(event_ticker, [])
        if len(related) < 2:
            return []

        signals.extend(self._check_monotonicity(related))
        signals.extend(self._check_overround(related))

        return signals

    def _check_monotonicity(self, markets: list[Market]) -> list[Signal]:
        """If thresholds are ordered, prices should be monotonically decreasing."""
        priced = []
        for m in markets:
            thresh = _extract_threshold(m.title)
            if thresh is not None and m.last_price > 0:
                priced.append((thresh, m))

        priced.sort(key=lambda x: x[0])
        signals: list[Signal] = []

        for i in range(len(priced) - 1):
            lower_thresh, lower_m = priced[i]
            upper_thresh, upper_m = priced[i + 1]

            if upper_m.last_price > lower_m.last_price + self._min_edge:
                edge = upper_m.last_price - lower_m.last_price
                signals.append(Signal(
                    market_ticker=upper_m.ticker,
                    action=Action.SELL,
                    side=Side.YES,
                    price=upper_m.last_price - 1,
                    quantity=self._quantity,
                    confidence=min(edge / 15.0, 1.0),
                    reason=(
                        f"monotonicity violation: {upper_m.ticker}@{upper_m.last_price}c > "
                        f"{lower_m.ticker}@{lower_m.last_price}c (edge={edge}c)"
                    ),
                    strategy_name=self.name,
                ))
                signals.append(Signal(
                    market_ticker=lower_m.ticker,
                    action=Action.BUY,
                    side=Side.YES,
                    price=lower_m.last_price + 1,
                    quantity=self._quantity,
                    confidence=min(edge / 15.0, 1.0),
                    reason=(
                        f"monotonicity arb counterpart: buy {lower_m.ticker}@{lower_m.last_price}c"
                    ),
                    strategy_name=self.name,
                ))
        return signals

    def _check_overround(self, markets: list[Market]) -> list[Signal]:
        """If mutually exclusive outcomes sum to != 100, there may be an arb."""
        total = sum(m.last_price for m in markets if m.last_price > 0)
        if total <= 0:
            return []

        signals: list[Signal] = []

        if total > 100 + self._min_edge:
            overround = total - 100
            most_overpriced = max(markets, key=lambda m: m.last_price)
            signals.append(Signal(
                market_ticker=most_overpriced.ticker,
                action=Action.SELL,
                side=Side.YES,
                price=most_overpriced.last_price - 1,
                quantity=self._quantity,
                confidence=min(overround / 20.0, 1.0),
                reason=f"overround={overround}c (sum={total}c), sell most expensive",
                strategy_name=self.name,
            ))
        elif total < 100 - self._min_edge:
            underround = 100 - total
            cheapest = min(markets, key=lambda m: m.last_price if m.last_price > 0 else 999)
            signals.append(Signal(
                market_ticker=cheapest.ticker,
                action=Action.BUY,
                side=Side.YES,
                price=cheapest.last_price + 1,
                quantity=self._quantity,
                confidence=min(underround / 20.0, 1.0),
                reason=f"underround={underround}c (sum={total}c), buy cheapest",
                strategy_name=self.name,
            ))

        return signals
