"""Market scanner: discovers and filters open markets from Kalshi."""

from __future__ import annotations

import asyncio
from typing import Callable

from pm_bot.api.client import KalshiClient
from pm_bot.api.models import Market, MarketsResponse
from pm_bot.data.store import DataStore
from pm_bot.utils.logging import get_logger

log = get_logger("engine.scanner")

MarketFilter = Callable[[Market], bool]


def min_volume_filter(min_vol: int) -> MarketFilter:
    def _filter(m: Market) -> bool:
        return m.volume >= min_vol
    return _filter


def has_liquidity_filter(m: Market) -> bool:
    return m.yes_bid > 0 and m.yes_ask > 0


class MarketScanner:
    """Periodically polls the Kalshi REST API for open markets and persists them."""

    def __init__(
        self,
        client: KalshiClient,
        store: DataStore,
        poll_interval: int = 60,
        filters: list[MarketFilter] | None = None,
    ) -> None:
        self._client = client
        self._store = store
        self._poll_interval = poll_interval
        self._filters = filters or []
        self._running = False
        self._markets: dict[str, Market] = {}

    @property
    def markets(self) -> dict[str, Market]:
        return dict(self._markets)

    def add_filter(self, f: MarketFilter) -> None:
        self._filters.append(f)

    async def scan_once(self) -> list[Market]:
        all_markets: list[Market] = []
        cursor = ""
        while True:
            resp: MarketsResponse = await self._client.get_markets(
                limit=100, cursor=cursor, status="open"
            )
            all_markets.extend(resp.markets)
            if not resp.cursor:
                break
            cursor = resp.cursor

        filtered = all_markets
        for f in self._filters:
            filtered = [m for m in filtered if f(m)]

        self._markets = {m.ticker: m for m in filtered}
        await self._store.save_markets(filtered)
        log.info("scan_complete", total=len(all_markets), filtered=len(filtered))
        return filtered

    async def run(self) -> None:
        self._running = True
        log.info("scanner_started", interval=self._poll_interval)
        while self._running:
            try:
                await self.scan_once()
            except Exception:
                log.exception("scan_error")
            await asyncio.sleep(self._poll_interval)

    def stop(self) -> None:
        self._running = False
