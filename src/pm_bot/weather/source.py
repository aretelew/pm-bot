"""WeatherDataSource: DataSource plugin for SignalBasedStrategy.

Parses Kalshi weather tickers, fetches forecasts from multiple providers,
and converts them to probability estimates using a normal CDF model.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import mean

from scipy.stats import norm

from pm_bot.api.models import Market
from pm_bot.strategies.signal import DataSource, ExternalEstimate
from pm_bot.utils.logging import get_logger
from pm_bot.weather.climatology import get_monthly_rain_normal, get_monthly_snow_normal
from pm_bot.weather.parser import WeatherMarketInfo, WeatherMetric, parse_weather_ticker
from pm_bot.weather.providers import WeatherForecast, WeatherProvider

log = get_logger("weather.source")

_DEFAULT_FORECAST_STD = 3.5  # °F fallback uncertainty
_MAX_FORECAST_DAYS = 7  # beyond this we use climatology
_MONTHLY_MAX_CONFIDENCE = 0.8  # cap confidence for monthly markets


class WeatherDataSource(DataSource):
    """Fetch weather forecasts and convert to market probability estimates.

    Works as a plugin for SignalBasedStrategy: for each market it receives,
    it parses the ticker, fetches forecasts from all configured providers,
    averages them, then uses a normal CDF to estimate the probability that
    the weather threshold is breached.
    """

    name = "weather"

    def __init__(self, providers: list[WeatherProvider]) -> None:
        self._providers = providers

    async def get_estimate(self, market: Market) -> ExternalEstimate | None:
        info = parse_weather_ticker(market.ticker)
        if info is None:
            return None

        if info.is_precipitation:
            return await self._get_precip_estimate(info)

        days_out = (info.target_date - date.today()).days
        if days_out < 0:
            return None

        forecasts = await self._fetch_all_for_date(info.city, info.target_date)
        if not forecasts:
            log.debug("no_forecasts", ticker=info.ticker)
            return None

        probability = self._estimate_probability(info, forecasts)
        confidence = self._compute_confidence(forecasts, days_out)

        log.info(
            "weather_estimate",
            ticker=info.ticker,
            prob=round(probability, 4),
            confidence=round(confidence, 3),
            sources=len(forecasts),
            days_out=days_out,
        )
        return ExternalEstimate(
            source=self.name,
            probability=probability,
            confidence=confidence,
        )

    async def _get_precip_estimate(
        self, info: WeatherMarketInfo
    ) -> ExternalEstimate | None:
        """Estimate probability for monthly precipitation markets."""
        today = date.today()
        end_date = info.target_end_date
        if end_date is None or end_date < today:
            return None

        if info.metric == WeatherMetric.MONTHLY_SNOW:
            probability = await self._estimate_monthly_snow(info, today)
        else:
            probability = await self._estimate_monthly_rain(info, today)

        confidence = self._compute_precip_confidence(info, today)

        log.info(
            "precip_estimate",
            ticker=info.ticker,
            metric=info.metric.value,
            prob=round(probability, 4),
            confidence=round(confidence, 3),
            threshold=info.threshold_inches,
        )
        return ExternalEstimate(
            source=self.name,
            probability=probability,
            confidence=confidence,
        )

    async def _estimate_monthly_snow(
        self, info: WeatherMarketInfo, today: date
    ) -> float:
        """Estimate P(monthly snow > threshold) using forecasts + climatology."""
        start = max(info.target_date, today)
        end = info.target_end_date
        assert end is not None

        total_days = (end - start).days + 1
        forecast_days = min(total_days, _MAX_FORECAST_DAYS)
        climo_days = total_days - forecast_days

        # Accumulate snow from forecasts for the forecastable window
        forecast_snow = 0.0
        forecast_count = 0
        for i in range(forecast_days):
            d = start + timedelta(days=i)
            daily = await self._fetch_all_for_date(info.city, d)
            if daily:
                day_snow = self._aggregate_daily_snow(daily)
                forecast_snow += day_snow
                forecast_count += 1

        # Climatology fallback for remaining days
        climo_snow = 0.0
        if climo_days > 0:
            monthly_normal = get_monthly_snow_normal(
                info.city_code, info.target_date.month
            )
            # Pro-rate the monthly normal by the fraction of days remaining
            days_in_month = (end - info.target_date).days + 1
            climo_snow = monthly_normal * (climo_days / days_in_month)

        total_snow_estimate = forecast_snow + climo_snow

        # Uncertainty: larger when more days use climatology
        forecast_frac = forecast_count / max(total_days, 1)
        base_std = max(total_snow_estimate * 0.4, 1.0)
        adjusted_std = base_std * (1.0 + (1.0 - forecast_frac) * 0.5)

        # P(total > threshold) via normal CDF
        prob = 1.0 - norm.cdf(
            info.threshold_inches, loc=total_snow_estimate, scale=adjusted_std
        )
        return float(max(0.01, min(0.99, prob)))

    async def _estimate_monthly_rain(
        self, info: WeatherMarketInfo, today: date
    ) -> float:
        """Estimate P(monthly rain > threshold) or P(any rain)."""
        start = max(info.target_date, today)
        end = info.target_end_date
        assert end is not None

        is_binary = info.threshold_inches <= 0.01  # "any rain" market
        total_days = (end - start).days + 1
        forecast_days = min(total_days, _MAX_FORECAST_DAYS)
        climo_days = total_days - forecast_days

        if is_binary:
            # Product-of-complements: P(no rain all month)
            prob_no_rain = 1.0

            # Forecast window: use daily precip probability
            for i in range(forecast_days):
                d = start + timedelta(days=i)
                daily = await self._fetch_all_for_date(info.city, d)
                if daily:
                    day_prob = mean(f.precip_prob for f in daily)
                    prob_no_rain *= (1.0 - day_prob)
                else:
                    # Fallback: use climatology-derived daily rain probability
                    prob_no_rain *= self._climo_daily_dry_prob(info)

            # Climatology days
            for _ in range(climo_days):
                prob_no_rain *= self._climo_daily_dry_prob(info)

            prob = 1.0 - prob_no_rain
        else:
            # Threshold-based rain market: aggregate and use normal CDF
            forecast_rain = 0.0
            forecast_count = 0
            for i in range(forecast_days):
                d = start + timedelta(days=i)
                daily = await self._fetch_all_for_date(info.city, d)
                if daily:
                    forecast_rain += mean(f.precip_inches for f in daily)
                    forecast_count += 1

            # Climatology fallback
            climo_rain = 0.0
            if climo_days > 0:
                monthly_normal = get_monthly_rain_normal(
                    info.city_code, info.target_date.month
                )
                days_in_month = (end - info.target_date).days + 1
                climo_rain = monthly_normal * (climo_days / days_in_month)

            total_rain_estimate = forecast_rain + climo_rain
            forecast_frac = forecast_count / max(total_days, 1)
            base_std = max(total_rain_estimate * 0.35, 0.5)
            adjusted_std = base_std * (1.0 + (1.0 - forecast_frac) * 0.5)

            prob = 1.0 - norm.cdf(
                info.threshold_inches, loc=total_rain_estimate, scale=adjusted_std
            )

        return float(max(0.01, min(0.99, prob)))

    def _climo_daily_dry_prob(self, info: WeatherMarketInfo) -> float:
        """Estimate P(no rain on a single day) from climatology."""
        monthly_rain = get_monthly_rain_normal(
            info.city_code, info.target_date.month
        )
        end = info.target_end_date
        assert end is not None
        days_in_month = (end - info.target_date).days + 1
        # Rough: assume rain days ~ monthly_rain / 0.3 inches per rain day
        avg_rain_days = min(monthly_rain / 0.3, days_in_month)
        daily_rain_prob = avg_rain_days / days_in_month
        return 1.0 - daily_rain_prob

    def _compute_precip_confidence(
        self, info: WeatherMarketInfo, today: date
    ) -> float:
        """Confidence for monthly precipitation markets, capped at 0.8."""
        end = info.target_end_date
        assert end is not None
        total_days = (end - max(info.target_date, today)).days + 1
        forecast_days = min(total_days, _MAX_FORECAST_DAYS)
        forecast_frac = forecast_days / max(total_days, 1)

        source_factor = min(len(self._providers) / 3.0, 1.0)
        coverage_factor = 0.4 + 0.6 * forecast_frac

        conf = source_factor * coverage_factor
        return max(0.1, min(_MONTHLY_MAX_CONFIDENCE, conf))

    @staticmethod
    def _aggregate_daily_snow(forecasts: list[WeatherForecast]) -> float:
        """Aggregate daily snow from forecasts, deriving from precip if needed."""
        snow_values = [f.snow_inches for f in forecasts if f.snow_inches > 0]
        if snow_values:
            return mean(snow_values)

        # Derive snow from liquid precip + temperature (snow:liquid ratio)
        derived = []
        for f in forecasts:
            if f.precip_inches > 0 and f.temp_high_f <= 36:
                avg_temp = (f.temp_high_f + f.temp_low_f) / 2.0
                if avg_temp <= 20:
                    ratio = 15.0
                elif avg_temp <= 28:
                    ratio = 12.0
                elif avg_temp <= 34:
                    ratio = 10.0
                else:
                    ratio = 8.0
                derived.append(f.precip_inches * ratio)
        return mean(derived) if derived else 0.0

    async def _fetch_all_for_date(
        self, city: "CityInfo", target_date: date
    ) -> list[WeatherForecast]:
        """Fetch forecasts from all providers for a single date."""
        from pm_bot.weather.parser import CityInfo as _CI  # noqa: F811

        results: list[WeatherForecast] = []
        for provider in self._providers:
            try:
                forecast = await provider.fetch_forecast(city, target_date)
                if forecast is not None:
                    results.append(forecast)
            except Exception:
                log.exception(
                    "provider_error", provider=provider.name, date=target_date.isoformat()
                )
        return results

    @staticmethod
    def _estimate_probability(
        info: WeatherMarketInfo, forecasts: list[WeatherForecast]
    ) -> float:
        """Convert averaged forecast into P(threshold breached) via normal CDF."""
        if info.metric == WeatherMetric.HIGH_TEMP:
            temps = [f.temp_high_f for f in forecasts]
        else:
            temps = [f.temp_low_f for f in forecasts]

        forecast_mean = mean(temps)
        stds = [f.forecast_std for f in forecasts if f.forecast_std > 0]
        forecast_std = mean(stds) if stds else _DEFAULT_FORECAST_STD

        threshold = float(info.threshold_f)

        if info.metric == WeatherMetric.HIGH_TEMP:
            # P(actual high > threshold)
            prob = 1.0 - norm.cdf(threshold, loc=forecast_mean, scale=forecast_std)
        else:
            # P(actual low < threshold)
            prob = norm.cdf(threshold, loc=forecast_mean, scale=forecast_std)

        return float(max(0.01, min(0.99, prob)))

    @staticmethod
    def _compute_confidence(forecasts: list[WeatherForecast], days_out: int) -> float:
        """Higher confidence when more sources agree and event is closer."""
        source_factor = min(len(forecasts) / 3.0, 1.0)

        if days_out <= 1:
            time_factor = 1.0
        elif days_out <= 3:
            time_factor = 0.85
        elif days_out <= 7:
            time_factor = 0.6
        else:
            time_factor = 0.35

        stds = [f.forecast_std for f in forecasts if f.forecast_std > 0]
        avg_std = mean(stds) if stds else _DEFAULT_FORECAST_STD
        precision_factor = max(0.3, 1.0 - (avg_std - 2.0) / 10.0)

        return max(0.1, min(1.0, source_factor * time_factor * precision_factor))
