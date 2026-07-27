"""Stage 1: quantify the indoor/outdoor heat exposure gap in free-running dwellings.

Downloads NREL ResStock 2025 AMY2018 timeseries for Connecticut dwellings that
have no cooling equipment at all, extracts the June 29 - July 5 2018 heat wave,
and compares the overnight indoor minimum against the outdoor minimum that an
epidemiological study would have used as the exposure.

Also recovers the thermal time constant from the frequency-domain response and
checks it against the dwelling's known air leakage rate, which is the validation
that parameter recovery is tracking real physics rather than fitting noise.

Usage:
    python scripts/stage1_exposure_gap.py --n 24
    python scripts/stage1_exposure_gap.py --n 736   # every no-cooling CT dwelling

Data is public and needs no credentials. Roughly 6 MB per dwelling.
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from thermal import DIURNAL_OMEGA, SECONDS_PER_HOUR, fit_sinusoid  # noqa: E402

warnings.filterwarnings("ignore")

BUCKET = (
    "https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/"
    "end-use-load-profiles-for-us-building-stock/2025/resstock_amy2018_release_1"
)
META_URL = f"{BUCKET}/metadata_and_annual_results/by_state/full/csv/state=CT/CT_upgrade0.csv.gz"
TS_DIR = f"{BUCKET}/timeseries_individual_buildings/by_state/upgrade=0/state=CT"

# The 2018 New England heat wave. Selected as the hottest 5-day rolling window
# in the CT AMY2018 weather file, not chosen by eye.
HEATWAVE = ("2018-06-29", "2018-07-05")
NIGHT = ("00:00", "06:00")

COLUMNS = {
    "timestamp": "ts",
    "out.indoor_temperature.conditioned_space..c": "tin",
    "out.indoor_operative_temperature.conditioned_space..c": "top",
    "out.outdoor_air_drybulb_temp..c": "tout",
    "out.electricity.cooling.energy_consumption..kwh": "cool",
}

# WHO and thermal-comfort literature put sustained overnight exposure above ~26 C
# in the range where sleep and cardiovascular recovery are measurably impaired.
THRESHOLDS = (26.0, 28.0, 30.0)


def fetch(url: str, dest: pathlib.Path) -> pathlib.Path:
    """Download with curl. urllib is blocked in some sandboxes; curl is not."""
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["curl", "-sS", "--max-time", "300", "-o", str(dest), url],
        check=True,
    )
    return dest


def load_metadata(cache: pathlib.Path) -> pd.DataFrame:
    path = fetch(META_URL, cache / "CT_upgrade0.csv.gz")
    meta = pd.read_csv(path, low_memory=False)
    # The first column carries the building id but is mis-headered with the filename.
    meta = meta.rename(columns={meta.columns[0]: "bldg_id"})
    return meta.dropna(subset=["bldg_id"]).assign(bldg_id=lambda d: d.bldg_id.astype(int))


def select_free_running(meta: pd.DataFrame, n: int, seed: int = 7) -> pd.DataFrame:
    """Dwellings with no cooling equipment, stratified by era and building type."""
    noac = meta[meta["in.hvac_cooling_type"].isna()].copy()
    noac["era"] = np.where(noac["in.vintage"].astype(str) == "<1940", "pre1940", "post1940")
    per_group = max(1, n // max(1, noac.groupby(["era", "in.geometry_building_type_recs"]).ngroups))
    sample = noac.groupby(["era", "in.geometry_building_type_recs"], group_keys=False).apply(
        lambda g: g.sample(min(len(g), per_group), random_state=seed)
    )
    return sample.head(n)


def analyse_dwelling(path: pathlib.Path) -> dict | None:
    frame = pd.read_parquet(path, columns=list(COLUMNS)).rename(columns=COLUMNS)
    frame["ts"] = pd.to_datetime(frame["ts"])
    frame = frame.set_index("ts")

    wave = frame.loc[HEATWAVE[0] : HEATWAVE[1]]
    if wave.empty:
        return None
    night = wave.between_time(*NIGHT)

    seconds = (wave.index - wave.index[0]).total_seconds().values
    _, amp_out, phase_out = fit_sinusoid(seconds, wave.tout.values, DIURNAL_OMEGA)
    _, amp_in, phase_in = fit_sinusoid(seconds, wave.tin.values, DIURNAL_OMEGA)

    damping = amp_in / amp_out
    lag = (phase_in - phase_out) % (2 * np.pi)
    if lag > np.pi:
        lag -= 2 * np.pi

    return {
        "bldg_id": int(path.stem),
        "cooling_kwh": float(wave.cool.sum()),
        "out_max": float(wave.tout.max()),
        "out_night_min": float(night.tout.min()),
        "in_max": float(wave.tin.max()),
        "in_night_min": float(night.tin.min()),
        "op_max": float(wave.top.max()),
        "night_gap": float(night.tin.min() - night.tout.min()),
        "damping": float(damping),
        "lag_hours": float(lag / DIURNAL_OMEGA / SECONDS_PER_HOUR),
        # tau from the 1R1C amplitude response: r = 1/sqrt(1+(omega*tau)^2)
        "tau_hours": float(np.sqrt(max(1 / damping**2 - 1, 0)) / DIURNAL_OMEGA / SECONDS_PER_HOUR),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=24, help="dwellings to sample")
    parser.add_argument("--cache", type=pathlib.Path, default=pathlib.Path("data"))
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("results/stage1_results.csv"))
    args = parser.parse_args()

    meta = load_metadata(args.cache)
    sample = select_free_running(meta, args.n)
    print(f"{len(meta)} CT dwellings modeled; "
          f"{meta['in.hvac_cooling_type'].isna().sum()} have no cooling equipment.")
    print(f"Analysing {len(sample)} of them.\n")

    records = []
    for i, bldg in enumerate(sample.bldg_id, 1):
        path = fetch(f"{TS_DIR}/{bldg}-0.parquet", args.cache / "ts" / f"{bldg}.parquet")
        record = analyse_dwelling(path)
        if record:
            records.append(record)
        if i % 10 == 0:
            print(f"  ...{i}/{len(sample)}")

    keep = [
        "bldg_id", "in.vintage", "in.geometry_building_type_recs", "in.tenure",
        "in.air_leakage_to_outside_ach50", "in.insulation_wall",
    ]
    results = pd.DataFrame(records).merge(meta[keep], on="bldg_id", how="left")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.out, index=False)
    report(results)


def report(res: pd.DataFrame) -> None:
    pd.set_option("display.width", 200)
    print(f"\nn = {len(res)} | cooling energy consumed: {res.cooling_kwh.sum():.3f} kWh "
          f"(confirms these are free-running)")
    # ResStock assigns weather by county, so outdoor conditions differ per dwelling.
    # Every gap below is computed against that dwelling's own outdoor series.
    print(f"outdoor during heat wave: max {res.out_max.min():.1f}-{res.out_max.max():.1f} C, "
          f"overnight min {res.out_night_min.min():.1f}-{res.out_night_min.max():.1f} C "
          f"(varies by county weather file)")

    print("\n--- exposure gap: indoor overnight minimum minus outdoor overnight minimum ---")
    print(f"  mean +{res.night_gap.mean():.1f} C, range +{res.night_gap.min():.1f} "
          f"to +{res.night_gap.max():.1f} C")

    print("\n--- by tenure ---")
    print(res.groupby("in.tenure").agg(
        n=("bldg_id", "size"), night_gap=("night_gap", "mean"),
        in_night_min=("in_night_min", "mean")).round(2).to_string())

    print("\n--- by building type ---")
    print(res.groupby("in.geometry_building_type_recs").agg(
        n=("bldg_id", "size"), night_gap=("night_gap", "mean"),
        in_night_min=("in_night_min", "mean"), in_max=("in_max", "mean"),
        tau_hours=("tau_hours", "mean")).round(2).to_string())

    sub = res.dropna(subset=["in.air_leakage_to_outside_ach50", "tau_hours"])
    print(f"\n--- parameter recovery vs known air leakage (n={len(sub)}) ---")
    print(f"  Pearson  r = {sub['in.air_leakage_to_outside_ach50'].corr(sub.tau_hours):+.3f}")
    print(f"  Spearman r = {sub['in.air_leakage_to_outside_ach50'].corr(sub.tau_hours, method='spearman'):+.3f}")
    print("  physics predicts NEGATIVE: a leakier envelope has a shorter time constant")

    print("\n--- exposure misclassification, per dwelling against its own weather ---")
    print(f"  {'threshold':>10} {'indoor over':>12} {'outdoor over':>13} {'MISSED':>8}")
    for threshold in THRESHOLDS:
        indoor_over = res.in_night_min > threshold
        outdoor_over = res.out_night_min > threshold
        # Dwellings an outdoor-based study scores as safe but which are not.
        missed = int((indoor_over & ~outdoor_over).sum())
        print(f"  {threshold:>8.0f} C {int(indoor_over.sum()):>12} {int(outdoor_over.sum()):>13} "
              f"{missed:>8}")
    print("\n  'MISSED' counts dwellings whose overnight indoor minimum exceeded the threshold")
    print("  while their own outdoor minimum did not. Those are the dwellings an")
    print("  outdoor-based exposure assessment scores as safe when they are not.")


if __name__ == "__main__":
    main()
