# Validation

What has been checked, what passed, what failed, and what is still unverified.

Everything here is reproducible from the repo. Where the model is wrong, the size
of the error is stated rather than described.

---

## Summary

| Check | Result |
|---|---|
| Energy balance vs hand-solved analytic steady state | **Pass**, error < 1e-3 K across 5 building types |
| Numerical convergence under step refinement | **Pass**, 0.011 K drift from 360 s to 15 s |
| Explicit-scheme stability (dt < 2 tau) | **Pass**, enforced automatically and tested |
| Thermostat setpoint honoured when capacity allows | **Pass**, exact to 0.01 K |
| Measured indoor temperature, 36 NYC apartments (Quinn 2017) | **Fail**, RMSE 2.76 K, model runs too cold |
| Relative AC benefit vs an independent simulation (Connolly 2023) | **Partial pass**, ratio matches, magnitudes ~2/3 |
| Indoor/outdoor thermal lag (Harlem Heat Project) | **Fail**, model gives ~1 h, reported 1-2 days |
| NIST NZERTF measured house | **Inconclusive**, wrong regime |

**Honest one-line verdict:** the model's internal physics is verified exactly; its
agreement with measured indoor temperature is poor and biased cold. Group
*comparisons* hold up considerably better than absolute temperatures, so the
project's claims are stated as comparisons.

---

## 1. Internal correctness (passes)

### Energy balance against a hand-solved analytic solution

The strongest test in the suite. For a free-running dwelling under constant
outdoor temperature and no sun, the two-node network has a closed-form steady
state that can be solved by hand. `tests/test_building.py::test_steady_state_matches_analytic`
solves that 2x2 linear system independently and compares.

| Configuration | Simulated | Analytic | Error |
|---|---|---|---|
| No roof term | 32.0740 C | 32.0740 C | 0.00000 K |
| Roof + sky longwave | 29.4856 C | 29.4856 C | 0.00000 K |
| Tight envelope, 2010s | 33.5736 C | 33.5736 C | 0.00000 K |
| Mobile home, leaky | 29.5647 C | 29.5647 C | 0.00000 K |

If the code's energy bookkeeping is wrong, this fails. It does not fail.

A note on how this test was built: the first version of it *appeared* to fail by
1.83 K. The model was right and the test was wrong — the hand solution had
omitted the roof's longwave radiation to the sky, which genuinely pulls a
clear-sky roof below ambient air temperature.

### Numerical convergence and stability

The air node's time constant can be under 1000 s when a small conditioned zone is
strongly coupled to the rest of the home. Explicit integration is stable only
while `dt < 2*tau`, so the internal step is now derived from the physics rather
than assumed, and the stability condition is asserted in the tests.

| Internal step | Whole-home peak | Drift |
|---|---|---|
| 360 s | 35.6309 C | — |
| 189 s | 35.6252 C | −0.0057 K |
| 77 s | 35.6220 C | −0.0089 K |
| 15 s | 35.6201 C | −0.0107 K |

Converged. This test exists because an earlier version ran an hourly step against
a ~1550 s time constant, went unstable, and reported the oscillation amplitude as
the peak temperature — which made window AC and central AC return identical
results.

---

## 2. Against measured data (the important part)

### Quinn, Kinney & Shaman 2017, *Indoor Air* — 36 NYC apartments

