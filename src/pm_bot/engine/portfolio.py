"""Portfolio tracker: syncs positions from Kalshi and computes PnL."""

from __future__ import annotations

from dataclasses import dataclass, field

from pm_bot.api.client import KalshiClient
from pm_bot.api.models import Position
from pm_bot.utils.logging import get_logger

log = get_logger("engine.portfolio")

CENTICENTS_PER_DOLLAR = 10_000


@dataclass
class PortfolioSnapshot:
    balance_cents: int = 0
    positions: list[Position] = field(default_factory=list)

    @property
    def balance_dollars(self) -> float:
        return self.balance_cents / 100

    @property
    def total_cost_dollars(self) -> float:
        return sum(p.position_cost for p in self.positions) / CENTICENTS_PER_DOLLAR

    @property
    def total_realized_pnl_dollars(self) -> float:
        return sum(p.realized_pnl for p in self.positions) / CENTICENTS_PER_DOLLAR

    @property
    def total_fees_dollars(self) -> float:
        return sum(p.fees_paid for p in self.positions) / CENTICENTS_PER_DOLLAR

    @property
    def total_quantity(self) -> int:
        return sum(abs(p.quantity) for p in self.positions)

    @property
    def num_positions(self) -> int:
        return len([p for p in self.positions if p.quantity != 0])


class PortfolioTracker:
    """Tracks portfolio state by syncing with the Kalshi API."""

    def __init__(self, client: KalshiClient) -> None:
        self._client = client
        self._snapshot = PortfolioSnapshot()
        self._daily_pnl_start: float | None = None

    @property
    def snapshot(self) -> PortfolioSnapshot:
        return self._snapshot

    async def sync(self) -> PortfolioSnapshot:
        bal = await self._client.get_balance()
        pos_resp = await self._client.get_positions()

        self._snapshot = PortfolioSnapshot(
            balance_cents=bal.balance,
            positions=pos_resp.market_positions,
        )

        if self._daily_pnl_start is None:
            self._daily_pnl_start = self._snapshot.total_realized_pnl_dollars

        log.info(
            "portfolio_synced",
            balance=self._snapshot.balance_dollars,
            positions=self._snapshot.num_positions,
            total_qty=self._snapshot.total_quantity,
            realized_pnl=self._snapshot.total_realized_pnl_dollars,
        )
        return self._snapshot

    @property
    def daily_pnl(self) -> float:
        if self._daily_pnl_start is None:
            return 0.0
        return self._snapshot.total_realized_pnl_dollars - self._daily_pnl_start

    def reset_daily_pnl(self) -> None:
        self._daily_pnl_start = self._snapshot.total_realized_pnl_dollars

    def get_position(self, ticker: str) -> Position | None:
        for p in self._snapshot.positions:
            if p.market_ticker == ticker:
                return p
        return None

    def position_quantity(self, ticker: str) -> int:
        p = self.get_position(ticker)
        return p.quantity if p else 0
