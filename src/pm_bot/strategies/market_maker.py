"""Market making strategy: provide liquidity by quoting bid/ask around the mid-price.

Manages inventory risk by skewing quotes away from the side where we hold
too much exposure.
"""

from __future__ import annotations

from pm_bot.api.models import Action, Market, OrderBook, Side
from pm_bot.strategies.base import Signal, Strategy
from pm_bot.utils.logging import get_logger

log = get_logger("strategies.market_maker")


class MarketMakerStrategy(Strategy):
    name = "market_maker"

    def __init__(
        self,
        *,
        half_spread: int = 3,
        quantity: int = 1,
        min_spread: int = 4,
        max_inventory: int = 20,
        min_volume: int = 50,
        skew_per_contract: float = 0.5,
    ) -> None:
        self._half_spread = half_spread
        self._quantity = quantity
        self._min_spread = min_spread
        self._max_inventory = max_inventory
        self._min_volume = min_volume
        self._skew_per_contract = skew_per_contract
        self._inventory: dict[str, int] = {}

    def should_trade(self, market: Market) -> bool:
        return market.volume >= self._min_volume

    def update_inventory(self, ticker: str, delta: int) -> None:
        self._inventory[ticker] = self._inventory.get(ticker, 0) + delta

    async def on_market_update(
        self,
        market: Market,
        orderbook: OrderBook,
    ) -> list[Signal]:
        mid = orderbook.mid_price
        spread = orderbook.spread
        if mid is None or spread is None:
            return []

        if spread < self._min_spread:
            return []

        inventory = self._inventory.get(market.ticker, 0)

        if abs(inventory) >= self._max_inventory:
            log.info("inventory_limit", ticker=market.ticker, inventory=inventory)
            return []

        skew = int(inventory * self._skew_per_contract)
        bid_price = max(1, int(mid - self._half_spread - skew))
        ask_price = min(99, int(mid + self._half_spread - skew))

        if bid_price >= ask_price:
            return []

        signals: list[Signal] = []

        signals.append(Signal(
            market_ticker=market.ticker,
            action=Action.BUY,
            side=Side.YES,
            price=bid_price,
            quantity=self._quantity,
            confidence=0.5,
            reason=f"MM bid at {bid_price}c (mid={mid:.1f}, inv={inventory})",
            strategy_name=self.name,
        ))

        signals.append(Signal(
            market_ticker=market.ticker,
            action=Action.SELL,
            side=Side.YES,
            price=ask_price,
            quantity=self._quantity,
            confidence=0.5,
            reason=f"MM ask at {ask_price}c (mid={mid:.1f}, inv={inventory})",
            strategy_name=self.name,
        ))

        return signals
