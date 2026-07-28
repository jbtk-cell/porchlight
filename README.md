# Porchlight

**How hot does your home actually get during a heat wave, and does your air
conditioner save you?**

A physics model of indoor temperature run across all 1,556,937 Connecticut
homes, plus an interactive map and an in-browser simulator.

Live: https://claude.ai/code/artifact/e184265a-b1f6-4a52-95c5-a1704a8f8a32

---

## The finding

Heat warnings are issued on *outdoor* temperature. Nobody dies outdoors of
indoor heat — they die inside, and indoor temperature is not measured anywhere
at scale. So we model it.

Running the June-July 2018 New England heat wave (outdoor peak 36.1 C, overnight
low 17.8 C) through every dwelling in the ResStock Connecticut sample:

| Cooling equipment | Homes | Avg indoor peak | Share above 32 C |
|---|---:|---:|---:|
| None | 186,619 | 38.8 C | 100.0% |
| **Room AC** | **797,258** | **37.0 C** | **95.4%** |
| Non-ducted heat pump | 12,441 | 33.4 C | 63.3% |
| Central AC | 525,327 | 28.3 C | 14.2% |
| Ducted heat pump | 35,293 | 27.6 C | 7.2% |

**Owning an air conditioner is not the variable. Whether it cools the whole home
is.** A window unit buys 1.8 C. Central air buys 10.5 C. And 797,258 Connecticut
homes — more than have central air — rely on the window unit.

Statewide, 66.3% of homes peak above 32 C. In New Haven it is 70.1% of 58,652
homes, 55.0% of which are room-AC-only and 64.9% of which are rented.

City ranking is led by Bridgeport (75.9%), Hartford (74.9%), West Haven (74.7%),
Meriden (74.4%).

Weather is held **constant across all cities on purpose**. The map is therefore a
picture of housing-driven risk — who is exposed because of what they live in —
not of which city happened to be hotter that week.

---

## Where everything is

### The model
| File | What it is |
|---|---|
| `src/building.py` | The engine. 3R2C lumped thermal network: air node + envelope mass node. Window-opening behaviour, solar gain through glazing, roof sol-air temperature, room-AC-serves-one-room logic, health metrics. |
| `src/thermal.py` | Standalone analysis module: `Model1R1C`, `Model3R2C`, closed-form sinusoidal response, `fit_sinusoid`, frequency-domain time-constant recovery, thermal penetration depth. Used for validation. |
| `tests/test_thermal.py` | 12 tests. The load-bearing one checks the numerical solver against the closed-form analytic response for tau in {1, 3, 8, 24, 72} h. |

Run tests with `PYTHONPATH=src pytest tests/` (there is no `conftest.py` yet, so
the path has to be set explicitly).

### The pipeline
| File | What it does |
|---|---|
| `scripts/run_stock.py` | Simulates all 6,132 sampled CT dwellings, aggregates by city / vintage / type / cooling / tenure using ResStock sampling weights, writes `web/data.json` and `results/stock_results.csv`. |
| `scripts/build_site.py` | Inlines `data.json` + `places.json` + `ct_outline.json` + the hourly weather series into `web/template.html`, producing the standalone `web/index.html`. |
| `scripts/stage1_exposure_gap.py` | Earlier analysis. Superseded — see "What was abandoned". |

Regenerate everything: `python scripts/run_stock.py && python scripts/build_site.py`

### The page
| File | What it is |
|---|---|
| `web/template.html` | Source of the page. Edit **this**, never `index.html`. Contains a JavaScript port of the physics. |
| `web/index.html` | Generated. 108 KB, fully self-contained, no network access required. |
| `web/data.json` | All modelled results — statewide, 19 cities, 5 breakdowns. |
| `web/places.json` | City geometry (Census TIGERweb). |
| `web/ct_outline.json` | State outline path. |
| `results/stock_results.csv` | Per-dwelling output, all 6,132 rows. The raw material for any further analysis. |

### Docs
| File | Status |
|---|---|
| `docs/superpowers/specs/2026-07-26-porchlight-design.md` | Superseded. The original sensor-and-dispatch concept. |
| `docs/superpowers/specs/2026-07-27-indoor-heat-exposure-design.md` | Partly superseded. Physics is current; the research-paper framing is not. |
| `docs/porchlight-walkthrough.md` | Superseded. Describes the abandoned hardware project. |

---

## How the model works

A two-node lumped-capacitance network. Indoor air is one node; the envelope mass
is another. Heat flows in through windows and ventilation directly to the air,
and through walls and roof via the mass node.

```
outdoor --[walls + roof]-- MASS --[surface coupling]-- AIR --[windows + ventilation]-- outdoor
                             ^                          ^
                      roof sol-air gain          solar + internal gains, AC
```

Three modelling decisions carry the result:

**1. Window-opening behaviour is modelled, not assumed.** The occupant opens
windows when it is uncomfortable inside *and* cooler outside, and shuts them when
opening would make things worse. This is the single biggest driver of overnight
temperature in a free-running home. Controlled test, identical dwelling:

```
windows open    peak 37.89 C    overnight min 22.25 C
windows shut    peak 44.94 C    overnight min 23.77 C
```

**2. A room air conditioner cools one room.** It removes `room_ac_watts` from
only `room_ac_fraction` (0.35) of the floor area; the rest keeps free-running.
This is what produces the headline gap, and it is the honest representation of a
window unit — it is not a small central system.

