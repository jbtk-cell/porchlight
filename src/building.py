"""Thermal model of a dwelling during a heat wave.

A 3R2C lumped network (air node + envelope mass node) driven by real weather,
with the pieces that actually decide whether a home is survivable:

  - window-opening behaviour, modelled explicitly rather than assumed
  - solar gain through windows, plus roof gain for top-floor units
  - a room air conditioner that cools ONE room, leaving the rest free-running
  - internal gains at a defensible occupied-home level

The window-opening model is the reason this exists. ResStock's own indoor
temperature output suppresses natural ventilation during exactly the extreme
events of interest -- measured across 53 Connecticut dwellings in the 2018 heat
wave, natural ventilation was active a median 0.15% of timesteps and never once
in 26 of them, while running ~10% of timesteps in an ordinary July. Trusting
that output would mean reporting a simulator artifact. So we take ResStock's
building CHARACTERISTICS, which are sound, and run our own physics.

Sign convention: all Q terms are heat flows INTO the air node, in watts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Air properties at ~30 C. Used to convert an air change rate into a conductance.
RHO_CP_AIR = 1200.0  # J / (m^3 K)
CEILING_HEIGHT_M = 2.6
SQFT_PER_SQM = 10.7639


# ---------------------------------------------------------------------------
# Building description
# ---------------------------------------------------------------------------

VINTAGE_R_WALL = {  # nominal wall R-value, SI (m^2 K / W)
    "<1940": 0.5, "1940s": 0.6, "1950s": 0.8, "1960s": 1.1, "1970s": 1.5,
    "1980s": 2.0, "1990s": 2.3, "2000s": 2.6, "2010s": 3.0,
}
# Ceiling assembly R including the attic air space above, not bare roof deck.
VINTAGE_R_ROOF = {
    "<1940": 1.3, "1940s": 1.5, "1950s": 1.9, "1960s": 2.5, "1970s": 3.5,
    "1980s": 4.5, "1990s": 5.5, "2000s": 6.5, "2010s": 7.5,
}
# Envelope areal heat capacity (J/m^2K of floor area). Masonry rowhouse-like
# construction stores far more than light wood frame.
MASS_BY_TYPE = {
    "Mobile Home": 30_000.0,
    "Single-Family Detached": 80_000.0,
    "Single-Family Attached": 110_000.0,
    "Multi-Family with 2 - 4 Units": 110_000.0,
    "Multi-Family with 5+ Units": 140_000.0,
}


@dataclass
class Dwelling:
    floor_area_m2: float = 90.0
    vintage: str = "<1940"
    building_type: str = "Multi-Family with 2 - 4 Units"
    ach50: float = 15.0
    window_to_floor: float = 0.15   # glazing area as a fraction of floor area
    shgc: float = 0.6               # clear single/double glazing
    top_floor: bool = False         # exposed roof above
    exposed_wall_fraction: float = 1.0  # 1.0 detached; ~0.5 mid-block; ~0.35 mid-rise
    internal_gain_w_per_m2: float = 4.0  # occupied home, appliances+people+lighting

    # Behaviour and equipment
    opens_windows: bool = True
    room_ac_watts: float = 0.0      # cooling delivered to the conditioned room only
    room_ac_fraction: float = 0.35  # fraction of floor area that room AC serves
    shading_factor: float = 0.7     # 1.0 = bare glass, 0.7 = typical curtains, 0.4 = blinds shut
    roof_absorptivity: float = 0.85  # 0.85 dark shingle, 0.25 cool roof

    def volume_m3(self) -> float:
        return self.floor_area_m2 * CEILING_HEIGHT_M

    def window_area_m2(self) -> float:
        return self.floor_area_m2 * self.window_to_floor

    def infiltration_ach(self) -> float:
        """Natural infiltration from a blower-door ACH50, via the LBL rule of thumb."""
        return self.ach50 / 20.0

    def conductances(self) -> dict[str, float]:
        """UA values in W/K for each envelope path."""
        # Wall area: perimeter of a roughly square plan, one storey.
        side = np.sqrt(self.floor_area_m2)
        wall_area = 4.0 * side * CEILING_HEIGHT_M * self.exposed_wall_fraction
        wall_area = max(wall_area - self.window_area_m2(), 5.0)

        r_wall = VINTAGE_R_WALL.get(self.vintage, 1.0)
        r_roof = VINTAGE_R_ROOF.get(self.vintage, 2.0)

        ua_wall = wall_area / (r_wall + 0.16)      # +film resistances
        ua_window = self.window_area_m2() / 0.35   # single/double glazing ~U 2.9
        ua_roof = (self.floor_area_m2 / (r_roof + 0.16)) if self.top_floor else 0.0

        return {"wall": ua_wall, "window": ua_window, "roof": ua_roof}

    def capacitances(self) -> tuple[float, float]:
        """(air-node C, envelope-mass C) in J/K."""
        # Air node includes furniture and the first few cm of interior surfaces.
        c_air = RHO_CP_AIR * self.volume_m3() * 4.0
        c_mass = MASS_BY_TYPE.get(self.building_type, 90_000.0) * self.floor_area_m2
        return c_air, c_mass


# ---------------------------------------------------------------------------
# Behaviour: when do people open windows?
# ---------------------------------------------------------------------------

OPEN_ACH = 5.0        # windows open, cross ventilation, still air outside
CLOSED_ACH_MIN = 0.2  # on top of infiltration


def ventilation_ach(dwell: Dwelling, t_in: float, t_out: float) -> float:
    """Air change rate, deciding whether the occupant would have windows open.

    The rule people actually follow: open the windows when outside is cooler
    than inside and inside is uncomfortable; close them when outside is hotter,
    because opening then makes it worse. This is the behaviour ResStock's output
    fails to reproduce during heat waves, and it is the single biggest driver of
    overnight indoor temperature in a home without air conditioning.
    """
    base = dwell.infiltration_ach()
    if not dwell.opens_windows:
        return base
    wants_cooling = t_in > 23.5
    outside_helps = t_out < t_in - 0.5
    return base + OPEN_ACH if (wants_cooling and outside_helps) else base


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

@dataclass
class Weather:
    """Hourly (or sub-hourly) forcing. All arrays the same length."""
    time_s: np.ndarray
    t_out: np.ndarray
    solar_direct: np.ndarray = field(default=None)   # W/m2 DNI
    solar_diffuse: np.ndarray = field(default=None)  # W/m2 DHI

    def __post_init__(self):
        n = len(self.time_s)
        if self.solar_direct is None:
            self.solar_direct = np.zeros(n)
        if self.solar_diffuse is None:
            self.solar_diffuse = np.zeros(n)


# Effective fraction of beam irradiance reaching vertical glazing, aggregated
# over four orientations and typical summer incidence angles at ~41 N. Applying
# the full DNI to all glazing at once overstates the gain by roughly 2x.
BEAM_TO_VERTICAL = 0.18
H_OUT = 25.0        # outside surface film coefficient, W/m2K, with light wind
SKY_LONGWAVE_K = 4.0  # sol-air correction for radiation to a clear sky, horizontal


def solar_gain_w(dwell: Dwelling, dni: float, dhi: float) -> tuple[float, float]:
    """(gain through windows in W, roof sol-air temperature rise in K).

    Glazing is split across four orientations, so only a fraction sees beam
    radiation at any hour; BEAM_TO_VERTICAL aggregates that. The roof term is
    returned as a sol-air temperature RISE rather than a flux, because that is
    how it enters the mass-node balance.
    """
    incident = dni * BEAM_TO_VERTICAL + dhi
    q_window = incident * dwell.window_area_m2() * dwell.shgc * dwell.shading_factor

    sol_air_rise = 0.0
    if dwell.top_floor:
        horizontal = dni * 0.85 + dhi
        sol_air_rise = (dwell.roof_absorptivity * horizontal) / H_OUT - SKY_LONGWAVE_K
        sol_air_rise = max(sol_air_rise, -SKY_LONGWAVE_K)
    return q_window, sol_air_rise


def simulate(dwell: Dwelling, weather: Weather, t_initial: float | None = None) -> dict:
    """Integrate the 3R2C system with an explicit scheme.

    Returns dict with 't_air', 't_mass', 'ach' arrays.
    """
    ua = dwell.conductances()
    c_air, c_mass = dwell.capacitances()

    # Split the envelope conductance either side of the mass node. Windows are
    # massless and connect air directly to outdoors; walls and roof go through
    # the mass.
    ua_direct = ua["window"]
    ua_solid = ua["wall"] + ua["roof"]
    ua_air_mass = max(ua_solid * 2.0, 1.0)   # interior surface coupling
    ua_mass_out = max(ua_solid, 0.5)

    n = len(weather.time_s)
    t_air = np.zeros(n)
    t_mass = np.zeros(n)
    ach_log = np.zeros(n)

    t_air[0] = weather.t_out[0] + 2.0 if t_initial is None else t_initial
    t_mass[0] = t_air[0]

    q_int = dwell.internal_gain_w_per_m2 * dwell.floor_area_m2
    ac_capacity = dwell.room_ac_watts

    for k in range(n - 1):
        dt = weather.time_s[k + 1] - weather.time_s[k]
        ta, tm, to = t_air[k], t_mass[k], weather.t_out[k]

        ach = ventilation_ach(dwell, ta, to)
        ach_log[k] = ach
        ua_vent = RHO_CP_AIR * dwell.volume_m3() * ach / 3600.0

        q_win, sol_air_rise = solar_gain_w(dwell, weather.solar_direct[k], weather.solar_diffuse[k])
        t_sol_air = to + sol_air_rise

        q_air = (
            (to - ta) * (ua_direct + ua_vent)
            + (tm - ta) * ua_air_mass
            + q_win
            + q_int
        )

        # A room AC removes heat only while the served room is above setpoint.
        # It cannot hold the whole dwelling: only room_ac_fraction of the floor
        # area is served, so the remaining volume keeps free-running.
        if ac_capacity > 0 and ta > 24.0:
            q_air -= ac_capacity * dwell.room_ac_fraction

        q_mass = (ta - tm) * ua_air_mass + (t_sol_air - tm) * ua_mass_out

        t_air[k + 1] = ta + dt * q_air / c_air
        t_mass[k + 1] = tm + dt * q_mass / c_mass

    ach_log[-1] = ach_log[-2] if n > 1 else 0.0
    return {"t_air": t_air, "t_mass": t_mass, "ach": ach_log}


# ---------------------------------------------------------------------------
# Metrics that matter for health
# ---------------------------------------------------------------------------

def night_mask(time_s: np.ndarray, start_hour: float = 0.0, end_hour: float = 6.0) -> np.ndarray:
    hours = (time_s / 3600.0) % 24.0
    return (hours >= start_hour) & (hours < end_hour)


def metrics(t_air: np.ndarray, time_s: np.ndarray, threshold: float = 26.0,
            warmup_s: float = 0.0) -> dict:
    """Overnight exposure metrics.

    The overnight minimum is the physiologically important quantity: the body
    needs a cool period to recover, and a dwelling that never drops below the
    threshold denies it. Degree-hours capture dose as well as peak.
    """
    # Discard the spin-up period: the first night reflects the initial condition,
    # not the building, and would otherwise dominate the overnight minimum.
    live = time_s >= (time_s[0] + warmup_s)
    t_air, time_s = t_air[live], time_s[live]

    night = night_mask(time_s)
    dt_h = np.diff(time_s, prepend=time_s[0] - (time_s[1] - time_s[0])) / 3600.0
    over = np.clip(t_air - threshold, 0, None)
    return {
        "peak": float(t_air.max()),
        "night_min": float(t_air[night].min()) if night.any() else float("nan"),
        "night_mean": float(t_air[night].mean()) if night.any() else float("nan"),
        "hours_over": float((dt_h * (t_air > threshold)).sum()),
        "degree_hours_over": float((dt_h * over).sum()),
        "nights_never_below": int(
            sum(1 for d in np.unique((time_s // 86400).astype(int))
                if (m := night & ((time_s // 86400).astype(int) == d)).any()
                and t_air[m].min() > threshold)
        ),
    }
