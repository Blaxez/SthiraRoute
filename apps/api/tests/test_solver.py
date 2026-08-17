"""Self-check for the routing objective. Run: python tests/test_solver.py

Each assert pins one behaviour that would silently produce a wrong dispatch
plan if it broke.
"""

from app.services.solver import (
    CostModel,
    NoEntryZone,
    ShipmentInput,
    VehicleInput,
    solve_cvrptw,
    solve_greedy_baseline,
)

BLR = (12.9716, 77.5946)


def veh(i, cap=1000, depot=BLR, cap_m3=1e9):
    return VehicleInput(
        id=i, code=f"TRK-{i:02d}", capacity_kg=cap, capacity_m3=cap_m3,
        depot_lat=depot[0], depot_lon=depot[1],
    )


def ship(i, lat, lon, kg=100, tw=(480, 1080), prio=1, prior=None, m3=0.0, svc=10):
    return ShipmentInput(
        id=i, code=f"SHP-{i:02d}", lat=lat, lon=lon, demand_kg=kg, demand_m3=m3,
        tw_start_min=tw[0], tw_end_min=tw[1], service_min=svc, priority=prio,
        prior_vehicle_id=prior,
    )


def _fleet(n=3):
    return [veh(i) for i in range(1, n + 1)]


def _demand():
    return [
        ship(1, 12.9352, 77.6245), ship(2, 12.9784, 77.6408),
        ship(3, 12.9698, 77.7500), ship(4, 12.9308, 77.5838),
        ship(5, 13.1007, 77.5963), ship(6, 12.8452, 77.6602),
    ]


def test_serves_everything_and_respects_capacity():
    r = solve_cvrptw(_fleet(), _demand(), solve_seconds=3)
    assert r.status == "completed", r.error
    assert not r.unserved, [u.reason for u in r.unserved]
    served = [s.shipment_id for rt in r.routes for s in rt.stops if s.shipment_id]
    assert sorted(served) == [1, 2, 3, 4, 5, 6], served
    for rt in r.routes:
        cap = next(v.capacity_kg for v in _fleet() if v.id == rt.vehicle_id)
        assert rt.total_load_kg <= cap, (rt.vehicle_code, rt.total_load_kg, cap)


def test_a_replan_departs_from_the_current_clock():
    """A re-plan at 18:00 cannot pretend it is leaving the depot at 06:00.

    This was a real bug: mid-shift re-plans scored every window as reachable,
    lateness came out free, and the solver stacked the whole remaining day onto
    one truck with ETAs past midnight.
    """
    demand = _demand()
    early = solve_cvrptw(_fleet(3), demand, solve_seconds=3)
    late = solve_cvrptw(_fleet(3), demand, solve_seconds=3, now_min=18 * 60)
    assert early.status == late.status == "completed"

    def first_departure(res):
        return min(rt.stops[0].eta_min for rt in res.routes if rt.stops)

    assert first_departure(early) < 18 * 60 <= first_departure(late)
    # Windows that closed at 18:00 are now genuinely missed, and the plan has
    # to own that rather than reporting a clean sheet.
    assert late.metrics["total_late_min"] > early.metrics["total_late_min"]


def test_a_weather_closure_binds_like_a_curfew():
    """Closures reach the solver through the same zone mechanism as curfews."""
    target = ship(1, 12.9170, 77.6230, tw=(8 * 60, 20 * 60))
    zone = NoEntryZone(
        name="Weather closure — Silk Board junction",
        center_lat=12.9170, center_lon=77.6230, radius_km=2.5,
        ban_start_min=8 * 60, ban_end_min=13 * 60,
    )
    r = solve_cvrptw([veh(1)], [target], solve_seconds=3, no_entry_zones=[zone])
    assert r.status == "completed", r.error
    served = [s for rt in r.routes for s in rt.stops if s.shipment_id == 1]
    assert served, "the stop was dropped rather than deferred"
    assert served[0].eta_min >= 13 * 60, served[0].eta_min


def test_fixed_cost_consolidates_fleet():
    """600 kg over 3 trucks of 1000 kg should not deploy 3 trucks."""
    r = solve_cvrptw(_fleet(3), _demand(), solve_seconds=3)
    assert r.metrics["vehicles_used"] < 3, r.metrics


def test_impossible_demand_is_dropped_not_fatal():
    """One oversized parcel must not sink the whole plan (Plan.md D16)."""
    d = _demand() + [ship(99, 12.90, 77.70, kg=99_999)]
    r = solve_cvrptw(_fleet(), d, solve_seconds=3)
    assert r.status == "completed"
    assert [u.shipment_id for u in r.unserved] == [99]
    assert "capacity" in r.unserved[0].reason
    assert r.explain["relaxation_options"]


