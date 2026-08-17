"""OR-Tools HF-CVRPTW solver — core optimization engine.

Implements the Plan.md §5.4 tiered objective in a single weighted model:

    min  alpha_v*C_vehicles + C_dist + alpha_l*C_lateness
         + sigma*C_stability + P_drop*C_unserved

Tier separation is achieved by weight magnitude, not by lexicographic passes:

    tier 0/1  unserved shipment   >> everything else   (serve the demand)
    tier 1    lateness            >> operating cost    (SLA before rupees)
    tier 2    distance + fixed vehicle cost            (operating cost)
    tier 3    stability (churn)   ~ operating cost     (gain-gated by design)

Stability sits *in the objective*, not as a post-hoc metric: reassigning a
shipment away from its incumbent vehicle costs `churn_cost_inr`, so the solver
only reshuffles a truck when it genuinely saves more than the churn is worth.
That is the Plan.md §10.2 gain gate expressed as arc cost.

Everything is money. Costs are integers in paise (INR * 100) so OR-Tools gets
exact integer arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from app.services.matrix import LatLon, build_matrices, haversine_km

# Objective scaling: 1 cost unit = 1 paise. Keeps OR-Tools in exact integers.
COST_SCALE = 100

# Planning horizon guard rails (minutes from midnight).
DAY_START_MIN = 6 * 60
DAY_END_MIN = 22 * 60


@dataclass
class CostModel:
    """Rupee weights for the §5.4 objective. Tune per fleet, not per demo."""

    # alpha_v — deploying one more truck (driver shift + fixed overhead)
    vehicle_fixed_inr: float = 800.0
    # operating cost per km (fuel + tyres + maintenance, Indian LCV baseline)
    per_km_inr: float = 22.0
    # alpha_l — SLA breach cost per minute late (dominates distance on purpose)
    late_per_min_inr: float = 30.0
    # sigma — cost of moving a shipment off its incumbent vehicle
    churn_inr: float = 150.0
    # P_drop — base cost of failing to serve a shipment, scaled by priority.
    # Must exceed the cost of any realistic single-stop detour, or the solver
    # will "optimize" by abandoning far customers.
    unserved_inr: float = 25000.0

    def drop_penalty(self, priority: int) -> int:
        return int(self.unserved_inr * max(1, priority) * COST_SCALE)


@dataclass
class VehicleInput:
    id: int
    code: str
    capacity_kg: float
    depot_lat: float
    depot_lon: float
    capacity_m3: float = 1e9
    # Capabilities this vehicle offers, e.g. {"reefer", "tail_lift"}.
    features: frozenset[str] = frozenset()
    # ponytail: per-vehicle cost overrides land here when the fleet is mixed
    # (reefer vs flatbed). One shared CostModel until that data exists.


@dataclass
class ShipmentInput:
    id: int
    code: str
    lat: float
    lon: float
    demand_kg: float
    tw_start_min: int
    tw_end_min: int
    service_min: int
    demand_m3: float = 0.0
    priority: int = 1
    # Incumbent assignment — drives the stability (churn) term.
    prior_vehicle_id: int | None = None
    # Position on the incumbent route, used to rebuild the warm start.
    prior_seq: int | None = None
    # Hard lock: already loaded / dispatcher-pinned. Cannot move vehicles.
    locked_vehicle_id: int | None = None
    # Vehicle capability this consignment demands, e.g. "reefer". "" = any.
    requires_feature: str = ""


@dataclass
class NoEntryZone:
    name: str
    center_lat: float
    center_lon: float
    radius_km: float
    ban_start_min: int
    ban_end_min: int


@dataclass
class StopResult:
    seq: int
    kind: str
    shipment_id: int | None
    lat: float
    lon: float
    eta_min: int | None
    late_min: int = 0


@dataclass
class RouteResult:
    vehicle_id: int
    vehicle_code: str
    stops: list[StopResult] = field(default_factory=list)
    total_distance_km: float = 0.0
    total_load_kg: float = 0.0
    total_load_m3: float = 0.0
    load_pct: float = 0.0


@dataclass
class UnservedResult:
    shipment_id: int
    code: str
    reason: str


@dataclass
class SolveResult:
    status: str  # completed | infeasible | failed
    objective: float | None
    routes: list[RouteResult]
    metrics: dict
    explain: dict
    unserved: list[UnservedResult] = field(default_factory=list)
    error: str | None = None


def _infeasibility_reason(
    ship: ShipmentInput,
    vehicles: list[VehicleInput],
    zones: list[NoEntryZone],
) -> str:
    """Best-effort binding-constraint diagnosis for a dropped shipment (D16)."""
    if ship.requires_feature and not any(
        ship.requires_feature in v.features for v in vehicles
    ):
        return f"no available vehicle offers '{ship.requires_feature}'"
    if ship.demand_kg > max((v.capacity_kg for v in vehicles), default=0):
        return "exceeds the capacity of every available vehicle"
    if ship.demand_m3 and ship.demand_m3 > max(
        (v.capacity_m3 for v in vehicles), default=0
    ):
        return "exceeds the volume capacity of every available vehicle"
    if ship.tw_end_min <= ship.tw_start_min:
        return "time window is empty or inverted"
    for z in zones:
        d = haversine_km(
            LatLon(ship.lat, ship.lon), LatLon(z.center_lat, z.center_lon)
        )
        if d <= z.radius_km and (
            z.ban_start_min <= ship.tw_start_min and ship.tw_end_min <= z.ban_end_min
        ):
            return f"time window sits entirely inside no-entry zone '{z.name}'"
    return "no vehicle could reach it within its time window at acceptable cost"


def solve_cvrptw(
    vehicles: list[VehicleInput],
    shipments: list[ShipmentInput],
    solve_seconds: int = 8,
    no_entry_zones: list[NoEntryZone] | None = None,
    cost: CostModel | None = None,
    now_min: int | None = None,
) -> SolveResult:
    cost = cost or CostModel()
    zones = no_entry_zones or []
    # A re-plan at 20:00 that starts its routes at 06:00 is fiction: every
    # window still looks reachable, lateness costs nothing, and the solver will
    # happily stack nine stops on one truck. Routes must depart from now.
    depart_min = max(DAY_START_MIN, min(now_min or DAY_START_MIN, DAY_END_MIN))

    if not vehicles:
        return SolveResult(
            status="failed",
            objective=None,
            routes=[],
            metrics={},
            explain={},
            error="No vehicles available",
        )
    if not shipments:
        return SolveResult(
            status="completed",
            objective=0,
            routes=[],
            metrics={"shipments": 0, "vehicles_used": 0},
            explain={"summary": "No pending shipments"},
        )

    # ---- node layout: [depot_0 .. depot_D-1][shipment_0 .. shipment_S-1] ----
    depot_keys: list[tuple[float, float]] = []
    vehicle_depot_node: list[int] = []
    for v in vehicles:
        key = (round(v.depot_lat, 6), round(v.depot_lon, 6))
        if key not in depot_keys:
            depot_keys.append(key)
        vehicle_depot_node.append(depot_keys.index(key))
    n_depots = len(depot_keys)

    points = [LatLon(lat, lon) for lat, lon in depot_keys] + [
        LatLon(s.lat, s.lon) for s in shipments
    ]
    time_matrix, dist_matrix, matrix_source = build_matrices(points)

    vehicle_ids = {v.id for v in vehicles}
    # An incumbent on a vehicle that is no longer available (broken down) is not
    # an incumbent — otherwise every candidate pays the churn penalty equally
    # and the term degenerates into a constant.
    for s in shipments:
        if s.prior_vehicle_id is not None and s.prior_vehicle_id not in vehicle_ids:
            s.prior_vehicle_id = None
            s.prior_seq = None

    manager = pywrapcp.RoutingIndexManager(
        len(points), len(vehicles), vehicle_depot_node, vehicle_depot_node
    )
    routing = pywrapcp.RoutingModel(manager)

    churn_units = int(cost.churn_inr * COST_SCALE)

    def make_cost_cb(v_idx: int):
        veh = vehicles[v_idx]

        def cb(from_index: int, to_index: int) -> int:
            i = manager.IndexToNode(from_index)
            j = manager.IndexToNode(to_index)
            c = dist_matrix[i][j] * cost.per_km_inr * COST_SCALE
            if j >= n_depots:
                ship = shipments[j - n_depots]
                # sigma — stability: charge for pulling work off its incumbent
                if (
                    ship.prior_vehicle_id is not None
                    and ship.prior_vehicle_id != veh.id
                ):
                    c += churn_units
            return int(round(c))

        return cb

    for v_idx in range(len(vehicles)):
        cb_idx = routing.RegisterTransitCallback(make_cost_cb(v_idx))
        routing.SetArcCostEvaluatorOfVehicle(cb_idx, v_idx)
        # alpha_v — a truck must earn its shift cost before the solver uses it
        routing.SetFixedCostOfVehicle(int(cost.vehicle_fixed_inr * COST_SCALE), v_idx)

    # ---- capacity: weight and volume ----
    def demand_kg_cb(from_index: int) -> int:
        node = manager.IndexToNode(from_index)
        if node < n_depots:
            return 0
        return int(round(shipments[node - n_depots].demand_kg))

    kg_idx = routing.RegisterUnaryTransitCallback(demand_kg_cb)
    routing.AddDimensionWithVehicleCapacity(
        kg_idx, 0, [int(v.capacity_kg) for v in vehicles], True, "Capacity"
    )

    has_volume = any(s.demand_m3 for s in shipments)
    if has_volume:
        # Litres, so fractional m3 survives integer rounding.
        def demand_m3_cb(from_index: int) -> int:
            node = manager.IndexToNode(from_index)
            if node < n_depots:
                return 0
            return int(round(shipments[node - n_depots].demand_m3 * 1000))

        m3_idx = routing.RegisterUnaryTransitCallback(demand_m3_cb)
        routing.AddDimensionWithVehicleCapacity(
            m3_idx, 0, [int(v.capacity_m3 * 1000) for v in vehicles], True, "Volume"
        )

    # ---- time: travel + service, with waiting slack ----
    def service_cb(from_index: int, to_index: int) -> int:
        f = manager.IndexToNode(from_index)
        travel = time_matrix[f][manager.IndexToNode(to_index)]
        if f < n_depots:
            return travel
        return travel + shipments[f - n_depots].service_min * 60

    service_idx = routing.RegisterTransitCallback(service_cb)
    routing.AddDimension(service_idx, 4 * 3600, DAY_END_MIN * 60, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")

    late_units_per_sec = max(1, int(cost.late_per_min_inr * COST_SCALE / 60))
    affected_zones: list[str] = []
    incompatible: list[str] = []

    for i, ship in enumerate(shipments):
        node = n_depots + i
        index = manager.NodeToIndex(node)
        cumul = time_dim.CumulVar(index)

        # Hard floor at window open (waiting is free), soft ceiling at window
        # close — a late delivery is expensive, not impossible. Plan.md §5.4.
        cumul.SetRange(ship.tw_start_min * 60, DAY_END_MIN * 60)
        time_dim.SetCumulVarSoftUpperBound(
            index, ship.tw_end_min * 60, late_units_per_sec
        )

        for z in zones:
            d = haversine_km(
                LatLon(ship.lat, ship.lon), LatLon(z.center_lat, z.center_lon)
            )
            if d <= z.radius_km:
                cumul.RemoveInterval(z.ban_start_min * 60, z.ban_end_min * 60)
                tag = f"{z.name} -> {ship.code}"
                if tag not in affected_zones:
                    affected_zones.append(tag)

        # Compatibility: a reefer load cannot ride on a dry van. Cheap to
        # enforce in the search (Plan.md §9 MVP row) — no geometry needed.
        if ship.requires_feature:
            allowed = [
                k for k, v in enumerate(vehicles) if ship.requires_feature in v.features
            ]
            if allowed:
                routing.VehicleVar(index).SetValues([-1, *allowed])
            else:
                # Nothing in the fleet can carry it; force the drop so the
                # dispatcher is told, rather than assigning it illegally.
                routing.VehicleVar(index).SetValues([-1])
                incompatible.append(ship.code)

        # Hard lock (already loaded / dispatcher-pinned). -1 keeps the drop
        # option alive so one impossible lock cannot kill the whole plan.
        if ship.locked_vehicle_id is not None:
            locked = next(
                (k for k, v in enumerate(vehicles) if v.id == ship.locked_vehicle_id),
                None,
            )
            if locked is not None:
                routing.VehicleVar(index).SetValues([-1, locked])

        # Never infeasible: dropping is always legal, just ruinously expensive.
        routing.AddDisjunction([index], cost.drop_penalty(ship.priority))

    for v in range(len(vehicles)):
        time_dim.CumulVar(routing.Start(v)).SetRange(
            depart_min * 60, DAY_END_MIN * 60
        )
        time_dim.CumulVar(routing.End(v)).SetRange(
            depart_min * 60, DAY_END_MIN * 60
        )
        # Let the solver finish early rather than pad routes to the horizon.
        routing.AddVariableMinimizedByFinalizer(time_dim.CumulVar(routing.End(v)))

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    params.time_limit.FromSeconds(max(1, solve_seconds))

    # ---- warm start from the incumbent plan (Plan.md §10.3) ----
    warm = _build_warm_start(vehicles, shipments, n_depots, manager, routing)
    solution = None
    if warm:
        initial = routing.ReadAssignmentFromRoutes(warm, True)
        if initial:
            solution = routing.SolveFromAssignmentWithParameters(initial, params)
    if solution is None:
        solution = routing.SolveWithParameters(params)

    if not solution:
        return SolveResult(
            status="infeasible",
            objective=None,
            routes=[],
            metrics={"matrix_source": matrix_source, "no_entry_hits": len(affected_zones)},
            explain={
                "summary": (
                    "Solver found no assignment at all. With drop penalties enabled "
                    "this means the model itself is contradictory — usually a hard "
                    "vehicle lock or an empty time window."
                ),
                "hints": [
                    "Check dispatcher-pinned (locked) shipments",
                    "Check for inverted time windows (end <= start)",
                    "Add vehicles or extend the shift horizon",
                ],
                "no_entry_applied": affected_zones,
            },
            error="INFEASIBLE",
        )

    # ---- extract ----
    routes: list[RouteResult] = []
    total_m = 0.0
    total_late_min = 0
    served_ids: set[int] = set()

    for v_idx, veh in enumerate(vehicles):
        index = routing.Start(v_idx)
        depot_pt = points[vehicle_depot_node[v_idx]]
        stops: list[StopResult] = [
            StopResult(
                seq=0,
                kind="depot",
                shipment_id=None,
                lat=depot_pt.lat,
                lon=depot_pt.lon,
                eta_min=solution.Min(time_dim.CumulVar(index)) // 60,
            )
        ]
        seq = 0
        load_kg = 0.0
        load_m3 = 0.0
        route_km = 0.0

        while not routing.IsEnd(index):
            prev = index
            index = solution.Value(routing.NextVar(index))
            node = manager.IndexToNode(index)
            route_km += dist_matrix[manager.IndexToNode(prev)][node]
            if routing.IsEnd(index):
                break
            seq += 1
            ship = shipments[node - n_depots]
            served_ids.add(ship.id)
            load_kg += ship.demand_kg
            load_m3 += ship.demand_m3
            eta_min = solution.Min(time_dim.CumulVar(index)) // 60
            late = max(0, eta_min - ship.tw_end_min)
            total_late_min += late
            stops.append(
                StopResult(
                    seq=seq,
                    kind="delivery",
                    shipment_id=ship.id,
                    lat=ship.lat,
                    lon=ship.lon,
                    eta_min=eta_min,
                    late_min=late,
                )
            )

        if len(stops) <= 1:
            continue
        # Close the loop back to the depot so the drawn route matches the cost.
        stops.append(
            StopResult(
                seq=seq + 1,
                kind="depot",
                shipment_id=None,
                lat=depot_pt.lat,
                lon=depot_pt.lon,
                eta_min=solution.Min(time_dim.CumulVar(routing.End(v_idx))) // 60,
            )
        )
        total_m += route_km
        routes.append(
            RouteResult(
                vehicle_id=veh.id,
                vehicle_code=veh.code,
                stops=stops,
                total_distance_km=round(route_km, 3),
                total_load_kg=round(load_kg, 2),
                total_load_m3=round(load_m3, 3),
                load_pct=round(100 * load_kg / max(veh.capacity_kg, 1e-9), 1),
            )
        )

    unserved = [
        UnservedResult(
            shipment_id=s.id,
            code=s.code,
            reason=_infeasibility_reason(s, vehicles, zones),
        )
        for s in shipments
        if s.id not in served_ids
    ]

    reassigned = sum(
        1
        for r in routes
        for st in r.stops
        if st.shipment_id is not None
        and (prior := _prior_of(shipments, st.shipment_id)) is not None
        and prior != r.vehicle_id
    )

    used_cap = sum(v.capacity_kg for v in vehicles if v.id in {r.vehicle_id for r in routes})
    carried = sum(r.total_load_kg for r in routes)
    utilization = round(100 * carried / used_cap, 1) if used_cap else 0.0

    objective_inr = solution.ObjectiveValue() / COST_SCALE
    ops_cost_inr = round(
        total_m * cost.per_km_inr + len(routes) * cost.vehicle_fixed_inr, 2
    )

    metrics = {
        "matrix_source": matrix_source,
        "shipments": len(shipments),
        "vehicles_available": len(vehicles),
        "vehicles_used": len(routes),
        "depots_used": n_depots,
        "total_distance_km": round(total_m, 3),
        "ops_cost_inr": ops_cost_inr,
        "objective_inr": round(objective_inr, 2),
        "unserved_count": len(unserved),
        "total_late_min": total_late_min,
        "on_time_pct": round(
            100 * (len(served_ids) - sum(1 for r in routes for s in r.stops if s.late_min))
            / max(len(served_ids), 1),
            1,
        ),
        "capacity_utilization_pct": utilization,
        "shipments_reassigned": reassigned,
        "solve_seconds": solve_seconds,
        "no_entry_hits": len(affected_zones),
    }

    explain = {
        "summary": (
            f"Served {len(served_ids)}/{len(shipments)} shipments on {len(routes)} of "
            f"{len(vehicles)} vehicles across {n_depots} depot(s). "
            f"{metrics['total_distance_km']} km, ops cost ~Rs {ops_cost_inr:,.0f}, "
            f"{utilization}% capacity used, {total_late_min} min total lateness. "
            f"No-entry windows bound {len(affected_zones)} stop(s)."
        ),
        "matrix_source": matrix_source,
        "metaheuristic": "GUIDED_LOCAL_SEARCH",
        "first_solution": "PATH_CHEAPEST_ARC" + (" (warm-started)" if warm else ""),
        "objective_terms": {
            "vehicle_fixed_inr": cost.vehicle_fixed_inr,
            "per_km_inr": cost.per_km_inr,
            "late_per_min_inr": cost.late_per_min_inr,
            "churn_inr": cost.churn_inr,
            "unserved_base_inr": cost.unserved_inr,
        },
        "vehicles_used": [r.vehicle_code for r in routes],
        "no_entry_applied": affected_zones,
        "incompatible": incompatible,
        "unserved": [{"code": u.code, "reason": u.reason} for u in unserved],
    }
    if unserved:
        explain["relaxation_options"] = [
            "Extend the delivery window by 30-60 min on the listed stops",
            "Release an additional vehicle or authorise overtime",
            "Deactivate the binding no-entry overlay (requires municipal permit)",
            "Split the consignment across two vehicles",
        ]

    return SolveResult(
        status="completed",
        objective=round(objective_inr, 2),
        routes=routes,
        metrics=metrics,
        explain=explain,
        unserved=unserved,
    )


def solve_greedy_baseline(
    vehicles: list[VehicleInput],
    shipments: list[ShipmentInput],
    cost: CostModel | None = None,
) -> dict:
    """Nearest-neighbour + capacity: what a dispatcher does on a whiteboard.

    Two jobs. It is the honest baseline the optimized plan is measured against
    (no invented "30% better" numbers), and it is the degraded-mode fallback
    when the solver times out (Plan.md §14).
    """
    cost = cost or CostModel()
    if not vehicles or not shipments:
        return {"total_distance_km": 0.0, "vehicles_used": 0, "ops_cost_inr": 0.0,
                "served": 0, "routes": []}

    depot_keys: list[tuple[float, float]] = []
    veh_depot: list[int] = []
    for v in vehicles:
        key = (round(v.depot_lat, 6), round(v.depot_lon, 6))
        if key not in depot_keys:
            depot_keys.append(key)
        veh_depot.append(depot_keys.index(key))

    points = [LatLon(a, b) for a, b in depot_keys] + [
        LatLon(s.lat, s.lon) for s in shipments
    ]
    _, dist, _src = build_matrices(points)
    n_depots = len(depot_keys)

    remaining = set(range(len(shipments)))
    total_km = 0.0
    routes: list[dict] = []

    for v_idx, veh in enumerate(vehicles):
        if not remaining:
            break
        here = veh_depot[v_idx]
        load = 0.0
        seq: list[int] = []
        route_km = 0.0
        while True:
            best, best_d = None, float("inf")
            for i in remaining:
                if load + shipments[i].demand_kg > veh.capacity_kg:
                    continue
                d = dist[here][n_depots + i]
                if d < best_d:
                    best, best_d = i, d
            if best is None:
                break
            route_km += best_d
            load += shipments[best].demand_kg
            here = n_depots + best
            seq.append(best)
            remaining.discard(best)
        if not seq:
            continue
        route_km += dist[here][veh_depot[v_idx]]
        total_km += route_km
        routes.append(
            {
                "vehicle_code": veh.code,
                "distance_km": round(route_km, 3),
                "stops": len(seq),
                "load_kg": round(load, 2),
            }
        )

    return {
        "total_distance_km": round(total_km, 3),
        "vehicles_used": len(routes),
        "ops_cost_inr": round(
            total_km * cost.per_km_inr + len(routes) * cost.vehicle_fixed_inr, 2
        ),
        "served": len(shipments) - len(remaining),
        "unserved": len(remaining),
        "routes": routes,
        "method": "nearest-neighbour + capacity (no time windows, no curfews)",
    }


def _prior_of(shipments: list[ShipmentInput], shipment_id: int) -> int | None:
    for s in shipments:
        if s.id == shipment_id:
            return s.prior_vehicle_id
    return None


def _build_warm_start(
    vehicles: list[VehicleInput],
    shipments: list[ShipmentInput],
    n_depots: int,
    manager,
    routing,
) -> list[list[int]] | None:
    """Rebuild the incumbent plan as OR-Tools initial routes.

    Returns per-vehicle lists of *node* indices, or None when there is no
    incumbent to warm-start from.
    """
    by_vehicle: dict[int, list[ShipmentInput]] = {}
    for i, s in enumerate(shipments):
        if s.prior_vehicle_id is None:
            continue
        by_vehicle.setdefault(s.prior_vehicle_id, []).append(s)
    if not by_vehicle:
        return None

    index_of = {id(s): n_depots + i for i, s in enumerate(shipments)}
    warm: list[list[int]] = []
    for v in vehicles:
        seq = sorted(
            by_vehicle.get(v.id, []),
            key=lambda s: (s.prior_seq if s.prior_seq is not None else 10**6),
        )
        warm.append([index_of[id(s)] for s in seq])
    return warm if any(warm) else None
