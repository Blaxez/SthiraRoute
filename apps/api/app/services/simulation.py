"""Shift simulation — a synthetic Bengaluru operating day.

Abstract "GPS ticks" prove nothing. This runs the fleet against a clock, so the
plan meets the things that actually break plans in an Indian city: peak-hour
congestion, monsoon rain, loading bays that overrun the manifest, trucks that
fail mid-shift, drivers who lose signal under a flyover, customers who cancel,
and orders that arrive after dispatch.

Two things keep it honest rather than theatrical:

  * every quantity is a modelling assumption gathered in `SimConfig`, so a
    reviewer can see and change them in one place instead of hunting the code;
  * the response to each disruption follows Plan.md §10.1's trigger table —
    mild traffic only refreshes ETAs, and a re-plan has to clear the churn gate
    before it is dispatched.

This is Plan.md §18 "India-conditioned digital twin" at demo scale.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.services.cities import City, city_list, get_city
from app.models import (
    Assignment,
    ConstraintOverlay,
    Depot,
    Route,
    RouteStatus,
    Shipment,
    ShipmentStatus,
    SimEvent,
    SimState,
    Stop,
    StopStatus,
    Vehicle,
    VehicleStatus,
)

SHIFT_START_MIN = 6 * 60
SHIFT_END_MIN = 22 * 60


@dataclass
class SimConfig:
    """Every assumption in one place. [Assumption] unless noted."""

    minutes_per_tick: int = 6
    # Free-flow urban running speed for an LCV, before congestion. Null means
    # "whatever this city runs at" — Mumbai's island roads are not Delhi's
    # arterials, and one global constant would flatten that difference.
    free_flow_kmh: float | None = None
    # Loading bays are unpredictable: service time is multiplied by noise in
    # [1 - v, 1 + 2v] — skewed late, because bays overrun far more often than
    # they underrun.
    service_variance: float = 0.4
    # Per-vehicle probability of a mechanical failure, per hour of driving.
    breakdown_per_vehicle_hour: float = 0.018
    # Fleet-wide probability that a corridor jams, per hour.
    congestion_per_hour: float = 0.35
    # Rate at which fresh orders arrive during the shift.
    new_order_per_hour: float = 0.45
    # Rate at which customers call off an undelivered order. Deliberately low:
    # cancellations are real, but a rate high enough to gut the seeded demand
    # also deletes the constraints the demo exists to show.
    cancel_per_hour: float = 0.12
    # Per-vehicle probability of losing GPS for a while, per hour.
    gps_dropout_per_vehicle_hour: float = 0.22
    # Probability the weather turns, per hour. Bengaluru gets afternoon rain.
    weather_change_per_hour: float = 0.20
    # Probability a depot bay backs up and holds a departure, per hour.
    warehouse_delay_per_hour: float = 0.18
    # Minutes of projected overrun before a stop is called at risk.
    sla_risk_margin_min: int = 10
    # A corridor needs time to settle; a fleet that reshuffles every six
    # minutes is unusable. (Plan.md §10.2 corridor cooldown.)
    reopt_cooldown_min: int = 30
    seed: int | None = 7


# Wet roads cost time before they close anything. Fog in Delhi does the same
# thing for a different reason, which is why the factor is keyed on severity
# rather than on precipitation.
WEATHER_FACTOR = {"clear": 1.0, "rain": 0.86, "storm": 0.68}


def weather_label(city: City, state: str) -> str:
    if state == "rain":
        return f"{city.rain_label} — slower going"
    if state == "storm":
        return f"{city.storm_label} — closures likely"
    return "clear"


def traffic_factor(
    clock_min: int, extra_congestion: float = 0.0, weather: str = "clear",
    city: City | None = None,
) -> float:
    """Speed multiplier now: 1.0 is free flow, 0.5 is crawling."""
    profile = (city or get_city(None)).traffic_by_hour
    base = profile.get((clock_min // 60) % 24, 1.0)
    base *= WEATHER_FACTOR.get(weather, 1.0)
    return max(0.15, base * (1.0 - extra_congestion))


def city_of(st: SimState) -> City:
    return get_city(getattr(st, "city", None))


def free_flow(cfg: SimConfig, city: City) -> float:
    """The configured speed if a reviewer set one, else the city's own."""
    return cfg.free_flow_kmh or city.free_flow_kmh


def traffic_label(factor: float) -> str:
    if factor >= 0.95:
        return "clear"
    if factor >= 0.75:
        return "moderate"
    if factor >= 0.55:
        return "heavy"
    return "gridlock"


def hhmm(minutes: int) -> str:
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


# ------------------------------------------------------------------ state --

# Fresh tallies for a day that has not started yet.
BLANK_RUNTIME = {
    "delivered": 0,
    "late": 0,
    "on_time": 0,
    "late_minutes": 0,
    "km_driven": 0.0,
    "adhoc_orders": 0,
    "cancellations": 0,
    "breakdowns": 0,
    "gps_dropouts": 0,
    "closures": 0,
    "reopts_committed": 0,
    "reopts_held": 0,
    "monitor_only": 0,
    "breaks_taken": [],
    "delayed_departures": [],
    # Closures already responded to, and when each ad-hoc order arrived.
    "handled_closures": [],
    "order_clock": {},
}


def get_state(db: Session) -> SimState:
    st = db.scalars(select(SimState).limit(1)).first()
    if not st:
        st = SimState(clock_min=SHIFT_START_MIN, running=False, config_json="{}")
        db.add(st)
        db.commit()
        db.refresh(st)
    return st


def get_config(st: SimState) -> SimConfig:
    try:
        stored = json.loads(st.config_json or "{}")
    except ValueError:
        return SimConfig()
    known = {f for f in SimConfig.__dataclass_fields__}
    return SimConfig(**{k: v for k, v in stored.items() if k in known})


def set_config(db: Session, **kw) -> SimState:
    st = get_state(db)
    cfg = asdict(get_config(st))
    cfg.update({k: v for k, v in kw.items() if v is not None})
    st.config_json = json.dumps(cfg)
    db.commit()
    db.refresh(st)
    return st


def get_runtime(st: SimState) -> dict:
    rt = dict(BLANK_RUNTIME)
    try:
        rt.update(json.loads(st.runtime_json or "{}"))
    except ValueError:
        pass
    return rt


def _bump(st: SimState, key: str, by=1) -> None:
    rt = get_runtime(st)
    rt[key] = (rt.get(key) or 0) + by
    st.runtime_json = json.dumps(rt)


def reset_clock(db: Session) -> SimState:
    """Rewind the day without touching the plan."""
    st = get_state(db)
    st.clock_min = SHIFT_START_MIN
    st.running = False
    st.congestion = 0.0
    st.congestion_until_min = 0
    st.weather = "clear"
    st.weather_until_min = 0
    st.scenario = None
    st.script_step = 0
    st.last_reopt_min = 0
    st.runtime_json = json.dumps(BLANK_RUNTIME)
    for e in db.scalars(select(SimEvent)):
        db.delete(e)
    for v in db.scalars(select(Vehicle)):
        v.path_progress_km = 0.0
        v.dwell_until_min = 0
        v.gps_stale_min = 0
    # Monsoon closures are simulation artefacts, not municipal policy — the
    # seeded curfews stay, anything the weather created goes.
    for o in db.scalars(select(ConstraintOverlay).where(ConstraintOverlay.kind == "closure")):
        db.delete(o)
    db.commit()
    db.refresh(st)
    return st


