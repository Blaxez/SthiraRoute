from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models import (
    Assignment,
    ConstraintOverlay,
    Depot,
    OptimizationRun,
    Route,
    RouteStatus,
    RunStatus,
    RunTrigger,
    Shipment,
    ShipmentStatus,
    SimState,
    Stop,
    Vehicle,
    VehicleStatus,
)
from app.services.matrix import LatLon, road_geometry
from app.services.packing import PackItem, PackResult, pack_route
from app.services.solver import (
    CostModel,
    NoEntryZone,
    ShipmentInput,
    VehicleInput,
    solve_cvrptw,
)


def pack_route_for(vehicle: Vehicle, stops, ship_by_id: dict) -> PackResult:
    """Build pack items from a solved route's delivery stops and pack them."""
    items = []
    for st in stops:
        if st.kind != "delivery" or not st.shipment_id:
            continue
        s = ship_by_id.get(st.shipment_id)
        if not s:
            continue
        items.append(
            PackItem(
                shipment_id=s.id,
                code=s.code,
                seq=st.seq,
                length_cm=s.length_cm,
                width_cm=s.width_cm,
                height_cm=s.height_cm,
                weight_kg=s.demand_kg,
                fragile=s.fragile,
                stackable=s.stackable,
            )
        )
    return pack_route(
        items,
        vehicle.deck_length_cm,
        vehicle.deck_width_cm,
        vehicle.deck_height_cm,
        payload_kg=vehicle.capacity_kg,
    )


def _route_geometry_json(stops) -> str | None:
    """Ask the routing engine for the path actually driven through these stops."""
    pts = [LatLon(s.lat, s.lon) for s in stops]
    geom = road_geometry(pts)
    return json.dumps(geom) if geom else None


def _pack_all(result, veh_by_id: dict, ship_by_id: dict) -> dict[int, PackResult]:
    """Load-plan every route in a solve result, keyed by vehicle id."""
    if result.status != "completed":
        return {}
    return {
        rr.vehicle_id: pack_route_for(veh_by_id[rr.vehicle_id], rr.stops, ship_by_id)
        for rr in result.routes
    }

# A truck mid-shift is still a plannable truck. Only down / off-duty vehicles
# leave the pool — filtering on `available` alone silently emptied the fleet
# after the first GPS tick.
PLANNABLE_VEHICLE_STATES = (
    VehicleStatus.available,
    VehicleStatus.en_route,
    VehicleStatus.loading,
)

# Everything not yet finished is in scope for replanning.
OPEN_SHIPMENT_STATES = (
    ShipmentStatus.pending,
    ShipmentStatus.assigned,
    ShipmentStatus.in_transit,
)


def _incumbent(db: Session) -> dict[int, tuple[int, int | None]]:
    """shipment_id -> (vehicle_id, seq) from the committed plan.

    This is the R0 that the stability term is measured against, so it must be
    read *before* anything touches the assignment table.
    """
    out: dict[int, tuple[int, int | None]] = {}
    rows = db.execute(
        select(Assignment, Stop.seq)
        .join(Stop, (Stop.route_id == Assignment.route_id)
              & (Stop.shipment_id == Assignment.shipment_id), isouter=True)
        .where(Assignment.active.is_(True))
    ).all()
    for assignment, seq in rows:
        out[assignment.shipment_id] = (assignment.vehicle_id, seq)
    return out


