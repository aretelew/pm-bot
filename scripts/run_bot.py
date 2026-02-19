"""Script to run the trading bot directly (alternative to CLI)."""

import asyncio
import sys

from pm_bot.api.client import KalshiClient
from pm_bot.config import get_settings
from pm_bot.data.store import DataStore
from pm_bot.engine.bot import Bot
from pm_bot.utils.logging import setup_logging


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    client = KalshiClient(settings)
    store = DataStore(settings.db_url)
    await store.init_db()

    strategy_names = sys.argv[1:] or ["naive_value"]
    bot = Bot(client=client, store=store, settings=settings, strategy_names=strategy_names)

    try:
        await bot.run()
    except KeyboardInterrupt:
        pass
    finally:
        await bot.shutdown()
        await client.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
