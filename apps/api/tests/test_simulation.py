"""Self-check for the shift simulation. Run: python tests/test_simulation.py

Each assert pins one behaviour that would quietly turn the operating day into
theatre if it broke: the clock has to advance, trucks have to actually deliver,
a seeded day has to replay, disruption has to be injectable, and the autopilot
has to show restraint rather than re-planning on every wobble.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
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
    VehicleStatus,
)
from app.services import simulation as sim

BLR = (12.9716, 77.5946)


def fresh_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed_dispatched_day(db, n_stops=4):
    """A committed route with road geometry, ready to be driven."""
    depot = Depot(name="Peenya Hub", lat=BLR[0], lon=BLR[1])
    db.add(depot)
    db.flush()

    veh = Vehicle(
        code="TRK-01", depot_id=depot.id, capacity_kg=1000, capacity_m3=10,
        status=VehicleStatus.available, lat=depot.lat, lon=depot.lon,
    )
    run = OptimizationRun(status=RunStatus.completed)
    db.add_all([veh, run])
    db.flush()

    route = Route(
        vehicle_id=veh.id, optimization_run_id=run.id,
        status=RouteStatus.committed, total_distance_km=20, total_load_kg=200,
    )
    db.add(route)
    db.flush()

    # Stops march east from the depot; the geometry passes through each one so
    # arc-length arrival works exactly as it does with a real OSRM path.
    path = [[depot.lon, depot.lat]]
    for i in range(1, n_stops + 1):
        lon = depot.lon + 0.02 * i
        ship = Shipment(
            code=f"SHP-{i:02d}", customer_name=f"Stop {i}", lat=depot.lat, lon=lon,
            demand_kg=50, demand_m3=0.5, tw_start_min=6 * 60, tw_end_min=12 * 60,
            service_min=10, priority=1, status=ShipmentStatus.assigned,
        )
        db.add(ship)
        db.flush()
        db.add(Stop(route_id=route.id, shipment_id=ship.id, seq=i, kind="delivery",
                    lat=depot.lat, lon=lon, eta_min=7 * 60 + i * 30))
        path.append([lon, depot.lat])
    path.append([depot.lon, depot.lat])

    import json
    route.geometry_json = json.dumps(path)
    db.commit()
    return veh, route


def quiet_config(db, autopilot=False, **kw):
    """Turn off the dice so a test measures one thing at a time.

    Autopilot is off by default: it runs OR-Tools against a live routing
    service, which does not belong in a unit check. The decision logic is
    exercised directly in `test_mild_sla_drift_is_monitored_not_replanned`.
    """
    st = sim.get_state(db)
    st.autopilot = autopilot
    db.commit()
    quiet = {
        "breakdown_per_vehicle_hour": 0.0,
        "congestion_per_hour": 0.0,
        "new_order_per_hour": 0.0,
        "cancel_per_hour": 0.0,
        "gps_dropout_per_vehicle_hour": 0.0,
        "weather_change_per_hour": 0.0,
        "warehouse_delay_per_hour": 0.0,
    }
    sim.set_config(db, **{**quiet, **kw})


def test_clock_advances_and_stops_at_end_of_shift():
    db = fresh_db()
    quiet_config(db, minutes_per_tick=60)
    st = sim.get_state(db)
    assert st.clock_min == sim.SHIFT_START_MIN

    snap = sim.tick(db)
    assert snap["clock_min"] == sim.SHIFT_START_MIN + 60, snap["clock"]

    for _ in range(40):
        snap = sim.tick(db)
    assert snap["shift_over"], snap["clock"]
    assert snap["clock_min"] <= sim.SHIFT_END_MIN + 60


def test_trucks_drive_and_deliver():
    db = fresh_db()
    veh, _ = seed_dispatched_day(db)
    quiet_config(db, minutes_per_tick=30)

    for _ in range(24):
        sim.tick(db)

    done = db.query(Stop).filter(Stop.status == StopStatus.completed).count()
    assert done >= 3, f"only {done} of 4 stops delivered across a full shift"
    delivered = db.query(Shipment).filter(Shipment.status == ShipmentStatus.delivered).count()
    assert delivered == done, (delivered, done)
    assert sim.snapshot(db)["scorecard"]["km_driven"] > 0


def test_a_replan_does_not_teleport_a_truck_back_to_its_depot():
    """Committing a plan zeroes the odometer; the truck must not follow it back.

    Approving a re-plan restarts `path_progress_km`, because a new route is a
    new path. For a truck already out in the city that reading is a lie, and
    before the fix the next tick obediently placed the marker at the depot —
    the fleet visibly teleporting every time the dispatcher accepted a re-plan.
    """
    db = fresh_db()
    veh, route = seed_dispatched_day(db)
    quiet_config(db, minutes_per_tick=1)

    # Exactly the state approve_run() leaves behind for a truck already out: a
    # live GPS position well into the run, and an odometer reset to zero.
    stops = sorted(route.stops, key=lambda s: s.seq)
    veh.lat, veh.lon = stops[1].lat, stops[1].lon
    veh.path_progress_km = 0.0
    db.commit()

    sim.tick(db)
    home = sim._km(veh.lat, veh.lon, BLR[0], BLR[1])
    assert home > 1.0, f"truck teleported back to the hub ({home:.2f} km out)"
    assert veh.path_progress_km > 0, "odometer was never re-anchored"

    # And it must not have been thrown forward over drops nobody reached: the
    # re-anchor stops short of the first pending stop for exactly this reason.
    pending = db.query(Stop).filter(Stop.status == StopStatus.pending).count()
    assert pending >= 3, f"only {pending} stops left — re-anchoring skipped drops"


def test_a_seeded_day_replays_identically():
    """Without this the demo is unrepeatable and every bug is a ghost."""
    def run_day():
        db = fresh_db()
        seed_dispatched_day(db)
        quiet_config(db, minutes_per_tick=30, new_order_per_hour=2.0, seed=42)
        for _ in range(10):
            sim.tick(db)
        return [(e.clock_min, e.kind, e.message) for e in db.query(sim.SimEvent).all()]

    first, second = run_day(), run_day()
    assert first == second, [a for a, b in zip(first, second) if a != b][:3]
    assert any(e[1] == "new_order" for e in first), "the seeded day produced no orders"


def test_unseeded_orders_land_in_different_places():
    """The reseed bug put every ad-hoc order on the same street."""
    db = fresh_db()
    quiet_config(db, minutes_per_tick=30, new_order_per_hour=6.0, seed=3)
    for _ in range(12):
        sim.tick(db)
    spots = {s.customer_name for s in db.query(Shipment).all()}
    assert len(spots) > 1, spots


def test_injected_breakdown_strands_freight():
    db = fresh_db()
    veh, _ = seed_dispatched_day(db)
    quiet_config(db, minutes_per_tick=30)
    sim.get_state(db).autopilot = False
    db.commit()

    snap = sim.inject(db, "breakdown")
    assert snap["injected"]["vehicle"] == "TRK-01", snap["injected"]
    assert snap["injected"]["stranded"] == 4
    db.refresh(veh)
    assert veh.status == VehicleStatus.down


def test_gps_dropout_freezes_the_marker_then_reconciles():
    db = fresh_db()
    veh, _ = seed_dispatched_day(db)
    # Short ticks so the truck spends whole ticks driving between stops rather
    # than arriving on every one of them.
    quiet_config(db, minutes_per_tick=2)
    # Drive until it is between stops with road still ahead — a truck already
    # parked at the depot has nowhere to be reconciled to.
    for _ in range(40):
        sim.tick(db)
        db.refresh(veh)
        left = db.query(Stop).filter(Stop.status == StopStatus.pending).count()
        if veh.status == VehicleStatus.en_route and left >= 2:
            break
    assert veh.status == VehicleStatus.en_route, veh.status

    sim.inject(db, "gps_loss")
    db.refresh(veh)
    assert veh.gps_stale_min > 0
    frozen = (veh.lat, veh.lon)

    sim.tick(db)
    db.refresh(veh)
    assert (veh.lat, veh.lon) == frozen, "a dark vehicle must not move on the map"

    for _ in range(6):
        sim.tick(db)
        db.refresh(veh)
        if veh.gps_stale_min == 0:
            break
    assert veh.gps_stale_min == 0, "GPS never came back"
    assert (veh.lat, veh.lon) != frozen, "position was not reconciled after the blackout"


def test_cancellation_frees_the_stop_but_spares_priority_freight():
    db = fresh_db()
    seed_dispatched_day(db)
    for s in db.query(Shipment).all():
        s.priority = 3  # cold chain: never a cancellation candidate
    db.commit()
    assert "error" in sim.inject(db, "cancel")

    db.query(Shipment).first().priority = 1
    db.commit()
    snap = sim.inject(db, "cancel")
    assert "injected" in snap, snap
    cancelled = db.query(Shipment).filter(Shipment.status == ShipmentStatus.cancelled).all()
    assert len(cancelled) == 1
    skipped = db.query(Stop).filter(Stop.status == StopStatus.skipped).count()
    assert skipped == 1, "the cancelled stop is still on the driver's list"


def test_storm_closes_a_corridor_as_a_hard_constraint():
    db = fresh_db()
    quiet_config(db)
    sim.get_state(db).autopilot = False
    db.commit()
    snap = sim.inject(db, "storm")
    assert snap["weather"]["state"] == "storm"
    assert snap["traffic"]["factor"] < 1.0

    snap = sim.inject(db, "closure")
    assert snap["closures"], "a flood must produce a closure the optimizer can see"


def test_mild_sla_drift_is_monitored_not_replanned():
    """Plan.md §10.1: traffic touches paths first. Re-planning on drift is churn."""
    db = fresh_db()
    veh, route = seed_dispatched_day(db)
    quiet_config(db, minutes_per_tick=6)
    # Windows already closed → every remaining stop reads as at risk, but only
    # mildly, because the truck is close behind.
    for s in db.query(Shipment).all():
        s.tw_end_min = 6 * 60 + 5
    db.commit()

    at_risk = sim._sla_risk(db, sim.get_state(db), sim.get_config(sim.get_state(db)), 30.0)
    assert at_risk, "windows in the past should read as at risk"

    decision = sim._autopilot(db, sim.get_state(db), sim.get_config(sim.get_state(db)),
                              [{**r, "over_min": 5} for r in at_risk])
    assert decision["action"] == "monitor", decision
    assert sim.snapshot(db)["scorecard"]["monitor_only"] == 1


def test_reset_rewinds_the_day_and_clears_simulated_closures():
    db = fresh_db()
    veh, _ = seed_dispatched_day(db)
    quiet_config(db, minutes_per_tick=30)
    sim.get_state(db).autopilot = False
    db.commit()
    sim.inject(db, "closure")
    for _ in range(4):
        sim.tick(db)

    snap = sim.reset_clock(db)
    assert snap.clock_min == sim.SHIFT_START_MIN
    fresh = sim.snapshot(db)
    assert fresh["closures"] == []
    assert fresh["events"] == []
    assert fresh["scorecard"]["delivered"] == 0
    db.refresh(veh)
    assert veh.path_progress_km == 0 and veh.gps_stale_min == 0


def test_every_city_pack_is_internally_consistent():
    """A bad coordinate in a city pack shows up as a truck in the sea."""
    from app.services.cities import CITIES

    for city in CITIES.values():
        depot_names = {d[0] for d in city.depots}
        clat, clon = city.center

        assert len(city.shipments) >= 12, city.id
        assert len({s[0] for s in city.shipments}) == len(city.shipments), f"{city.id} dup codes"
        assert any(s[8] == 3 for s in city.shipments), f"{city.id} has no cold-chain load"
        assert city.overlays and city.adhoc_spots and city.hazard_corridors, city.id
        assert 20 <= city.free_flow_kmh <= 60, city.id
        # A whole day has to be describable by the profile.
        for hour in range(6, 22):
            assert 0.2 <= city.traffic_by_hour[hour] <= 1.5, (city.id, hour)

        for code, depot, *_ in city.vehicles:
            assert depot in depot_names, f"{city.id}/{code} parks at an unknown depot"

        # Everything must sit within ~120 km of the city centre; anything
        # further is a typo, not a suburb.
        points = (
            [(d[1], d[2]) for d in city.depots]
            + [(s[2], s[3]) for s in city.shipments]
            + [(a[1], a[2]) for a in city.adhoc_spots]
            + [(h[1], h[2]) for h in city.hazard_corridors]
            + [(o[1], o[2]) for o in city.overlays]
        )
        for lat, lon in points:
            assert sim._km(clat, clon, lat, lon) < 120, (city.id, lat, lon)


def test_switching_city_changes_the_geography_and_the_traffic():
    from app.services.cities import get_city

    db = fresh_db()
    quiet_config(db, minutes_per_tick=30, new_order_per_hour=6.0, seed=11)
    st = sim.get_state(db)
    st.city = "mumbai"
    db.commit()

    for _ in range(8):
        sim.tick(db)

    mumbai_spots = {s[0] for s in get_city("mumbai").adhoc_spots}
    ordered = [s.customer_name.replace(" (ad-hoc)", "") for s in db.query(Shipment).all()]
    assert ordered, "no ad-hoc orders were raised"
    assert all(name in mumbai_spots for name in ordered), ordered

    # Mumbai is slower than Delhi at the same hour, and the packs must say so.
    hour9 = 9 * 60
    assert sim.traffic_factor(hour9, 0, "clear", get_city("mumbai")) < sim.traffic_factor(
        hour9, 0, "clear", get_city("delhi")
    )
    assert sim.free_flow(sim.get_config(st), get_city("mumbai")) == 28.0


def test_playbook_beats_fire_on_the_clock():
    db = fresh_db()
    seed_dispatched_day(db)
    quiet_config(db, minutes_per_tick=30)
    sim.get_state(db).autopilot = False
    db.commit()
    sim.load_playbook(db, "monsoon")

    snap = sim.snapshot(db)
    assert snap["scenario"]["total"] == 4
    assert snap["scenario"]["step"] == 0

    # Nothing should fire before 14:00.
    for _ in range(4):
        snap = sim.tick(db)
    assert snap["scenario"]["step"] == 0, snap["scenario"]

    while not snap["shift_over"] and snap["scenario"]["step"] == 0:
        snap = sim.tick(db)
    assert snap["scenario"]["step"] >= 1, "scripted beats never fired"
    assert snap["weather"]["state"] == "storm"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
            print(f"  PASS  {name}")
    print(f"\n{passed} simulation checks passed.")
