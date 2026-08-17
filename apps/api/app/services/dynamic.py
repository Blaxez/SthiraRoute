"""Dynamic re-optimization: breakdown, insert, gain gate, GPS/geofence."""

from __future__ import annotations

import json
import math
import random

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models import (
    Assignment,
    OptimizationRun,
    Route,
    RouteStatus,
    RunStatus,
    RunTrigger,
    Shipment,
    ShipmentStatus,
    Stop,
    StopStatus,
    Vehicle,
    VehicleStatus,
)
from app.services.optimize import _load_run, execute_optimization_run
from app.services.solver import CostModel

# Arrival geofence for automatic stop completion.
ARRIVAL_RADIUS_KM = 0.35


def _incumbent_vehicle_map(db: Session) -> dict[int, int]:
    rows = db.scalars(select(Assignment).where(Assignment.active.is_(True)))
    return {a.shipment_id: a.vehicle_id for a in rows}


def _incumbent_ops_cost(db: Session, cost: CostModel) -> float | None:
    """Operating cost of the plan currently in force, in INR."""
    routes = list(
        db.scalars(select(Route).where(Route.status == RouteStatus.committed))
    )
    if not routes:
        return None
    km = sum(r.total_distance_km for r in routes)
    return km * cost.per_km_inr + len(routes) * cost.vehicle_fixed_inr


def _stability_metrics(before: dict[int, int], after_routes: list) -> dict:
    after: dict[int, int] = {}
    for r in after_routes:
        for st in r.stops:
            if st.shipment_id:
                after[st.shipment_id] = r.vehicle_id
    moved = sum(1 for sid, vid in after.items() if sid in before and before[sid] != vid)
    kept = sum(1 for sid, vid in after.items() if before.get(sid) == vid)
    return {
        "shipments_reassigned": moved,
        "shipments_kept_same_vehicle": kept,
        "stability_score": round(1.0 - (moved / max(len(after), 1)), 3),
    }


def evaluate_gain_gate(
    db: Session,
    run: OptimizationRun,
    reassigned: int,
    incumbent_ops: float | None,
    cost: CostModel | None = None,
    forced: bool = False,
) -> dict:
    """Plan.md §5.4 gain gate: is this reshuffle worth the operational churn?

    Accept when the ops saving beats the churn cost, or when the new plan
    rescues service (fewer unserved / less lateness). Otherwise recommend
    keeping the incumbent — the dispatcher still has the final say (§15).
    """
    cost = cost or CostModel()
    metrics = json.loads(run.metrics_json or "{}")
    new_ops = metrics.get("ops_cost_inr")

    churn_cost = reassigned * cost.churn_inr
    if incumbent_ops is None or new_ops is None:
        return {
            "decision": "commit",
            "reason": "No incumbent plan in force — nothing to churn.",
            "churn_cost_inr": round(churn_cost, 2),
        }
    if reassigned == 0:
        return {
            "decision": "commit",
            "reason": "No shipment changes vehicle — zero churn, nothing to gate.",
            "incumbent_ops_inr": round(incumbent_ops, 2),
            "proposed_ops_inr": round(new_ops, 2),
            "delta_ops_inr": round(new_ops - incumbent_ops, 2),
            "churn_cost_inr": 0.0,
            "net_inr": round(new_ops - incumbent_ops, 2),
        }

    delta_ops = new_ops - incumbent_ops  # negative == cheaper
    net = delta_ops + churn_cost

    # A breakdown removes a truck the plan in force depends on. There is no
    # incumbent left to hold, so the gate reports the price rather than
    # pretending "keep the old plan" is on the table.
    if forced:
        return {
            "decision": "commit",
            "reason": (
                f"The plan in force is no longer executable — the vehicle is out. "
                f"Recovering costs Rs {max(0.0, delta_ops):,.0f} more plus "
                f"Rs {churn_cost:,.0f} of churn across {reassigned} shipment(s)."
            ),
            "incumbent_ops_inr": round(incumbent_ops, 2),
            "proposed_ops_inr": round(new_ops, 2),
            "delta_ops_inr": round(delta_ops, 2),
            "churn_cost_inr": round(churn_cost, 2),
            "net_inr": round(net, 2),
            "forced": True,
        }
    # Service is only "rescued" if the new plan beats the plan in force — a
    # shipment that is impossible in both is not a reason to reshuffle.
    incumbent_late = db.scalar(
        select(func.count(Stop.id))
        .join(Route, Route.id == Stop.route_id)
        .where(Route.status == RouteStatus.committed, Stop.late_min > 0)
    ) or 0
    rescues_service = metrics.get("total_late_min", 0) < incumbent_late

    if net < 0:
        decision, reason = "commit", (
            f"Saves Rs {abs(delta_ops):,.0f} in operating cost against "
            f"Rs {churn_cost:,.0f} of churn — net Rs {abs(net):,.0f} better."
        )
    elif rescues_service:
        decision, reason = "commit", (
            f"Costs Rs {delta_ops:,.0f} more but cuts lateness from "
            f"{incumbent_late} late stop(s) to {metrics.get('total_late_min', 0)} min; "
            "service outranks cost in the objective tiers."
        )
    elif delta_ops > 0:
        decision, reason = "hold", (
            f"Costs Rs {delta_ops:,.0f} more and moves {reassigned} shipment(s) "
            f"(Rs {churn_cost:,.0f} of churn) without improving service. "
            "Dispatcher can override."
        )
    else:
        decision, reason = "hold", (
            f"Saves only Rs {-delta_ops:,.0f} against Rs {churn_cost:,.0f} of churn — "
            f"not worth reshuffling {reassigned} shipment(s). Dispatcher can override."
        )

    return {
        "decision": decision,
        "reason": reason,
        "incumbent_ops_inr": round(incumbent_ops, 2),
        "proposed_ops_inr": round(new_ops, 2),
        "delta_ops_inr": round(delta_ops, 2),
        "churn_cost_inr": round(churn_cost, 2),
        "net_inr": round(net, 2),
    }


