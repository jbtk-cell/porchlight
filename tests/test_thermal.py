"""Verification of the thermal models against closed-form results.

The first test is the load-bearing one: if the numerical solver does not
reproduce the analytical sinusoidal response of the 1R1C model, nothing built on
top of it can be trusted.
"""

import numpy as np
import pytest

from thermal import (
    DIURNAL_OMEGA,
    SECONDS_PER_HOUR,
    Model1R1C,
    Model3R2C,
    analytic_response,
    fit_sinusoid,
    penetration_depth,
    recover_tau_frequency_domain,
)


def diurnal_forcing(mean=25.0, amplitude=8.0, omega=DIURNAL_OMEGA):
    """A clean sinusoidal outdoor temperature, for verification against theory."""
    return lambda t: mean + amplitude * np.cos(omega * t)


def _settled(tau, days=30, per_hour=4):
    """Time grid long enough that the transient has decayed, plus the last 5 days."""
    total = days * 24 * SECONDS_PER_HOUR
    n = int(days * 24 * per_hour) + 1
    t = np.linspace(0.0, total, n)
    keep = t >= total - 5 * 24 * SECONDS_PER_HOUR
    return t, keep


@pytest.mark.parametrize("tau_hours", [1.0, 3.0, 8.0, 24.0, 72.0])
def test_1r1c_matches_analytic_frequency_response(tau_hours):
    """Numerical solution must reproduce amplitude ratio and phase lag exactly."""
    tau = tau_hours * SECONDS_PER_HOUR
    model = Model1R1C(tau=tau)
    t_out = diurnal_forcing()

    t, keep = _settled(tau_hours)
    t_in = model.simulate(t, t_out)

    _, amp_out, phase_out = fit_sinusoid(t[keep], np.array([t_out(x) for x in t[keep]]), DIURNAL_OMEGA)
    _, amp_in, phase_in = fit_sinusoid(t[keep], t_in[keep], DIURNAL_OMEGA)

    expected_ratio, expected_lag = analytic_response(tau)

    assert amp_in / amp_out == pytest.approx(expected_ratio, rel=1e-3)
    # indoor lags outdoor, and fit_sinusoid returns each signal's own lag
    assert (phase_in - phase_out) == pytest.approx(expected_lag, abs=1e-3)


def test_1r1c_tracks_outdoor_when_time_constant_is_tiny():
    """A leaky, massless building is just outdoor temperature indoors."""
    model = Model1R1C(tau=0.05 * SECONDS_PER_HOUR)
    t_out = diurnal_forcing()
    t, keep = _settled(10, per_hour=60)
    t_in = model.simulate(t, t_out)

    outdoor = np.array([t_out(x) for x in t[keep]])
    assert np.max(np.abs(t_in[keep] - outdoor)) < 0.2


def test_1r1c_flattens_when_time_constant_is_huge():
    """A very massive building barely swings at all."""
    model = Model1R1C(tau=2000.0 * SECONDS_PER_HOUR)
    t_out = diurnal_forcing(mean=25.0, amplitude=8.0)
    t, keep = _settled(400, per_hour=1)
    t_in = model.simulate(t, t_out)

    swing = np.ptp(t_in[keep])
    assert swing < 0.5, f"expected a nearly flat interior, got {swing:.2f} K swing"


def test_tau_recovered_from_synthetic_signal():
    """Round trip: simulate with a known tau, recover it from the two observables."""
    tau_true = 12.0 * SECONDS_PER_HOUR
    model = Model1R1C(tau=tau_true)
    t_out = diurnal_forcing()

    t, keep = _settled(30)
    t_in = model.simulate(t, t_out)
    outdoor = np.array([t_out(x) for x in t])

    result = recover_tau_frequency_domain(t[keep], outdoor[keep], t_in[keep])

    assert result["tau_from_amplitude_s"] == pytest.approx(tau_true, rel=2e-3)
    assert result["tau_from_phase_s"] == pytest.approx(tau_true, rel=2e-3)


def test_amplitude_and_phase_estimates_diverge_for_a_two_node_system():
    """The consistency check must actually be able to fail.

    A 3R2C system is not a 1R1C system, so the tau inferred from amplitude and
    the tau inferred from phase should disagree. If they agreed regardless, the
    check in `recover_tau_frequency_domain` would be diagnostically worthless.
    """
    model = Model3R2C(
        R_in=1.0e-3, R_inf=6.0e-3, R_out=2.0e-3, C_air=2.0e6, C_env=6.0e7
    )
    t_out = diurnal_forcing()
    t, keep = _settled(60, per_hour=4)
    t_in = model.simulate(t, t_out)
    outdoor = np.array([t_out(x) for x in t])

    result = recover_tau_frequency_domain(t[keep], outdoor[keep], t_in[keep])
    tau_a = result["tau_from_amplitude_s"]
    tau_p = result["tau_from_phase_s"]

    rel_gap = abs(tau_a - tau_p) / max(tau_a, tau_p)
    assert rel_gap > 0.05, (
        f"two-node system looked like a single-node one "
        f"(tau_amp={tau_a / 3600:.2f} h, tau_phase={tau_p / 3600:.2f} h)"
    )


def test_3r2c_lags_and_damps_relative_to_outdoor():
    """Envelope mass must delay and shrink the indoor swing, not advance it."""
    model = Model3R2C(
        R_in=1.0e-3, R_inf=6.0e-3, R_out=2.0e-3, C_air=2.0e6, C_env=6.0e7
    )
    t_out = diurnal_forcing()
    t, keep = _settled(60, per_hour=4)
    t_in = model.simulate(t, t_out)
    outdoor = np.array([t_out(x) for x in t])

    _, amp_out, phase_out = fit_sinusoid(t[keep], outdoor[keep], DIURNAL_OMEGA)
    _, amp_in, phase_in = fit_sinusoid(t[keep], t_in[keep], DIURNAL_OMEGA)

    assert amp_in < amp_out, "interior swing should be damped"
    assert 0.0 < (phase_in - phase_out) < np.pi / 2, "interior should lag, not lead"


def test_3r2c_reaches_outdoor_temperature_at_steady_state():
    """With no gains, a constant outdoor temperature must equilibrate indoors."""
    model = Model3R2C(
        R_in=1.0e-3, R_inf=6.0e-3, R_out=2.0e-3, C_air=2.0e6, C_env=6.0e7
    )
    t = np.linspace(0.0, 120 * 24 * SECONDS_PER_HOUR, 4000)
    t_in = model.simulate(t, lambda _t: 30.0, t_initial=10.0)

    assert t_in[-1] == pytest.approx(30.0, abs=0.05)


def test_penetration_depth_is_physically_sensible():
    """Dense masonry should have a diurnal penetration depth of ~0.1-0.2 m."""
    alpha_brick = 5.2e-7  # m^2/s
    delta = penetration_depth(alpha_brick)
    assert 0.08 < delta < 0.20, f"got {delta:.3f} m"

    # Insulation is far less diffusive, so the wave dies much closer to the surface.
    alpha_fiberglass = 2.5e-7
    assert penetration_depth(alpha_fiberglass) < delta
