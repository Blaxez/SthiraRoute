"""Build the demo dataset for a chosen city.

Stops are deliberately spread across each city's restricted core, its ring and
its far industrial corridor so every constraint in the model actually binds
during a demo — a seed where nothing conflicts proves nothing. The per-city
data lives in `services/cities.py`; this module only writes it to the database.

Driven by `scripts/seed.py` (CLI), `POST /api/events/reset-demo` and the city
switcher in the dispatcher UI.
"""

from sqlalchemy import delete

from app.core.db import SessionLocal, init_db
from app.models import (
    Assignment,
    ConstraintOverlay,
    Depot,
    OptimizationRun,
    Route,
    Shipment,
    SimEvent,
    SimState,
    Stop,
    Vehicle,
    VehicleStatus,
)
from app.services.cities import DEFAULT_CITY, get_city


def reset(db) -> None:
    """Drop operational data. Overlays and fleet are rebuilt from scratch too."""
    for model in (SimEvent, SimState, Stop, Assignment, Route, OptimizationRun,
                  Shipment, Vehicle, Depot, ConstraintOverlay):
        db.execute(delete(model))
    db.commit()
    print("Reset: cleared plans, fleet, shipments and overlays.")


def seed(do_reset: bool = False, city_id: str = DEFAULT_CITY) -> str:
    """Seed one city's demo. Returns the city id actually used."""
    init_db()
    city = get_city(city_id)
    db = SessionLocal()
    try:
        if do_reset:
            reset(db)

        if not db.query(Depot).first():
            depots = {}
            for name, lat, lon in city.depots:
                d = Depot(name=name, lat=lat, lon=lon)
                db.add(d)
                db.flush()
                depots[name] = d

            for code, depot_name, kg, m3, deck, features in city.vehicles:
                d = depots[depot_name]
                db.add(
                    Vehicle(
                        code=code, depot_id=d.id, capacity_kg=kg, capacity_m3=m3,
                        deck_length_cm=deck[0], deck_width_cm=deck[1],
                        deck_height_cm=deck[2], features=features,
                        status=VehicleStatus.available, lat=d.lat, lon=d.lon,
                    )
                )

            for (code, name, lat, lon, kg, m3, tw0, tw1, prio,
                 dl, dw, dh, fragile, stackable, feature) in city.shipments:
                db.add(
                    Shipment(
                        code=code, customer_name=name, lat=lat, lon=lon,
                        demand_kg=kg, demand_m3=m3, tw_start_min=tw0,
                        tw_end_min=tw1, service_min=10, priority=prio,
                        length_cm=dl, width_cm=dw, height_cm=dh,
                        fragile=fragile, stackable=stackable,
                        requires_feature=feature,
                    )
                )
            print(
                f"Seeded {city.label}: {len(city.depots)} depots, "
                f"{len(city.vehicles)} vehicles, {len(city.shipments)} shipments."
            )
        else:
            print("Fleet already seeded (use --reset to rebuild).")

        if not db.query(ConstraintOverlay).first():
            for name, lat, lon, radius, ban0, ban1, notes in city.overlays:
                db.add(
                    ConstraintOverlay(
                        name=name, kind="no_entry", center_lat=lat, center_lon=lon,
                        radius_km=radius, ban_start_min=ban0, ban_end_min=ban1,
                        active=True, notes=notes,
                    )
                )
            print(f"Seeded {len(city.overlays)} no-entry overlays.")
        else:
            print("Overlays already seeded.")

        # The clock has to know which city it is running, so the traffic
        # profile, hazard corridors and ad-hoc geography all agree with the map.
        # A reset deletes the row, so it has to be created rather than only
        # updated: without this a `--reset --city delhi` left the clock on the
        # default city, and the shift invented ad-hoc orders 1,700 km away —
        # which the map then dutifully zoomed out to fit.
        state = db.query(SimState).first()
        if not state:
            state = SimState(clock_min=6 * 60, running=False, config_json="{}")
            db.add(state)
        state.city = city.id
        db.commit()
        print("Done.")
        return city.id
    finally:
        db.close()