def mark_vehicle_down(db: Session, vehicle_id: int) -> Vehicle:
    v = db.get(Vehicle, vehicle_id)
    if not v:
        raise ValueError("vehicle not found")
    v.status = VehicleStatus.down
    # Freight on a dead truck is no longer "in transit" — it is stranded and
    # must be free to move to another vehicle.
    for a in db.scalars(
        select(Assignment).where(
            Assignment.vehicle_id == vehicle_id, Assignment.active.is_(True)
        )
    ):
        ship = db.get(Shipment, a.shipment_id)
        if ship and ship.status == ShipmentStatus.in_transit:
            ship.status = ShipmentStatus.assigned
    db.commit()
    db.refresh(v)
    return v


def _run_event_reopt(
    db: Session,
    trigger: RunTrigger,
    solve_seconds: int | None,
    event_note: str,
    extra_metrics: dict | None = None,
    forced: bool = False,
) -> OptimizationRun:
    """Shared path for every event-driven re-optimization."""
    cost = CostModel()
    before = _incumbent_vehicle_map(db)
    incumbent_ops = _incumbent_ops_cost(db, cost)

    seconds = solve_seconds or max(3, settings.default_solve_seconds // 2)
    run = OptimizationRun(trigger=trigger, status=RunStatus.queued, solve_seconds=seconds)
    db.add(run)
    db.commit()
    db.refresh(run)

    run = execute_optimization_run(db, run.id)
    if run.status != RunStatus.completed:
        return run

    explain = json.loads(run.explain_json or "{}")
    metrics = json.loads(run.metrics_json or "{}")

    stab = _stability_metrics(before, run.routes)
    metrics.update(stab)
    metrics.update(extra_metrics or {})

    gate = evaluate_gain_gate(
        db, run, stab["shipments_reassigned"], incumbent_ops, cost, forced=forced
    )
    metrics["gain_gate"] = gate["decision"]

    explain["event"] = trigger.value
    explain["stability"] = stab
    explain["gain_gate"] = gate
    explain["summary"] = (
        f"{event_note} Reassigned {stab['shipments_reassigned']} shipment(s); "
        f"stability {stab['stability_score']}. Gate: {gate['reason']} "
        + explain.get("summary", "")
    )

    run.metrics_json = json.dumps(metrics)
    run.explain_json = json.dumps(explain)
    db.commit()
    return _load_run(db, run.id)


def reoptimize_after_breakdown(
    db: Session, vehicle_id: int, solve_seconds: int | None = None
) -> OptimizationRun:
    count = len(
        list(
            db.scalars(
                select(Assignment).where(
                    Assignment.vehicle_id == vehicle_id, Assignment.active.is_(True)
                )
            )
        )
    )
    veh = mark_vehicle_down(db, vehicle_id)
    return _run_event_reopt(
        db,
        RunTrigger.breakdown,
        solve_seconds,
        f"Breakdown on {veh.code}: {count} stranded shipment(s) redistributed.",
        {"broken_vehicle_id": vehicle_id, "stranded_shipments": count},
        forced=True,
    )


def reoptimize_after_insert(
    db: Session, solve_seconds: int | None = None, shipment_code: str | None = None
) -> OptimizationRun:
    return _run_event_reopt(
        db,
        RunTrigger.insert,
        solve_seconds,
        f"New shipment {shipment_code or ''} injected mid-shift.".replace("  ", " "),
    )


def reoptimize_after_constraint_change(
    db: Session, solve_seconds: int | None = None, note: str = "Constraint overlay changed."
) -> OptimizationRun:
    return _run_event_reopt(db, RunTrigger.traffic, solve_seconds, note)


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _cumulative_km(path: list[list[float]]) -> list[float]:
    """Arc length at each vertex of a [[lon,lat], ...] polyline."""
    out = [0.0]
    for (lon1, lat1), (lon2, lat2) in zip(path, path[1:]):
        out.append(out[-1] + _km(lat1, lon1, lat2, lon2))
    return out


def _point_at_km(path: list[list[float]], cum: list[float], target: float) -> tuple[float, float]:
    """Interpolate a position at `target` km along the polyline."""
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
    lon = path[lo][0] + (path[hi][0] - path[lo][0]) * f
    lat = path[lo][1] + (path[hi][1] - path[lo][1]) * f
    return lat, lon


def _arc_at(path: list[list[float]], cum: list[float], lat: float, lon: float) -> float:
    """Distance along the polyline of the vertex nearest this point.

    Stops are waypoints of the route OSRM returned, so the path passes through
    them; the nearest vertex is where the truck actually serves the customer.
    """
    best_i, best_d = 0, float("inf")
    for i, (plon, plat) in enumerate(path):
        d = (plat - lat) ** 2 + (plon - lon) ** 2
        if d < best_d:
            best_i, best_d = i, d
    return cum[best_i]


def simulate_gps_step(db: Session, step_km: float | None = None) -> list[Vehicle]:
    """Advance each working vehicle along the road it was actually routed down.

    The truck follows the OSRM polyline rather than flying straight at the next
    stop, so the marker tracks the drawn route and the arrival geofence fires
    where the road reaches the customer. Falls back to straight-line travel only
    when no road geometry is stored.

    Doubles as the geofence trigger: crossing ARRIVAL_RADIUS_KM of a stop
    completes it and moves the shipment through in_transit -> delivered.
    """
    step_km = step_km or settings.gps_step_km
    updated: list[Vehicle] = []
    for v in db.scalars(select(Vehicle)):
        if v.status in (VehicleStatus.down, VehicleStatus.off_duty):
            continue
        route = db.scalars(
            select(Route)
            .options(selectinload(Route.stops))
            .where(Route.vehicle_id == v.id, Route.status == RouteStatus.committed)
            .order_by(Route.id.desc())
        ).first()
        if not route or v.lat is None or v.lon is None:
            continue

        pending = [
            s for s in sorted(route.stops, key=lambda s: s.seq)
            if s.kind == "delivery" and s.status == StopStatus.pending
        ]
        if not pending:
            v.status = VehicleStatus.available
            v.path_progress_km = 0.0
            updated.append(v)
            continue

        v.status = VehicleStatus.en_route
        nxt = pending[0]
        path = route.geometry

        if path and len(path) > 1:
            cum = _cumulative_km(path)
            v.path_progress_km = min((v.path_progress_km or 0.0) + step_km, cum[-1])
            v.lat, v.lon = _point_at_km(path, cum, v.path_progress_km)
            # Completion is decided on distance travelled, not on proximity at
            # the instant we happen to sample. A 4 km tick straddles a stop
            # entirely, so a proximity test would drive straight past it.
            reached = [
                s for s in pending
                if _arc_at(path, cum, s.lat, s.lon) <= v.path_progress_km + 1e-6
            ]
        else:
            # No road geometry — degrade to a straight run at the same speed.
            d = _km(v.lat, v.lon, nxt.lat, nxt.lon)
            f = min(1.0, step_km / d) if d > 1e-6 else 1.0
            v.lat += (nxt.lat - v.lat) * f
            v.lon += (nxt.lon - v.lon) * f
            reached = [
                s for s in pending
                if _km(v.lat, v.lon, s.lat, s.lon) <= ARRIVAL_RADIUS_KM
            ]

        if nxt.shipment_id:
            ship = db.get(Shipment, nxt.shipment_id)
            if ship and ship.status == ShipmentStatus.assigned:
                ship.status = ShipmentStatus.in_transit

        # Drops are completed in route order — a truck cannot deliver stop 5
        # before stop 4 just because the road passes closer to it.
        for stop in pending:
            if stop not in reached:
                break
            stop.status = StopStatus.completed
            if stop.shipment_id:
                ship = db.get(Shipment, stop.shipment_id)
                if ship:
                    ship.status = ShipmentStatus.delivered
        updated.append(v)

    db.commit()
    for v in updated:
        db.refresh(v)
    return updated
