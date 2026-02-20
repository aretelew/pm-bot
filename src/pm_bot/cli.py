"""CLI entry point for the pm-bot."""

from __future__ import annotations

import asyncio

import click
from rich.console import Console
from rich.table import Table

from pm_bot.config import get_settings
from pm_bot.utils.logging import setup_logging

console = Console()


@click.group()
def cli() -> None:
    """Kalshi prediction market trading bot."""
    settings = get_settings()
    setup_logging(settings.log_level)


@cli.command()
def check() -> None:
    """Verify API connectivity by fetching open markets."""
    asyncio.run(_check())


async def _check() -> None:
    from pm_bot.api.client import KalshiClient

    settings = get_settings()
    client = KalshiClient(settings)
    try:
        resp = await client.get_markets(limit=10)
        table = Table(title=f"Open Markets ({settings.kalshi_env.value})")
        table.add_column("Ticker", style="cyan")
        table.add_column("Title", style="white")
        table.add_column("Yes Bid", justify="right")
        table.add_column("Yes Ask", justify="right")
        table.add_column("Volume", justify="right", style="green")
        for m in resp.markets:
            table.add_row(m.ticker, m.title[:60], str(m.yes_bid), str(m.yes_ask), str(m.volume))
        console.print(table)
        console.print(f"\n[green]Connected to {settings.base_url}[/green]")
    except Exception as e:
        console.print(f"[red]Connection failed: {e}[/red]")
    finally:
        await client.close()


@cli.command()
@click.argument("ticker")
def orderbook(ticker: str) -> None:
    """Show orderbook for a specific market ticker."""
    asyncio.run(_orderbook(ticker))


async def _orderbook(ticker: str) -> None:
    from pm_bot.api.client import KalshiClient

    settings = get_settings()
    client = KalshiClient(settings)
    try:
        resp = await client.get_orderbook(ticker)
        ob = resp.orderbook
        table = Table(title=f"Orderbook: {ticker}")
        table.add_column("YES Bids (price x qty)", style="green")
        table.add_column("NO Bids (price x qty)", style="red")
        max_rows = max(len(ob.yes), len(ob.no))
        for i in range(max_rows):
            yes_str = f"{ob.yes[i].price}c x {ob.yes[i].quantity}" if i < len(ob.yes) else ""
            no_str = f"{ob.no[i].price}c x {ob.no[i].quantity}" if i < len(ob.no) else ""
            table.add_row(yes_str, no_str)
        console.print(table)
        console.print(f"Mid: {ob.mid_price}  Spread: {ob.spread}")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        await client.close()


@cli.command()
def balance() -> None:
    """Show portfolio balance."""
    asyncio.run(_balance())


