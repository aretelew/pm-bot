"""Alert dispatchers for sending notifications via Discord and Telegram."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

import httpx

from pm_bot.utils.logging import get_logger

log = get_logger("utils.alerts")


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertDispatcher(ABC):
    @abstractmethod
    async def send(self, message: str, level: AlertLevel = AlertLevel.INFO) -> bool:
        ...


class DiscordWebhookAlert(AlertDispatcher):
    """Send alerts to a Discord channel via webhook."""

    LEVEL_COLORS = {
        AlertLevel.INFO: 0x3498DB,
        AlertLevel.WARNING: 0xF39C12,
        AlertLevel.CRITICAL: 0xE74C3C,
    }

    def __init__(self, webhook_url: str) -> None:
        self._url = webhook_url

    async def send(self, message: str, level: AlertLevel = AlertLevel.INFO) -> bool:
        payload = {
            "embeds": [{
                "title": f"PM-Bot Alert [{level.value.upper()}]",
                "description": message,
                "color": self.LEVEL_COLORS.get(level, 0x3498DB),
            }]
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(self._url, json=payload)
                resp.raise_for_status()
            return True
        except Exception:
            log.exception("discord_alert_failed")
            return False


class TelegramBotAlert(AlertDispatcher):
    """Send alerts to a Telegram chat via Bot API."""

    LEVEL_EMOJI = {
        AlertLevel.INFO: "ℹ️",
        AlertLevel.WARNING: "⚠️",
        AlertLevel.CRITICAL: "🚨",
    }

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id

    async def send(self, message: str, level: AlertLevel = AlertLevel.INFO) -> bool:
        emoji = self.LEVEL_EMOJI.get(level, "")
        text = f"{emoji} *PM-Bot [{level.value.upper()}]*\n{message}"
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            return True
        except Exception:
            log.exception("telegram_alert_failed")
            return False


class ConsoleAlert(AlertDispatcher):
    """Print alerts to the console (always available, no config needed)."""

    async def send(self, message: str, level: AlertLevel = AlertLevel.INFO) -> bool:
        log.msg(f"ALERT [{level.value}]: {message}", level=level.value)
        return True


class AlertManager:
    """Manages multiple alert dispatchers and sends to all of them."""

    def __init__(self) -> None:
        self._dispatchers: list[AlertDispatcher] = [ConsoleAlert()]

    def add_dispatcher(self, dispatcher: AlertDispatcher) -> None:
        self._dispatchers.append(dispatcher)

    async def alert(self, message: str, level: AlertLevel = AlertLevel.INFO) -> None:
        for dispatcher in self._dispatchers:
            try:
                await dispatcher.send(message, level)
            except Exception:
                log.exception("alert_dispatch_error", dispatcher=type(dispatcher).__name__)

    async def info(self, message: str) -> None:
        await self.alert(message, AlertLevel.INFO)

    async def warning(self, message: str) -> None:
        await self.alert(message, AlertLevel.WARNING)

    async def critical(self, message: str) -> None:
        await self.alert(message, AlertLevel.CRITICAL)
