"""Live KPIs and the honest baseline comparison (Plan.md success criteria)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.models import (
    Depot,
    OptimizationRun,
    Route,
    RouteStatus,
    RunStatus,
    Shipment,
    ShipmentStatus,
    Stop,
    StopStatus,
    Vehicle,
)
from app.services.optimize import OPEN_SHIPMENT_STATES, PLANNABLE_VEHICLE_STATES
from app.services.solver import (
    CostModel,
    ShipmentInput,
    VehicleInput,
    solve_greedy_baseline,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _committed(db: Session) -> list[Route]:
    return list(
        db.execute(
            select(Route)
            .options(selectinload(Route.stops))
            .where(Route.status == RouteStatus.committed)
        ).scalars()
    )


@router.get("/kpis")
def kpis(db: Session = Depends(get_db)):
    cost = CostModel()
    routes = _committed(db)
    vehicles = list(db.scalars(select(Vehicle)))
    shipments = list(db.scalars(select(Shipment)))

    committed_km = sum(r.total_distance_km for r in routes)
    carried_kg = sum(r.total_load_kg for r in routes)
    used_vehicle_ids = {r.vehicle_id for r in routes}
    used_capacity = sum(v.capacity_kg for v in vehicles if v.id in used_vehicle_ids)

    delivery_stops = [s for r in routes for s in r.stops if s.kind == "delivery"]
    late_stops = [s for s in delivery_stops if s.late_min > 0]

    # Deadhead = the depot return legs, i.e. distance driven with an empty box.
    empty_km = 0.0
    for r in routes:
        stops = sorted(r.stops, key=lambda s: s.seq)
        if len(stops) >= 2 and stops[-1].kind == "depot":
            empty_km += _leg_km(stops[-2], stops[-1])

    last = db.scalars(
        select(OptimizationRun)
        .where(OptimizationRun.status == RunStatus.completed)
        .order_by(OptimizationRun.id.desc())
    ).first()
    last_metrics = json.loads(last.metrics_json or "{}") if last else {}
    solve_latency = None
    if last and last.finished_at and last.created_at:
        solve_latency = round((last.finished_at - last.created_at).total_seconds(), 2)

    by_status: dict[str, int] = {}
    for s in shipments:
        by_status[s.status.value] = by_status.get(s.status.value, 0) + 1

    return {
        "fleet": {
            "total": len(vehicles),
            "in_use": len(used_vehicle_ids),
            "down": sum(1 for v in vehicles if v.status.value == "down"),
            "idle": len(vehicles) - len(used_vehicle_ids),
        },
        "shipments": {
            "total": len(shipments),
            "by_status": by_status,
            "unassigned": by_status.get("pending", 0),
        },
        "plan": {
            "committed_routes": len(routes),
            "total_distance_km": round(committed_km, 2),
            "empty_km": round(empty_km, 2),
            "empty_km_pct": round(100 * empty_km / committed_km, 1) if committed_km else 0.0,
            "ops_cost_inr": round(
                committed_km * cost.per_km_inr + len(routes) * cost.vehicle_fixed_inr, 2
            ),
            "capacity_utilization_pct": (
                round(100 * carried_kg / used_capacity, 1) if used_capacity else 0.0
            ),
            "on_time_pct": (
                round(100 * (len(delivery_stops) - len(late_stops)) / len(delivery_stops), 1)
                if delivery_stops
                else 100.0
            ),
            "late_stops": len(late_stops),
            "stops_completed": sum(
                1 for s in delivery_stops if s.status == StopStatus.completed
            ),
            "stops_total": len(delivery_stops),
        },
        "last_run": {
            "id": last.id if last else None,
            "trigger": last.trigger.value if last else None,
            "latency_s": solve_latency,
            "stability_score": last_metrics.get("stability_score"),
            "gain_gate": last_metrics.get("gain_gate"),
            "unserved_count": last_metrics.get("unserved_count", 0),
            "matrix_source": last_metrics.get("matrix_source"),
        },
    }


@router.get("/benchmark")
def benchmark(db: Session = Depends(get_db)):
    """Optimized plan vs a greedy whiteboard baseline on the same demand."""
    cost = CostModel()
    vehicles = list(
        db.scalars(select(Vehicle).where(Vehicle.status.in_(PLANNABLE_VEHICLE_STATES)))
    )
    shipments = list(
        db.scalars(select(Shipment).where(Shipment.status.in_(OPEN_SHIPMENT_STATES)))
    )
    v_in = []
    for v in vehicles:
        d = db.get(Depot, v.depot_id)
        if d:
            v_in.append(
                VehicleInput(
                    id=v.id, code=v.code, capacity_kg=v.capacity_kg,
                    capacity_m3=v.capacity_m3, depot_lat=d.lat, depot_lon=d.lon,
                    features=frozenset(
                        f.strip() for f in (v.features or "").split(",") if f.strip()
                    ),
                )
            )
    s_in = [
        ShipmentInput(
            id=s.id, code=s.code, lat=s.lat, lon=s.lon, demand_kg=s.demand_kg,
            demand_m3=s.demand_m3, tw_start_min=s.tw_start_min,
            tw_end_min=s.tw_end_min, service_min=s.service_min, priority=s.priority,
            requires_feature=s.requires_feature or "",
        )
        for s in shipments
    ]
    base = solve_greedy_baseline(v_in, s_in, cost)

    last = db.scalars(
        select(OptimizationRun)
        .where(OptimizationRun.status == RunStatus.completed)
        .order_by(OptimizationRun.id.desc())
    ).first()
    opt = json.loads(last.metrics_json or "{}") if last else {}

    opt_km = opt.get("total_distance_km")
    opt_cost = opt.get("ops_cost_inr")
    improvement = None
    if opt_km and base["total_distance_km"]:
        improvement = {
            "distance_pct": round(
                100 * (base["total_distance_km"] - opt_km) / base["total_distance_km"], 1
            ),
            "cost_pct": (
                round(100 * (base["ops_cost_inr"] - opt_cost) / base["ops_cost_inr"], 1)
                if opt_cost and base["ops_cost_inr"]
                else None
            ),
        }

    return {
        "baseline": base,
        "optimized": {
            "run_id": last.id if last else None,
            "total_distance_km": opt_km,
            "vehicles_used": opt.get("vehicles_used"),
            "ops_cost_inr": opt_cost,
            "served": (opt.get("shipments", 0) - opt.get("unserved_count", 0)) or None,
            "on_time_pct": opt.get("on_time_pct"),
            "method": "OR-Tools GLS, time windows + curfews + stability",
        },
        "improvement": improvement,
        "note": (
            "Baseline ignores time windows and municipal curfews, so it is an "
            "optimistic lower bound on distance for an infeasible plan."
        ),
    }


def _leg_km(a: Stop, b: Stop) -> float:
    import math

    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = math.radians(b.lat - a.lat), math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))