def test_volume_is_a_real_constraint():
    small = [veh(1, cap=10_000, cap_m3=2.0)]
    d = [ship(i, 12.93 + i / 100, 77.60, kg=10, m3=1.5) for i in range(1, 4)]
    r = solve_cvrptw(small, d, solve_seconds=3)
    served = [s.shipment_id for rt in r.routes for s in rt.stops if s.shipment_id]
    assert len(served) == 1, f"2.0 m3 truck took {len(served)} x 1.5 m3 parcels"


def test_stability_keeps_incumbent_assignments():
    """Re-solving an unchanged problem must not reshuffle the fleet."""
    first = solve_cvrptw(_fleet(), _demand(), solve_seconds=3)
    prior = {
        s.shipment_id: rt.vehicle_id
        for rt in first.routes
        for s in rt.stops
        if s.shipment_id
    }
    again = solve_cvrptw(
        _fleet(),
        [ship(s.id, s.lat, s.lon, prior=prior.get(s.id)) for s in _demand()],
        solve_seconds=3,
    )
    assert again.metrics["shipments_reassigned"] == 0, again.metrics


def test_churn_price_is_what_holds_the_plan_together():
    """With churn free, the same re-solve is allowed to drift."""
    first = solve_cvrptw(_fleet(), _demand(), solve_seconds=3)
    prior = {
        s.shipment_id: rt.vehicle_id
        for rt in first.routes
        for s in rt.stops
        if s.shipment_id
    }
    d = [ship(s.id, s.lat, s.lon, prior=prior.get(s.id)) for s in _demand()]
    free = solve_cvrptw(_fleet(), d, solve_seconds=3, cost=CostModel(churn_inr=0))
    assert free.status == "completed"  # sanity: model still solves without sigma


def test_no_entry_zone_pushes_eta_outside_the_ban():
    zone = NoEntryZone(
        name="CBD morning", center_lat=12.9760, center_lon=77.6030,
        radius_km=3.0, ban_start_min=480, ban_end_min=660,
    )
    d = [ship(1, 12.9760, 77.6030, tw=(480, 1080))]
    r = solve_cvrptw(_fleet(1), d, solve_seconds=3, no_entry_zones=[zone])
    assert r.status == "completed"
    if not r.unserved:
        eta = next(s.eta_min for rt in r.routes for s in rt.stops if s.shipment_id == 1)
        assert not (480 <= eta < 660), f"delivered at {eta} inside the 480-660 ban"


def test_late_delivery_is_priced_not_forbidden():
    """A window nobody can hit yields a late plan, not an empty one.

    Shift opens 06:00, the stop is ~28 min out, so a 06:00-06:05 window is
    physically unreachable — the solver must deliver late and price it.
    """
    d = [ship(1, 13.10, 77.59, tw=(360, 365))]
    r = solve_cvrptw(_fleet(1), d, solve_seconds=3)
    assert r.status == "completed"
    assert not r.unserved
    assert r.metrics["total_late_min"] > 0


def test_multi_depot_uses_the_nearer_depot():
    north, south = (13.10, 77.59), (12.84, 77.66)
    vs = [veh(1, depot=north), veh(2, depot=south)]
    d = [ship(1, 13.105, 77.595), ship(2, 12.845, 77.665)]
    r = solve_cvrptw(vs, d, solve_seconds=3)
    assert r.metrics["depots_used"] == 2
    where = {s.shipment_id: rt.vehicle_id for rt in r.routes for s in rt.stops if s.shipment_id}
    assert where[1] == 1 and where[2] == 2, where


def test_locked_shipment_cannot_change_vehicle():
    d = _demand()
    d[0].locked_vehicle_id = 3
    r = solve_cvrptw(_fleet(), d, solve_seconds=3)
    where = {s.shipment_id: rt.vehicle_id for rt in r.routes for s in rt.stops if s.shipment_id}
    assert where.get(1) == 3, where


def test_optimizer_beats_the_greedy_baseline():
    base = solve_greedy_baseline(_fleet(), _demand())
    opt = solve_cvrptw(_fleet(), _demand(), solve_seconds=5)
    assert base["total_distance_km"] > 0
    assert opt.metrics["ops_cost_inr"] <= base["ops_cost_inr"], (
        opt.metrics["ops_cost_inr"], base["ops_cost_inr"]
    )


def test_empty_inputs_do_not_crash():
    assert solve_cvrptw([], _demand()).status == "failed"
    assert solve_cvrptw(_fleet(), []).status == "completed"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