async def _balance() -> None:
    from pm_bot.api.client import KalshiClient

    settings = get_settings()
    client = KalshiClient(settings)
    try:
        bal = await client.get_balance()
        console.print(f"Balance: [green]${bal.balance_dollars:.2f}[/green]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        await client.close()


@cli.command()
def positions() -> None:
    """Show current portfolio positions."""
    asyncio.run(_positions())


async def _positions() -> None:
    from pm_bot.api.client import KalshiClient

    settings = get_settings()
    client = KalshiClient(settings)
    try:
        resp = await client.get_positions()
        if not resp.market_positions:
            console.print("[yellow]No open positions.[/yellow]")
            return
        table = Table(title="Positions")
        table.add_column("Ticker", style="cyan")
        table.add_column("Qty", justify="right")
        table.add_column("Cost ($)", justify="right")
        table.add_column("Realized PnL ($)", justify="right")
        table.add_column("Fees ($)", justify="right")
        for p in resp.market_positions:
            table.add_row(
                p.market_ticker,
                str(p.quantity),
                f"{p.position_cost_dollars:.2f}",
                f"{p.realized_pnl_dollars:.2f}",
                f"{p.fees_paid_dollars:.2f}",
            )
        console.print(table)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    finally:
        await client.close()


@cli.command()
def init_db() -> None:
    """Initialize the database tables."""
    asyncio.run(_init_db())


async def _init_db() -> None:
    from pm_bot.data.store import DataStore

    settings = get_settings()
    store = DataStore(settings.db_url)
    await store.init_db()
    console.print("[green]Database initialized.[/green]")
    await store.close()


# --- Phase 1: Data pipeline commands ---


@cli.command()
@click.option("--interval", default=15, help="Poll interval in seconds.")
def scan(interval: int) -> None:
    """Run the market scanner continuously."""
    asyncio.run(_scan(interval))


async def _scan(interval: int) -> None:
    from pm_bot.api.client import KalshiClient
    from pm_bot.data.store import DataStore
    from pm_bot.engine.scanner import MarketScanner

    settings = get_settings()
    client = KalshiClient(settings)
    store = DataStore(settings.db_url)
    await store.init_db()
    scanner = MarketScanner(client, store, poll_interval=interval)
    try:
        console.print(f"[cyan]Scanner started (poll every {interval}s)...[/cyan]")
        await scanner.run()
    except KeyboardInterrupt:
        scanner.stop()
    finally:
        await client.close()
        await store.close()


@cli.command()
@click.option("--limit", default=20, help="Number of markets to show.")
def top_markets(limit: int) -> None:
    """Show top stored markets by volume."""
    asyncio.run(_top_markets(limit))


async def _top_markets(limit: int) -> None:
    from pm_bot.data.store import DataStore

    settings = get_settings()
    store = DataStore(settings.db_url)
    records = await store.get_latest_markets(limit=limit)
    if not records:
        console.print("[yellow]No markets in DB. Run 'scan' first.[/yellow]")
        await store.close()
        return
    table = Table(title="Stored Markets (latest snapshot)")
    table.add_column("Ticker", style="cyan")
    table.add_column("Title", style="white", max_width=50)
    table.add_column("Last Price", justify="right")
    table.add_column("Volume", justify="right", style="green")
    table.add_column("Fetched At", style="dim")
    for r in records:
        table.add_row(
            r.ticker,
            r.title[:50],
            str(r.last_price),
            str(r.volume),
            r.fetched_at.strftime("%Y-%m-%d %H:%M") if r.fetched_at else "",
        )
    console.print(table)
    await store.close()


@cli.command()
@click.argument("ticker")
@click.option("--limit", default=50, help="Number of price records to show.")
def price_history(ticker: str, limit: int) -> None:
    """Show price history for a market ticker."""
    asyncio.run(_price_history(ticker, limit))


async def _price_history(ticker: str, limit: int) -> None:
    from pm_bot.data.store import DataStore

    settings = get_settings()
    store = DataStore(settings.db_url)
    records = await store.get_price_history(ticker, limit=limit)
    if not records:
        console.print(f"[yellow]No price data for {ticker}.[/yellow]")
        await store.close()
        return
    table = Table(title=f"Price History: {ticker}")
    table.add_column("Yes Price", justify="right", style="green")
    table.add_column("Volume", justify="right")
    table.add_column("Source", style="dim")
    table.add_column("Time", style="dim")
    for r in records:
        table.add_row(
            str(r.yes_price),
            str(r.volume),
            r.source,
            r.captured_at.strftime("%Y-%m-%d %H:%M:%S") if r.captured_at else "",
        )
    console.print(table)
    await store.close()


@cli.command()
@click.argument("tickers", nargs=-1)
@click.option("--channel", "-c", multiple=True, default=["ticker", "trade"],
              help="WebSocket channels to subscribe to.")
def stream(tickers: tuple[str, ...], channel: tuple[str, ...]) -> None:
    """Stream real-time data via WebSocket for given market tickers."""
    asyncio.run(_stream(list(tickers), list(channel)))


async def _stream(tickers: list[str], channels: list[str]) -> None:
    from pm_bot.api.websocket import KalshiWebSocket
    from pm_bot.data.store import DataStore

    settings = get_settings()
    store = DataStore(settings.db_url)
    await store.init_db()
    ws = KalshiWebSocket(settings)

    async def on_ticker(data: dict) -> None:
        msg = data.get("msg", {})
        ticker = msg.get("market_ticker", "")
        price = msg.get("yes_price") or msg.get("price", 0)
        volume = msg.get("volume", 0)
        console.print(f"  [cyan]TICK[/cyan] {ticker}: {price}c  vol={volume}")
        if ticker and price:
            await store.save_price(ticker, price, volume, source="ws_ticker")

    async def on_trade(data: dict) -> None:
        msg = data.get("msg", {})
        ticker = msg.get("market_ticker", "")
        price = msg.get("yes_price", 0)
        count = msg.get("count", 0)
        console.print(f"  [green]TRADE[/green] {ticker}: {price}c x{count}")
        if ticker and price:
            await store.save_price(ticker, price, count, source="ws_trade")

    ws.on("ticker", on_ticker)
    ws.on("trade", on_trade)

    console.print(f"[cyan]Streaming {channels} for {tickers or 'all'}...[/cyan]")
    try:
        await ws.run(channels, market_tickers=list(tickers) if tickers else None)
    except KeyboardInterrupt:
        pass
    finally:
        await ws.disconnect()
        await store.close()


# --- Backtest (Phase 5) ---


@cli.command()
@click.option("--strategy", "-s", default="naive_value", help="Strategy to backtest.")
@click.option("--balance", default=10000.0, help="Starting balance.")
@click.option("--html", "html_output", is_flag=True, help="Generate HTML report.")
def backtest(strategy: str, balance: float, html_output: bool) -> None:
    """Run a backtest against stored historical data."""
    asyncio.run(_backtest(strategy, balance, html_output))


async def _backtest(strategy_name: str, balance: float, html_output: bool) -> None:
    from sqlmodel import select
    from sqlmodel.ext.asyncio.session import AsyncSession

    from pm_bot.backtest.engine import BacktestEngine
    from pm_bot.backtest.report import generate_html_report, print_report
    from pm_bot.data.models import OrderBookSnapshot
    from pm_bot.data.store import DataStore
    from pm_bot.engine.bot import STRATEGY_REGISTRY, _load_strategies

    settings = get_settings()
    store = DataStore(settings.db_url)

    market_records = await store.get_latest_markets(limit=5000)
    if not market_records:
        console.print("[yellow]No market data in DB. Run 'scan' first.[/yellow]")
        await store.close()
        return

    async with AsyncSession(store._engine) as session:
        result = await session.exec(select(OrderBookSnapshot).limit(10000))
        snapshots = list(result.all())

    _load_strategies()
    cls = STRATEGY_REGISTRY.get(strategy_name)
    if cls is None:
        console.print(f"[red]Unknown strategy: {strategy_name}[/red]")
        console.print(f"Available: {list(STRATEGY_REGISTRY.keys())}")
        await store.close()
        return

    strategies = [cls()]
    engine = BacktestEngine(strategies=strategies, starting_balance=balance)
    bt_result = await engine.run(market_records, snapshots)

    print_report(bt_result)
    if html_output:
        generate_html_report(bt_result)
    await store.close()


# --- Bot runner (Phase 2+) ---


@cli.command()
@click.option("--strategy", "-s", multiple=True, default=["naive_value"],
              help="Strategies to run.")
def run(strategy: tuple[str, ...]) -> None:
    """Run the trading bot with specified strategies."""
    asyncio.run(_run_bot(list(strategy)))


async def _run_bot(strategy_names: list[str]) -> None:
    from pm_bot.api.client import KalshiClient
    from pm_bot.data.store import DataStore
    from pm_bot.engine.bot import Bot

    settings = get_settings()
    client = KalshiClient(settings)
    store = DataStore(settings.db_url)
    await store.init_db()

    bot = Bot(client=client, store=store, settings=settings, strategy_names=strategy_names)
    try:
        console.print(f"[cyan]Bot starting with strategies: {strategy_names}[/cyan]")
        await bot.run()
    except KeyboardInterrupt:
        console.print("[yellow]Shutting down...[/yellow]")
    finally:
        await bot.shutdown()
        await client.close()
        await store.close()


if __name__ == "__main__":
    cli()
