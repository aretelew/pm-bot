"""Weather forecast providers: OpenWeatherMap, NOAA, and Tomorrow.io.

Each provider fetches daily forecasts and returns a common WeatherForecast
dataclass. Results are cached per (city, date) to respect rate limits.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

import httpx

from pm_bot.utils.logging import get_logger
from pm_bot.weather.parser import CityInfo

log = get_logger("weather.providers")

_CACHE_TTL_SECONDS = 1800  # 30 minutes


@dataclass
class WeatherForecast:
    """Unified daily forecast from any provider."""

    temp_high_f: float
    temp_low_f: float
    precip_prob: float  # 0.0 – 1.0
    precip_inches: float
    wind_speed_mph: float
    source: str
    forecast_std: float  # uncertainty estimate in °F
    snow_inches: float = 0.0  # daily snowfall in inches
    precip_std: float = 0.0  # uncertainty estimate in inches for precip


def _kelvin_to_f(k: float) -> float:
    return (k - 273.15) * 9.0 / 5.0 + 32.0


def _celsius_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


CacheKey = tuple[float, float, str]  # (lat, lon, date_iso)


class _ForecastCache:
    """Simple in-memory TTL cache for forecasts."""

    def __init__(self, ttl: int = _CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl
        self._store: dict[CacheKey, tuple[float, WeatherForecast]] = {}

    def get(self, key: CacheKey) -> WeatherForecast | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, forecast = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            return None
        return forecast

    def put(self, key: CacheKey, forecast: WeatherForecast) -> None:
        self._store[key] = (time.monotonic(), forecast)

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, (ts, _) in self._store.items() if now - ts > self._ttl]
        for k in expired:
            del self._store[k]


_CIRCUIT_BREAKER_THRESHOLD = 3


class WeatherProvider(ABC):
    """Base class for weather forecast providers."""

    name: str = "base"

    def __init__(self) -> None:
        self._consecutive_failures = 0
        self._disabled = False

    async def fetch_forecast(
        self, city: CityInfo, target_date: date
    ) -> WeatherForecast | None:
        """Fetch a daily forecast. Auto-disables after repeated failures."""
        if self._disabled:
            return None
        result = await self._do_fetch(city, target_date)
        if result is None:
            self._consecutive_failures += 1
            if self._consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
                log.warning("provider_disabled", provider=self.name, failures=self._consecutive_failures)
                self._disabled = True
        else:
            self._consecutive_failures = 0
        return result

    @abstractmethod
    async def _do_fetch(
        self, city: CityInfo, target_date: date
    ) -> WeatherForecast | None:
        """Subclass implementation of the actual fetch."""
        ...


class OpenWeatherMapProvider(WeatherProvider):
    """OpenWeatherMap 5-day / 3-hour forecast provider (free tier).

    Free tier: unlimited calls (rate-limited to 60/min).
    Docs: https://openweathermap.org/forecast5
    """

    name = "openweathermap"

    def __init__(self, api_key: str) -> None:
        super().__init__()
        self._api_key = api_key
        self._cache = _ForecastCache()

    async def _do_fetch(
        self, city: CityInfo, target_date: date
    ) -> WeatherForecast | None:
        cache_key: CacheKey = (city.lat, city.lon, target_date.isoformat())
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        url = "https://api.openweathermap.org/data/2.5/forecast"
        params = {
            "lat": city.lat,
            "lon": city.lon,
            "appid": self._api_key,
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            log.exception("owm_fetch_error", city=city.name)
            return None

        target_iso = target_date.isoformat()
        temps: list[float] = []
        pops: list[float] = []
        winds: list[float] = []
        rain_mm = 0.0
        snow_mm = 0.0

        for entry in data.get("list", []):
            dt = date.fromtimestamp(entry["dt"])
            if dt.isoformat() != target_iso:
                continue
            main = entry.get("main", {})
            temps.append(main.get("temp", 0))
            pops.append(entry.get("pop", 0.0))
            winds.append(entry.get("wind", {}).get("speed", 0))
            rain_mm += entry.get("rain", {}).get("3h", 0)
            snow_mm += entry.get("snow", {}).get("3h", 0)

        if not temps:
            log.debug("owm_date_not_in_range", city=city.name, target=target_iso)
            return None

        precip_inches = rain_mm / 25.4
        snow_inches = snow_mm / 25.4
        forecast = WeatherForecast(
            temp_high_f=_kelvin_to_f(max(temps)),
            temp_low_f=_kelvin_to_f(min(temps)),
            precip_prob=max(pops),
            precip_inches=precip_inches,
            wind_speed_mph=max(winds) * 2.237,
            source=self.name,
            forecast_std=3.5,
            snow_inches=snow_inches,
            precip_std=precip_inches * 0.4 if precip_inches > 0 else 0.1,
        )
        self._cache.put(cache_key, forecast)
        return forecast


class NOAAProvider(WeatherProvider):
    """NOAA Weather.gov API provider.

    Free, no API key required. US locations only.
    Docs: https://www.weather.gov/documentation/services-web-api
    """

    name = "noaa"

    def __init__(self) -> None:
        super().__init__()
        self._cache = _ForecastCache()
        self._grid_cache: dict[tuple[float, float], str] = {}

    async def _get_forecast_url(
        self, client: httpx.AsyncClient, lat: float, lon: float
    ) -> str | None:
        """Resolve lat/lon to a NWS grid forecast URL (cached)."""
        grid_key = (round(lat, 4), round(lon, 4))
        if grid_key in self._grid_cache:
            return self._grid_cache[grid_key]

        url = f"https://api.weather.gov/points/{lat},{lon}"
        headers = {"User-Agent": "pm-bot/1.0 (weather trading bot)"}
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            forecast_url = resp.json()["properties"]["forecast"]
            self._grid_cache[grid_key] = forecast_url
            return forecast_url
        except Exception:
            log.exception("noaa_grid_error", lat=lat, lon=lon)
            return None

    async def _do_fetch(
        self, city: CityInfo, target_date: date
    ) -> WeatherForecast | None:
        cache_key: CacheKey = (city.lat, city.lon, target_date.isoformat())
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        headers = {"User-Agent": "pm-bot/1.0 (weather trading bot)"}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                forecast_url = await self._get_forecast_url(client, city.lat, city.lon)
                if forecast_url is None:
                    return None

                resp = await client.get(forecast_url, headers=headers)
                resp.raise_for_status()
                periods = resp.json()["properties"]["periods"]
        except Exception:
            log.exception("noaa_fetch_error", city=city.name)
            return None

        high: float | None = None
        low: float | None = None
        wind_speed: float = 0.0
        precip_prob: float = 0.0
        has_snow = False

        for period in periods:
            start = period.get("startTime", "")
            if not start.startswith(target_date.isoformat()):
                continue

            temp_f = period.get("temperature")
            if temp_f is None:
                continue

            if period.get("isDaytime", True):
                high = float(temp_f)
            else:
                low = float(temp_f)

            prob = period.get("probabilityOfPrecipitation", {})
            if prob and prob.get("value") is not None:
                precip_prob = max(precip_prob, prob["value"] / 100.0)

            wind_str = period.get("windSpeed", "0 mph")
            try:
                wind_speed = max(wind_speed, float(wind_str.split()[0]))
            except (ValueError, IndexError):
                pass

            detail = period.get("detailedForecast", "").lower()
            if "snow" in detail:
                has_snow = True

        if high is None and low is None:
            log.debug("noaa_date_not_found", city=city.name, target=target_date.isoformat())
            return None

        # Rough snow estimate: if forecast text mentions snow and temp is cold
        avg_temp = ((high or 32) + (low or 20)) / 2.0
        snow_inches = 0.0
        if has_snow and avg_temp <= 40 and precip_prob > 0.3:
            snow_inches = precip_prob * 2.0  # rough heuristic

        forecast = WeatherForecast(
            temp_high_f=high if high is not None else (low + 15.0 if low is not None else 0),
            temp_low_f=low if low is not None else (high - 15.0 if high is not None else 0),
            precip_prob=precip_prob,
            precip_inches=0.0,
            wind_speed_mph=wind_speed,
            source=self.name,
            forecast_std=3.0,
            snow_inches=snow_inches,
        )
        self._cache.put(cache_key, forecast)
        return forecast


class TomorrowIOProvider(WeatherProvider):
    """Tomorrow.io Timeline API provider.

    Free tier: 500 calls/day.
    Docs: https://docs.tomorrow.io/reference/get-timelines
    """

    name = "tomorrowio"

    def __init__(self, api_key: str) -> None:
        super().__init__()
        self._api_key = api_key
        self._cache = _ForecastCache()

    async def _do_fetch(
        self, city: CityInfo, target_date: date
    ) -> WeatherForecast | None:
        cache_key: CacheKey = (city.lat, city.lon, target_date.isoformat())
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        url = "https://api.tomorrow.io/v4/timelines"
        params = {
            "location": f"{city.lat},{city.lon}",
            "fields": "temperatureMax,temperatureMin,precipitationProbability,"
                      "precipitationIntensity,windSpeed,snowAccumulation",
            "timesteps": "1d",
            "units": "imperial",
            "apikey": self._api_key,
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            log.exception("tio_fetch_error", city=city.name)
            return None

        target_iso = target_date.isoformat()
        timelines = data.get("data", {}).get("timelines", [])
        if not timelines:
            return None

        for interval in timelines[0].get("intervals", []):
            start = interval.get("startTime", "")
            if not start.startswith(target_iso):
                continue

            vals = interval.get("values", {})
            precip_inches = vals.get("precipitationIntensity", 0)
            snow_inches = vals.get("snowAccumulation", 0)
            forecast = WeatherForecast(
                temp_high_f=vals.get("temperatureMax", 0),
                temp_low_f=vals.get("temperatureMin", 0),
                precip_prob=vals.get("precipitationProbability", 0) / 100.0,
                precip_inches=precip_inches,
                wind_speed_mph=vals.get("windSpeed", 0),
                source=self.name,
                forecast_std=3.5,
                snow_inches=snow_inches,
                precip_std=precip_inches * 0.4 if precip_inches > 0 else 0.1,
            )
            self._cache.put(cache_key, forecast)
            return forecast

        log.debug("tio_date_not_found", city=city.name, target=target_iso)
        return None
