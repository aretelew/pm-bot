"""Bot: top-level orchestrator that runs the scan-evaluate-trade loop."""

from __future__ import annotations

import asyncio
import signal
import sys

from pm_bot.api.client import KalshiClient
from pm_bot.config import Settings
from pm_bot.data.store import DataStore
from pm_bot.engine.order_manager import OrderManager
from pm_bot.engine.portfolio import PortfolioTracker
from pm_bot.engine.risk import RiskLimits, RiskManager
from pm_bot.engine.scanner import MarketScanner
from pm_bot.engine.strategy_engine import StrategyEngine
from pm_bot.strategies.base import Strategy
from pm_bot.utils.alerts import AlertManager
from pm_bot.utils.logging import get_logger

log = get_logger("engine.bot")

STRATEGY_REGISTRY: dict[str, type[Strategy]] = {}


def _load_strategies() -> None:
    from pm_bot.strategies.naive_value import NaiveValueStrategy
    from pm_bot.strategies.market_maker import MarketMakerStrategy
    from pm_bot.strategies.arbitrage import CrossMarketArbStrategy
    from pm_bot.strategies.signal import SignalBasedStrategy

    STRATEGY_REGISTRY["naive_value"] = NaiveValueStrategy
    STRATEGY_REGISTRY["market_maker"] = MarketMakerStrategy
    STRATEGY_REGISTRY["arbitrage"] = CrossMarketArbStrategy
    STRATEGY_REGISTRY["signal_based"] = SignalBasedStrategy


class Bot:
    """Main trading bot that coordinates all subsystems."""

    def __init__(
        self,
        client: KalshiClient,
        store: DataStore,
        settings: Settings,
        strategy_names: list[str] | None = None,
        alert_manager: AlertManager | None = None,
    ) -> None:
        self._client = client
        self._store = store
        self._settings = settings
        self._alerts = alert_manager or AlertManager()

        self._portfolio = PortfolioTracker(client)
        self._order_manager = OrderManager(client, store)

        limits = RiskLimits(
            max_position_per_market=settings.max_position_per_market,
            max_total_exposure=settings.max_total_exposure,
            max_daily_loss=settings.max_daily_loss,
        )
        self._risk = RiskManager(self._portfolio, limits)

        _load_strategies()
        names = strategy_names or ["naive_value"]
        strategies: list[Strategy] = []
        for name in names:
            cls = STRATEGY_REGISTRY.get(name)
            if cls is None:
                log.warning("unknown_strategy", name=name, available=list(STRATEGY_REGISTRY))
                continue
            strategies.append(cls())
        self._strategies = strategies

        self._scanner = MarketScanner(
            client, store, poll_interval=settings.scanner_poll_interval_seconds
        )
        self._engine = StrategyEngine(
            client=client,
            order_manager=self._order_manager,
            risk_manager=self._risk,
            store=store,
            strategies=self._strategies,
        )
        self._running = False
        self._shutdown_event = asyncio.Event()

    def _install_signal_handlers(self) -> None:
        """Register OS signal handlers for graceful shutdown (Unix only)."""
        loop = asyncio.get_running_loop()
        if sys.platform != "win32":
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))
        # On Windows, KeyboardInterrupt (Ctrl+C) is handled in the CLI runner.

    async def run(self) -> None:
        self._running = True
        self._install_signal_handlers()

        log.info(
            "bot_started",
            env=self._settings.kalshi_env.value,
            strategies=[s.name for s in self._strategies],
        )
        await self._alerts.info(
            f"Bot started ({self._settings.kalshi_env.value}) "
            f"with strategies: {[s.name for s in self._strategies]}"
        )

        await self._portfolio.sync()

        while self._running:
            try:
                self._risk.check_kill_switch()
                if self._risk.kill_switch_active:
                    log.warning("bot_paused_kill_switch")
                    await self._alerts.critical(
                        f"Kill switch active! Daily PnL: ${self._portfolio.daily_pnl:.2f}"
                    )
                    cancelled = await self._order_manager.cancel_all()
                    log.info("emergency_cancel", orders_cancelled=cancelled)
                    await asyncio.sleep(60)
                    continue

                markets = await self._scanner.scan_once()
                await self._portfolio.sync()
                signals = await self._engine.evaluate_markets(markets)

                log.info(
                    "cycle_complete",
                    markets_scanned=len(markets),
                    signals_generated=len(signals),
                    balance=self._portfolio.snapshot.balance_dollars,
                    daily_pnl=self._portfolio.daily_pnl,
                )
            except Exception:
                log.exception("bot_cycle_error")
                await self._alerts.warning("Bot cycle error -- check logs.")

            await asyncio.sleep(self._settings.scanner_poll_interval_seconds)

    async def shutdown(self) -> None:
        if not self._running:
            return
        self._running = False
        self._scanner.stop()
        log.info("bot_shutting_down")

        cancelled = await self._order_manager.cancel_all()
        log.info("bot_shutdown", orders_cancelled=cancelled)
        await self._alerts.warning(
            f"Bot shut down. Cancelled {cancelled} open orders."
        )
        self._shutdown_event.set()
