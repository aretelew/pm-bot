"""Backtesting engine: replay historical data through strategies and simulate fills."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pm_bot.api.models import Market, MarketStatus, OrderBook, OrderBookLevel
from pm_bot.data.models import MarketRecord, OrderBookSnapshot
from pm_bot.strategies.base import Strategy
from pm_bot.utils.logging import get_logger

log = get_logger("backtest.engine")


@dataclass
class SimulatedFill:
    timestamp: datetime
    ticker: str
    action: str
    side: str
    price: int
    quantity: int
    strategy: str
    reason: str
    pnl: float = 0.0


@dataclass
class BacktestPosition:
    ticker: str
    quantity: int = 0
    avg_cost: float = 0.0
    realized_pnl: float = 0.0

    def apply_fill(self, action: str, price: int, quantity: int) -> float:
        """Apply a fill and return the realized PnL (0 if opening)."""
        price_dollars = price / 100.0

        if action == "buy":
            new_qty = self.quantity + quantity
            if self.quantity >= 0:
                total_cost = self.avg_cost * self.quantity + price_dollars * quantity
                self.avg_cost = total_cost / new_qty if new_qty > 0 else 0
                self.quantity = new_qty
                return 0.0
            else:
                closed = min(quantity, abs(self.quantity))
                pnl = closed * (self.avg_cost - price_dollars)
                self.realized_pnl += pnl
                self.quantity = new_qty
                if self.quantity > 0:
                    self.avg_cost = price_dollars
                return pnl
        else:
            new_qty = self.quantity - quantity
            if self.quantity <= 0:
                total_cost = abs(self.avg_cost * self.quantity) + price_dollars * quantity
                self.avg_cost = total_cost / abs(new_qty) if new_qty != 0 else 0
                self.quantity = new_qty
                return 0.0
            else:
                closed = min(quantity, self.quantity)
                pnl = closed * (price_dollars - self.avg_cost)
                self.realized_pnl += pnl
                self.quantity = new_qty
                if self.quantity < 0:
                    self.avg_cost = price_dollars
                return pnl


@dataclass
class BacktestResult:
    fills: list[SimulatedFill] = field(default_factory=list)
    positions: dict[str, BacktestPosition] = field(default_factory=dict)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    starting_balance: float = 10_000.0
    ending_balance: float = 10_000.0

    @property
    def total_trades(self) -> int:
        return len(self.fills)

    @property
    def total_realized_pnl(self) -> float:
        return sum(p.realized_pnl for p in self.positions.values())

    @property
    def winning_trades(self) -> int:
        return sum(1 for f in self.fills if f.pnl > 0)

    @property
    def losing_trades(self) -> int:
        return sum(1 for f in self.fills if f.pnl < 0)

    @property
    def win_rate(self) -> float:
        closing = [f for f in self.fills if f.pnl != 0]
        if not closing:
            return 0.0
        return sum(1 for f in closing if f.pnl > 0) / len(closing)

    @property
    def max_drawdown(self) -> float:
        if not self.equity_curve:
            return 0.0
        peak = self.equity_curve[0][1]
        max_dd = 0.0
        for _, equity in self.equity_curve:
            peak = max(peak, equity)
            dd = peak - equity
            max_dd = max(max_dd, dd)
        return max_dd

    @property
    def total_return_pct(self) -> float:
        if self.starting_balance <= 0:
            return 0.0
        return ((self.ending_balance - self.starting_balance) / self.starting_balance) * 100


def _snapshot_to_orderbook(snap: OrderBookSnapshot) -> OrderBook:
    yes_levels = [OrderBookLevel(**lv) for lv in json.loads(snap.yes_levels_json)]
    no_levels = [OrderBookLevel(**lv) for lv in json.loads(snap.no_levels_json)]
    return OrderBook(yes=yes_levels, no=no_levels)


def _record_to_market(rec: MarketRecord) -> Market:
    return Market(
        ticker=rec.ticker,
        title=rec.title,
        status=MarketStatus(rec.status) if rec.status in ("open", "closed", "settled") else MarketStatus.OPEN,
        event_ticker=rec.event_ticker,
        category=rec.category,
        yes_bid=rec.yes_bid,
        yes_ask=rec.yes_ask,
        no_bid=rec.no_bid,
        no_ask=rec.no_ask,
        last_price=rec.last_price,
        volume=rec.volume,
        open_interest=rec.open_interest,
    )


class BacktestEngine:
    """Replays historical market data through strategies and simulates order fills."""

    def __init__(
        self,
        strategies: list[Strategy],
        starting_balance: float = 10_000.0,
        slippage_cents: int = 1,
    ) -> None:
        self._strategies = strategies
        self._starting_balance = starting_balance
        self._slippage = slippage_cents

    async def run(
        self,
        market_records: list[MarketRecord],
        orderbook_snapshots: list[OrderBookSnapshot],
    ) -> BacktestResult:
        result = BacktestResult(starting_balance=self._starting_balance)
        balance = self._starting_balance

        ob_by_ticker: dict[str, list[OrderBookSnapshot]] = {}
        for snap in orderbook_snapshots:
            ob_by_ticker.setdefault(snap.ticker, []).append(snap)

        sorted_records = sorted(market_records, key=lambda r: r.fetched_at or datetime.min)

        for record in sorted_records:
            market = _record_to_market(record)
            snaps = ob_by_ticker.get(record.ticker, [])
            closest_snap = self._find_closest_snapshot(snaps, record.fetched_at)

            if closest_snap is None:
                orderbook = OrderBook()
            else:
                orderbook = _snapshot_to_orderbook(closest_snap)

            for strategy in self._strategies:
                if not strategy.should_trade(market):
                    continue

                try:
                    signals = await strategy.on_market_update(market, orderbook)
                except Exception:
                    log.exception("backtest_strategy_error", strategy=strategy.name)
                    continue

                for signal in signals:
                    fill_price = signal.price
                    if signal.action.value == "buy":
                        fill_price += self._slippage
                    else:
                        fill_price -= self._slippage
                    fill_price = max(1, min(99, fill_price))

                    cost = fill_price / 100.0 * signal.quantity
                    if signal.action.value == "buy" and balance < cost:
                        continue

                    pos = result.positions.setdefault(
                        signal.market_ticker,
                        BacktestPosition(ticker=signal.market_ticker),
                    )
                    pnl = pos.apply_fill(signal.action.value, fill_price, signal.quantity)

                    if signal.action.value == "buy":
                        balance -= cost
                    else:
                        balance += fill_price / 100.0 * signal.quantity

                    balance += pnl

                    fill = SimulatedFill(
                        timestamp=record.fetched_at or datetime.now(timezone.utc),
                        ticker=signal.market_ticker,
                        action=signal.action.value,
                        side=signal.side.value,
                        price=fill_price,
                        quantity=signal.quantity,
                        strategy=signal.strategy_name,
                        reason=signal.reason,
                        pnl=pnl,
                    )
                    result.fills.append(fill)

            result.equity_curve.append(
                (record.fetched_at or datetime.now(timezone.utc), balance)
            )

        result.ending_balance = balance
        log.info(
            "backtest_complete",
            trades=result.total_trades,
            pnl=f"{result.total_realized_pnl:.2f}",
            return_pct=f"{result.total_return_pct:.2f}%",
            max_drawdown=f"{result.max_drawdown:.2f}",
        )
        return result

    @staticmethod
    def _find_closest_snapshot(
        snapshots: list[OrderBookSnapshot],
        target_time: datetime | None,
    ) -> OrderBookSnapshot | None:
        if not snapshots:
            return None
        if target_time is None:
            return snapshots[0]
        best = snapshots[0]
        best_diff = abs((best.captured_at - target_time).total_seconds()) if best.captured_at else float("inf")
        for snap in snapshots[1:]:
            if snap.captured_at is None:
                continue
            diff = abs((snap.captured_at - target_time).total_seconds())
            if diff < best_diff:
                best = snap
                best_diff = diff
        return best
