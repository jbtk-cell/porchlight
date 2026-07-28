"""Tests for the dwelling thermal model.

The load-bearing test is `test_steady_state_matches_analytic`: the simulated
steady state is compared against a hand-solved linear system for the same
network. If the code's energy balance is wrong, that test fails, and no amount
of plausible-looking output elsewhere will hide it.
"""

from __future__ import annotations

import numpy as np
import pytest

import building as B
from building import Dwelling, Weather, available_capacity_w, metrics, simulate


def flat_weather(n: int = 6000, t_out: float = 30.0, dt: float = 900.0) -> Weather:
    return Weather(time_s=np.arange(n) * dt, t_out=np.full(n, t_out),
                   solar_direct=np.zeros(n), solar_diffuse=np.zeros(n))


def diurnal_weather(days: int = 8, mean: float = 28.0, amp: float = 6.0) -> Weather:
    """Synthetic heat wave: outdoor peak at 15:00, solar peak at noon.

    The series starts at midnight, near the daily minimum, so the initial
    condition is never itself the hottest point in the run.
    """
    t = np.arange(days * 96) * 900.0
    hour = (t / 3600.0) % 24.0
    return Weather(time_s=t,
                   t_out=mean + amp * np.cos(2 * np.pi * (hour - 15.0) / 24.0),
                   solar_direct=np.clip(700 * np.sin(np.pi * (hour - 6) / 12), 0, None),
                   solar_diffuse=np.clip(150 * np.sin(np.pi * (hour - 6) / 12), 0, None))


WARMUP_S = 3 * 86400.0


def settled(arr: np.ndarray, wx: Weather) -> np.ndarray:
    """Drop the spin-up so an initial condition cannot masquerade as a result."""
    return arr[wx.time_s >= wx.time_s[0] + WARMUP_S]


def analytic_steady_state(d: Dwelling, t_out: float = 30.0) -> float:
    """Hand-solved steady state of the two-node network, free-running."""
    ua = d.conductances()
    ua_vent = B.RHO_CP_AIR * d.volume_m3() * d.infiltration_ach() / 3600.0
    ua_solid = ua["wall"] + ua["roof"]
    ua_am = max(ua_solid * 2.0, 1.0)
    ua_mo = max(ua_solid, 0.5)
    ua_direct = ua["window"] + ua_vent
    # With no sun the roof still radiates to the sky, so the mass sees a
    # depressed sol-air temperature.
    t_sa = t_out - (B.SKY_LONGWAVE_K if d.top_floor else 0.0)
    q = d.internal_gain_w_per_m2 * d.floor_area_m2
    A = np.array([[-(ua_direct + ua_am), ua_am], [ua_am, -(ua_am + ua_mo)]])
    b = np.array([-(ua_direct * t_out + q), -(ua_mo * t_sa)])
    return float(np.linalg.solve(A, b)[0])


FREE_RUNNING = dict(opens_windows=False, cooling_capacity_w=0.0)


@pytest.mark.parametrize("kw", [
    dict(top_floor=False),
    dict(top_floor=True),
    dict(top_floor=True, vintage="2010s", ach50=3.0),
    dict(top_floor=True, building_type="Mobile Home", ach50=25.0),
    dict(top_floor=False, building_type="Multi-Family with 5+ Units",
         exposed_wall_fraction=0.35, floor_area_m2=70.0),
])
def test_steady_state_matches_analytic(kw):
    """Energy balance: simulated steady state must equal the hand-solved value."""
    d = Dwelling(**{**dict(floor_area_m2=110.0, vintage="<1940",
                           building_type="Single-Family Detached", ach50=15.0),
                    **FREE_RUNNING, **kw})
    sim = simulate(d, flat_weather(), t_initial=30.0)["t_home"][-1]
    assert sim == pytest.approx(analytic_steady_state(d), abs=1e-3)


def test_converges_under_refinement():
    """Halving the internal timestep must not move the answer appreciably."""
    d = Dwelling(floor_area_m2=110.0, vintage="<1940", ach50=15.0, top_floor=True,
                 cooling_capacity_w=2500.0, conditioned_fraction=0.35)
    wx = diurnal_weather()
    original = B.STABILITY_MARGIN
    try:
        peaks = []
        for margin in (0.25, 0.05, 0.01):
            B.STABILITY_MARGIN = margin
            peaks.append(simulate(d, wx)["t_home"].max())
    finally:
        B.STABILITY_MARGIN = original
    assert max(peaks) - min(peaks) < 0.05


def test_explicit_scheme_is_stable():
    """The chosen substep must satisfy dt < 2*tau for the fastest node.

    A previous version ran an hourly step against a ~1550 s time constant, went
    unstable, and reported the oscillation amplitude as the peak temperature.
    """
    for kw in (dict(cooling_capacity_w=0.0),
               dict(cooling_capacity_w=2500.0, conditioned_fraction=0.35, interzone_ach=15.0),
               dict(cooling_capacity_w=7000.0, conditioned_fraction=1.0)):
        d = Dwelling(floor_area_m2=60.0, ach50=25.0, top_floor=True, **kw)
        wx = diurnal_weather()
        r = simulate(d, wx)
        dt = float(np.median(np.diff(wx.time_s))) / r["substeps"]
        assert dt < 2.0 * r["tau_min_s"], f"unstable: dt={dt} tau={r['tau_min_s']}"
        assert np.isfinite(r["t_home"]).all()
        # A stable solution cannot swing more than the forcing does by a wide margin.
        assert r["t_home"].max() - r["t_home"].min() < 40.0