def create_and_run_optimization(
    db: Session,
    trigger: RunTrigger = RunTrigger.plan,
    solve_seconds: int | None = None,
    depot_id: int | None = None,
) -> OptimizationRun:
    seconds = solve_seconds or settings.default_solve_seconds
    run = OptimizationRun(
        trigger=trigger,
        status=RunStatus.queued,
        solve_seconds=seconds,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    if settings.optimize_in_process:
        return execute_optimization_run(db, run.id, depot_id=depot_id)
    # Phase 2: enqueue to Redis worker
    run.status = RunStatus.queued
    db.commit()
    return run


def execute_optimization_run(
    db: Session, run_id: int, depot_id: int | None = None
) -> OptimizationRun:
    run = db.get(OptimizationRun, run_id)
    if not run:
        raise ValueError("run not found")

    run.status = RunStatus.running
    db.commit()

    try:
        incumbent = _incumbent(db)

        q = select(Vehicle).where(Vehicle.status.in_(PLANNABLE_VEHICLE_STATES))
        if depot_id is not None:
            q = q.where(Vehicle.depot_id == depot_id)
        vehicles = list(db.scalars(q))
        shipments = list(
            db.scalars(select(Shipment).where(Shipment.status.in_(OPEN_SHIPMENT_STATES)))
        )

        v_in = []
        for v in vehicles:
            depot = db.get(Depot, v.depot_id)
            if not depot:
                continue
            v_in.append(
                VehicleInput(
                    id=v.id,
                    code=v.code,
                    capacity_kg=v.capacity_kg,
                    capacity_m3=v.capacity_m3,
                    features=frozenset(
                        f.strip() for f in (v.features or "").split(",") if f.strip()
                    ),
                    depot_lat=depot.lat,
                    depot_lon=depot.lon,
                )
            )

        s_in = []
        for s in shipments:
            prior_vehicle, prior_seq = incumbent.get(s.id, (None, None))
            s_in.append(
                ShipmentInput(
                    id=s.id,
                    code=s.code,
                    lat=s.lat,
                    lon=s.lon,
                    demand_kg=s.demand_kg,
                    demand_m3=s.demand_m3,
                    tw_start_min=s.tw_start_min,
                    tw_end_min=s.tw_end_min,
                    service_min=s.service_min,
                    priority=s.priority,
                    requires_feature=s.requires_feature or "",
                    prior_vehicle_id=prior_vehicle,
                    prior_seq=prior_seq,
                    # Freight already on the truck cannot change vehicles.
                    locked_vehicle_id=(
                        prior_vehicle
                        if s.status == ShipmentStatus.in_transit
                        else None
                    ),
                )
            )

        # Municipal curfews *and* weather closures. Both forbid freight inside a
        # radius for a window, and leaving closures out meant the autopilot
        # announced "the corridor closed, re-planning" and then routed straight
        # back through the water.
        overlays = list(
            db.scalars(
                select(ConstraintOverlay).where(
                    ConstraintOverlay.active.is_(True),
                    ConstraintOverlay.kind.in_(("no_entry", "closure")),
                )
            )
        )
        zones = [
            NoEntryZone(
                name=o.name,
                center_lat=o.center_lat,
                center_lon=o.center_lon,
                radius_km=o.radius_km,
                ban_start_min=o.ban_start_min,
                ban_end_min=o.ban_end_min,
            )
            for o in overlays
        ]

        ship_by_id = {s.id: s for s in shipments}
        veh_by_id = {v.id: v for v in vehicles}

        # Plan from the shift clock, not from the start of the day. Without this
        # a mid-afternoon re-plan is scored against windows it has already
        # missed, so lateness looks free.
        sim_state = db.scalars(select(SimState).limit(1)).first()
        now_min = sim_state.clock_min if sim_state else None

        result = solve_cvrptw(
            v_in, s_in, solve_seconds=run.solve_seconds,
            no_entry_zones=zones, cost=CostModel(), now_min=now_min,
        )

        # Routing reasons about volume; the deck reasons about geometry. When a
        # route turns out not to be loadable, hand the solver the capacity the
        # packer actually achieved and let it try once more. One retry only —
        # this is a correction, not a search loop.
        packs = _pack_all(result, veh_by_id, ship_by_id)
        if result.status == "completed" and any(not p.feasible for p in packs.values()):
            derated = False
            for rr in result.routes:
                pack = packs[rr.vehicle_id]
                if pack.feasible:
                    continue
                achieved_m3 = sum(
                    p.length_cm * p.width_cm * p.height_cm for p in pack.placements
                ) / 1e6
                for vi in v_in:
                    if vi.id == rr.vehicle_id and achieved_m3 > 0:
                        vi.capacity_m3 = round(achieved_m3, 3)
                        derated = True
            if derated:
                # Half the budget: the retry only has to re-place a few stops,
                # and a re-optimization must still land inside its latency SLA.
                retry = solve_cvrptw(
                    v_in, s_in, solve_seconds=max(2, run.solve_seconds // 2),
                    no_entry_zones=zones, cost=CostModel(), now_min=now_min,
                )
                retry_packs = _pack_all(retry, veh_by_id, ship_by_id)
                improved = sum(1 for p in retry_packs.values() if not p.feasible) < sum(
                    1 for p in packs.values() if not p.feasible
                )
                if retry.status == "completed" and improved:
                    result, packs = retry, retry_packs
                    result.explain["load_retry"] = (
                        "Re-planned once after the load packer rejected a route; "
                        "affected vehicles were re-costed at their achievable "
                        "stowage volume rather than their nominal capacity."
                    )

        if result.status == "infeasible":
            run.status = RunStatus.infeasible
            run.error = result.error
            run.explain_json = json.dumps(result.explain)
            run.metrics_json = json.dumps(result.metrics)
            run.finished_at = datetime.utcnow()
            db.commit()
            return _load_run(db, run.id)

        if result.status != "completed":
            run.status = RunStatus.failed
            run.error = result.error or "solve failed"
            run.explain_json = json.dumps(
                result.explain
                or {
                    "summary": (
                        f"Could not plan: {result.error or 'solver failed'}. "
                        "No routes were produced, so the plan in force is unchanged."
                    ),
                    "hints": [
                        "Return a vehicle to service",
                        "Check that at least one vehicle is not marked down",
                    ],
                }
            )
            run.metrics_json = json.dumps(result.metrics or {})
            run.finished_at = datetime.utcnow()
            db.commit()
            return _load_run(db, run.id)

        load_summary: list[dict] = []

        for rr in result.routes:
            pack = packs[rr.vehicle_id]
            route = Route(
                vehicle_id=rr.vehicle_id,
                optimization_run_id=run.id,
                version=1,
                status=RouteStatus.candidate,
                total_distance_km=rr.total_distance_km,
                total_load_kg=rr.total_load_kg,
                total_load_m3=rr.total_load_m3,
                load_pct=rr.load_pct,
                load_plan_json=json.dumps(pack.as_dict()),
                load_feasible=pack.feasible,
                geometry_json=_route_geometry_json(rr.stops),
            )
            db.add(route)
            db.flush()
            load_summary.append(
                {
                    "vehicle_code": rr.vehicle_code,
                    "feasible": pack.feasible,
                    "volume_utilization_pct": pack.volume_utilization_pct,
                    "cog_x_pct": pack.cog_x_pct,
                    "cog_ok": pack.cog_ok,
                    "lifo_ok": pack.lifo_ok,
                    "unplaced": [u.code for u in pack.unplaced],
                }
            )
            for st in rr.stops:
                db.add(
                    Stop(
                        route_id=route.id,
                        shipment_id=st.shipment_id,
                        seq=st.seq,
                        kind=st.kind,
                        lat=st.lat,
                        lon=st.lon,
                        eta_min=st.eta_min,
                        late_min=st.late_min,
                    )
                )

        result.metrics["load_feasible_routes"] = sum(
            1 for r in load_summary if r["feasible"]
        )
        result.metrics["load_infeasible_routes"] = sum(
            1 for r in load_summary if not r["feasible"]
        )
        result.metrics["avg_volume_utilization_pct"] = (
            round(sum(r["volume_utilization_pct"] for r in load_summary) / len(load_summary), 1)
            if load_summary else 0.0
        )
        result.explain["load_plan"] = load_summary
        blocked = [r for r in load_summary if not r["feasible"]]
        if blocked:
            result.explain["load_warning"] = (
                f"{len(blocked)} route(s) cannot be physically loaded under LIFO "
                "unloading. Review the load plan before dispatching: "
                + ", ".join(r["vehicle_code"] for r in blocked)
            )

        run.status = RunStatus.completed
        run.objective = result.objective
        run.metrics_json = json.dumps(result.metrics)
        run.explain_json = json.dumps(result.explain)
        run.finished_at = datetime.utcnow()
        run.error = None
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        run = db.get(OptimizationRun, run_id)
        if run is None:
            # The run row is gone — the demo dataset was rebuilt underneath a
            # solve that was already in flight. Nothing to record the failure
            # on, and raising here would take the caller down with it.
            raise RuntimeError(
                f"optimization run {run_id} disappeared mid-solve "
                f"(after {type(exc).__name__}: {exc})"
            ) from exc
        run.status = RunStatus.failed
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = datetime.utcnow()
        db.commit()

    return _load_run(db, run_id)


def _load_run(db: Session, run_id: int) -> OptimizationRun:
    run = db.execute(
        select(OptimizationRun)
        .options(selectinload(OptimizationRun.routes).selectinload(Route.stops))
        .where(OptimizationRun.id == run_id)
    ).scalar_one_or_none()
    if run is None:
        # ValueError so the router turns it into a 400/404, not a 500.
        raise ValueError(f"run {run_id} not found")
    return run


def approve_run(db: Session, run_id: int) -> OptimizationRun:
    run = _load_run(db, run_id)
    if run.status != RunStatus.completed:
        raise ValueError("only completed runs can be approved")

    for r in db.scalars(select(Route).where(Route.status == RouteStatus.committed)):
        r.status = RouteStatus.superseded

    for a in list(db.scalars(select(Assignment))):
        db.delete(a)
    db.flush()

    planned: set[int] = set()
    for route in run.routes:
        route.status = RouteStatus.committed
        # A new route means a new path — restart the odometer.
        veh = db.get(Vehicle, route.vehicle_id)
        if veh:
            veh.path_progress_km = 0.0
        for stop in route.stops:
            if not stop.shipment_id:
                continue
            planned.add(stop.shipment_id)
            ship = db.get(Shipment, stop.shipment_id)
            # An in-transit shipment stays in-transit; approving a plan must not
            # rewind work the driver has already started.
            if ship and ship.status == ShipmentStatus.pending:
                ship.status = ShipmentStatus.assigned
            db.add(
                Assignment(
                    shipment_id=stop.shipment_id,
                    route_id=route.id,
                    vehicle_id=route.vehicle_id,
                    active=True,
                )
            )

    # Anything the solver could not place goes back to the unassigned pool and
    # stays visible to the dispatcher — never silently published. (Plan.md D16)
    for ship in db.scalars(select(Shipment).where(Shipment.status.in_(OPEN_SHIPMENT_STATES))):
        if ship.id not in planned and ship.status != ShipmentStatus.in_transit:
            ship.status = ShipmentStatus.pending

    db.commit()
    return _load_run(db, run_id)
