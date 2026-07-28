"""Predict indoor temperature in a real house, at a real place, on real dates.

    python scripts/my_house.py --place "Baltimore, MD" --area 1800 --built 1925 \
        --type detached --cooling window --setpoint 74

Everything except the building description comes off the network: the location is
geocoded, the weather is pulled from Open-Meteo, and the current date and local
time come from the weather service rather than being asked for. Give it no dates
and it models the last seven days, so you can hold the prediction against a
thermometer in the room.

The point of this script is to be checkable. It prints a prediction you can walk
into a room and falsify.
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from building import Dwelling, Weather, metrics, simulate  # noqa: E402
from weather import WeatherError, fetch, geocode, local_now  # noqa: E402

SQFT_TO_M2 = 0.092903

TYPES = {
    "detached": ("Single-Family Detached", 1.0),
    "attached": ("Single-Family Attached", 0.6),
    "duplex": ("Multi-Family with 2 - 4 Units", 0.5),
    "apartment": ("Multi-Family with 5+ Units", 0.35),
    "mobile": ("Mobile Home", 1.0),
}

# Rated TOTAL capacity and the share of floor area the equipment serves.
COOLING = {
    "none": (0.0, 0.0),
    "window": (2500.0, 0.35),     # one ~8,500 BTU unit, one room
    "window2": (5000.0, 0.6),     # two units
    "minisplit": (3500.0, 0.6),
    "central": (7000.0, 1.0),
}


def vintage_for(year: int) -> str:
    if year < 1940:
        return "<1940"
    return f"{min(2010, year // 10 * 10)}s"


def leakage_for(year: int) -> float:
    """Blower-door ACH50 typical of the era, absent a real measurement."""
    for cutoff, ach in ((1940, 20.0), (1960, 16.0), (1980, 12.0),
                        (2000, 8.0), (2010, 6.0)):
        if year < cutoff:
            return ach
    return 4.0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Predict indoor temperature for a real house on real weather.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--place", required=True,
                   help='"New Haven, CT", a postal code, or "lat,lon"')
    p.add_argument("--area", type=float, default=1600.0, help="floor area, sq ft")
    p.add_argument("--built", type=int, default=1950, help="year built")
    p.add_argument("--type", choices=sorted(TYPES), default="detached")
    p.add_argument("--cooling", choices=sorted(COOLING), default="none")
    p.add_argument("--setpoint", type=float, default=None,
                   help="thermostat setting in F (default 72.7 F, the RECS 2020 median)")
    p.add_argument("--top-floor", action="store_true",
                   help="living space is directly under the roof")
    p.add_argument("--no-windows", action="store_true",
                   help="windows stay shut (noise, safety, or sealed)")
    p.add_argument("--start", help="YYYY-MM-DD (default: 7 days ago)")
    p.add_argument("--end", help="YYYY-MM-DD (default: today)")
    p.add_argument("--metric", action="store_true", help="area is m2, temps in C")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        place = geocode(args.place)
    except WeatherError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    now = local_now(place)
    print(f"\n  {place.label()}  ({place.latitude:.3f}, {place.longitude:.3f})")
    print(f"  local time there, from the weather service: {now:%A %d %B %Y, %H:%M}")

    end = dt.date.fromisoformat(args.end) if args.end else now.date()
    start = dt.date.fromisoformat(args.start) if args.start else end - dt.timedelta(days=6)

    try:
        series = fetch(place, start, end)
    except WeatherError as exc:
        print(f"error fetching weather: {exc}", file=sys.stderr)
        return 2

    area = args.area if args.metric else args.area * SQFT_TO_M2
    btype, exposure = TYPES[args.type]
    cap, frac = COOLING[args.cooling]
    if args.setpoint is None:
        setpoint = 22.6
    else:
        setpoint = args.setpoint if args.metric else (args.setpoint - 32) * 5 / 9

    dwell = Dwelling(
        floor_area_m2=area, vintage=vintage_for(args.built), building_type=btype,
        ach50=leakage_for(args.built), top_floor=args.top_floor,
        exposed_wall_fraction=exposure, opens_windows=not args.no_windows,
        cooling_capacity_w=cap, conditioned_fraction=frac or 1.0,
        cooling_setpoint_c=setpoint,
    )

    wx = Weather(time_s=series.time_s(), t_out=series.t_out,
                 solar_direct=series.dni, solar_diffuse=series.dhi)
    r = simulate(dwell, wx)

    print(f"  weather: {series.source}, {series.times[0]:%b %d} to {series.times[-1]:%b %d}"
          f"  |  outdoor {series.t_out.min():.1f} to {series.t_out.max():.1f} C")
    print(f"  modelled as: {btype}, {vintage_for(args.built)}, {area:.0f} m2, "
          f"ACH50 {dwell.ach50:.0f}, cooling {args.cooling} @ {setpoint:.1f} C setpoint")
    print(f"  numerics: {r['substeps']} substeps/hour, fastest node tau {r['tau_min_s']:.0f} s")

    warm = min(2 * 86400.0, wx.time_s[-1] / 3)
    m = metrics(r["t_home"], wx.time_s, threshold=26.0, warmup_s=warm)

    print("\n  PREDICTED INDOOR TEMPERATURE  (first day discarded as spin-up)")
    if cap > 0 and frac < 1.0:
        print(f"    cooled room          peak {metrics(r['t_cond'], wx.time_s, warmup_s=warm)['peak']:.1f} C")
        print(f"    rest of the home     peak {metrics(r['t_free'], wx.time_s, warmup_s=warm)['peak']:.1f} C")
    print(f"    whole-home average   peak {m['peak']:.1f} C   overnight low {m['night_min']:.1f} C")
    if cap > 0:
        short = int(r["ac_short"].sum())
        print(f"    equipment could not hold setpoint for {short} of {len(r['ac_short'])} hours")

    print(f"\n  {'day':<10}{'outdoor hi':>11}{'indoor hi':>11}{'indoor lo':>11}")
    live = wx.time_s >= wx.time_s[0] + warm
    days = np.array([(t.date() - series.times[0].date()).days for t in series.times])
    for d in sorted(set(days[live])):
        sel = live & (days == d)
        when = series.times[int(np.argmax(sel))]
        print(f"  {when:%a %b %d}{series.t_out[sel].max():>10.1f}C"
              f"{r['t_home'][sel].max():>10.1f}C{r['t_home'][sel].min():>10.1f}C")

    hottest = series.times[int(np.argmax(np.where(live, r["t_home"], -np.inf)))]
    print(f"\n  Hottest predicted moment indoors: {hottest:%A %H:%M}."
          f"\n  Put a thermometer in the room then and tell the model it is wrong.")
    print("\n  Accuracy: validated against 36 measured NYC apartments (Quinn et al. 2017)")
    print("  at RMSE 2.8 K, running COOLER than reality. See docs/validation.md.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
