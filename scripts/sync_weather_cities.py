#!/usr/bin/env python3
"""Sync weather cities: fetch from Kalshi API + major US cities, merge into parser.py."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
CITIES_JSON_URL = "https://gist.githubusercontent.com/Miserlou/c5cd8364bf9b2420bb29/raw/cities.json"
PARSER_PATH = Path(__file__).resolve().parents[1] / "src" / "pm_bot" / "weather" / "parser.py"

CODE_TO_NAME: dict[str, str] = {
    "NY": "New York", "NYC": "New York", "CHI": "Chicago", "LA": "Los Angeles",
    "MIA": "Miami", "DEN": "Denver", "HOU": "Houston", "PHX": "Phoenix",
    "PHI": "Philadelphia", "ATL": "Atlanta", "BOS": "Boston", "SEA": "Seattle",
    "SF": "San Francisco", "DC": "Washington", "DAL": "Dallas", "MSP": "Minneapolis",
    "DET": "Detroit", "STL": "St. Louis", "AUS": "Austin", "SND": "San Diego",
    "SNDIEGO": "San Diego", "SAN": "San Diego", "SD": "San Diego",
    "IND": "Indianapolis", "JAX": "Jacksonville", "CLB": "Columbus",
    "CLT": "Charlotte", "FW": "Fort Worth", "ELP": "El Paso", "MEM": "Memphis",
    "PDX": "Portland", "LV": "Las Vegas", "VEGAS": "Las Vegas", "MIL": "Milwaukee",
    "ABQ": "Albuquerque", "TUC": "Tucson", "FRE": "Fresno", "SAC": "Sacramento",
    "SFO": "San Francisco", "SJC": "San Jose", "OKC": "Oklahoma City",
    "BAL": "Baltimore", "LVL": "Louisville", "KC": "Kansas City",
    "NASH": "Nashville-Davidson", "RAL": "Raleigh", "OAK": "Oakland",
    "TUL": "Tulsa", "CLE": "Cleveland", "NOL": "New Orleans", "TAM": "Tampa",
    "ORL": "Orlando", "PIT": "Pittsburgh", "CIN": "Cincinnati",
    "SLC": "Salt Lake City", "BLD": "Boise City", "RIC": "Richmond", "COL": "Columbia",
}


def fetch_kalshi_weather_series() -> list[dict]:
    all_series: list[dict] = []
    cursor = ""
    for category in ("weather", "climate"):
        while True:
            params: dict[str, str] = {"category": category, "limit": "200"}
            if cursor:
                params["cursor"] = cursor
            with httpx.Client(timeout=30) as client:
                resp = client.get(f"{KALSHI_BASE}/series", params=params)
                resp.raise_for_status()
            data = resp.json()
            series = data.get("series") or []
            all_series.extend(series)
            cursor = data.get("cursor", "")
            if not cursor or not series:
                break
    return all_series


def extract_city_codes_from_series(series: list[dict]) -> dict[str, str | None]:
    pattern = re.compile(r"^KX(HIGH|LOW)([A-Z]{2,6})$")
    result: dict[str, str | None] = {}
    title_pattern = re.compile(
        r"(?:highest|lowest)\s+temperature\s+in\s+(.+?)(?:\s+(?:today|on|\,|\.)|$)", re.I
    )
    for s in series:
        ticker = s.get("ticker", "")
        m = pattern.match(ticker)
        if not m:
            continue
        city_code = m.group(2)
        if city_code in result:
            continue
        title = s.get("title", "")
        title_m = title_pattern.search(title)
        name = title_m.group(1).strip() if title_m else None
        if name:
            name = name.replace("NYC", "New York").replace("DC", "Washington")
        result[city_code] = name
    return result


def fetch_major_cities(limit: int = 100) -> list[dict]:
    with httpx.Client(timeout=30) as client:
        resp = client.get(CITIES_JSON_URL)
        resp.raise_for_status()
    data = resp.json()
    return sorted(
        (c for c in data if c.get("rank")),
        key=lambda c: int(c.get("rank", 9999)),
    )[:limit]


def resolve_city(code: str, name_from_title: str | None, major_cities: list[dict]) -> tuple[float, float, str] | None:
    name = CODE_TO_NAME.get(code) or name_from_title
    if name:
        name_lower = name.lower()
        for c in major_cities:
            cn = (c.get("city") or "").lower()
            if cn == name_lower or name_lower in cn or cn in name_lower:
                return (float(c["latitude"]), float(c["longitude"]), c["city"])
        if "washington" in name_lower:
            for c in major_cities:
                if (c.get("city") or "").lower() == "washington":
                    return (float(c["latitude"]), float(c["longitude"]), "Washington DC")
        name_norm = name.replace(".", "").lower()
        for c in major_cities:
            if (c.get("city") or "").replace(".", "").lower() == name_norm:
                return (float(c["latitude"]), float(c["longitude"]), c["city"])
    code_lower = code.lower()
    for c in major_cities:
        cn = (c.get("city") or "").lower()
        if cn.startswith(code_lower) or code_lower in cn[:4]:
            return (float(c["latitude"]), float(c["longitude"]), c["city"])
    return None


def load_existing_coords() -> dict[str, tuple[float, float, str]]:
    content = PARSER_PATH.read_text()
    result: dict[str, tuple[float, float, str]] = {}
    for m in re.compile(r'"([A-Z]{2,6})":\s*CityInfo\(([\d.-]+),\s*([\d.-]+),\s*"([^"]+)"\)').finditer(content):
        result[m.group(1)] = (float(m.group(2)), float(m.group(3)), m.group(4))
    return result


def generate_parser_patch(merged: dict[str, tuple[float, float, str]]) -> str:
    return "\n".join(
        f'    "{code}": CityInfo({lat}, {lon}, "{name.replace(chr(34), chr(92)+chr(34))}"),'
        for code in sorted(merged.keys())
        for lat, lon, name in [merged[code]]
    )


def fetch_weather_cities_from_markets() -> dict[str, str | None]:
    """Fallback: fetch markets and extract city codes from KXHIGH*/KXLOW* tickers."""
    pattern = re.compile(r"^KX(HIGH|LOW)([A-Z]{2,6})-\d{2}[A-Z]{3}\d{2}-T")
    result: dict[str, str | None] = {}
    cursor = ""
    with httpx.Client(timeout=30) as client:
        for _ in range(5):  # limit pages
            params: dict[str, str] = {"limit": "200"}
            if cursor:
                params["cursor"] = cursor
            resp = client.get(f"{KALSHI_BASE}/markets", params=params)
            resp.raise_for_status()
            data = resp.json()
            markets = data.get("markets") or []
            for m in markets:
                ticker = m.get("ticker", "")
                if ticker.startswith(("KXHIGH", "KXLOW")):
                    city_part = ticker.split("-")[0]
                    m2 = re.match(r"^KX(HIGH|LOW)([A-Z]{2,6})$", city_part)
                    if m2 and m2.group(2) not in result:
                        result[m2.group(2)] = None
            cursor = data.get("cursor") or ""
            if not cursor or not markets:
                break
    return result


def main() -> int:
    print("Fetching major US cities...")
    major_cities = fetch_major_cities(limit=150)
    print(f"  Loaded top {len(major_cities)} cities")

    print("Fetching Kalshi weather series...")
    series = fetch_kalshi_weather_series()
    city_codes = extract_city_codes_from_series(series)
    print(f"  Found {len(city_codes)} city codes from {len(series)} series")

    if not city_codes:
        print("  Trying markets endpoint as fallback...")
        city_codes = fetch_weather_cities_from_markets()
        print(f"  Found {len(city_codes)} city codes from markets")

    if not city_codes:
        print("  Kalshi API returned no weather data. Seeding from CODE_TO_NAME + major cities...")
        for code, name in CODE_TO_NAME.items():
            for c in major_cities:
                if (c.get("city") or "").lower() == name.lower():
                    city_codes[code] = name
                    break

    existing = load_existing_coords()
    merged = dict(existing)
    added, skipped = [], []

    for code, name_from_title in city_codes.items():
        if code in merged:
            continue
        resolved = resolve_city(code, name_from_title, major_cities)
        if resolved:
            merged[code] = resolved
            added.append(f"  {code} -> {resolved[2]}")
        else:
            skipped.append(code)

    if added:
        print(f"\nAdded {len(added)} cities:")
        for line in added[:30]:
            print(line)
        if len(added) > 30:
            print(f"  ... and {len(added) - 30} more")
    if skipped:
        print(f"\nSkipped: {', '.join(skipped)}")

    parser_content = PARSER_PATH.read_text()
    new_dict_body = generate_parser_patch(merged)
    old_pat = re.compile(r"(CITY_COORDS: dict\[str, CityInfo\] = \{\n)(.*?)(\n\})", re.DOTALL)
    if not old_pat.search(parser_content):
        print("ERROR: Could not find CITY_COORDS block in parser.py")
        return 1

    PARSER_PATH.write_text(old_pat.sub(rf"\g<1>{new_dict_body}\n\3", parser_content))
    print(f"\nUpdated {PARSER_PATH} with {len(merged)} cities")
    return 0


if __name__ == "__main__":
    sys.exit(main())