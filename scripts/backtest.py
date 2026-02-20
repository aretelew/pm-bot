"""Script to run a backtest against stored historical data."""

import asyncio
import sys

from pm_bot.backtest.engine import BacktestEngine
from pm_bot.backtest.report import generate_html_report, print_report
from pm_bot.config import get_settings
from pm_bot.data.store import DataStore
from pm_bot.strategies.naive_value import NaiveValueStrategy
from pm_bot.utils.logging import setup_logging


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    store = DataStore(settings.db_url)
    market_records = await store.get_latest_markets(limit=5000)
    if not market_records:
        print("No market data in DB. Run the scanner first to collect data.")
        await store.close()
        return

    from sqlmodel import select
    from sqlmodel.ext.asyncio.session import AsyncSession
    from pm_bot.data.models import OrderBookSnapshot

    async with AsyncSession(store._engine) as session:
        result = await session.exec(select(OrderBookSnapshot).limit(10000))
        snapshots = list(result.all())

    strategies = [NaiveValueStrategy(threshold_cents=2, quantity=1)]
    engine = BacktestEngine(strategies=strategies, starting_balance=1000.0)
    result = await engine.run(market_records, snapshots)

    print_report(result)

    output = "backtest_report.html"
    if "--html" in sys.argv:
        generate_html_report(result, output)

    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
