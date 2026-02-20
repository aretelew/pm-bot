"""Strategy engine: orchestrates market scanning, strategy evaluation, risk checks, and order execution."""

from __future__ import annotations

import httpx

from pm_bot.api.client import KalshiClient
from pm_bot.api.models import Market, OrderType
from pm_bot.data.store import DataStore
from pm_bot.engine.order_manager import OrderManager
from pm_bot.engine.risk import RiskManager
from pm_bot.strategies.base import Signal, Strategy
from pm_bot.utils.logging import get_logger

log = get_logger("engine.strategy_engine")


class StrategyEngine:
    """Feeds market data to strategies, filters signals through risk, and executes orders."""

    def __init__(
        self,
        client: KalshiClient,
        order_manager: OrderManager,
        risk_manager: RiskManager,
        store: DataStore,
        strategies: list[Strategy],
    ) -> None:
        self._client = client
        self._order_manager = order_manager
        self._risk = risk_manager
        self._store = store
        self._strategies = strategies

    async def evaluate_market(self, market: Market) -> list[Signal]:
        """Run all applicable strategies on a single market and execute approved signals."""
        # Skip markets no strategy wants to trade (saves API calls, avoids 404s)
        if not any(s.should_trade(market) for s in self._strategies):
            return []

        try:
            ob_resp = await self._client.get_orderbook(market.ticker)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                log.debug("orderbook_not_available", ticker=market.ticker)
            else:
                log.warning("orderbook_fetch_failed", ticker=market.ticker, status=e.response.status_code)
            return []
        except Exception:
            log.exception("orderbook_fetch_failed", ticker=market.ticker)
            return []

        orderbook = ob_resp.orderbook
        await self._store.save_orderbook(market.ticker, orderbook)

        all_signals: list[Signal] = []

        for strategy in self._strategies:
            if not strategy.should_trade(market):
                continue
            try:
                signals = await strategy.on_market_update(market, orderbook)
                all_signals.extend(signals)
            except Exception:
                log.exception("strategy_error", strategy=strategy.name, ticker=market.ticker)

        for signal in all_signals:
            await self._process_signal(signal)

        return all_signals

    async def _process_signal(self, signal: Signal) -> None:
        allowed, reason = self._risk.validate_order(
            ticker=signal.market_ticker,
            quantity=signal.quantity,
            action=signal.action.value,
        )

        await self._store.log_signal(
            strategy=signal.strategy_name,
            ticker=signal.market_ticker,
            side=signal.side.value,
            price=signal.price,
            quantity=signal.quantity,
            confidence=signal.confidence,
            reason=signal.reason,
            executed=allowed,
        )

        if not allowed:
            log.info(
                "signal_rejected",
                ticker=signal.market_ticker,
                strategy=signal.strategy_name,
                risk_reason=reason,
            )
            return

        await self._order_manager.place_order(
            ticker=signal.market_ticker,
            action=signal.action,
            side=signal.side,
            count=signal.quantity,
            order_type=OrderType.LIMIT,
            yes_price=signal.price if signal.side.value == "yes" else None,
            no_price=signal.price if signal.side.value == "no" else None,
            strategy=signal.strategy_name,
            reason=signal.reason,
        )

    async def evaluate_markets(self, markets: list[Market]) -> list[Signal]:
        all_signals: list[Signal] = []
        for market in markets:
            signals = await self.evaluate_market(market)
            all_signals.extend(signals)
        return all_signals