def test_setpoint_is_honoured_when_capacity_allows():
    """Given a huge system, indoor temperature must sit exactly at setpoint."""
    d = Dwelling(floor_area_m2=110.0, ach50=15.0, top_floor=True,
                 cooling_capacity_w=60_000.0, conditioned_fraction=1.0,
                 cooling_setpoint_c=22.0)
    wx = diurnal_weather()
    r = simulate(d, wx, t_initial=22.0)
    assert settled(r["t_cond"], wx).max() == pytest.approx(22.0, abs=0.01)
    assert not r["ac_short"].any()


def test_setpoint_changes_outcome():
    """A lower setpoint must give a cooler home and use more energy."""
    wx = diurnal_weather()
    peaks, energy = [], []
    for sp in (20.0, 24.0, 28.0):
        d = Dwelling(floor_area_m2=110.0, ach50=10.0, cooling_capacity_w=9000.0,
                     conditioned_fraction=1.0, cooling_setpoint_c=sp)
        r = simulate(d, wx)
        peaks.append(settled(r["t_home"], wx).max())
        energy.append(np.trapezoid(r["ac_w"], wx.time_s))
    assert peaks[0] < peaks[1] < peaks[2]
    assert energy[0] > energy[1] > energy[2]


def test_undersized_equipment_cannot_hold_setpoint():
    """The interesting regime: capacity runs out and the zone drifts above setpoint."""
    d = Dwelling(floor_area_m2=140.0, vintage="<1940", ach50=25.0, top_floor=True,
                 cooling_capacity_w=1500.0, conditioned_fraction=0.35,
                 cooling_setpoint_c=22.0)
    wx = diurnal_weather(amp=8.0)
    r = simulate(d, wx)
    assert r["ac_short"].any()
    assert settled(r["t_cond"], wx).max() > 24.0


def test_capacity_derates_with_outdoor_temperature():
    d = Dwelling(cooling_capacity_w=5000.0)
    at_rating = available_capacity_w(d, 35.0)
    assert at_rating == pytest.approx(5000.0 * B.SENSIBLE_HEAT_RATIO)
    assert available_capacity_w(d, 25.0) == pytest.approx(at_rating)  # no boost below rating
    assert available_capacity_w(d, 42.0) < at_rating
    assert available_capacity_w(Dwelling(cooling_capacity_w=0.0), 40.0) == 0.0


def test_room_ac_leaves_the_rest_of_the_home_hot():
    """The central claim of the model: a window unit does not cool the dwelling."""
    d = Dwelling(floor_area_m2=110.0, vintage="<1940", ach50=15.0, top_floor=True,
                 cooling_capacity_w=2500.0, conditioned_fraction=0.35,
                 cooling_setpoint_c=23.9)
    wx = diurnal_weather()
    r = simulate(d, wx)
    assert settled(r["t_free"], wx).max() > settled(r["t_cond"], wx).max() + 1.0


def test_shutting_the_door_helps_the_cooled_room_and_hurts_the_rest():
    wx = diurnal_weather()
    base = dict(floor_area_m2=110.0, vintage="<1940", ach50=15.0, top_floor=True,
                cooling_capacity_w=2500.0, conditioned_fraction=0.35, cooling_setpoint_c=23.9)
    shut = simulate(Dwelling(**base, interzone_ach=1.5), wx)
    open_ = simulate(Dwelling(**base, interzone_ach=15.0), wx)
    assert settled(shut["t_cond"], wx).max() < settled(open_["t_cond"], wx).max()
    assert settled(shut["t_free"], wx).max() > settled(open_["t_free"], wx).max()


def test_more_ventilation_pulls_indoor_toward_outdoor():
    prev = None
    for ach50 in (1.0, 5.0, 15.0, 40.0):
        d = Dwelling(floor_area_m2=110.0, ach50=ach50, top_floor=False, **FREE_RUNNING)
        t = simulate(d, flat_weather(), t_initial=30.0)["t_home"][-1]
        if prev is not None:
            assert t < prev  # leakier -> closer to the 30 C outdoors
        prev = t


def test_windows_open_only_when_it_helps():
    """No window opening when outdoors is hotter than indoors."""
    n = 2000
    hot = Weather(time_s=np.arange(n) * 900.0, t_out=np.full(n, 40.0),
                  solar_direct=np.zeros(n), solar_diffuse=np.zeros(n))
    # Internal gains removed so indoor cannot climb above outdoor; otherwise the
    # occupant would be right to open up, and the model correctly does so.
    d = Dwelling(floor_area_m2=90.0, ach50=10.0, opens_windows=True,
                 cooling_capacity_w=0.0, internal_gain_w_per_m2=0.0)
    r = simulate(d, hot, t_initial=25.0)
    assert r["ach"].max() == pytest.approx(d.infiltration_ach(), abs=1e-6)

    cool = Weather(time_s=np.arange(n) * 900.0, t_out=np.full(n, 18.0),
                   solar_direct=np.zeros(n), solar_diffuse=np.zeros(n))
    r2 = simulate(d, cool, t_initial=30.0)
    assert r2["ach"].max() > d.infiltration_ach() + 1.0


def test_metrics_discard_warmup():
    """Spin-up must not contaminate the overnight minimum."""
    t = np.arange(8 * 96) * 900.0
    series = np.where(t < 3 * 86400, 45.0, 30.0)   # absurd first three days
    m = metrics(series, t, threshold=26.0, warmup_s=4 * 86400)
    assert m["peak"] == pytest.approx(30.0)


def test_metrics_degree_hours():
    t = np.arange(48) * 3600.0
    series = np.full(48, 30.0)
    m = metrics(series, t, threshold=26.0)
    assert m["degree_hours_over"] == pytest.approx(4.0 * 48, rel=0.05)
    assert m["hours_over"] == pytest.approx(48.0, rel=0.05)
