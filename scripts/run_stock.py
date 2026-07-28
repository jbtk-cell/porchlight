"""Run the thermal model across Connecticut's housing stock and aggregate by city.

Every dwelling in the ResStock Connecticut sample is turned into a Dwelling and
simulated through the same 2018 heat wave. Weather is held CONSTANT across all
cities deliberately: the map is then a picture of housing-driven risk, not of
which city happened to be hotter that week.

Outputs web/data.json for the front end.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from building import Dwelling, Weather, metrics, simulate  # noqa: E402

warnings.filterwarnings("ignore")

SQFT_TO_M2 = 0.092903
THRESHOLD = 26.0      # degree-hour base
DANGER_PEAK = 32.0    # sustained indoor peak treated as heat-stress risk
WARMUP_DAYS = 4

EXPOSED_WALL = {
    "Single-Family Detached": 1.0,
    "Mobile Home": 1.0,
    "Single-Family Attached": 0.6,
    "Multi-Family with 2 - 4 Units": 0.5,
    "Multi-Family with 5+ Units": 0.35,
}


def floor_area_m2(binned: str) -> float:
    """ResStock reports floor area as a range string like '1000-1499'."""
    try:
        lo, hi = str(binned).replace("+", "").split("-")
        return (float(lo) + float(hi)) / 2 * SQFT_TO_M2
    except Exception:
        return 120.0 * SQFT_TO_M2 * 10  # fallback ~112 m2


def window_fraction(spec: str) -> float:
    """'F9 B9 L9 R9' is window-to-wall percent per facade; convert to window/floor."""
    try:
        vals = [float(tok[1:]) for tok in str(spec).split()]
        wwr = np.mean(vals) / 100.0
    except Exception:
        wwr = 0.12
    # window/floor ~ wwr * (wall area / floor area); wall/floor is ~1.1 for a
    # typical one-storey plan at 2.6 m height.
    return float(np.clip(wwr * 1.1, 0.04, 0.30))


def is_top_floor(row) -> bool:
    level = str(row.get("in.geometry_building_level_mf", "nan"))
    if level in ("Top", "Bottom", "Middle"):
        return level == "Top"
    return True  # single-family and mobile homes all have roof directly above


def cooling(row) -> tuple[float, float]:
    """(watts delivered, fraction of floor area served)."""
    kind = str(row.get("in.hvac_cooling_type", "nan"))
    if kind == "Room AC":
        return 2500.0, 0.35          # one window unit, one room
    if kind in ("Central AC", "Ducted Heat Pump"):
        return 7000.0, 1.0           # whole-dwelling
    if kind == "Non-Ducted Heat Pump":
        return 3500.0, 0.6           # mini-split, a couple of zones
    return 0.0, 0.0


def build(row) -> Dwelling:
    btype = str(row.get("in.geometry_building_type_recs", "Single-Family Detached"))
    watts, frac = cooling(row)
    ach50 = row.get("in.air_leakage_to_outside_ach50")
    return Dwelling(
        floor_area_m2=floor_area_m2(row.get("in.geometry_floor_area")),
        vintage=str(row.get("in.vintage", "<1940")),
        building_type=btype,
        ach50=float(ach50) if pd.notna(ach50) else 15.0,
        window_to_floor=window_fraction(row.get("in.window_areas")),
        top_floor=is_top_floor(row),
        exposed_wall_fraction=EXPOSED_WALL.get(btype, 0.7),
        room_ac_watts=watts,
        room_ac_fraction=frac if frac else 0.35,
    )


def load_weather(cache: pathlib.Path) -> tuple[Weather, np.ndarray, dict]:
    """One representative Connecticut heat-wave series, applied to every city."""
    files = sorted((cache / "nh").glob("*.parquet")) or sorted((cache / "ts").glob("*.parquet"))
    cols = {
        "timestamp": "ts",
        "out.outdoor_air_drybulb_temp..c": "tout",
        "out.weather.direct_normal_solar_radiation..watt_per_m2": "dni",
        "out.weather.diffuse_solar_radiation..watt_per_m2": "dhi",
    }
    d = pd.read_parquet(files[0], columns=list(cols)).rename(columns=cols)
    d["ts"] = pd.to_datetime(d["ts"])
    d = d.set_index("ts").loc["2018-06-25":"2018-07-06"]
    t = (d.index - d.index[0]).total_seconds().values
    wx = Weather(time_s=t, t_out=d.tout.values,
                 solar_direct=d.dni.values, solar_diffuse=d.dhi.values)

    wave = d.loc["2018-06-29":"2018-07-06"]
    summary = {
        "outdoor_peak": round(float(wave.tout.max()), 1),
        "outdoor_night_min": round(float(wave.between_time("00:00", "06:00").tout.min()), 1),
        "start": str(d.index[0].date()),
        "end": str(d.index[-1].date()),
        # Hourly outdoor series for the front-end chart.
        "series": [round(float(v), 2) for v in d.tout.values[::4]],
        "hours": len(d.tout.values[::4]),
    }
    return wx, t, summary


def main() -> None:
    cache = pathlib.Path(os.environ.get("CLAUDE_JOB_DIR", "/tmp")) / "tmp"
    meta = pd.read_csv("data/CT_upgrade0.csv.gz", low_memory=False)
    meta = meta.rename(columns={meta.columns[0]: "bldg_id"}).dropna(subset=["bldg_id"])

    wx, t, wsummary = load_weather(cache)
    warm = WARMUP_DAYS * 86400
    print(f"weather {wsummary['start']} to {wsummary['end']}: "
          f"peak {wsummary['outdoor_peak']} C, overnight min {wsummary['outdoor_night_min']} C")
    print(f"simulating {len(meta):,} Connecticut dwellings...")

    rows = []
    for i, (_, r) in enumerate(meta.iterrows(), 1):
        dw = build(r)
        m = metrics(simulate(dw, wx)["t_air"], t, threshold=THRESHOLD, warmup_s=warm)
        rows.append({
            "city": str(r.get("in.city")),
            "county": str(r.get("in.county_name")),
            "weight": float(r.get("weight", 1.0)),
            "vintage": str(r.get("in.vintage")),
            "btype": str(r.get("in.geometry_building_type_recs")),
            "tenure": str(r.get("in.tenure")),
            "cooling": str(r.get("in.hvac_cooling_type")),
            "top_floor": bool(dw.top_floor),
            "peak": m["peak"], "night_min": m["night_min"],
            "hours_over": m["hours_over"], "degree_hours": m["degree_hours_over"],
        })
        if i % 1000 == 0:
            print(f"  {i:,}/{len(meta):,}")

    df = pd.DataFrame(rows)
    # New England heat waves have cool nights (17.8 C here), so an openable
    # window genuinely rescues the overnight minimum. The risk in this climate
    # is therefore concentrated in the daytime peak, not the overnight low.
    df["dangerous"] = df.peak > DANGER_PEAK
    df["cooling"] = df.cooling.replace("nan", "None")
    df.to_csv("results/stock_results.csv", index=False)

    def agg(g: pd.DataFrame) -> dict:
        w = g.weight
        return {
            "n": int(len(g)),
            "dwellings": int(round(w.sum())),
            "pct_dangerous": round(float((w * g.dangerous).sum() / w.sum() * 100), 1),
            "night_min": round(float((w * g.night_min).sum() / w.sum()), 1),
            "peak": round(float((w * g.peak).sum() / w.sum()), 1),
            "degree_hours": round(float((w * g.degree_hours).sum() / w.sum()), 0),
            "pct_no_cooling": round(float((w * g.cooling.eq("None")).sum() / w.sum() * 100), 1),
            "pct_room_ac_only": round(float((w * g.cooling.eq("Room AC")).sum() / w.sum() * 100), 1),
            "pct_pre1940": round(float((w * g.vintage.eq("<1940")).sum() / w.sum() * 100), 1),
            "pct_renter": round(float((w * g.tenure.eq("Renter")).sum() / w.sum() * 100), 1),
        }

    cities = {c: agg(g) for c, g in df[df.city.str.startswith("CT, ")].groupby("city")}
    cities = {c.replace("CT, ", ""): v for c, v in cities.items()}

    breakdowns = {
        "by_vintage": {k: agg(g) for k, g in df.groupby("vintage")},
        "by_type": {k: agg(g) for k, g in df.groupby("btype")},
        "by_cooling": {k: agg(g) for k, g in df.groupby("cooling")},
        "by_tenure": {k: agg(g) for k, g in df.groupby("tenure")},
        "by_floor": {("Top floor" if k else "Not top floor"): agg(g)
                     for k, g in df.groupby("top_floor")},
    }

    out = {
        "weather": wsummary,
        "threshold": THRESHOLD,
        "statewide": agg(df),
        "cities": cities,
        "breakdowns": breakdowns,
        "new_haven": agg(df[df.city == "CT, New Haven"]),
    }
    pathlib.Path("web").mkdir(exist_ok=True)
    json.dump(out, open("web/data.json", "w"), indent=1)

    json.dump({"danger_peak": DANGER_PEAK}, open("web/meta.json", "w"))
    print(f"\nstatewide: {out['statewide']['pct_dangerous']}% of "
          f"{out['statewide']['dwellings']:,} dwellings peak above {DANGER_PEAK} C")
    print(f"New Haven: {out['new_haven']['pct_dangerous']}% of "
          f"{out['new_haven']['dwellings']:,} dwellings")
    print("\nby cooling equipment:")
    for k, v in out["breakdowns"]["by_cooling"].items():
        print(f"  {k:<22}{v['dwellings']:>10,}  peak {v['peak']:>5.1f} C  {v['pct_dangerous']:>5.1f}% over {DANGER_PEAK} C")
    print("\ntop cities by share of dangerous dwellings:")
    for c, v in sorted(cities.items(), key=lambda kv: -kv[1]["pct_dangerous"])[:10]:
        print(f"  {c:<22}{v['pct_dangerous']:>6.1f}%  ({v['dwellings']:,} dwellings, "
              f"{v['pct_pre1940']:.0f}% pre-1940, {v['pct_no_cooling']:.0f}% no cooling)")


if __name__ == "__main__":
    main()