**3. ResStock's building characteristics are used; its indoor temperatures are
not.** ResStock's own output suppresses natural ventilation during exactly the
extreme events of interest — measured across 53 CT dwellings, natural ventilation
was active a median 0.15% of heat-wave timesteps and **never once in 26 of them**,
versus ~10% in an ordinary July. Reporting those temperatures would be reporting
a simulator artifact. So the characteristics (vintage, floor area, air leakage,
glazing, storey, equipment, tenure) — which are sound — feed our own physics.

That also resolves a contradiction in the original spec: if you trust ResStock's
indoor temperatures, your own thermal model is redundant.

---

## Numerical verification

The air node's time constant is short — roughly 1546 s with windows open — so
explicit integration is only stable below dt = 2*tau = 3091 s.

| Step | Update factor `1 - dt/tau` | Result |
|---|---|---|
| 900 s (Python) | +0.418 | stable |
| 3600 s (naive hourly) | -1.329 | **unstable, oscillates** |
| 300 s (browser, 12x sub-stepped) | +0.806 | stable |

Convergence: peak differs by 0.068 C between a 15-minute and a 1-minute step.

This was a real bug, not a hypothetical. The browser port originally ran hourly,
the solution oscillated, and `Math.max` was reading the oscillation amplitude
rather than the physics — which made window AC and central AC return *identical*
peaks (37.5 C). Fixed with 12x sub-stepping and interpolated forcing. The browser
now agrees with Python to within 0.4 C.

Other bugs found and fixed along the way:

- Beam radiation was applied to all glazing simultaneously, overstating solar
  gain roughly 2x. Glazing faces four directions; `BEAM_TO_VERTICAL = 0.18`
  aggregates the fraction actually seeing beam radiation. Peak gain went from
  96 W/m2 (implausible) to 46 W/m2.
- Roof sol-air temperature omitted longwave radiation to the sky. Added
  `SKY_LONGWAVE_K = 4.0` with `H_OUT = 25`.
- Overnight-minimum metrics were reading the spin-up night, making `night_min`
  identical across every scenario. Fixed with a 4-day warm-up discard.
- A phase-lag sign inversion in `thermal.py` — magnitudes matched analytics to 8
  significant figures but the sign was flipped. Caught by the analytic test.
- Two JavaScript crashes: a null `aria-label` on the state outline path, and a
  temporal-dead-zone call to `row()`.

---

## What this does not claim

**No mortality claim is made.** Converting indoor degree-hours into deaths
requires an indoor exposure-response function, and the epidemiological literature
is built almost entirely on outdoor station temperature. That function does not
exist yet. Claiming a body count would be the least defensible thing here, so the
page does not.

Other honest limits:

- Absolute temperatures depend on assumed R-values, mass, and infiltration.
  **Comparisons between groups are far more trustworthy than any single number.**
  The room-AC-versus-central-AC gap survives any plausible parameter choice; "38.8 C"
  should be read as "dangerously hot", not as a prediction.
- The 32 C danger threshold is a modelling choice, not a clinical one. It sits
  near the top of CIBSE TM59 overheating guidance. WHO has explicitly declined to
  set an upper indoor threshold.
- One representative weather series is applied statewide by design, so the map
  cannot say anything about which city has worse weather.
- Occupant behaviour is a single deterministic rule. Real people vary — some
  cannot open windows for safety or noise reasons, and that group is likely both
  poorer and more exposed than the model shows.
- `by_floor` in `data.json` is a **composition artifact** and is deliberately not
  rendered on the page: the "not top floor" group is 100% multifamily in dense
  old cities with far more room-AC-only homes. Like-for-like within
  multifamily-5+, a top floor raises heat *dose* (degree-hours +8.6%) but slightly
  clips the instantaneous peak, because once indoor air overshoots outdoor air the
  roof becomes a heat-loss path.

---

## What was abandoned, and why

**The sensor-dispatch project** (`docs/porchlight-walkthrough.md`, the
2026-07-26 spec). Originally this was BLE thermometers in vulnerable people's
homes feeding a wellness-check dispatch system. Dropped because the hardware
dependency made it unbuildable and untestable solo, and because the modelling
question turned out to be the more interesting one.

**The +7.5 C "exposure gap" headline** (`results/stage1_report.txt`). This was
measured off ResStock's own indoor temperatures and is an artifact of the
suppressed-ventilation bug described above. It collapses to +0.1 C under a
realistic 2 ACH. The file is kept because the finding — that ResStock's indoor
output is unusable for heat-wave work — is what motivated writing our own physics,
and that is worth a record.

**The research-paper framing.** This is a tool, not a manuscript. The novelty
claim would not have survived contact with the literature anyway — O'Lenick 2020
(EHP), Gronlund 2025 (EHP), Taylor 2015/2017/2021, Huang 2025, Lin & Nagy 2025
(ResStock, Austin), and Cole 2024 (15M dwellings) all occupy this ground. What is
genuinely uncrowded is the *delivery*: nobody has put this in front of an ordinary
person as "type in your building, see your temperature."

---

## Data sources

- **NREL ResStock** 2025.1 AMY2018, public S3 (`oedi-data-lake`). Connecticut
  sample: 6,132 dwellings, 771 metadata columns, sampling weights expanding to
  1,556,937 homes.
- **Weather**: ResStock AMY2018 county weather files, 2018-06-25 to 2018-07-06.
- **Geometry**: US Census TIGERweb ArcGIS REST. (The Census ACS API now requires
  a key; city population and characteristics come from ResStock's own `in.city`
  and `weight` fields instead.)

## Setup

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
PYTHONPATH=src .venv/bin/pytest tests/     # 12 tests
```

ResStock data is downloaded to `data/` (gitignored). Fetching uses `curl` rather
than `urllib`, which is blocked in some sandboxes.