The best available test, because it measures the exact quantity the model
predicts, split by the exact categories the project's claim rests on. iButton
loggers in 36 New York apartments, summers 2014-15, mean nighttime bedroom
temperature. ([PMC5812346](https://pmc.ncbi.nlm.nih.gov/articles/PMC5812346/))

Modelled as a 70 m², pre-1940, 5+ unit apartment, single-aspect, setpoint 22.6 C.

| | Measured, all nights | Model | Error | Measured, hottest 10% | Model | Error |
|---|---|---|---|---|---|---|
| No AC | 26.0 C | 23.8 C | −2.2 | 28.3 C | 26.0 C | −2.3 |
| Window AC | 25.6 C | 22.5 C | −3.1 | 26.6 C | 22.6 C | −4.0 |
| Central AC | 24.0 C | 22.4 C | −1.6 | 25.3 C | 22.6 C | −2.7 |

**RMSE 2.76 K, biased cold everywhere.**

One correction was made as a result of this test, and only one, to avoid fitting
six data points. The original model gave every dwelling 5 ACH when windows
opened, which assumes cross ventilation. A single-aspect apartment has windows on
one wall only and gets single-sided ventilation, which moves several times less
air. Splitting this into `OPEN_ACH_CROSS = 6.0` and `OPEN_ACH_SINGLE = 2.0`,
selected by exposed wall fraction, is a physics correction rather than a fitted
one. It moved RMSE from **3.97 K to 2.76 K**.

**What the model still gets wrong, and why it matters:**

The model predicts window AC and central AC produce *identical* nighttime
bedroom temperatures, because both reach thermostat setpoint overnight when the
load is low. The measurements show a 1.3 K spread. The measured window-AC
bedrooms sit at 26.6 C on hot nights — far above any thermostat setting — which
means those units are simply not running all night.

That is occupant behaviour, not envelope physics. RECS 2020 reports 17.1% of AC
users "turn it on and off as needed", and the cooling-energy-equity literature
(Nock et al. 2022, *Nature Communications*) finds low-income households delay
cooling until roughly 1.7 K hotter than higher-income households. This model has
no usage model, so it systematically flatters air conditioning.

### Connolly et al. 2023, *Journal of Urban Health* — independent simulation

An EnergyPlus + CONTAM study of a pre-1980 Boston multifamily building reports
indoor temperature reductions of **6.1 K for full AC** and **3.5 K for partial AC**
relative to no AC. ([doi:10.1007/s11524-023-00790-3](https://doi.org/10.1007/s11524-023-00790-3))

This model, on a comparable building: **4.1 K full, 2.3 K partial.**

Absolute magnitudes are about two-thirds of theirs, but the **ratio** is
6.1/3.5 = 1.74 against 4.1/2.3 = 1.78. The relative claim — that whole-home
cooling is worth far more than partial cooling — reproduces closely against a
completely independent modelling approach, even though the absolute numbers do
not. This is the main reason the project states its findings as comparisons.

### Harlem Heat Project — thermal lag (fails)

The 2016 CUNY / WE ACT deployment of indoor sensors in 30 Harlem apartments
reported that indoor peak temperature lags outdoor peak by **1-2 days**.

This model gives an hourly cross-correlation lag of **1 hour** and a daily-peak
envelope lag of **0 days**. It does not reproduce the reported behaviour.

Partly the test instrument was wrong: cross-correlation of a strongly diurnal
signal is mathematically bounded near 6 hours for any first-order system, so it
could never have shown a multi-day lag. But the daily-peak envelope also showed
no lag, so the discrepancy is real.

Diagnosis: the envelope mass in the model does carry a long time constant
(~35 h), but the air node is far more tightly coupled to outdoors than to that
mass whenever windows are open (415 W/K versus 164 W/K), and window solar gain
peaks *before* outdoor air temperature, pulling the indoor peak earlier. Both
effects erase the lag.

This is left as an open discrepancy rather than tuned away. Note also that the
1-2 day figure reached this project through a secondary source; the primary
Harlem Heat data has not been obtained.

### NIST NZERTF — inconclusive, wrong regime

NIST's Net-Zero Energy Residential Test Facility publishes hourly room-by-room
measured indoor temperature for two full years, public domain
([data](https://pages.nist.gov/netzero/data.html)). It was downloaded and examined.

It does not validate this model, for three reasons:
- The living space is thermostat-controlled all summer (std 2.45 K, only 15 of
  1539 summer hours outside 22-26 C), so it tests almost nothing about
  free-running behaviour.
- Its attic is unvented and inside the thermal envelope — it peaks at 28.3 C when
  it is 35.4 C outdoors, the opposite of the hot attic this model represents.
- It is a 2012 Passive-House-class building. The stock this project cares about is
  pre-1940 and leaky.

A bounded check was still run: predicted sensible cooling load versus measured
heat-pump electrical energy over the hottest week (14-21 July 2013, outdoor
peak 35.4 C). Model 2082 W mean against a measured 2796 W mean, assuming COP 3.0
and 75% sensible — a ratio of **0.74**. Suggestive that the model somewhat
under-predicts cooling load, and consistent with its cold bias elsewhere, but
both the COP and the envelope specifications are assumed rather than measured, so
this is not a clean result.

---

## 3. Known-unvalidated

- **No measured free-running indoor temperature for Northeast US homes during a
  heat wave has been located.** This is the exact regime the headline claims sit
  in. Quinn et al. is the closest available and is NYC apartments, nighttime only.
- **No epidemiological literature distinguishes window AC from central AC** as a
  differential mortality or morbidity risk factor. The health literature treats
  air conditioning as binary. The engineering result and any health claim must
  not be blurred together.
- Absolute temperature depends on assumed R-values, thermal mass, and
  infiltration, none of which are measured per dwelling.
- Occupant behaviour is one deterministic rule with no variation, no usage model,
  and no representation of people who cannot open windows.
- ERA5 reanalysis smooths extremes: it gives New Haven's 2018 peak as 33.0 C
  where ResStock's station-based file says 36.1 C. Results are not comparable
  across those two weather sources.

---

## 4. Reproducing this

```bash
pytest tests/                     # 29 tests, internal correctness
python scripts/my_house.py --place "New Haven, CT" --built 1925 --cooling window
```

The Quinn, Connolly, and Harlem comparisons are the analysis scripts described
above; the NIST data is fetched from the S3 paths in that section.
