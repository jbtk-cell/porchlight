"""Reduced-order thermal models for a free-running dwelling.

Two lumped-capacitance models of increasing order:

  1R1C  a single air node, time constant tau = R*C
  3R2C  an air node plus an envelope-mass node, which is what reproduces the
        overnight behaviour that matters here (a massive wall re-radiates stored
        heat inward after sunset, so the indoor minimum both lags the outdoor
        minimum and never reaches it)

The 1R1C model has a closed-form response to sinusoidal forcing, which gives an
exact verification target for the numerical solver. That check lives in
`analytic_response` and is exercised by the test suite. Nothing downstream is
trustworthy if it fails.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.integrate import solve_ivp

SECONDS_PER_HOUR = 3600.0
DIURNAL_OMEGA = 2.0 * np.pi / (24.0 * SECONDS_PER_HOUR)  # rad/s, one cycle/day


# --------------------------------------------------------------------------
# Analytical reference
# --------------------------------------------------------------------------

def analytic_response(tau: float, omega: float = DIURNAL_OMEGA) -> tuple[float, float]:
    """Steady periodic response of the 1R1C model to sinusoidal forcing.

    Solving  dT/dt + T/tau = T_out/tau  with  T_out = A*exp(i*omega*t)  gives
    T = A/(1 + i*omega*tau) * exp(i*omega*t), hence

        amplitude ratio = 1 / sqrt(1 + (omega*tau)^2)
        phase lag       = arctan(omega*tau)

    Returns (amplitude_ratio, phase_lag_radians).
    """
    wt = omega * tau
    return 1.0 / np.sqrt(1.0 + wt**2), np.arctan(wt)


def fit_sinusoid(t: np.ndarray, y: np.ndarray, omega: float) -> tuple[float, float, float]:
    """Least-squares fit of y = mean + amp*cos(omega*t - phase).

    Linear in the parameters once expanded as mean + b*cos + c*sin, so this is a
    single lstsq call rather than an iterative fit.

    Returns (mean, amplitude, phase_radians).

    Sign convention, since it is easy to invert: with y = mean + b*cos + c*sin
    and y = mean + amp*cos(omega*t - phase), we have b = amp*cos(phase) and
    c = amp*sin(phase). The returned phase is therefore the signal's own *lag*
    relative to cos(omega*t). A signal that lags another has the *larger* phase,
    so a lag between two signals is `phase_of_later - phase_of_earlier`.
    """
    design = np.column_stack([np.ones_like(t), np.cos(omega * t), np.sin(omega * t)])
    mean, b, c = np.linalg.lstsq(design, y, rcond=None)[0]
    return mean, float(np.hypot(b, c)), float(np.arctan2(c, b))


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------

@dataclass
class Model1R1C:
    """Single air node.

    tau : thermal time constant [s]
    C   : thermal capacitance [J/K]. Only needed to convert a heat input into a
          temperature rate; if no heat input is supplied it does not affect the
          solution, which is precisely the identifiability problem described in
          the design doc -- free-running temperature alone constrains tau, not R
          and C separately.
    """

    tau: float
    C: float = 1.0e7

    def derivative(self, t: float, state: np.ndarray, t_out, q_gain) -> np.ndarray:
        (t_in,) = state
        drive = (t_out(t) - t_in) / self.tau
        return np.array([drive + q_gain(t) / self.C])

    def simulate(self, t_eval, t_out, q_gain=None, t_initial=None) -> np.ndarray:
        return _integrate(self, t_eval, t_out, q_gain, t_initial, n_states=1)[:, 0]


@dataclass
class Model3R2C:
    """Air node coupled to an envelope-mass node.

    R_in  : air <-> envelope resistance [K/W]
    R_inf : air <-> outdoor resistance, i.e. infiltration and glazing [K/W]
    R_out : envelope <-> outdoor resistance [K/W]
    C_air : air-node capacitance [J/K]
    C_env : envelope-mass capacitance [J/K]
    """

    R_in: float
    R_inf: float
    R_out: float
    C_air: float
    C_env: float

    def derivative(self, t: float, state: np.ndarray, t_out, q_gain) -> np.ndarray:
        t_air, t_env = state
        outdoor = t_out(t)
        q = q_gain(t)

        d_air = ((t_env - t_air) / self.R_in + (outdoor - t_air) / self.R_inf + q) / self.C_air
        d_env = ((t_air - t_env) / self.R_in + (outdoor - t_env) / self.R_out) / self.C_env
        return np.array([d_air, d_env])

    def simulate(self, t_eval, t_out, q_gain=None, t_initial=None) -> np.ndarray:
        """Returns the air-node temperature series."""
        return _integrate(self, t_eval, t_out, q_gain, t_initial, n_states=2)[:, 0]

    @property
    def steady_state_gain(self) -> float:
        """Fraction of a sustained outdoor step that reaches the air node.

        At steady state with no internal gain the air node sits at the outdoor
        temperature regardless of the resistances, so this is 1.0 by
        construction. It exists as an energy-balance sanity check.
        """
        return 1.0


def _integrate(model, t_eval, t_out, q_gain, t_initial, n_states: int) -> np.ndarray:
    t_eval = np.asarray(t_eval, dtype=float)
    if q_gain is None:
        def q_gain(_t):
            return 0.0

    if t_initial is None:
        t_initial = float(t_out(t_eval[0]))
    y0 = np.full(n_states, float(t_initial))

    solution = solve_ivp(
        model.derivative,
        (t_eval[0], t_eval[-1]),
        y0,
        t_eval=t_eval,
        args=(t_out, q_gain),
        method="RK45",
        rtol=1e-8,
        atol=1e-8,
        max_step=SECONDS_PER_HOUR,  # keep the solver from stepping over the forcing
    )
    if not solution.success:
        raise RuntimeError(f"integration failed: {solution.message}")
    return solution.y.T


# --------------------------------------------------------------------------
# Parameter recovery
# --------------------------------------------------------------------------

def recover_tau_frequency_domain(
    t: np.ndarray,
    t_out_series: np.ndarray,
    t_in_series: np.ndarray,
    omega: float = DIURNAL_OMEGA,
) -> dict[str, float]:
    """Estimate tau two independent ways, from amplitude and from phase.

    The 1R1C response gives
        amplitude ratio = 1/sqrt(1 + (omega*tau)^2)  ->  tau = sqrt(1/r^2 - 1)/omega
        phase lag       = arctan(omega*tau)          ->  tau = tan(phase)/omega

    Agreement between the two is a genuine internal consistency check: they use
    different features of the same signal, so a real 1R1C system makes them
    agree and a system with significant envelope mass makes them diverge. The
    divergence is therefore diagnostic, not merely an error.
    """
    _, amp_out, phase_out = fit_sinusoid(t, t_out_series, omega)
    _, amp_in, phase_in = fit_sinusoid(t, t_in_series, omega)

    ratio = amp_in / amp_out
    # Indoor lags outdoor, and fit_sinusoid returns each signal's own lag, so the
    # relative lag is indoor minus outdoor. See the note in fit_sinusoid.
    lag = (phase_in - phase_out) % (2.0 * np.pi)
    if lag > np.pi:
        lag -= 2.0 * np.pi

    tau_amp = np.sqrt(max(1.0 / ratio**2 - 1.0, 0.0)) / omega
    tau_phase = np.tan(lag) / omega if abs(lag) < np.pi / 2 else np.inf

    return {
        "amplitude_ratio": float(ratio),
        "phase_lag_rad": float(lag),
        "phase_lag_hours": float(lag / omega / SECONDS_PER_HOUR),
        "tau_from_amplitude_s": float(tau_amp),
        "tau_from_phase_s": float(tau_phase),
    }


def penetration_depth(alpha: float, omega: float = DIURNAL_OMEGA) -> float:
    """Thermal penetration depth delta = sqrt(2*alpha/omega) [m].

    From the heat equation with sinusoidal surface forcing: a wave decays as
    exp(-x/delta) and lags by x/delta. This is what physically connects a lumped
    tau to real wall material properties, and it is why a thick masonry wall
    keeps releasing heat inward long after sunset.

    alpha : thermal diffusivity [m^2/s]
    """
    return float(np.sqrt(2.0 * alpha / omega))
