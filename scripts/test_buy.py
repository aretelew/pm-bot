#!/usr/bin/env python3
"""Test script: place a small (~$1) buy order to verify Kalshi API connectivity.

Usage:
    python scripts/test_buy.py              # Auto-pick a market with liquidity
    python scripts/test_buy.py --ticker X   # Use specific market ticker

Note: Demo (demo-api.kalshi.co) has limited markets with orderbooks; many return 404.
      Run `pm-bot check` or `pm-bot top-markets` to find tickers, then try --ticker.
"""
from __future__ import annotations

import argparse
import asyncio
import uuid

from pm_bot.api.client import KalshiClient
from pm_bot.api.models import Action, OrderRequest, OrderType, Side
from pm_bot.config import get_settings


async def _run(ticker: str | None = None) -> None:
    settings = get_settings()
    client = KalshiClient(settings)

    try:
        # Find a market with liquidity
        if ticker:
            try:
                market = await client.get_market(ticker)
                markets = [market]
            except Exception as e:
                print(f"Error fetching market {ticker}: {e}")
                return
        else:
            resp = await client.get_markets(limit=200, status="open")
            # Prefer markets with liquidity; fall back to any if demo is thin
            markets = [m for m in resp.markets if m.yes_bid > 0 and m.yes_ask > 0]
            if not markets:
                markets = resp.markets[:20]  # Try first 20 by volume/recency
            if not markets:
                print("No markets found. Try specifying --ticker.")
                return

        # Try to get orderbook and place order
        last_error = None
        for market in markets[:10]:  # Try up to 10 markets
            try:
                ob_resp = await client.get_orderbook(market.ticker)
            except Exception as e:
                last_error = e
                continue

            ob = ob_resp.orderbook
            best_ask = ob.best_yes_ask
            best_bid = ob.best_yes_bid

            if best_ask is None or best_bid is None:
                continue

            # Buy 2 contracts at best ask (~$1 if ask is 50c)
            count = 2
            price = best_ask
            cost_dollars = count * (price / 100.0)

            print(f"Market: {market.ticker}")
            print(f"Title: {market.title[:60]}...")
            print(f"Best bid: {best_bid}c  Best ask: {best_ask}c")
            print(f"Placing: BUY {count} YES @ {price}c (cost ~${cost_dollars:.2f})")

            req = OrderRequest(
                ticker=market.ticker,
                action=Action.BUY,
                side=Side.YES,
                count=count,
                type=OrderType.LIMIT,
                yes_price=price,
                no_price=None,
                client_order_id=str(uuid.uuid4()),
            )

            resp = await client.create_order(req)
            order = resp.order

            print("\nOrder placed successfully!")
            print(f"  Order ID: {order.order_id}")
            print(f"  Status: {order.status.value}")
            print(f"  Check your orders at demo.kalshi.co")
            return

        print("Could not place order on any market (404 or no liquidity).")
        if last_error:
            print(f"Last error: {last_error}")

    except Exception as e:
        print(f"Error: {e}")
        raise
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Place a ~$1 test buy order")
    parser.add_argument("--ticker", type=str, help="Market ticker (e.g. KXQUICKSETTLE-26FEB19)")
    args = parser.parse_args()
    asyncio.run(_run(ticker=args.ticker))


if __name__ == "__main__":
    main()
