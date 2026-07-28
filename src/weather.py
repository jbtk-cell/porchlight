"""Fetch real weather for any location and date from Open-Meteo.

Open-Meteo is used because it needs no API key, covers the whole world, reaches
back to 1940, and returns the exact three forcing variables the thermal model
needs: dry-bulb air temperature, direct normal irradiance, and diffuse
horizontal irradiance. It also returns the location's timezone and the current
local time, so the caller never has to ask the user what day it is.

Two endpoints, chosen automatically:
  archive-api  -- ERA5 reanalysis, 1940 to about 3 days ago
  api (forecast) -- recent past (past_days) plus forecast

Network access here goes through `curl`, not urllib, because urllib is blocked
in some sandboxes.
"""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
from dataclasses import dataclass

import numpy as np

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

HOURLY_VARS = "temperature_2m,relative_humidity_2m,direct_normal_irradiance,diffuse_radiation"

# The archive is ERA5-based and lags real time. Anything more recent than this
# many days goes to the forecast endpoint instead.
ARCHIVE_LAG_DAYS = 5


class WeatherError(RuntimeError):
    pass


def _get(url: str, params: dict) -> dict:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    full = f"{url}?{query}"
    try:
        out = subprocess.run(
            ["curl", "-sS", "-f", "--max-time", "40", full],
            capture_output=True, text=True, check=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise WeatherError(f"request failed: {exc.stderr.strip() or exc}") from exc
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise WeatherError(f"bad JSON from {url}: {out[:200]}") from exc


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

@dataclass
class Place:
    name: str
    latitude: float
    longitude: float
    timezone: str
    admin: str = ""
    country: str = ""

    def label(self) -> str:
        bits = [self.name] + [b for b in (self.admin, self.country) if b]
        return ", ".join(bits)


def geocode(query: str) -> Place:
    """Turn a place name into coordinates. Accepts 'New Haven', 'Baltimore, MD',
    a postal code, or a raw 'lat,lon' pair."""
    q = query.strip()

    # Accept explicit coordinates so the user can bypass geocoding entirely.
    if "," in q:
        parts = [p.strip() for p in q.split(",")]
        if len(parts) == 2:
            try:
                lat, lon = float(parts[0]), float(parts[1])
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    return Place(f"{lat:.4f}, {lon:.4f}", lat, lon, "auto")
            except ValueError:
                pass  # not coordinates, fall through to the geocoder

    data = _get(GEOCODE_URL, {"name": q.split(",")[0].replace(" ", "+"),
                              "count": 10, "language": "en", "format": "json"})
    results = data.get("results") or []
    if not results:
        raise WeatherError(f"could not find a place called {query!r}")

    # If the caller wrote "City, ST" or "City, Country", prefer a match on the
    # second part rather than blindly taking the geocoder's first hit.
    if "," in q:
        hint = q.split(",", 1)[1].strip().lower()
        for r in results:
            fields = [str(r.get("admin1", "")), str(r.get("country", "")),
                      str(r.get("country_code", ""))]
            if any(hint == f.lower() or hint == f.lower()[:len(hint)] for f in fields if f):
                results = [r]
                break

    r = results[0]
    return Place(
        name=r["name"], latitude=r["latitude"], longitude=r["longitude"],
        timezone=r.get("timezone", "auto"),
        admin=str(r.get("admin1") or ""), country=str(r.get("country_code") or ""),
    )


def local_now(place: Place) -> _dt.datetime:
    """Current local time at the location, taken from the weather service.

    Read from the network rather than the machine clock so the answer is right
    for the place being modelled, not the place the code is running.
    """
    data = _get(FORECAST_URL, {
        "latitude": round(place.latitude, 4), "longitude": round(place.longitude, 4),
        "current": "temperature_2m", "timezone": "auto", "forecast_days": 1,
    })
    return _dt.datetime.fromisoformat(data["current"]["time"])


# ---------------------------------------------------------------------------
# Weather series
# ---------------------------------------------------------------------------

@dataclass
class Series:
    """An hourly weather series ready to drive the thermal model."""
    place: Place
    times: list          # list[datetime], local time at the place
    t_out: np.ndarray    # C
    dni: np.ndarray      # W/m2 direct normal
    dhi: np.ndarray      # W/m2 diffuse horizontal
    rh: np.ndarray       # %
    source: str

    def time_s(self) -> np.ndarray:
        t0 = self.times[0]
        return np.array([(t - t0).total_seconds() for t in self.times], dtype=float)

    def summary(self) -> str:
        return (f"{self.place.label()} | {self.times[0]:%Y-%m-%d %H:%M} to "
                f"{self.times[-1]:%Y-%m-%d %H:%M} ({len(self.times)} h) | "
                f"outdoor {self.t_out.min():.1f}-{self.t_out.max():.1f} C | {self.source}")


def _needs_forecast(end: _dt.date) -> bool:
    return (_dt.date.today() - end).days < ARCHIVE_LAG_DAYS


def fetch(place: Place, start: _dt.date, end: _dt.date) -> Series:
    """Hourly weather for [start, end] inclusive, local time."""
    if end < start:
        raise WeatherError("end date is before start date")

    base = {"latitude": round(place.latitude, 4), "longitude": round(place.longitude, 4),
            "hourly": HOURLY_VARS, "timezone": "auto"}

    if _needs_forecast(end):
        past = max(0, min(92, (_dt.date.today() - start).days + 1))
        ahead = max(1, min(16, (end - _dt.date.today()).days + 1))
        data = _get(FORECAST_URL, {**base, "past_days": past, "forecast_days": ahead})
        source = "Open-Meteo forecast/recent"
    else:
        data = _get(ARCHIVE_URL, {**base, "start_date": start.isoformat(),
                                  "end_date": end.isoformat()})
        source = "Open-Meteo ERA5 archive"

    h = data.get("hourly")
    if not h:
        raise WeatherError(f"no hourly data returned: {str(data)[:200]}")

    times = [_dt.datetime.fromisoformat(t) for t in h["time"]]

    def col(key: str) -> np.ndarray:
        vals = h.get(key) or [None] * len(times)
        arr = np.array([np.nan if v is None else float(v) for v in vals])
        # Short gaps happen at the archive/forecast seam; interpolate rather
        # than propagate NaN into the integrator.
        if np.isnan(arr).any():
            idx = np.arange(len(arr))
            good = ~np.isnan(arr)
            if not good.any():
                raise WeatherError(f"{key} is entirely missing for this location/date")
            arr = np.interp(idx, idx[good], arr[good])
        return arr

    t_out, rh = col("temperature_2m"), col("relative_humidity_2m")
    dni, dhi = col("direct_normal_irradiance"), col("diffuse_radiation")

    # Trim to the requested window (the forecast endpoint returns whole days).
    keep = [i for i, t in enumerate(times) if start <= t.date() <= end]
    if not keep:
        raise WeatherError("requested dates fell outside the returned data")
    s, e = keep[0], keep[-1] + 1

    return Series(place=place, times=times[s:e], t_out=t_out[s:e], dni=dni[s:e],
                  dhi=dhi[s:e], rh=rh[s:e], source=source)


def fetch_recent(place: Place, days: int = 7) -> Series:
    """The last `days` days up to today -- the usual case for checking the model
    against a thermometer in your own home."""
    today = _dt.date.today()
    return fetch(place, today - _dt.timedelta(days=days - 1), today)


def hottest_window(series: Series, days: int = 4) -> tuple[int, int]:
    """Index range of the hottest `days`-long stretch, for picking the heat wave
    out of a longer record."""
    span = days * 24
    if len(series.t_out) <= span:
        return 0, len(series.t_out)
    means = np.convolve(series.t_out, np.ones(span) / span, mode="valid")
    i = int(np.argmax(means))
    return i, i + span
