"""Order manager: place, cancel, and track orders through their lifecycle."""

from __future__ import annotations

import uuid
from typing import Any

from pm_bot.api.client import KalshiClient
from pm_bot.api.models import (
    Action,
    Order,
    OrderRequest,
    OrderType,
    Side,
)
from pm_bot.data.store import DataStore
from pm_bot.utils.logging import get_logger

log = get_logger("engine.order_manager")


class OrderManager:
    """Manages order placement, cancellation, and lifecycle tracking."""

    def __init__(self, client: KalshiClient, store: DataStore) -> None:
        self._client = client
        self._store = store
        self._active_orders: dict[str, Order] = {}

    @property
    def active_orders(self) -> dict[str, Order]:
        return dict(self._active_orders)

    async def place_order(
        self,
        *,
        ticker: str,
        action: Action,
        side: Side,
        count: int,
        order_type: OrderType = OrderType.LIMIT,
        yes_price: int | None = None,
        no_price: int | None = None,
        strategy: str = "",
        reason: str = "",
    ) -> Order | None:
        client_order_id = str(uuid.uuid4())
        req = OrderRequest(
            ticker=ticker,
            action=action,
            side=side,
            count=count,
            type=order_type,
            yes_price=yes_price,
            no_price=no_price,
            client_order_id=client_order_id,
        )
        try:
            resp = await self._client.create_order(req)
            order = resp.order
            self._active_orders[order.order_id] = order

            await self._store.log_order(
                order_id=order.order_id,
                client_order_id=client_order_id,
                ticker=ticker,
                action=action.value,
                side=side.value,
                order_type=order_type.value,
                yes_price=yes_price or 0,
                no_price=no_price or 0,
                count=count,
                remaining_count=order.remaining_count,
                status=order.status.value,
                strategy=strategy,
                reason=reason,
            )
            log.info(
                "order_placed",
                order_id=order.order_id,
                ticker=ticker,
                action=action.value,
                side=side.value,
                price=yes_price or no_price,
                count=count,
                strategy=strategy,
            )
            return order
        except Exception:
            log.exception("order_placement_failed", ticker=ticker)
            return None

    async def cancel_order(self, order_id: str) -> bool:
        try:
            await self._client.cancel_order(order_id)
            self._active_orders.pop(order_id, None)

            await self._store.log_order(
                order_id=order_id,
                client_order_id="",
                ticker="",
                action="",
                side="",
                order_type="",
                status="canceled",
            )
            log.info("order_canceled", order_id=order_id)
            return True
        except Exception:
            log.exception("order_cancel_failed", order_id=order_id)
            return False

    async def cancel_all(self, ticker: str = "") -> int:
        cancelled = 0
        orders_resp = await self._client.get_orders(status="resting", ticker=ticker)
        for order in orders_resp.orders:
            if await self.cancel_order(order.order_id):
                cancelled += 1
        log.info("cancel_all_complete", ticker=ticker or "all", cancelled=cancelled)
        return cancelled

    async def sync_orders(self) -> None:
        """Refresh active orders from the API."""
        resp = await self._client.get_orders(status="resting")
        self._active_orders = {o.order_id: o for o in resp.orders}
        log.info("orders_synced", count=len(self._active_orders))

    async def get_fills_for_order(self, order_id: str) -> list[dict[str, Any]]:
        """Get fill records for a specific order (for reconciliation)."""
        resp = await self._client.get_fills()
        return [f.model_dump() for f in resp.fills if f.order_id == order_id]
