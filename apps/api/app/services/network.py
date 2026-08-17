"""The one picture every stakeholder is supposed to share.

PS2 names four failures: idle trucks, bad routes, no live picture, and
desks that do not talk to each other. The dispatcher, the dock, the driver
and the consignee must therefore read the same committed plan — not four
copies of it. This module is that plan, sliced for each desk.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Depot,
    Route,
    RouteStatus,
    Shipment,
    SimState,
    Stop,
    Vehicle,
)


def _hhmm(m: int | None) -> str | None:
    if m is None:
        return None
    return f"{int(m) // 60:02d}:{int(m) % 60:02d}"


def _clock(db: Session) -> tuple[int, str]:
    st = db.get(SimState, 1)
    minutes = st.clock_min if st else 6 * 60
    return minutes, _hhmm(minutes) or "06:00"


def _committed(db: Session) -> list[Route]:
    return list(
        db.execute(
            select(Route)
            .options(selectinload(Route.stops))
            .where(Route.status == RouteStatus.committed)
        ).scalars()
    )


def _pack(route: Route) -> dict:
    if not route.load_plan_json:
        return {}
    try:
        return json.loads(route.load_plan_json)
    except ValueError:
        return {}


def _index_plan(routes: list[Route]) -> dict[int, tuple[Route, Stop]]:
    """shipment_id → (route, delivery stop) on the committed plan."""
    out: dict[int, tuple[Route, Stop]] = {}
    for route in routes:
        for stop in route.stops:
            if stop.kind == "delivery" and stop.shipment_id:
                out[stop.shipment_id] = (route, stop)
    return out


def consignment_row(
    ship: Shipment,
    *,
    route: Route | None = None,
    stop: Stop | None = None,
    vehicle: Vehicle | None = None,
) -> dict:
    return {
        "code": ship.code,
        "customer": ship.customer_name,
        "status": ship.status.value,
        "priority": ship.priority,
        "kg": ship.demand_kg,
        "m3": ship.demand_m3,
        "requires_feature": ship.requires_feature or "",
        "tw_start_min": ship.tw_start_min,
        "tw_end_min": ship.tw_end_min,
        "window": f"{_hhmm(ship.tw_start_min)}–{_hhmm(ship.tw_end_min)}",
        "lat": ship.lat,
        "lon": ship.lon,
        "vehicle_id": vehicle.id if vehicle else None,
        "vehicle_code": vehicle.code if vehicle else None,
        "vehicle_lat": vehicle.lat if vehicle else None,
        "vehicle_lon": vehicle.lon if vehicle else None,
        "route_id": route.id if route else None,
        "eta_min": stop.eta_min if stop else None,
        "eta": _hhmm(stop.eta_min) if stop else None,
        "late_min": stop.late_min if stop else 0,
        "stop_status": stop.status.value if stop else None,
        "stop_seq": stop.seq if stop else None,
    }


def board(db: Session) -> dict:
    """Dispatcher / dock / driver / consignee — one payload, four slices."""
    clock_min, clock = _clock(db)
    vehicles = list(db.scalars(select(Vehicle)))
    shipments = list(db.scalars(select(Shipment)))
    ships = {s.id: s for s in shipments}
    depots = {d.id: d for d in db.scalars(select(Depot))}
    routes = _committed(db)
    by_id = {v.id: v for v in vehicles}
    planned = _index_plan(routes)

    dock = []
    drivers = []
    for route in routes:
        vehicle = by_id.get(route.vehicle_id)
        if not vehicle:
            continue
        pack = _pack(route)
        drops = []
        next_stop = None
        for stop in sorted(route.stops, key=lambda s: s.seq):
            if stop.kind != "delivery":
                continue
            ship = ships.get(stop.shipment_id)
            row = {
                "seq": stop.seq,
                "stop_id": stop.id,
                "status": stop.status.value,
                "eta_min": stop.eta_min,
                "eta": _hhmm(stop.eta_min),
                "late_min": stop.late_min,
                "code": ship.code if ship else None,
                "customer": ship.customer_name if ship else None,
                "kg": ship.demand_kg if ship else None,
                "fragile": bool(ship.fragile) if ship else False,
            }
            drops.append(row)
            if next_stop is None and stop.status.value != "completed":
                next_stop = row
        dock.append(
            {
                "vehicle_id": vehicle.id,
                "code": vehicle.code,
                "depot": depots[vehicle.depot_id].name if vehicle.depot_id in depots else "",
                "status": vehicle.status.value,
                "route_id": route.id,
                "load_pct": route.load_pct,
                "kg": route.total_load_kg,
                "m3": route.total_load_m3,
                "distance_km": round(route.total_distance_km, 1),
                "load_feasible": route.load_feasible,
                "lifo_ok": pack.get("lifo_ok", True),
                "cog_ok": pack.get("cog_ok", True),
                "volume_utilization_pct": pack.get("volume_utilization_pct", 0),
                "placements": pack.get("placements") or [],
                "container": pack.get("container") or {},
                "cog_x_pct": pack.get("cog_x_pct", 0),
                "drops": drops,
                "drop_count": len(drops),
                "delivered": sum(1 for d in drops if d["status"] == "completed"),
            }
        )
        drivers.append(
            {
                "vehicle_id": vehicle.id,
                "code": vehicle.code,
                "status": vehicle.status.value,
                "next": next_stop,
                "delivered": sum(1 for d in drops if d["status"] == "completed"),
                "remaining": sum(1 for d in drops if d["status"] != "completed"),
            }
        )

    consignments = []
    for ship in shipments:
        hit = planned.get(ship.id)
        route, stop = hit if hit else (None, None)
        vehicle = by_id.get(route.vehicle_id) if route else None
        remaining = 0
        if stop and route:
            remaining = sum(
                1
                for s in route.stops
                if s.kind == "delivery"
                and s.status.value != "completed"
                and s.seq < stop.seq
            )
        consignments.append({
            **consignment_row(ship, route=route, stop=stop, vehicle=vehicle),
            "stops_ahead": remaining,
            "same_plan": route is not None,
        })

    used = {r.vehicle_id for r in routes}
    load_pcts = [r.load_pct for r in routes if r.load_pct is not None]
    by_status: dict[str, int] = {}
    for s in shipments:
        by_status[s.status.value] = by_status.get(s.status.value, 0) + 1
    delivery_stops = [s for r in routes for s in r.stops if s.kind == "delivery"]
    late = [s for s in delivery_stops if s.late_min > 0]

    return {
        "clock": clock,
        "clock_min": clock_min,
        "ps2": {
            "monitor": {
                "vehicles": len(vehicles),
                "live": sum(1 for v in vehicles if (v.gps_stale_min or 0) == 0),
                "stale": sum(1 for v in vehicles if (v.gps_stale_min or 0) > 0),
                "down": sum(1 for v in vehicles if v.status.value == "down"),
            },
            "optimize": {
                "routes": len(routes),
                "on_time_pct": (
                    round(100 * (len(delivery_stops) - len(late)) / len(delivery_stops), 1)
                    if delivery_stops
                    else None
                ),
                "unplanned": by_status.get("pending", 0),
            },
            "allocate": {
                "trucks_used": len(used),
                "trucks_idle": len(vehicles) - len(used),
                "avg_load_pct": round(sum(load_pcts) / len(load_pcts), 1) if load_pcts else 0,
                "all_loadable": all(r.load_feasible for r in routes) if routes else True,
            },
            "track": {"by_status": by_status, "total": len(shipments)},
        },
        "dock": dock,
        "drivers": drivers,
        "consignments": consignments,
    }


def track_code(db: Session, code: str) -> dict | None:
    ship = db.scalars(select(Shipment).where(Shipment.code == code)).first()
    if not ship:
        return None
    routes = _committed(db)
    planned = _index_plan(routes)
    hit = planned.get(ship.id)
    route, stop = hit if hit else (None, None)
    vehicle = db.get(Vehicle, route.vehicle_id) if route else None
    row = consignment_row(ship, route=route, stop=stop, vehicle=vehicle)
    remaining = []
    if route:
        remaining = [
            s.seq
            for s in sorted(route.stops, key=lambda x: x.seq)
            if s.kind == "delivery"
            and s.status.value != "completed"
            and (stop is None or s.seq < stop.seq)
        ]
    row["stops_ahead"] = len(remaining)
    row["same_plan"] = True
    return row
