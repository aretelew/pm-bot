"""Parse Kalshi weather market tickers into structured weather queries.

Kalshi weather tickers encode city, metric, date, and threshold:
  KXHIGHNY-25FEB20-T45       -> high temp in NYC above 45F on 2025-02-20
  KXLOWCHI-25MAR01-T20       -> low  temp in Chicago below 20F on 2025-03-01
  KXNYCSNOWM-26FEB-3.0       -> monthly snow in NYC above 3.0" in Feb 2026
  KXRAINSFOM-26FEB-1.5        -> monthly rain in SFO above 1.5" in Feb 2026
  KXRAINSFOM-26FEB            -> any rain in SFO in Feb 2026
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class WeatherMetric(str, Enum):
    HIGH_TEMP = "high"
    LOW_TEMP = "low"
    SNOW_MONTHLY = "snow_monthly"
    RAIN_MONTHLY = "rain_monthly"


@dataclass(frozen=True)
class CityInfo:
    lat: float
    lon: float
    name: str


CITY_COORDS: dict[str, CityInfo] = {
    "ABQ": CityInfo(35.0853336, -106.6055534, "Albuquerque"),
    "ATL": CityInfo(33.749, -84.388, "Atlanta"),
    "AUS": CityInfo(30.2672, -97.7431, "Austin"),
    "BAL": CityInfo(39.2903848, -76.6121893, "Baltimore"),
    "BLD": CityInfo(43.6187102, -116.2146068, "Boise City"),
    "BOS": CityInfo(42.3601, -71.0589, "Boston"),
    "CHI": CityInfo(41.8781, -87.6298, "Chicago"),
    "CIN": CityInfo(39.1031182, -84.5120196, "Cincinnati"),
    "CLB": CityInfo(39.9611755, -82.99879419999999, "Columbus"),
    "CLE": CityInfo(41.49932, -81.6943605, "Cleveland"),
    "CLT": CityInfo(35.2270869, -80.8431267, "Charlotte"),
    "DAL": CityInfo(32.7767, -96.797, "Dallas"),
    "DC": CityInfo(38.9072, -77.0369, "Washington DC"),
    "DEN": CityInfo(39.7392, -104.9903, "Denver"),
    "DET": CityInfo(42.3314, -83.0458, "Detroit"),
    "ELP": CityInfo(31.7775757, -106.4424559, "El Paso"),
    "FRE": CityInfo(36.7468422, -119.7725868, "Fresno"),
    "FW": CityInfo(32.7554883, -97.3307658, "Fort Worth"),
    "HOU": CityInfo(29.7604, -95.3698, "Houston"),
    "IND": CityInfo(39.768403, -86.158068, "Indianapolis"),
    "JAX": CityInfo(30.3321838, -81.65565099999999, "Jacksonville"),
    "KC": CityInfo(39.0997265, -94.5785667, "Kansas City"),
    "LA": CityInfo(34.0522, -118.2437, "Los Angeles"),
    "LV": CityInfo(36.1699412, -115.1398296, "Las Vegas"),
    "MEM": CityInfo(35.1495343, -90.0489801, "Memphis"),
    "MIA": CityInfo(25.7617, -80.1918, "Miami"),
    "MIL": CityInfo(43.0389025, -87.9064736, "Milwaukee"),
    "MSP": CityInfo(44.9778, -93.265, "Minneapolis"),
    "NASH": CityInfo(36.1626638, -86.7816016, "Nashville-Davidson"),
    "NOL": CityInfo(29.95106579999999, -90.0715323, "New Orleans"),
    "NY": CityInfo(40.7128, -74.006, "New York"),
    "NYC": CityInfo(40.7127837, -74.0059413, "New York"),
    "OAK": CityInfo(37.8043637, -122.2711137, "Oakland"),
    "OKC": CityInfo(35.4675602, -97.5164276, "Oklahoma City"),
    "ORL": CityInfo(28.5383355, -81.3792365, "Orlando"),
    "PDX": CityInfo(45.5230622, -122.6764816, "Portland"),
    "PHI": CityInfo(39.9526, -75.1652, "Philadelphia"),
    "PHX": CityInfo(33.4484, -112.074, "Phoenix"),
    "PIT": CityInfo(40.44062479999999, -79.9958864, "Pittsburgh"),
    "RAL": CityInfo(35.7795897, -78.6381787, "Raleigh"),
    "RIC": CityInfo(37.5407246, -77.4360481, "Richmond"),
    "SAC": CityInfo(38.5815719, -121.4943996, "Sacramento"),
    "SAN": CityInfo(32.715738, -117.1610838, "San Diego"),
    "SD": CityInfo(32.715738, -117.1610838, "San Diego"),
    "SEA": CityInfo(47.6062, -122.3321, "Seattle"),
    "SF": CityInfo(37.7749, -122.4194, "San Francisco"),
    "SFO": CityInfo(37.7749295, -122.4194155, "San Francisco"),
    "SJC": CityInfo(37.3382082, -121.8863286, "San Jose"),
    "SLC": CityInfo(40.7607793, -111.8910474, "Salt Lake City"),
    "SND": CityInfo(32.715738, -117.1610838, "San Diego"),
    "SNDIEGO": CityInfo(32.715738, -117.1610838, "San Diego"),
    "STL": CityInfo(38.627, -90.1994, "St. Louis"),
    "TAM": CityInfo(27.950575, -82.4571776, "Tampa"),
    "TUC": CityInfo(32.2217429, -110.926479, "Tucson"),
    "TUL": CityInfo(36.1539816, -95.99277500000001, "Tulsa"),
    "VEGAS": CityInfo(36.1699412, -115.1398296, "Las Vegas"),

}

_TEMP_RE = re.compile(
    r"^KX(HIGH|LOW)([A-Z]{2,4})-(\d{2})([A-Z]{3})(\d{2})-T(-?\d+)$"
)

_SNOW_RE = re.compile(
    r"^KX([A-Z]{2,6})SNOWM-(\d{2})([A-Z]{3})-([\d.]+)$"
)

_RAIN_RE = re.compile(
    r"^KXRAIN([A-Z]{2,6})M-(\d{2})([A-Z]{3})(?:-([\d.]+))?$"
)

_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


@dataclass(frozen=True)
class WeatherMarketInfo:
    """Structured representation of a Kalshi weather market."""

    ticker: str
    metric: WeatherMetric
    city_code: str
    city: CityInfo
    target_date: date
    threshold: float | None

    @property
    def is_high_temp(self) -> bool:
        return self.metric == WeatherMetric.HIGH_TEMP

    @property
    def is_monthly(self) -> bool:
        return self.metric in (WeatherMetric.SNOW_MONTHLY, WeatherMetric.RAIN_MONTHLY)


def _parse_month_year(yy: str, mon_str: str) -> tuple[int, int] | None:
    """Parse YY + MON into (year, month) or None."""
    month = _MONTH_MAP.get(mon_str)
    if month is None:
        return None
    year = 2000 + int(yy)
    return year, month


def _parse_temp_ticker(ticker: str) -> WeatherMarketInfo | None:
    m = _TEMP_RE.match(ticker)
    if m is None:
        return None

    metric_str, city_code, yy, mon_str, dd, threshold = m.groups()
    metric = WeatherMetric.HIGH_TEMP if metric_str == "HIGH" else WeatherMetric.LOW_TEMP

    city = CITY_COORDS.get(city_code)
    if city is None:
        return None

    ym = _parse_month_year(yy, mon_str)
    if ym is None:
        return None
    year, month = ym

    try:
        target_date = datetime(year, month, int(dd)).date()
    except ValueError:
        return None

    return WeatherMarketInfo(
        ticker=ticker,
        metric=metric,
        city_code=city_code,
        city=city,
        target_date=target_date,
        threshold=float(threshold),
    )


def _parse_snow_ticker(ticker: str) -> WeatherMarketInfo | None:
    m = _SNOW_RE.match(ticker)
    if m is None:
        return None

    city_code, yy, mon_str, threshold_str = m.groups()

    city = CITY_COORDS.get(city_code)
    if city is None:
        return None

    ym = _parse_month_year(yy, mon_str)
    if ym is None:
        return None
    year, month = ym

    last_day = calendar.monthrange(year, month)[1]
    target_date = date(year, month, last_day)

    return WeatherMarketInfo(
        ticker=ticker,
        metric=WeatherMetric.SNOW_MONTHLY,
        city_code=city_code,
        city=city,
        target_date=target_date,
        threshold=float(threshold_str),
    )


def _parse_rain_ticker(ticker: str) -> WeatherMarketInfo | None:
    m = _RAIN_RE.match(ticker)
    if m is None:
        return None

    city_code, yy, mon_str, threshold_str = m.groups()

    city = CITY_COORDS.get(city_code)
    if city is None:
        return None

    ym = _parse_month_year(yy, mon_str)
    if ym is None:
        return None
    year, month = ym

    last_day = calendar.monthrange(year, month)[1]
    target_date = date(year, month, last_day)

    threshold = float(threshold_str) if threshold_str is not None else None

    return WeatherMarketInfo(
        ticker=ticker,
        metric=WeatherMetric.RAIN_MONTHLY,
        city_code=city_code,
        city=city,
        target_date=target_date,
        threshold=threshold,
    )


def parse_weather_ticker(ticker: str) -> WeatherMarketInfo | None:
    """Parse a Kalshi weather ticker string into a WeatherMarketInfo.

    Returns None if the ticker doesn't match any known weather format.
    """
    return _parse_temp_ticker(ticker) or _parse_snow_ticker(ticker) or _parse_rain_ticker(ticker)


def is_weather_market_ticker(ticker: str) -> bool:
    """Quick check whether a ticker looks like a Kalshi weather market."""
    return (
        _TEMP_RE.match(ticker) is not None
        or _SNOW_RE.match(ticker) is not None
        or _RAIN_RE.match(ticker) is not None
    )
