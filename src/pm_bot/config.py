from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    DEMO = "demo"
    PRODUCTION = "production"


_BASE_URLS = {
    Environment.DEMO: "https://demo-api.kalshi.co/trade-api/v2",
    Environment.PRODUCTION: "https://api.elections.kalshi.com/trade-api/v2",
}

_WS_URLS = {
    Environment.DEMO: "wss://demo-api.kalshi.co/trade-api/ws/v2",
    Environment.PRODUCTION: "wss://api.elections.kalshi.com/trade-api/ws/v2",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    kalshi_api_key_id: str = ""
    kalshi_private_key_path: Path = Path("private_key.pem")
    kalshi_env: Environment = Environment.DEMO

    db_url: str = "sqlite+aiosqlite:///pm_bot.db"
    log_level: str = "INFO"

    # Risk defaults
    max_position_per_market: int = 100
    max_total_exposure: int = 1000
    max_daily_loss: int = 500

    # Scanner defaults
    scanner_poll_interval_seconds: int = 15

    # Weather provider API keys
    openweathermap_api_key: str = ""
    tomorrowio_api_key: str = ""

    @property
    def base_url(self) -> str:
        return _BASE_URLS[self.kalshi_env]

    @property
    def ws_url(self) -> str:
        return _WS_URLS[self.kalshi_env]

    @property
    def private_key_pem(self) -> str:
        return self.kalshi_private_key_path.read_text()


settings = Field(default_factory=Settings)


def get_settings() -> Settings:
    return Settings()