def _log(db: Session, st: SimState, kind: str, message: str, vehicle_id=None, shipment_id=None):
    db.add(
        SimEvent(
            clock_min=st.clock_min,
            kind=kind,
            message=message,
            vehicle_id=vehicle_id,
            shipment_id=shipment_id,
        )
    )


# --------------------------------------------------------------- geometry --


def _km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _cumulative(path: list[list[float]]) -> list[float]:
    out = [0.0]
    for (lo1, la1), (lo2, la2) in zip(path, path[1:]):
        out.append(out[-1] + _km(la1, lo1, la2, lo2))
    return out


def _point_at(path, cum, target):
    if target <= 0:
        return path[0][1], path[0][0]
    if target >= cum[-1]:
        return path[-1][1], path[-1][0]
    lo, hi = 0, len(cum) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if cum[mid] <= target:
            lo = mid
        else:
            hi = mid
    span = cum[hi] - cum[lo]
    f = (target - cum[lo]) / span if span > 1e-9 else 0.0
    return (
        path[lo][1] + (path[hi][1] - path[lo][1]) * f,
        path[lo][0] + (path[hi][0] - path[lo][0]) * f,
    )


def _arc_of(path, cum, lat, lon) -> float:
    best_i, best_d = 0, float("inf")
    for i, (plon, plat) in enumerate(path):
        d = (plat - lat) ** 2 + (plon - lon) ** 2
        if d < best_d:
            best_i, best_d = i, d
    return cum[best_i]


def _reanchor(v: Vehicle, path, cum, pending) -> None:
    """Put a truck back on the road it is now being told to drive.

    Committing a plan restarts the odometer, because a new route is a new path
    and kilometre 4 of the old one means nothing on the new one. For a truck
    still parked at its hub that is exactly right. For one already out in the
    city it is not: the next tick would read progress 0, place it at the depot
    and the marker would jump ten kilometres backwards — the fleet visibly
    teleporting every time the dispatcher accepts a re-plan.

    So when the odometer disagrees with the GPS, the GPS wins: the truck is
    re-anchored to the nearest point of its new route. It is never advanced
    past a stop it has not served — a projection that skipped a drop would have
    the simulation deliver freight the driver never reached.
    """
    told_lat, told_lon = _point_at(path, cum, v.path_progress_km or 0.0)
    if _km(v.lat, v.lon, told_lat, told_lon) < 0.5:
        return
    gate = min(
        (_arc_of(path, cum, s.lat, s.lon) for s in pending),
        default=cum[-1],
    )
    v.path_progress_km = max(0.0, min(_arc_of(path, cum, v.lat, v.lon), gate - 0.01))
    v.lat, v.lon = _point_at(path, cum, v.path_progress_km)


def _driver_style(vehicle_id: int) -> float:
    """A stable per-driver speed bias, 0.9–1.1. Not every driver is average."""
    return 0.9 + ((vehicle_id * 2654435761) % 200) / 1000.0


# ------------------------------------------------------------------- tick --


def _rng(cfg: SimConfig, clock_min: int) -> random.Random:
    """A fresh stream per tick, so a seeded day replays exactly.

    Seeding on the tick index as well as the seed matters: keyed on the seed
    alone every tick drew the same numbers, and every ad-hoc order landed on
    the same street.
    """
    if cfg.seed is None:
        return random.Random()
    return random.Random(f"{cfg.seed}:{clock_min}")


def tick(db: Session, rng: random.Random | None = None) -> dict:
    """Advance the shift by one step and return everything that happened."""
    st = get_state(db)
    cfg = get_config(st)
    rng = rng or _rng(cfg, st.clock_min)

    if st.clock_min >= SHIFT_END_MIN:
        st.running = False
        db.commit()
        return _snapshot(db, st, cfg, ["Shift is over — 22:00. Reset to run the day again."])

    st.clock_min += cfg.minutes_per_tick
    hours = cfg.minutes_per_tick / 60.0
    city = city_of(st)

    _expire_incidents(db, st)
    _roll_weather(db, st, cfg, rng, hours)

    factor = traffic_factor(st.clock_min, st.congestion, st.weather, city)
    speed = free_flow(cfg, city) * factor

    moved, km_this_tick = _advance_fleet(db, st, cfg, rng, hours, speed)

    fleet_events = _fleet_events(db, st, cfg, rng, hours)
    at_risk = _sla_risk(db, st, cfg, speed)

    _bump(st, "km_driven", round(km_this_tick, 2))
    db.commit()

    # Scripted beats fire before autopilot so the response lands in the same
    # tick the disruption does.
    script = _run_script(db, st, cfg) if st.scenario else []
    if script:
        at_risk = _sla_risk(db, st, cfg, speed, log=False)

    decision = _autopilot(db, st, cfg, at_risk) if st.autopilot else None

    snap = _snapshot(db, st, cfg, [])
    snap["moved"] = moved
    snap["at_risk"] = at_risk
    snap["events_this_tick"] = fleet_events + script
    snap["decision"] = decision
    return snap


def _expire_incidents(db: Session, st: SimState) -> None:
    if st.congestion and st.clock_min >= st.congestion_until_min:
        st.congestion = 0.0
        _log(db, st, "traffic", "Corridor cleared — traffic back to its normal profile.")
    for o in db.scalars(
        select(ConstraintOverlay).where(
            ConstraintOverlay.kind == "closure", ConstraintOverlay.active.is_(True)
        )
    ):
        if st.clock_min >= o.ban_end_min:
            o.active = False
            _log(db, st, "closure_cleared", f"{o.name} reopened — water has drained.")


