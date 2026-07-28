"""Assemble the standalone web page with all data inlined.

The page must run with no network access, so the weather series, the city
results and the simplified geometry are all embedded directly.
"""

from __future__ import annotations

import glob
import json
import os
import pathlib

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE = pathlib.Path(os.environ.get("CLAUDE_JOB_DIR", "/tmp")) / "tmp"


def weather_arrays() -> dict:
    files = sorted((CACHE / "nh").glob("*.parquet")) or sorted((CACHE / "ts").glob("*.parquet"))
    cols = {
        "timestamp": "ts",
        "out.outdoor_air_drybulb_temp..c": "tout",
        "out.weather.direct_normal_solar_radiation..watt_per_m2": "dni",
        "out.weather.diffuse_solar_radiation..watt_per_m2": "dhi",
    }
    d = pd.read_parquet(files[0], columns=list(cols)).rename(columns=cols)
    d["ts"] = pd.to_datetime(d["ts"])
    d = d.set_index("ts").loc["2018-06-25":"2018-07-06"].resample("1h").mean()
    return {
        "tout": [round(float(v), 2) for v in d.tout],
        "dni": [round(float(v), 1) for v in d.dni],
        "dhi": [round(float(v), 1) for v in d.dhi],
        "start_hour": int(d.index[0].hour),
    }


def main() -> None:
    data = json.load(open(ROOT / "web/data.json"))
    places = json.load(open(ROOT / "web/places.json"))
    outline = json.load(open(ROOT / "web/ct_outline.json"))
    data["wx"] = weather_arrays()
    payload = json.dumps(data, separators=(",", ":"))
    geo = json.dumps(places, separators=(",", ":"))
    out_json = json.dumps(outline, separators=(",", ":"))

    html = (ROOT / "web/template.html").read_text()
    html = (html.replace("/*__DATA__*/", payload)
                .replace("/*__GEO__*/", geo)
                .replace("/*__OUTLINE__*/", out_json))
    out = ROOT / "web/index.html"
    out.write_text(html)
    print(f"wrote {out} ({out.stat().st_size/1024:.0f} KB), "
          f"{len(data['wx']['tout'])} weather hours, {len(places['features'])} cities")


if __name__ == "__main__":
    main()
