"""Risk manager: enforces position limits, exposure caps, and a daily loss kill switch."""

from __future__ import annotations

from dataclasses import dataclass

from pm_bot.engine.portfolio import PortfolioTracker
from pm_bot.utils.logging import get_logger

log = get_logger("engine.risk")


@dataclass
class RiskLimits:
    max_position_per_market: int = 100
    max_total_exposure: int = 1000
    max_daily_loss: float = 500.0


class RiskManager:
    """Validates proposed trades against risk limits."""

    def __init__(self, portfolio: PortfolioTracker, limits: RiskLimits) -> None:
        self._portfolio = portfolio
        self._limits = limits
        self._kill_switch_active = False

    @property
    def kill_switch_active(self) -> bool:
        return self._kill_switch_active

    def check_kill_switch(self) -> bool:
        daily_pnl = self._portfolio.daily_pnl
        if daily_pnl < -self._limits.max_daily_loss:
            self._kill_switch_active = True
            log.warning(
                "kill_switch_triggered",
                daily_pnl=daily_pnl,
                limit=-self._limits.max_daily_loss,
            )
            return True
        return False

    def reset_kill_switch(self) -> None:
        self._kill_switch_active = False
        log.info("kill_switch_reset")

    def validate_order(
        self,
        *,
        ticker: str,
        quantity: int,
        action: str,
    ) -> tuple[bool, str]:
        """Return (allowed, reason). If not allowed, reason explains why."""
        if self._kill_switch_active:
            return False, "kill switch is active"

        if self.check_kill_switch():
            return False, "daily loss limit exceeded"

        current_qty = self._portfolio.position_quantity(ticker)
        total_qty = self._portfolio.snapshot.total_quantity

        if action == "buy":
            new_position = current_qty + quantity
            new_total = total_qty + quantity
        else:
            new_position = current_qty - quantity
            new_total = total_qty - quantity

        if abs(new_position) > self._limits.max_position_per_market:
            return False, (
                f"position limit: {abs(new_position)} > {self._limits.max_position_per_market}"
            )

        if new_total > self._limits.max_total_exposure:
            return False, (
                f"total exposure limit: {new_total} > {self._limits.max_total_exposure}"
            )

        return True, "ok"
