"""Kalshi WebSocket client for real-time market data."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any, Callable, Coroutine

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from pm_bot.config import Settings
from pm_bot.utils.logging import get_logger

log = get_logger("api.websocket")

Callback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class KalshiWebSocket:
    """Manages a persistent WebSocket connection to Kalshi for streaming data."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._ws_url = settings.ws_url
        self._api_key_id = settings.kalshi_api_key_id
        self._private_key = self._load_private_key(settings)
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._callbacks: dict[str, list[Callback]] = {}
        self._running = False
        self._cmd_id = 0

    @staticmethod
    def _load_private_key(settings: Settings) -> rsa.RSAPrivateKey:
        pem_data = settings.private_key_pem.encode()
        return serialization.load_pem_private_key(pem_data, password=None)

    def _sign(self, timestamp_ms: str, method: str, path: str) -> str:
        message = f"{timestamp_ms}{method}{path}".encode()
        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode()

    def _auth_headers(self) -> dict[str, str]:
        timestamp_ms = str(int(time.time() * 1000))
        path = "/trade-api/ws/v2"
        signature = self._sign(timestamp_ms, "GET", path)
        return {
            "KALSHI-ACCESS-KEY": self._api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": signature,
        }

    def on(self, channel: str, callback: Callback) -> None:
        self._callbacks.setdefault(channel, []).append(callback)

    def _next_cmd_id(self) -> int:
        self._cmd_id += 1
        return self._cmd_id

    async def subscribe(self, channels: list[str], market_tickers: list[str] | None = None) -> None:
        if not self._ws:
            return
        msg: dict[str, Any] = {
            "id": self._next_cmd_id(),
            "cmd": "subscribe",
            "params": {"channels": channels},
        }
        if market_tickers:
            msg["params"]["market_tickers"] = market_tickers
        await self._ws.send(json.dumps(msg))
        log.info("subscribed", channels=channels, tickers=market_tickers)

    async def unsubscribe(self, channels: list[str], market_tickers: list[str] | None = None) -> None:
        if not self._ws:
            return
        msg: dict[str, Any] = {
            "id": self._next_cmd_id(),
            "cmd": "unsubscribe",
            "params": {"channels": channels},
        }
        if market_tickers:
            msg["params"]["market_tickers"] = market_tickers
        await self._ws.send(json.dumps(msg))

    async def connect(self) -> None:
        headers = self._auth_headers()
        self._ws = await websockets.connect(self._ws_url, additional_headers=headers)
        self._running = True
        log.info("websocket_connected", url=self._ws_url)

    async def disconnect(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        log.info("websocket_disconnected")

    async def listen(self) -> None:
        """Main loop: read messages and dispatch to registered callbacks."""
        while self._running and self._ws:
            try:
                raw = await self._ws.recv()
                data = json.loads(raw)
                channel = data.get("type", "")
                callbacks = self._callbacks.get(channel, [])
                for cb in callbacks:
                    try:
                        await cb(data)
                    except Exception:
                        log.exception("callback_error", channel=channel)
            except websockets.ConnectionClosed:
                log.warning("websocket_closed, reconnecting in 5s")
                await asyncio.sleep(5)
                if self._running:
                    await self.connect()
            except Exception:
                log.exception("websocket_error")
                await asyncio.sleep(1)

    async def run(self, channels: list[str], market_tickers: list[str] | None = None) -> None:
        await self.connect()
        await self.subscribe(channels, market_tickers)
        await self.listen()
