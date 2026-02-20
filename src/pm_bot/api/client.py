"""Kalshi REST API client with RSA-PSS authentication and rate limiting."""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from pm_bot.api.models import (
    Balance,
    EventsResponse,
    FillsResponse,
    Market,
    MarketsResponse,
    OrderBookResponse,
    OrderRequest,
    OrderResponse,
    OrdersResponse,
    PositionsResponse,
)
from pm_bot.config import Settings
from pm_bot.utils.logging import get_logger

log = get_logger("api.client")

MAX_RETRIES = 3
RATE_LIMIT_SLEEP = 1.0


class RateLimiter:
    """Token-bucket rate limiter for API requests."""

    def __init__(self, requests_per_second: float = 10.0) -> None:
        self._rate = requests_per_second
        self._tokens = requests_per_second
        self._max_tokens = requests_per_second
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._max_tokens, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens < 1:
                wait = (1 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._tokens = 0
            else:
                self._tokens -= 1


class KalshiClient:
    """Async HTTP client for Kalshi's REST API with rate limiting and retries."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.base_url
        self._api_key_id = settings.kalshi_api_key_id
        self._private_key = self._load_private_key(settings)
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=30.0,
            http2=True,
        )
        self._rate_limiter = RateLimiter(requests_per_second=8.0)

    @staticmethod
    def _load_private_key(settings: Settings) -> rsa.RSAPrivateKey:
        pem_data = settings.private_key_pem.encode()
        return serialization.load_pem_private_key(pem_data, password=None)

    def _sign(self, timestamp_ms: str, method: str, path: str) -> str:
        """Sign request per Kalshi docs: timestamp + method + path (no query params)."""
        message = f"{timestamp_ms}{method}{path}".encode()
        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode()

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        """Path for signing must include /trade-api/v2 prefix (Kalshi requirement)."""
        timestamp_ms = str(int(time.time() * 1000))
        sign_path = f"/trade-api/v2{path}"
        signature = self._sign(timestamp_ms, method, sign_path)
        return {
            "KALSHI-ACCESS-KEY": self._api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "Content-Type": "application/json",
        }

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict:
        for attempt in range(MAX_RETRIES):
            await self._rate_limiter.acquire()
            headers = self._auth_headers(method, path)
            try:
                if method == "GET":
                    resp = await self._http.get(path, headers=headers, params=params)
                elif method == "POST":
                    resp = await self._http.post(path, headers=headers, json=json_data or {})
                elif method == "DELETE":
                    resp = await self._http.delete(path, headers=headers)
                else:
                    raise ValueError(f"Unsupported method: {method}")

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", RATE_LIMIT_SLEEP))
                    log.warning("rate_limited", path=path, retry_after=retry_after, attempt=attempt)
                    await asyncio.sleep(retry_after)
                    continue

                resp.raise_for_status()
                return resp.json()

            except httpx.HTTPStatusError:
                raise
            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                if attempt < MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    log.warning("request_retry", path=path, error=str(e), wait=wait)
                    await asyncio.sleep(wait)
                else:
                    raise

        raise RuntimeError(f"Max retries exceeded for {method} {path}")

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        return await self._request_with_retry("GET", path, params=params)

    async def _post(self, path: str, data: dict[str, Any] | None = None) -> dict:
        return await self._request_with_retry("POST", path, json_data=data)

    async def _delete(self, path: str) -> dict:
        return await self._request_with_retry("DELETE", path)

    async def close(self) -> None:
        await self._http.aclose()

    # --- Markets ---

    async def get_markets(
        self,
        *,
        limit: int = 100,
        cursor: str = "",
        status: str = "open",
        event_ticker: str = "",
    ) -> MarketsResponse:
        params: dict[str, Any] = {"limit": limit, "status": status}
        if cursor:
            params["cursor"] = cursor
        if event_ticker:
            params["event_ticker"] = event_ticker
        data = await self._get("/markets", params=params)
        return MarketsResponse.model_validate(data)

    async def get_market(self, ticker: str) -> Market:
        data = await self._get(f"/markets/{ticker}")
        return Market.model_validate(data.get("market", data))

    async def get_events(
        self,
        *,
        limit: int = 100,
        cursor: str = "",
        status: str = "open",
    ) -> EventsResponse:
        params: dict[str, Any] = {"limit": limit, "status": status}
        if cursor:
            params["cursor"] = cursor
        data = await self._get("/events", params=params)
        return EventsResponse.model_validate(data)

    async def get_orderbook(self, ticker: str, depth: int = 10) -> OrderBookResponse:
        data = await self._get(f"/markets/{ticker}/orderbook", params={"depth": depth})
        # Normalize [price, quantity] arrays to {price, quantity} objects.
        # Kalshi returns ascending order; we want best (highest) bid first.
        ob = data.get("orderbook", data)
        if ob:
            for key in ("yes", "no"):
                raw = ob.get(key, [])
                levels = [{"price": p, "quantity": q} for p, q in raw] if raw else []
                ob[key] = list(reversed(levels))  # best bid first
        return OrderBookResponse.model_validate(data)

    # --- Orders ---

    async def create_order(self, order: OrderRequest) -> OrderResponse:
        data = await self._post(
            "/portfolio/orders",
            data=order.model_dump(exclude_none=True),
        )
        return OrderResponse.model_validate(data)

    async def cancel_order(self, order_id: str) -> dict:
        return await self._delete(f"/portfolio/orders/{order_id}")

    async def get_orders(
        self,
        *,
        ticker: str = "",
        status: str = "",
        limit: int = 100,
        cursor: str = "",
    ) -> OrdersResponse:
        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        data = await self._get("/portfolio/orders", params=params)
        return OrdersResponse.model_validate(data)

    # --- Portfolio ---

    async def get_positions(
        self,
        *,
        limit: int = 100,
        cursor: str = "",
    ) -> PositionsResponse:
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        data = await self._get("/portfolio/positions", params=params)
        return PositionsResponse.model_validate(data)

    async def get_balance(self) -> Balance:
        data = await self._get("/portfolio/balance")
        return Balance.model_validate(data)

    async def get_fills(
        self,
        *,
        ticker: str = "",
        limit: int = 100,
        cursor: str = "",
    ) -> FillsResponse:
        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if cursor:
            params["cursor"] = cursor
        data = await self._get("/portfolio/fills", params=params)
        return FillsResponse.model_validate(data)
