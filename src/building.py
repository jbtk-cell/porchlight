"""Thermal model of a dwelling during a heat wave.

Two air zones plus an envelope mass node, driven by real weather.

The two-zone split is the point of the model. A window air conditioner cools the
room it is in; the rest of the home free-runs and exchanges air with that room
through the doorway. Averaging both into one node -- which is what a single-node
model does -- erases exactly the effect being studied. So:

    conditioned zone   the room (or rooms) the equipment actually serves
    free zone          everywhere else, ventilated by windows when that helps
    envelope mass      walls, floors, furniture; shared, slow

A central system sets the conditioned fraction to 1.0 and the free zone vanishes.

Cooling is modelled as an ideal-load system with a real thermostat setpoint and a
capacity limit: each step it removes exactly the heat needed to hold setpoint, up
to what the equipment can actually deliver. When capacity runs out the zone
drifts above setpoint, which is the regime that matters.

The window-opening rule is behavioural, not assumed: occupants open windows when
inside is uncomfortable and outside is cooler, and shut them otherwise. This is
what ResStock's own indoor output fails to reproduce during heat waves -- across
53 Connecticut dwellings in the 2018 event natural ventilation was active a
median 0.15% of timesteps and never once in 26 of them, against ~10% in an
ordinary July. So we take ResStock's building CHARACTERISTICS, which are sound,
and run our own physics.

Sign convention: all Q terms are heat flows INTO the node they belong to, in watts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

RHO_CP_AIR = 1200.0  # J/(m^3 K), air at ~30 C
CEILING_HEIGHT_M = 2.6


# ---------------------------------------------------------------------------
# Envelope properties by vintage and construction
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
# Envelope areal heat capacity (J/m^2 K of floor area).
MASS_BY_TYPE = {
    "Mobile Home": 30_000.0,
    "Single-Family Detached": 80_000.0,
    "Single-Family Attached": 110_000.0,
    "Multi-Family with 2 - 4 Units": 110_000.0,
    "Multi-Family with 5+ Units": 140_000.0,
}

# Solar and surface constants.
BEAM_TO_VERTICAL = 0.18   # fraction of DNI reaching vertical glazing, aggregated
                          # over four orientations at ~41 N in summer
H_OUT = 25.0              # outside film coefficient, W/m2K, light wind
SKY_LONGWAVE_K = 4.0      # sol-air correction for radiation to a clear sky

# Ventilation. Opening a window does NOT give everyone the same air change rate.
# A dwelling with windows on opposite walls gets cross ventilation; a single-aspect
# apartment gets single-sided ventilation, driven only by the weak buoyancy and
# turbulence across one opening, and moves several times less air. Treating every
# home as cross-ventilated made this model far too good at dumping heat overnight
# -- see docs/validation.md, where it predicted no-AC bedrooms 2.6 K cooler than
# Quinn et al. (2017) measured in 36 NYC apartments.
OPEN_ACH_CROSS = 6.0      # windows on 2+ orientations, air can path through
OPEN_ACH_SINGLE = 2.0     # one exposed wall: single-sided ventilation only
CROSS_VENT_MIN_EXPOSURE = 0.5   # exposed wall fraction below which a dwelling is
                                # treated as single-aspect
OPEN_ACH = OPEN_ACH_CROSS  # retained for callers that want the optimistic case

# Cooling equipment.
SENSIBLE_HEAT_RATIO = 0.72   # a unit's rated capacity is TOTAL; only this share
                             # is sensible cooling, the rest removes moisture.
                             # The model is dry-bulb, so only sensible counts.
CAPACITY_DERATE_PER_K = 0.02  # fractional capacity loss per K of outdoor air
                              # above the 35 C rating point
RATING_T_OUT = 35.0


@dataclass
class Dwelling:
    floor_area_m2: float = 90.0
    vintage: str = "<1940"
    building_type: str = "Multi-Family with 2 - 4 Units"
    ach50: float = 15.0
    window_to_floor: float = 0.15
    shgc: float = 0.6
    top_floor: bool = False
    exposed_wall_fraction: float = 1.0
    internal_gain_w_per_m2: float = 4.0

    # Behaviour
    opens_windows: bool = True
    shading_factor: float = 0.7        # 1.0 bare glass, 0.7 curtains, 0.4 blinds shut
    roof_absorptivity: float = 0.85    # 0.85 dark shingle, 0.25 cool roof

    # Cooling equipment
    cooling_capacity_w: float = 0.0    # RATED TOTAL capacity at 35 C outdoor
    conditioned_fraction: float = 1.0  # share of floor area the equipment serves
    cooling_setpoint_c: float = 22.6   # EIA RECS 2020 median US reported
                                       # indoor temp, daytime occupied (72.7 F)
    interzone_ach: float = 8.0         # air exchange between the conditioned zone
                                       # and the rest, referenced to the smaller
                                       # zone. A wide-open doorway is ~10-15;
                                       # a shut door with undercut is ~1-2.

    def volume_m3(self) -> float:
        return self.floor_area_m2 * CEILING_HEIGHT_M

    def window_area_m2(self) -> float:
        return self.floor_area_m2 * self.window_to_floor

    def infiltration_ach(self) -> float:
        """Natural infiltration from a blower-door ACH50, via the LBL rule of thumb."""
        return self.ach50 / 20.0

    def has_cooling(self) -> bool:
        return self.cooling_capacity_w > 0

    def conductances(self) -> dict[str, float]:
        """Whole-dwelling UA values in W/K for each envelope path."""
        side = np.sqrt(self.floor_area_m2)
        wall_area = 4.0 * side * CEILING_HEIGHT_M * self.exposed_wall_fraction
        wall_area = max(wall_area - self.window_area_m2(), 5.0)

        r_wall = VINTAGE_R_WALL.get(self.vintage, 1.0)
        r_roof = VINTAGE_R_ROOF.get(self.vintage, 2.0)

        return {
            "wall": wall_area / (r_wall + 0.16),
            "window": self.window_area_m2() / 0.35,   # U ~2.9, single/double glazing
            "roof": (self.floor_area_m2 / (r_roof + 0.16)) if self.top_floor else 0.0,
        }

    def mass_capacitance(self) -> float:
        return MASS_BY_TYPE.get(self.building_type, 90_000.0) * self.floor_area_m2

    def air_capacitance(self) -> float:
        """Air plus furniture and the first few cm of interior surfaces."""
        return RHO_CP_AIR * self.volume_m3() * 4.0


# ---------------------------------------------------------------------------
# Behaviour and equipment
# ---------------------------------------------------------------------------

def wants_windows_open(t_in: float, t_out: float) -> bool:
    """The rule people actually follow: open up when it is uncomfortable inside
    and cooler outside; shut when opening would make things worse."""
    return t_in > 23.5 and t_out < t_in - 0.5


def open_window_ach(dwell: "Dwelling") -> float:
    """Air change rate added by opening windows, set by whether the dwelling can
    actually cross-ventilate."""
    return (OPEN_ACH_CROSS if dwell.exposed_wall_fraction >= CROSS_VENT_MIN_EXPOSURE
            else OPEN_ACH_SINGLE)


def available_capacity_w(dwell: Dwelling, t_out: float) -> float:
    """Sensible cooling the equipment can actually deliver right now.

    Rated capacity is a total (sensible + latent) figure measured at 35 C
    outdoor. Both corrections matter in a heat wave: the latent share does
    nothing for dry-bulb temperature, and capacity fades as it gets hotter
    outside -- exactly when it is needed.
    """
    if dwell.cooling_capacity_w <= 0:
        return 0.0
    cap = dwell.cooling_capacity_w * SENSIBLE_HEAT_RATIO
    if t_out > RATING_T_OUT:
        cap *= max(0.35, 1.0 - CAPACITY_DERATE_PER_K * (t_out - RATING_T_OUT))
    return cap


def solar_gain_w(dwell: Dwelling, dni: float, dhi: float) -> tuple[float, float]:
    """(gain through all glazing in W, roof sol-air temperature rise in K).

    Glazing faces four directions, so only a fraction sees beam radiation at any
    hour; applying full DNI to all of it at once overstates the gain about 2x.
    """
    incident = dni * BEAM_TO_VERTICAL + dhi
    q_window = incident * dwell.window_area_m2() * dwell.shgc * dwell.shading_factor

    sol_air_rise = 0.0
    if dwell.top_floor:
        horizontal = dni * 0.85 + dhi
        sol_air_rise = (dwell.roof_absorptivity * horizontal) / H_OUT - SKY_LONGWAVE_K
        sol_air_rise = max(sol_air_rise, -SKY_LONGWAVE_K)
    return q_window, sol_air_rise


@dataclass
class Weather:
    """Hourly (or sub-hourly) forcing. All arrays the same length."""
    time_s: np.ndarray
    t_out: np.ndarray
    solar_direct: np.ndarray = field(default=None)
    solar_diffuse: np.ndarray = field(default=None)

    def __post_init__(self):
        n = len(self.time_s)
        if self.solar_direct is None:
            self.solar_direct = np.zeros(n)
        if self.solar_diffuse is None:
            self.solar_diffuse = np.zeros(n)


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

# Explicit integration is only stable while dt < 2*tau for the fastest node.
# The conditioned zone can be small and strongly coupled, giving time constants
# under 1000 s, so the step is chosen from the physics rather than assumed.
# A previous version of this model ran an hourly step against a ~1550 s time
# constant, went unstable, and reported the oscillation amplitude as the peak.
STABILITY_MARGIN = 0.25   # target dt <= 0.25 * tau_min
MAX_SUBSTEPS = 240


def _min_time_constant(dwell: Dwelling, f: float, ua: dict) -> float:
    """Shortest node time constant under the worst-case (most conductive) state."""
    vol = dwell.volume_m3()
    c_air = dwell.air_capacitance()
    c_cond = max(c_air * f, 1.0)
    c_free = max(c_air * (1.0 - f), 1.0) if f < 1.0 else np.inf

    max_ach = dwell.infiltration_ach() + open_window_ach(dwell)
    ua_solid = ua["wall"] + ua["roof"]

    def zone_ua(share: float, v_share: float) -> float:
        return (ua["window"] * share
                + RHO_CP_AIR * vol * v_share * max_ach / 3600.0
                + max(ua_solid * 2.0, 1.0) * share
                + (RHO_CP_AIR * min(v_share, 1 - v_share) * vol
                   * dwell.interzone_ach / 3600.0 if f < 1.0 else 0.0))

    taus = [c_cond / max(zone_ua(f, f), 1e-6)]
    if f < 1.0:
        taus.append(c_free / max(zone_ua(1 - f, 1 - f), 1e-6))
    taus.append(dwell.mass_capacitance() / max(max(ua_solid, 0.5), 1e-6))
    return float(min(taus))


def simulate(dwell: Dwelling, weather: Weather, t_initial: float | None = None) -> dict:
    """Integrate the two-zone system.

    Returns arrays sampled at the input timestamps:
      t_cond   conditioned-zone air temperature (C)
      t_free   free-running-zone air temperature (C)
      t_home   floor-area-weighted mean of the two
      t_mass   envelope mass temperature (C)
      ach      ventilation rate of the free zone
      ac_w     sensible cooling actually delivered (W)
      ac_short True where the equipment could not hold setpoint
    """
    ua = dwell.conductances()
    f = float(np.clip(dwell.conditioned_fraction if dwell.has_cooling() else 0.0, 0.0, 1.0))
    single_zone = f >= 0.999 or f <= 0.001

    vol = dwell.volume_m3()
    c_air = dwell.air_capacitance()
    c_mass = dwell.mass_capacitance()
    ua_solid = ua["wall"] + ua["roof"]
    ua_air_mass = max(ua_solid * 2.0, 1.0)     # interior surface coupling
    ua_mass_out = max(ua_solid, 0.5)

    # Zone shares. With no cooling the whole dwelling is one free-running zone.
    f_cond = f if not single_zone else (1.0 if f >= 0.999 else 0.0)
    f_free = 1.0 - f_cond
    c_cond = max(c_air * f_cond, 1.0)
    c_free = max(c_air * f_free, 1.0)
    ua_zz = (RHO_CP_AIR * vol * min(f_cond, f_free) * dwell.interzone_ach / 3600.0
             if not single_zone else 0.0)

    n = len(weather.time_s)
    out = {k: np.zeros(n) for k in ("t_cond", "t_free", "t_home", "t_mass", "ach", "ac_w")}
    out["ac_short"] = np.zeros(n, dtype=bool)

    t0 = weather.t_out[0] + 2.0 if t_initial is None else t_initial
    t_cond = t_free = t_mass = t0

    # Choose an internal step from the stiffest node, then sub-step within each
    # input interval, interpolating the forcing.
    dt_in = float(np.median(np.diff(weather.time_s))) if n > 1 else 900.0
    tau_min = _min_time_constant(dwell, f_cond, ua)
    sub = int(np.clip(np.ceil(dt_in / (STABILITY_MARGIN * tau_min)), 1, MAX_SUBSTEPS))

    q_int_total = dwell.internal_gain_w_per_m2 * dwell.floor_area_m2
    setpoint = dwell.cooling_setpoint_c
    infil = dwell.infiltration_ach()
    vent_open = open_window_ach(dwell)

    for k in range(n):
        out["t_cond"][k], out["t_free"][k] = t_cond, t_free
        out["t_home"][k] = f_cond * t_cond + f_free * t_free
        out["t_mass"][k] = t_mass
        if k == n - 1:
            out["ach"][k], out["ac_w"][k] = out["ach"][k - 1], out["ac_w"][k - 1]
            break

        dt = (weather.time_s[k + 1] - weather.time_s[k]) / sub
        for s in range(sub):
            w = (s + 0.5) / sub  # midpoint of the sub-interval
            to = weather.t_out[k] * (1 - w) + weather.t_out[k + 1] * w
            dni = weather.solar_direct[k] * (1 - w) + weather.solar_direct[k + 1] * w
            dhi = weather.solar_diffuse[k] * (1 - w) + weather.solar_diffuse[k + 1] * w

            q_win, sol_air_rise = solar_gain_w(dwell, dni, dhi)
            t_sol_air = to + sol_air_rise

            # Free zone opens windows when that helps. The conditioned zone
            # keeps them shut -- nobody runs an air conditioner into an open
            # window, and the unit occupies the opening.
            ach_free = infil + (vent_open if (dwell.opens_windows and f_free > 0
                                              and wants_windows_open(t_free, to)) else 0.0)
            if single_zone and f_cond >= 0.999:
                ach_cond = infil
            elif single_zone:
                ach_cond = ach_free
            else:
                ach_cond = infil

            ua_vent_c = RHO_CP_AIR * vol * f_cond * ach_cond / 3600.0
            ua_vent_f = RHO_CP_AIR * vol * f_free * ach_free / 3600.0

            # --- conditioned zone -------------------------------------------
            q_cond = 0.0
            if f_cond > 0:
                q_cond = ((to - t_cond) * (ua["window"] * f_cond + ua_vent_c)
                          + (t_mass - t_cond) * ua_air_mass * f_cond
                          + (t_free - t_cond) * ua_zz
                          + q_win * f_cond + q_int_total * f_cond)

            # Ideal-load control: remove exactly enough to land on setpoint,
            # capped by what the equipment can deliver at this outdoor temp.
            ac = 0.0
            short = False
            if dwell.has_cooling() and f_cond > 0:
                cap = available_capacity_w(dwell, to)
                q_needed = q_cond - (setpoint - t_cond) * c_cond / dt
                if q_needed > 0:
                    ac = min(cap, q_needed)
                    short = q_needed > cap
                q_cond -= ac

            # --- free zone ---------------------------------------------------
            q_free = 0.0
            if f_free > 0:
                q_free = ((to - t_free) * (ua["window"] * f_free + ua_vent_f)
                          + (t_mass - t_free) * ua_air_mass * f_free
                          + (t_cond - t_free) * ua_zz
                          + q_win * f_free + q_int_total * f_free)

            q_mass = ((t_cond - t_mass) * ua_air_mass * f_cond
                      + (t_free - t_mass) * ua_air_mass * f_free
                      + (t_sol_air - t_mass) * ua_mass_out)

            if f_cond > 0:
                t_cond += dt * q_cond / c_cond
            if f_free > 0:
                t_free += dt * q_free / c_free
            t_mass += dt * q_mass / c_mass
            if f_cond <= 0:
                t_cond = t_free
            if f_free <= 0:
                t_free = t_cond

        out["ach"][k] = ach_free
        out["ac_w"][k] = ac
        out["ac_short"][k] = short

    out["substeps"] = sub
    out["tau_min_s"] = tau_min
    return out


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def night_mask(time_s: np.ndarray, start_hour: float = 0.0, end_hour: float = 6.0) -> np.ndarray:
    hours = (time_s / 3600.0) % 24.0
    return (hours >= start_hour) & (hours < end_hour)


def metrics(t_air: np.ndarray, time_s: np.ndarray, threshold: float = 26.0,
            warmup_s: float = 0.0) -> dict:
    """Exposure metrics for one temperature series.

    Degree-hours capture dose rather than just the worst instant, and the
    overnight minimum captures whether the body ever gets a recovery period.
    """
    # Discard spin-up: the first night reflects the initial condition, not the
    # building, and would otherwise dominate the overnight minimum.
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
