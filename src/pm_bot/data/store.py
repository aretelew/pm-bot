"""Database access layer."""

from __future__ import annotations

import json

from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from pm_bot.api.models import Fill, Market, OrderBook
from pm_bot.data.models import (
    MarketRecord,
    OrderBookSnapshot,
    OrderRecord,
    PriceRecord,
    StrategySignalRecord,
    TradeRecord,
)
from pm_bot.utils.logging import get_logger

log = get_logger("data.store")


class DataStore:
    def __init__(self, db_url: str = "sqlite+aiosqlite:///pm_bot.db") -> None:
        self._engine: AsyncEngine = create_async_engine(db_url, echo=False)

    async def init_db(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
        log.info("database_initialized")

    async def _session(self) -> AsyncSession:
        return AsyncSession(self._engine)

    # --- Markets ---

    async def save_market(self, market: Market) -> None:
        async with AsyncSession(self._engine) as session:
            record = MarketRecord(
                ticker=market.ticker,
                title=market.title,
                status=market.status.value,
                event_ticker=market.event_ticker,
                category=market.category,
                yes_bid=market.yes_bid,
                yes_ask=market.yes_ask,
                no_bid=market.no_bid,
                no_ask=market.no_ask,
                last_price=market.last_price,
                volume=market.volume,
                open_interest=market.open_interest,
                close_time=market.close_time,
            )
            session.add(record)
            await session.commit()

    async def save_markets(self, markets: list[Market]) -> None:
        async with AsyncSession(self._engine) as session:
            for m in markets:
                record = MarketRecord(
                    ticker=m.ticker,
                    title=m.title,
                    status=m.status.value,
                    event_ticker=m.event_ticker,
                    category=m.category,
                    yes_bid=m.yes_bid,
                    yes_ask=m.yes_ask,
                    no_bid=m.no_bid,
                    no_ask=m.no_ask,
                    last_price=m.last_price,
                    volume=m.volume,
                    open_interest=m.open_interest,
                    close_time=m.close_time,
                )
                session.add(record)
            await session.commit()

    async def get_latest_markets(self, limit: int = 50) -> list[MarketRecord]:
        async with AsyncSession(self._engine) as session:
            stmt = (
                select(MarketRecord)
                .order_by(MarketRecord.fetched_at.desc())
                .limit(limit)
            )
            results = await session.exec(stmt)
            return list(results.all())

    # --- Orderbook ---

    async def save_orderbook(self, ticker: str, ob: OrderBook) -> None:
        async with AsyncSession(self._engine) as session:
            record = OrderBookSnapshot(
                ticker=ticker,
                best_yes_bid=ob.best_yes_bid,
                best_yes_ask=ob.best_yes_ask,
                mid_price=ob.mid_price,
                spread=ob.spread,
                yes_levels_json=json.dumps(
                    [{"price": lv.price, "quantity": lv.quantity} for lv in ob.yes]
                ),
                no_levels_json=json.dumps(
                    [{"price": lv.price, "quantity": lv.quantity} for lv in ob.no]
                ),
            )
            session.add(record)
            await session.commit()

    # --- Prices ---

    async def save_price(self, ticker: str, yes_price: int, volume: int = 0, source: str = "ticker") -> None:
        async with AsyncSession(self._engine) as session:
            record = PriceRecord(
                ticker=ticker,
                yes_price=yes_price,
                volume=volume,
                source=source,
            )
            session.add(record)
            await session.commit()

    async def get_price_history(self, ticker: str, limit: int = 100) -> list[PriceRecord]:
        async with AsyncSession(self._engine) as session:
            stmt = (
                select(PriceRecord)
                .where(PriceRecord.ticker == ticker)
                .order_by(PriceRecord.captured_at.desc())
                .limit(limit)
            )
            results = await session.exec(stmt)
            return list(results.all())

    # --- Trades ---

    async def save_trade(self, fill: Fill, client_order_id: str = "") -> None:
        async with AsyncSession(self._engine) as session:
            record = TradeRecord(
                trade_id=fill.trade_id,
                ticker=fill.ticker,
                action=fill.action.value,
                side=fill.side.value,
                count=fill.count,
                yes_price=fill.yes_price,
                no_price=fill.no_price,
                order_id=fill.order_id,
                client_order_id=client_order_id,
                created_time=fill.created_time,
            )
            session.add(record)
            await session.commit()

    # --- Order log ---

    async def log_order(
        self,
        *,
        order_id: str,
        client_order_id: str,
        ticker: str,
        action: str,
        side: str,
        order_type: str,
        yes_price: int = 0,
        no_price: int = 0,
        count: int = 0,
        remaining_count: int = 0,
        status: str = "",
        strategy: str = "",
        reason: str = "",
    ) -> None:
        async with AsyncSession(self._engine) as session:
            record = OrderRecord(
                order_id=order_id,
                client_order_id=client_order_id,
                ticker=ticker,
                action=action,
                side=side,
                order_type=order_type,
                yes_price=yes_price,
                no_price=no_price,
                count=count,
                remaining_count=remaining_count,
                status=status,
                strategy=strategy,
                reason=reason,
            )
            session.add(record)
            await session.commit()

    # --- Strategy signals ---

    async def log_signal(
        self,
        *,
        strategy: str,
        ticker: str,
        side: str,
        price: int,
        quantity: int,
        confidence: float,
        reason: str,
        executed: bool = False,
    ) -> None:
        async with AsyncSession(self._engine) as session:
            record = StrategySignalRecord(
                strategy=strategy,
                ticker=ticker,
                side=side,
                price=price,
                quantity=quantity,
                confidence=confidence,
                reason=reason,
                executed=executed,
            )
            session.add(record)
            await session.commit()

    async def close(self) -> None:
        await self._engine.dispose()