def _roll_weather(db, st: SimState, cfg: SimConfig, rng, hours: float) -> None:
    city = city_of(st)
    if st.weather != "clear" and st.clock_min >= st.weather_until_min:
        was = st.weather
        st.weather = "clear"
        _log(
            db, st, "weather",
            f"{city.storm_label.capitalize() if was == 'storm' else city.rain_label.capitalize()}"
            " has cleared — roads back to normal.",
        )
        return
    if st.weather != "clear" or rng.random() >= cfg.weather_change_per_hour * hours:
        return
    # Each city has its own bad hours: Bengaluru's afternoon thunderstorm,
    # Mumbai's all-day monsoon, Delhi's winter fog before the sun burns it off.
    hour = (st.clock_min // 60) % 24
    lo, hi = city.storm_hours
    severe = rng.random() < (city.storm_bias if lo <= hour <= hi else 0.2)
    st.weather = "storm" if severe else "rain"
    st.weather_until_min = st.clock_min + rng.choice([45, 60, 90, 120])
    _log(
        db, st, "weather",
        f"{(city.storm_label if severe else city.rain_label).capitalize()} across "
        f"{city.label} — running speeds down "
        f"{int((1 - WEATHER_FACTOR[st.weather]) * 100)}% until about {hhmm(st.weather_until_min)}.",
    )
    if severe:
        _flood_closure(db, st, rng)


def _flood_closure(db: Session, st: SimState, rng) -> ConstraintOverlay:
    """Bad weather takes a corridor out. A hard constraint, not a cost."""
    city = city_of(st)
    name, lat, lon, radius = rng.choice(city.hazard_corridors)
    until = min(SHIFT_END_MIN, st.clock_min + rng.choice([90, 120, 180]))
    zone = ConstraintOverlay(
        name=f"Weather closure — {name}",
        kind="closure",
        center_lat=lat, center_lon=lon, radius_km=radius,
        ban_start_min=st.clock_min, ban_end_min=until,
        active=True,
        notes=f"Simulated {city.storm_label} closure. Production would take this "
              "from a city feed.",
    )
    db.add(zone)
    _bump(st, "closures")
    _log(
        db, st, "closure",
        f"{name} is impassable — corridor closed to freight until {hhmm(until)}. "
        "Vehicles routed through it need a new path.",
    )
    return zone


def _advance_fleet(db, st: SimState, cfg: SimConfig, rng, hours: float, speed: float):
    """Drive every working truck, deliver what it reaches, break what breaks."""
    moved = 0
    km_total = 0.0

    for v in db.scalars(select(Vehicle)):
        if v.status in (VehicleStatus.down, VehicleStatus.off_duty):
            continue
        route = db.scalars(
            select(Route)
            .options(selectinload(Route.stops))
            .where(Route.vehicle_id == v.id, Route.status == RouteStatus.committed)
            .order_by(Route.id.desc())
        ).first()
        if not route or v.lat is None:
            continue

        stops = sorted(route.stops, key=lambda s: s.seq)
        pending = [s for s in stops if s.kind == "delivery" and s.status == StopStatus.pending]

        path = route.geometry
        cum = _cumulative(path) if path and len(path) > 1 else None

        if cum:
            _reanchor(v, path, cum, pending)

        # GPS first: a dark truck is dark whether it is delivering or driving
        # home, and either way its marker must stop moving until it reports.
        dark = _handle_gps(db, st, cfg, rng, v, path, cum, speed)
        if dark:
            continue

        if not pending:
            # Still has the empty run home. A truck is not "available" until it
            # is actually back at the depot.
            if cum and (v.path_progress_km or 0) < cum[-1] - 0.05:
                step = speed * hours * _driver_style(v.id)
                before = v.path_progress_km or 0.0
                v.path_progress_km = min(before + step, cum[-1])
                v.lat, v.lon = _point_at(path, cum, v.path_progress_km)
                km_total += v.path_progress_km - before
                v.status = VehicleStatus.en_route
                moved += 1
            elif v.status != VehicleStatus.available:
                v.status = VehicleStatus.available
                _log(db, st, "returned", f"{v.code} is back at the depot, shift complete.", v.id)
            continue

        # --- sitting at a bay or on a break ---
        if v.dwell_until_min and st.clock_min < v.dwell_until_min:
            v.status = VehicleStatus.loading
            continue

        if _maybe_hold_at_depot(db, st, cfg, rng, v, hours):
            continue
        if _maybe_driver_break(db, st, rng, v):
            continue

        if not cum:
            continue

        v.status = VehicleStatus.en_route
        step = speed * hours * _driver_style(v.id)
        before = v.path_progress_km or 0.0
        v.path_progress_km = min(before + step, cum[-1])
        v.lat, v.lon = _point_at(path, cum, v.path_progress_km)
        km_total += v.path_progress_km - before
        moved += 1

        _serve_reached_stops(db, st, cfg, rng, v, pending, path, cum)

        if rng.random() < cfg.breakdown_per_vehicle_hour * hours:
            _break_down(db, st, v, pending)

    return moved, km_total


def _handle_gps(db, st, cfg: SimConfig, rng, v: Vehicle, path, cum, speed: float) -> bool:
    """Drop, hold or reconcile this vehicle's GPS. True means it stays dark.

    While dark the marker is frozen at the last fix — that is the honest thing
    to draw, and it is what the driver-offline scenario is about (Plan.md §13.6).
    The truck has still been moving, so recovery jumps it forward by dead
    reckoning rather than teleporting it back to where we last saw it.
    """
    hours = cfg.minutes_per_tick / 60.0

    if not v.gps_stale_min:
        if rng.random() >= cfg.gps_dropout_per_vehicle_hour * hours:
            return False
        v.gps_stale_min = cfg.minutes_per_tick
        _bump(st, "gps_dropouts")
        _log(
            db, st, "gps_lost",
            f"{v.code} has lost GPS. Showing last-known position with an uncertainty "
            "ring — the plan is unchanged.",
            v.id,
        )
        return True

    v.gps_stale_min += cfg.minutes_per_tick
    if v.gps_stale_min < 18 and rng.random() >= 0.35:
        return True

    blind_km = speed * (v.gps_stale_min / 60.0) * _driver_style(v.id)
    if cum:
        v.path_progress_km = min((v.path_progress_km or 0.0) + blind_km, cum[-1])
        v.lat, v.lon = _point_at(path, cum, v.path_progress_km)
    _log(
        db, st, "gps_back",
        f"{v.code} is reporting again after {v.gps_stale_min} min dark — position "
        f"reconciled {blind_km:.1f} km further along its route.",
        v.id,
    )
    v.gps_stale_min = 0
    return False


def _maybe_hold_at_depot(db, st, cfg: SimConfig, rng, v: Vehicle, hours: float) -> bool:
    """Loading bays back up. A truck that has not left yet can be held."""
    if (v.path_progress_km or 0) > 0.05:
        return False
    rt = get_runtime(st)
    if v.id in rt["delayed_departures"]:
        return False
    if rng.random() >= cfg.warehouse_delay_per_hour * hours:
        return False
    hold = rng.choice([15, 20, 30])
    v.dwell_until_min = st.clock_min + hold
    v.status = VehicleStatus.loading
    rt["delayed_departures"].append(v.id)
    st.runtime_json = json.dumps(rt)
    depot = db.get(Depot, v.depot_id)
    _log(
        db, st, "warehouse_delay",
        f"Bay congestion at {depot.name if depot else 'the depot'} — {v.code} held "
        f"{hold} min before departure. Downstream ETAs shift.",
        v.id,
    )
    return True


def _maybe_driver_break(db, st, rng, v: Vehicle) -> bool:
    """One statutory-ish break each, in the early afternoon."""
    if not (13 * 60 <= st.clock_min <= 14 * 60 + 30):
        return False
    rt = get_runtime(st)
    if v.id in rt["breaks_taken"] or rng.random() > 0.5:
        return False
    rest = rng.choice([20, 25, 30])
    v.dwell_until_min = st.clock_min + rest
    v.status = VehicleStatus.loading
    rt["breaks_taken"].append(v.id)
    st.runtime_json = json.dumps(rt)
    _log(db, st, "break", f"{v.code}'s driver is on a {rest} min break.", v.id)
    return True


def _serve_reached_stops(db, st, cfg: SimConfig, rng, v: Vehicle, pending, path, cum) -> None:
    """Deliver the next stop if the truck has driven past it. One bay at a time."""
    for stop in pending:
        if _arc_of(path, cum, stop.lat, stop.lon) > (v.path_progress_km or 0) + 1e-6:
            return
        ship = db.get(Shipment, stop.shipment_id) if stop.shipment_id else None
        stop.status = StopStatus.completed
        if ship:
            ship.status = ShipmentStatus.delivered

        late = st.clock_min - ship.tw_end_min if ship else 0
        stop.late_min = max(0, late)
        service = ship.service_min if ship else 10
        dwell = max(
            2,
            int(service * rng.uniform(1 - cfg.service_variance, 1 + 2 * cfg.service_variance)),
        )
        v.dwell_until_min = st.clock_min + dwell
        v.status = VehicleStatus.loading

        _bump(st, "delivered")
        if late > 0:
            _bump(st, "late")
            _bump(st, "late_minutes", late)
            _log(
                db, st, "late",
                f"{v.code} delivered {ship.code if ship else 'a stop'} {late} min after the "
                f"window closed ({hhmm(ship.tw_end_min) if ship else '--:--'}).",
                v.id, stop.shipment_id,
            )
        else:
            _bump(st, "on_time")
            _log(
                db, st, "delivered",
                f"{v.code} delivered {ship.code if ship else 'a stop'} on time; "
                f"{dwell} min at the bay.",
                v.id, stop.shipment_id,
            )
        return


def _break_down(db, st, v: Vehicle, pending) -> None:
    v.status = VehicleStatus.down
    for s in pending:
        sh = db.get(Shipment, s.shipment_id) if s.shipment_id else None
        if sh and sh.status == ShipmentStatus.in_transit:
            sh.status = ShipmentStatus.assigned
    _bump(st, "breakdowns")
    _log(
        db, st, "breakdown",
        f"{v.code} has broken down with {len(pending)} stop(s) undelivered. "
        "The plan in force is no longer executable.",
        v.id,
    )


def _fleet_events(db, st: SimState, cfg: SimConfig, rng, hours: float) -> list[dict]:
    """City-scale things that happen to nobody in particular."""
    out: list[dict] = []

    if not st.congestion and rng.random() < cfg.congestion_per_hour * hours:
        st.congestion = round(rng.uniform(0.2, 0.5), 2)
        st.congestion_until_min = st.clock_min + rng.choice([30, 45, 60])
        _log(
            db, st, "traffic",
            f"Congestion on a major corridor — speeds down {int(st.congestion * 100)}% "
            f"until about {hhmm(st.congestion_until_min)}.",
        )
        out.append({"kind": "traffic", "until": hhmm(st.congestion_until_min)})

    if rng.random() < cfg.new_order_per_hour * hours and st.clock_min < SHIFT_END_MIN - 150:
        out.append({"kind": "new_order", **_spawn_order(db, st, rng, cfg)})

    if rng.random() < cfg.cancel_per_hour * hours:
        cancelled = _cancel_order(db, st, rng)
        if cancelled:
            out.append({"kind": "cancel", **cancelled})

    return out


def _promise_min(db: Session, st: SimState, cfg: SimConfig | None, lat, lon, rng) -> int:
    """How long a dispatcher would quote for a walk-in order, in minutes.

    A flat two-hour promise is fine in a compact city and absurd for a Narela
    depot serving Faridabad: the ad-hoc orders in Delhi were arriving five hours
    late because the simulation promised something no fleet could hit. Quote
    from the drive time instead — roughly twice the one-way leg, plus an hour
    for the queue ahead of it, rounded to the half hour a human would say.
    """
    cfg = cfg or get_config(st)
    depots = list(db.scalars(select(Depot)))
    if not depots:
        return rng.choice([180, 240])
    km = min(_km(d.lat, d.lon, lat, lon) for d in depots)
    speed = max(12.0, current_speed(st, cfg))
    leg = km / speed * 60
    quoted = 60 + 2.0 * leg + rng.choice([0, 30])
    return int(min(360, max(120, round(quoted / 30) * 30)))


def _spawn_order(db: Session, st: SimState, rng, cfg: SimConfig | None = None) -> dict:
    """A customer rings up mid-shift, as customers do."""
    city = city_of(st)
    name, lat, lon = rng.choice(city.adhoc_spots)
    last = db.scalars(select(Shipment).order_by(Shipment.id.desc()).limit(1)).first()
    code = f"ADHOC-{(last.id if last else 0) + 1:03d}"
    kg = rng.choice([40, 60, 80, 110])
    ship = Shipment(
        code=code, customer_name=f"{name} (ad-hoc)", lat=lat, lon=lon,
        demand_kg=kg, demand_m3=round(kg / 160, 2),
        tw_start_min=st.clock_min,
        tw_end_min=min(SHIFT_END_MIN, st.clock_min + _promise_min(db, st, cfg, lat, lon, rng)),
        service_min=10, priority=rng.choice([1, 1, 2]),
        length_cm=rng.choice([70, 90, 100]), width_cm=60, height_cm=80,
    )
    db.add(ship)
    db.flush()
    rt = get_runtime(st)
    rt["order_clock"][str(ship.id)] = st.clock_min
    st.runtime_json = json.dumps(rt)
    _bump(st, "adhoc_orders")
    _log(
        db, st, "new_order",
        f"New order {code} at {name}, {kg} kg, due by {hhmm(ship.tw_end_min)}.",
        shipment_id=ship.id,
    )
    return {"code": code, "customer": ship.customer_name, "due": hhmm(ship.tw_end_min)}


def _cancel_order(db: Session, st: SimState, rng) -> dict | None:
    """Cancel something still undelivered, and free the capacity it was holding."""
    open_ships = list(
        db.scalars(
            select(Shipment).where(
                Shipment.status.in_(
                    (ShipmentStatus.pending, ShipmentStatus.assigned, ShipmentStatus.in_transit)
                )
            )
        )
    )
    # A cold-chain or priority load being "cancelled" is not the interesting
    # case, and dropping it would misrepresent how the objective tiers work.
    candidates = [s for s in open_ships if s.priority < 3]
    # Nobody phones in an order and calls it off six minutes later. Without
    # this every ad-hoc order died before a truck could reach it, so the
    # insertion logic never got to prove anything.
    rt = get_runtime(st)
    settled = [
        s for s in candidates
        if st.clock_min - rt["order_clock"].get(str(s.id), -10_000) > 60
    ]
    candidates = settled or candidates
    if not candidates:
        return None
    # Prefer orders this simulation invented. Cancelling a seeded shipment
    # quietly removes a constraint the demo is meant to demonstrate.
    adhoc = [s for s in candidates if s.code.startswith("ADHOC")]
    ship = rng.choice(adhoc or candidates)
    ship.status = ShipmentStatus.cancelled
    for stop in db.scalars(select(Stop).where(Stop.shipment_id == ship.id)):
        if stop.status == StopStatus.pending:
            stop.status = StopStatus.skipped
    _bump(st, "cancellations")
    _log(
        db, st, "cancel",
        f"{ship.customer_name} cancelled {ship.code} — the stop is dropped and the "
        "capacity is free again.",
        shipment_id=ship.id,
    )
    return {"code": ship.code, "customer": ship.customer_name}


def _sla_risk(
    db: Session, st: SimState, cfg: SimConfig, speed_kmh: float, log: bool = True
) -> list[dict]:
    """Which promises are we about to break, at the speed we are actually doing?

    `log` exists because the projection is also read on the injection path, and
    the same warning appearing three times in one minute reads as a bug.
    """
    risks: list[dict] = []
    for route in db.execute(
        select(Route).options(selectinload(Route.stops)).where(Route.status == RouteStatus.committed)
    ).scalars():
        veh = db.get(Vehicle, route.vehicle_id)
        if not veh or veh.status == VehicleStatus.down or veh.lat is None:
            continue
        path = route.geometry
        cum = _cumulative(path) if path and len(path) > 1 else None
        for stop in sorted(route.stops, key=lambda s: s.seq):
            if stop.kind != "delivery" or stop.status != StopStatus.pending:
                continue
            ship = db.get(Shipment, stop.shipment_id) if stop.shipment_id else None
            if not ship or ship.status == ShipmentStatus.cancelled:
                continue
            remaining_km = (
                max(0.0, _arc_of(path, cum, stop.lat, stop.lon) - (veh.path_progress_km or 0))
                if cum else _km(veh.lat, veh.lon, stop.lat, stop.lon)
            )
            eta = st.clock_min + int(remaining_km / max(speed_kmh, 1) * 60)
            over = eta - ship.tw_end_min
            if over > -cfg.sla_risk_margin_min:
                risks.append({
                    "shipment": ship.code, "vehicle": veh.code,
                    "projected": hhmm(eta), "due": hhmm(ship.tw_end_min),
                    "over_min": over,
                })
    if not log:
        return risks

    # Repeating an unchanged warning every six minutes buries the events that
    # did change. Only speak when the picture actually moves — a different
    # count, a different worst offender, or another five minutes of overrun.
    rt = get_runtime(st)
    if risks:
        worst = max(risks, key=lambda r: r["over_min"])
        signature = f"{len(risks)}:{worst['shipment']}:{worst['over_min'] // 5}"
        if rt.get("last_risk_sig") != signature:
            _log(
                db, st, "sla_risk",
                f"{len(risks)} delivery window(s) at risk at current speeds — worst is "
                f"{worst['shipment']} on {worst['vehicle']}, projected {worst['projected']} "
                f"against {worst['due']}.",
            )
            rt["last_risk_sig"] = signature
            st.runtime_json = json.dumps(rt)
    elif rt.get("last_risk_sig"):
        _log(db, st, "sla_clear", "Every remaining window is back inside its promise.")
        rt["last_risk_sig"] = None
        st.runtime_json = json.dumps(rt)
    return risks


# --------------------------------------------------------------- response --


def _autopilot(db: Session, st: SimState, cfg: SimConfig, at_risk: list[dict]) -> dict | None:
    """Classify what just happened and respond the way §10.1 says to.

    The important behaviour here is restraint. Mild traffic gets an ETA refresh
    and nothing else; only a stranded load, a stack of unassigned work, a live
    closure or a hard SLA breach is worth reshuffling a plan that drivers are
    already executing.
    """
    from app.models import RunTrigger

    pending = list(
        db.scalars(select(Shipment).where(Shipment.status == ShipmentStatus.pending))
    )
    stranded = _stranded_vehicles(db)
    closures = list(
        db.scalars(
            select(ConstraintOverlay).where(
                ConstraintOverlay.kind == "closure", ConstraintOverlay.active.is_(True)
            )
        )
    )
    hard_risk = [r for r in at_risk if r["over_min"] > 20]

    if stranded:
        trigger = RunTrigger.breakdown
        reason = f"a breakdown stranded undelivered freight on {stranded[0].code}"
        forced = True
    elif len(pending) >= 2:
        trigger = RunTrigger.insert
        reason = f"{len(pending)} order(s) were waiting for a vehicle"
        forced = False
    elif new_closures := [c for c in closures if c.id not in get_runtime(st)["handled_closures"]]:
        trigger = RunTrigger.traffic
        reason = f"{new_closures[0].name} closed the corridor under the plan"
        forced = False
        # Respond to a closure once. Re-planning every time the cooldown
        # expires while the water is still there produced eight identical runs
        # in one afternoon — churn dressed up as responsiveness.
        rt = get_runtime(st)
        rt["handled_closures"] = rt["handled_closures"] + [c.id for c in new_closures]
        st.runtime_json = json.dumps(rt)
    elif len(hard_risk) >= 2:
        # Re-plan for a breach set once. Mumbai produced fourteen runs against
        # the same four windows, every one of them returning the plan it started
        # from: if the same freight is still the problem, the solver has already
        # said this is the best it can do and running it again is theatre.
        sig = sorted(r["shipment"] for r in hard_risk)
        rt = get_runtime(st)
        if rt.get("last_sla_replan") == sig:
            _bump(st, "monitor_only")
            db.commit()
            return {
                "action": "monitor",
                "reason": (
                    f"{len(hard_risk)} window(s) still projected to breach, but the "
                    "same freight was already re-planned and the solver found no "
                    "better assignment. Holding."
                ),
            }
        rt["last_sla_replan"] = sig
        st.runtime_json = json.dumps(rt)
        trigger = RunTrigger.sla
        reason = f"{len(hard_risk)} window(s) are projected to breach by over 20 min"
        forced = False
    elif at_risk:
        _bump(st, "monitor_only")
        db.commit()
        return {
            "action": "monitor",
            "reason": (
                f"{len(at_risk)} window(s) drifting, none past 20 min — ETAs refreshed, "
                "plan held. Re-planning on drift this small is churn."
            ),
        }
    else:
        return None

    waited = st.clock_min - (st.last_reopt_min or 0)
    if not forced and waited < cfg.reopt_cooldown_min:
        return {
            "action": "cooldown",
            "reason": (
                f"{reason}, but the last re-plan was {waited} min ago — holding until the "
                f"{cfg.reopt_cooldown_min} min corridor cooldown expires."
            ),
        }

    return _replan(db, st, cfg, trigger, reason, forced)


def _stranded_vehicles(db: Session) -> list[Vehicle]:
    """Broken trucks that still have undelivered work on them."""
    out = []
    for v in db.scalars(select(Vehicle).where(Vehicle.status == VehicleStatus.down)):
        has_work = db.scalar(
            select(Stop.id)
            .join(Route, Route.id == Stop.route_id)
            .where(
                Route.vehicle_id == v.id,
                Route.status == RouteStatus.committed,
                Stop.status == StopStatus.pending,
                Stop.kind == "delivery",
            )
            .limit(1)
        )
        if has_work:
            out.append(v)
    return out


def _replan(db: Session, st: SimState, cfg: SimConfig, trigger, reason: str, forced: bool) -> dict:
    """Run a re-optimization, then respect the churn gate's answer."""
    from app.services.dynamic import _run_event_reopt
    from app.services.optimize import approve_run

    st.last_reopt_min = st.clock_min
    db.commit()

    try:
        run = _run_event_reopt(
            db, trigger, max(3, cfg.minutes_per_tick // 2),
            f"Autopilot re-planned at {hhmm(st.clock_min)} because {reason}.",
            forced=forced,
        )
    except Exception as exc:  # noqa: BLE001
        # A solver failure is a bad minute, not the end of the shift. The clock
        # keeps running and the incumbent plan stays in force, which is exactly
        # what a dispatcher would do.
        db.rollback()
        st = get_state(db)
        _log(
            db, st, "replan_failed",
            f"Autopilot tried to re-plan ({reason}) and the solve failed: "
            f"{type(exc).__name__}. Holding the plan in force.",
        )
        db.commit()
        return {"action": "failed", "reason": reason, "error": type(exc).__name__}
    metrics = json.loads(run.metrics_json or "{}")
    explain = json.loads(run.explain_json or "{}")
    gate = (explain.get("gain_gate") or {}).get("decision", "commit")
    gate_reason = (explain.get("gain_gate") or {}).get("reason", "")

    st = get_state(db)
    if run.status.value == "completed" and gate == "commit":
        approve_run(db, run.id)
        _bump(st, "reopts_committed")
        _log(
            db, st, "replan",
            f"Autopilot dispatched run #{run.id} — {reason}. "
            f"{metrics.get('vehicles_used', '?')} vehicles, "
            f"{metrics.get('unserved_count', 0)} unserved, "
            f"stability {metrics.get('stability_score', '?')}.",
        )
        action = "dispatched"
    else:
        _bump(st, "reopts_held")
        _log(
            db, st, "replan_held",
            f"Autopilot proposed run #{run.id} ({reason}) but the churn gate said hold. "
            "Waiting for the dispatcher.",
        )
        action = "held"
    db.commit()
    return {
        "action": action,
        "run_id": run.id,
        "trigger": trigger.value,
        "gate": gate,
        "gate_reason": gate_reason,
        "reason": reason,
        "reassigned": metrics.get("shipments_reassigned"),
        "stability": metrics.get("stability_score"),
    }


# ------------------------------------------------------- manual injection --

INJECTIONS = {
    "congestion": "Jam a corridor for the next hour",
    "storm": "Break the monsoon over the city",
    "closure": "Flood a corridor and close it to freight",
    "breakdown": "Break the busiest loaded truck",
    "new_order": "A customer rings in a fresh order",
    "cancel": "A customer calls one off",
    "gps_loss": "A driver goes dark under a flyover",
    "warehouse_delay": "Back up a loading bay",
}


def inject(db: Session, kind: str, rng: random.Random | None = None) -> dict:
    """Fire one disruption on demand, so a demo does not wait on probability."""
    st = get_state(db)
    cfg = get_config(st)
    rng = rng or _rng(cfg, st.clock_min + 977)

    if kind == "congestion":
        st.congestion = round(rng.uniform(0.3, 0.55), 2)
        st.congestion_until_min = st.clock_min + 60
        _log(
            db, st, "traffic",
            f"Dispatcher injected congestion — speeds down {int(st.congestion * 100)}% "
            f"until {hhmm(st.congestion_until_min)}.",
        )
        result = {"congestion": st.congestion}

    elif kind == "storm":
        city = city_of(st)
        st.weather = "storm"
        st.weather_until_min = min(SHIFT_END_MIN, st.clock_min + 120)
        _log(
            db, st, "weather",
            f"{city.storm_label.capitalize()} across {city.label} — running speeds "
            "down 32%.",
        )
        result = {"weather": st.weather}

    elif kind == "closure":
        zone = _flood_closure(db, st, rng)
        result = {"closure": zone.name, "until": hhmm(zone.ban_end_min)}

    elif kind == "breakdown":
        target = _busiest_loaded_vehicle(db)
        if not target:
            return {"error": "No vehicle is carrying undelivered work — approve a plan first."}
        pending = list(
            db.scalars(
                select(Stop)
                .join(Route, Route.id == Stop.route_id)
                .where(
                    Route.vehicle_id == target.id,
                    Route.status == RouteStatus.committed,
                    Stop.status == StopStatus.pending,
                    Stop.kind == "delivery",
                )
            )
        )
        _break_down(db, st, target, pending)
        result = {"vehicle": target.code, "stranded": len(pending)}

    elif kind == "new_order":
        result = _spawn_order(db, st, rng)

    elif kind == "cancel":
        cancelled = _cancel_order(db, st, rng)
        if not cancelled:
            return {"error": "Nothing left to cancel."}
        result = cancelled

    elif kind == "gps_loss":
        # A truck at a bay can lose signal too, but the interesting case is one
        # in motion, so prefer that.
        working = [
            v for v in db.scalars(select(Vehicle).where(Vehicle.gps_stale_min == 0))
            if v.status in (VehicleStatus.en_route, VehicleStatus.loading)
        ]
        v = next((v for v in working if v.status == VehicleStatus.en_route), None) or (
            working[0] if working else None
        )
        if not v:
            return {"error": "No vehicle is out on the road yet."}
        v.gps_stale_min = cfg.minutes_per_tick
        _bump(st, "gps_dropouts")
        _log(
            db, st, "gps_lost",
            f"{v.code} has lost GPS. Showing last-known position with an uncertainty "
            "ring — the plan is unchanged.",
            v.id,
        )
        result = {"vehicle": v.code}

    elif kind == "warehouse_delay":
        v = db.scalars(
            select(Vehicle).where(Vehicle.status != VehicleStatus.down).order_by(Vehicle.id)
        ).first()
        if not v:
            return {"error": "No vehicle to hold."}
        v.dwell_until_min = st.clock_min + 25
        v.status = VehicleStatus.loading
        depot = db.get(Depot, v.depot_id)
        _log(
            db, st, "warehouse_delay",
            f"Bay congestion at {depot.name if depot else 'the depot'} — {v.code} held 25 min.",
            v.id,
        )
        result = {"vehicle": v.code, "hold_min": 25}

    else:
        raise ValueError(f"unknown injection: {kind}")

    db.commit()
    decision = (
        _autopilot(db, st, cfg, _sla_risk(db, st, cfg, current_speed(st, cfg), log=False))
        if st.autopilot else None
    )
    snap = _snapshot(db, st, cfg, [])
    snap["injected"] = {"kind": kind, **result}
    snap["decision"] = decision
    return snap


def _busiest_loaded_vehicle(db: Session) -> Vehicle | None:
    """The truck whose failure actually costs something."""
    best, best_load = None, -1.0
    for route in db.execute(
        select(Route).options(selectinload(Route.stops)).where(Route.status == RouteStatus.committed)
    ).scalars():
        open_stops = [
            s for s in route.stops
            if s.kind == "delivery" and s.status == StopStatus.pending
        ]
        if not open_stops:
            continue
        v = db.get(Vehicle, route.vehicle_id)
        if not v or v.status == VehicleStatus.down:
            continue
        if route.total_load_kg > best_load:
            best, best_load = v, route.total_load_kg
    return best


def current_speed(st: SimState, cfg: SimConfig) -> float:
    city = city_of(st)
    return free_flow(cfg, city) * traffic_factor(
        st.clock_min, st.congestion, st.weather, city
    )


# ---------------------------------------------------------------- scripts --

# A presentable operating day: every beat maps to a row of Plan.md §10.1 and to
# a minute of the demo script in internals/05. Times are shift-clock minutes.
PLAYBOOKS: dict[str, dict] = {
    "full_day": {
        "label": "Full operating day",
        "blurb": "Peak congestion, an ad-hoc order, a breakdown, monsoon closure and a lost driver.",
        # Beats sit inside the morning-to-early-afternoon window because that is
        # when the demo fleet is actually carrying freight. A breakdown timed at
        # 17:00 lands on trucks already back at the depot and strands nothing,
        # which makes the system look like it ignored the event.
        "beats": [
            (7 * 60, "congestion", "Morning peak bites on the outer ring."),
            (7 * 60 + 30, "new_order", "A customer rings in a rush job."),
            (8 * 60 + 30, "breakdown", "The busiest loaded truck fails mid-route."),
            (9 * 60 + 30, "warehouse_delay", "A depot bay backs up."),
            (10 * 60 + 30, "storm", "Monsoon breaks over the city."),
            (11 * 60, "closure", "A corridor floods and closes to freight."),
            (12 * 60, "gps_loss", "A driver goes dark under a flyover."),
            (13 * 60, "cancel", "A customer calls one off."),
        ],
    },
    "disruption_drill": {
        "label": "Disruption drill",
        "blurb": "Back-to-back failures inside two hours — the stress case for the churn gate.",
        "beats": [
            (7 * 60, "new_order", "Late order lands before dispatch settles."),
            (7 * 60 + 30, "breakdown", "A truck fails with freight aboard."),
            (8 * 60, "closure", "The recovery corridor closes."),
            (8 * 60 + 30, "gps_loss", "The recovering vehicle loses signal."),
            (9 * 60, "new_order", "Another order arrives mid-recovery."),
        ],
    },
    "monsoon": {
        "label": "Monsoon day",
        "blurb": "Weather-driven: rain, two closures, and the ETA damage that follows.",
        "beats": [
            (9 * 60, "storm", "Heavy rain sets in."),
            (9 * 60 + 30, "closure", "First corridor floods."),
            (10 * 60 + 30, "closure", "A second corridor goes under."),
            (11 * 60 + 30, "congestion", "Everything backs up behind it."),
        ],
    },
}


def load_playbook(db: Session, scenario: str) -> SimState:
    if scenario not in PLAYBOOKS:
        raise ValueError(f"unknown playbook: {scenario}")
    st = get_state(db)
    st.scenario = scenario
    st.script_step = 0
    _log(
        db, st, "script",
        f"Loaded '{PLAYBOOKS[scenario]['label']}' — "
        f"{len(PLAYBOOKS[scenario]['beats'])} scripted events across the shift.",
    )
    db.commit()
    db.refresh(st)
    return st


def _run_script(db: Session, st: SimState, cfg: SimConfig) -> list[dict]:
    """Fire every scripted beat now due. Beats are ordered, so an index is enough."""
    book = PLAYBOOKS.get(st.scenario or "")
    if not book:
        return []
    fired = []
    while st.script_step < len(book["beats"]):
        at, kind, note = book["beats"][st.script_step]
        if at > st.clock_min:
            break
        st.script_step += 1
        db.commit()
        # Injections run their own autopilot pass; the tick's own pass follows.
        result = inject(db, kind)
        st = get_state(db)
        if "error" in result:
            # A beat that cannot fire has to say so. Silently skipping it makes
            # the fleet look like it ignored a disruption that never happened.
            _log(db, st, "script_skipped",
                 f"Scripted beat skipped — {note} ({result['error']})")
        else:
            _log(db, st, "script", f"Scripted beat — {note}")
        fired.append({"kind": kind, "note": note, "at": hhmm(at),
                      "result": result.get("injected", result)})
    db.commit()
    return fired


# --------------------------------------------------------------- snapshot --


def _snapshot(db: Session, st: SimState, cfg: SimConfig, notes: list[str]) -> dict:
    city = city_of(st)
    factor = traffic_factor(st.clock_min, st.congestion, st.weather, city)
    events = list(
        db.execute(select(SimEvent).order_by(SimEvent.id.desc()).limit(80)).scalars()
    )
    vehicles = list(db.scalars(select(Vehicle)))
    shipments = list(db.scalars(select(Shipment)))
    rt = get_runtime(st)

    by_status: dict[str, int] = {}
    for s in shipments:
        by_status[s.status.value] = by_status.get(s.status.value, 0) + 1

    closures = [
        {"name": o.name, "until": hhmm(o.ban_end_min),
         "lat": o.center_lat, "lon": o.center_lon, "radius_km": o.radius_km}
        for o in db.scalars(
            select(ConstraintOverlay).where(
                ConstraintOverlay.kind == "closure", ConstraintOverlay.active.is_(True)
            )
        )
    ]

    served = rt["on_time"] + rt["late"]
    open_states = (ShipmentStatus.pending, ShipmentStatus.assigned, ShipmentStatus.in_transit)
    undelivered = sum(1 for s in shipments if s.status in open_states)
    book = PLAYBOOKS.get(st.scenario or "")

    return {
        "clock_min": st.clock_min,
        "clock": hhmm(st.clock_min),
        "running": st.running,
        "autopilot": st.autopilot,
        "shift_over": st.clock_min >= SHIFT_END_MIN,
        "day_pct": round(
            100 * (st.clock_min - SHIFT_START_MIN) / (SHIFT_END_MIN - SHIFT_START_MIN), 1
        ),
        "minutes_per_tick": cfg.minutes_per_tick,
        "city": {
            "id": city.id,
            "label": city.label,
            "region": city.region,
            "center": {"lat": city.center[0], "lon": city.center[1]},
            "notes": city.notes,
        },
        "cities": city_list(),
        "traffic": {
            "factor": round(factor, 2),
            "label": traffic_label(factor),
            "speed_kmh": round(free_flow(cfg, city) * factor, 1),
            "incident": bool(st.congestion),
            "incident_until": hhmm(st.congestion_until_min) if st.congestion else None,
        },
        "weather": {
            "state": st.weather,
            "label": weather_label(city, st.weather),
            "until": hhmm(st.weather_until_min) if st.weather != "clear" else None,
        },
        "closures": closures,
        "fleet": {
            "en_route": sum(1 for v in vehicles if v.status == VehicleStatus.en_route),
            "loading": sum(1 for v in vehicles if v.status == VehicleStatus.loading),
            "down": sum(1 for v in vehicles if v.status == VehicleStatus.down),
            "idle": sum(1 for v in vehicles if v.status == VehicleStatus.available),
            "dark": sum(1 for v in vehicles if v.gps_stale_min),
        },
        "vehicles": [
            {
                "id": v.id, "code": v.code, "status": v.status.value,
                "gps_stale_min": v.gps_stale_min,
                "dwell_until": hhmm(v.dwell_until_min) if v.dwell_until_min > st.clock_min else None,
                "progress_km": round(v.path_progress_km or 0, 1),
            }
            for v in vehicles
        ],
        "shipments": by_status,
        "scorecard": {
            "delivered": rt["delivered"],
            "on_time": rt["on_time"],
            "late": rt["late"],
            "late_minutes": rt["late_minutes"],
            "on_time_pct": round(100 * rt["on_time"] / served, 1) if served else None,
            "undelivered": undelivered,
            "km_driven": round(rt["km_driven"], 1),
            "adhoc_orders": rt["adhoc_orders"],
            "cancellations": rt["cancellations"],
            "breakdowns": rt["breakdowns"],
            "gps_dropouts": rt["gps_dropouts"],
            "closures": rt["closures"],
            "reopts_committed": rt["reopts_committed"],
            "reopts_held": rt["reopts_held"],
            "monitor_only": rt["monitor_only"],
        },
        "scenario": (
            {
                "id": st.scenario,
                "label": book["label"],
                "blurb": book["blurb"],
                "step": st.script_step,
                "total": len(book["beats"]),
                # The whole score, not just the next bar: the dispatcher can see
                # what is coming and when, which is most of what makes a
                # scripted demo legible rather than magic.
                "beats": [
                    {
                        "at": hhmm(at), "at_min": at, "kind": kind, "note": note,
                        "fired": i < st.script_step,
                    }
                    for i, (at, kind, note) in enumerate(book["beats"])
                ],
                "next": (
                    {"at": hhmm(book["beats"][st.script_step][0]),
                     "note": book["beats"][st.script_step][2]}
                    if st.script_step < len(book["beats"]) else None
                ),
            }
            if book else None
        ),
        "playbooks": [
            {"id": k, "label": v["label"], "blurb": v["blurb"], "beats": len(v["beats"])}
            for k, v in PLAYBOOKS.items()
        ],
        "injections": INJECTIONS,
        "events": [
            {
                "id": e.id, "at": hhmm(e.clock_min), "kind": e.kind,
                "message": e.message, "vehicle_id": e.vehicle_id,
            }
            for e in events
        ],
        "notes": notes,
        "config": asdict(cfg),
    }


def snapshot(db: Session) -> dict:
    st = get_state(db)
    return _snapshot(db, st, get_config(st), [])


def switch_city(db: Session, city_id: str) -> dict:
    """Rebuild the whole demo in another metro.

    Cities are not a skin. Changing one swaps the depots, the demand, the fleet
    registration, the municipal restriction that binds, the corridors that
    flood and the congestion profile — so the plan that comes out is a
    different plan, for local reasons.
    """
    from app.services.demo_seed import seed

    city = get_city(city_id)
    # The seeder runs in its own session and truncates the tables this session
    # has already read, so let go of everything before and after.
    db.commit()
    db.expire_all()
    seed(do_reset=True, city_id=city.id)
    db.expire_all()

    st = get_state(db)
    st.city = city.id
    st.clock_min = SHIFT_START_MIN
    st.runtime_json = json.dumps(BLANK_RUNTIME)
    db.commit()
    _log(
        db, st, "script",
        f"Now running {city.label} ({city.region}) — {len(city.depots)} depots, "
        f"{len(city.vehicles)} vehicles, {len(city.shipments)} orders. {city.notes}",
    )
    db.commit()
    return _snapshot(
        db, get_state(db), get_config(get_state(db)),
        [f"{city.label} loaded. Prepare the shift to plan and dispatch it."],
    )


def restore_demand(db: Session) -> int:
    """Put the day's work back: undo yesterday so today can be planned.

    Rewinding the clock alone is not enough. After a full shift every shipment
    is delivered, so the next plan has nothing to solve and the demo runs on an
    empty city — which is precisely the state a judge hits when they ask to see
    it a second time. Orders the simulation invented are dropped rather than
    revived; the seeded demand is what the day is supposed to be about.
    """
    for route in db.scalars(select(Route)):
        route.status = RouteStatus.superseded
    for stop in db.scalars(select(Stop)):
        stop.status = StopStatus.pending
        stop.late_min = 0
    revived = 0
    for ship in db.scalars(select(Shipment)):
        if ship.code.startswith("ADHOC"):
            for stop in db.scalars(select(Stop).where(Stop.shipment_id == ship.id)):
                db.delete(stop)
            for a in db.scalars(select(Assignment).where(Assignment.shipment_id == ship.id)):
                db.delete(a)
            db.delete(ship)
            continue
        if ship.status != ShipmentStatus.pending:
            ship.status = ShipmentStatus.pending
            revived += 1
    for a in db.scalars(select(Assignment)):
        a.active = False
    for v in db.scalars(select(Vehicle)):
        v.status = VehicleStatus.available
        v.path_progress_km = 0.0
        v.dwell_until_min = 0
        v.gps_stale_min = 0
        depot = db.get(Depot, v.depot_id)
        if depot:
            v.lat, v.lon = depot.lat, depot.lon
    db.commit()
    return revived


def prepare_shift(db: Session, solve_seconds: int = 8) -> dict:
    """Get to 06:00 with a dispatched plan, in one call.

    The demo's weakest moment used to be the setup: plan, approve, reset the
    clock, remember to turn autopilot on. One button removes four chances to
    fumble in front of judges.
    """
    from app.models import OptimizationRun, RunStatus, RunTrigger
    from app.services.optimize import approve_run, execute_optimization_run

    reset_clock(db)
    revived = restore_demand(db)
    if revived:
        st = get_state(db)
        _log(
            db, st, "script",
            f"Reset the day's work — {revived} shipment(s) back to pending, "
            "ad-hoc orders from the last run cleared.",
        )
        db.commit()

    run = OptimizationRun(trigger=RunTrigger.plan, status=RunStatus.queued,
                          solve_seconds=solve_seconds)
    db.add(run)
    db.commit()
    db.refresh(run)
    run = execute_optimization_run(db, run.id)

    st = get_state(db)
    if run.status != RunStatus.completed:
        _log(db, st, "script", f"Shift preparation failed: {run.error or run.status.value}")
        db.commit()
        snap = _snapshot(db, st, get_config(st), ["Planning failed — nothing dispatched."])
        snap["run_id"] = run.id
        return snap

    approve_run(db, run.id)
    st = get_state(db)
    st.autopilot = True
    metrics = json.loads(run.metrics_json or "{}")
    _log(
        db, st, "script",
        f"Shift prepared at 06:00 — run #{run.id} dispatched: "
        f"{metrics.get('vehicles_used', '?')} vehicles, "
        # Metre precision on a shift total is noise in a sentence a human reads.
        f"{round(metrics.get('total_distance_km') or 0)} km, "
        f"{metrics.get('unserved_count', 0)} unserved.",
    )
    db.commit()
    snap = _snapshot(db, get_state(db), get_config(st), ["Plan dispatched. Press play to run the day."])
    snap["run_id"] = run.id
    return snap
