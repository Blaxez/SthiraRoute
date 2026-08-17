"""End-to-end check of the demo path over HTTP. Run: python tests/test_api.py

The unit suites prove the solver and the clock in isolation. This one proves
the thing a judge actually drives: pick a city, prepare the shift, run it,
inject a disruption, rewind. Every failure here is a failure the audience
would see.

Runs against a throwaway SQLite file so it can never touch the demo database.
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sthira-api-test-")) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP.as_posix()}"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.demo_seed import seed  # noqa: E402

client = TestClient(app)

# One solve second: this suite is checking the wiring, not the plan quality.
FAST = 1


def setup_module_once():
    seed(do_reset=True, city_id="bengaluru")


def setup_module():
    setup_module_once()


def test_the_city_catalogue_is_served():
    r = client.get("/api/sim/cities")
    assert r.status_code == 200, r.text
    cities = r.json()
    ids = {c["id"] for c in cities}
    assert {"bengaluru", "mumbai", "delhi", "hyderabad"} <= ids, ids
    for c in cities:
        assert c["label"] and c["region"] and c["notes"], c
        assert c["center"]["lat"] and c["center"]["lon"], c
        assert c["shipments"] >= 12 and c["vehicles"] >= 3, c


def test_switching_city_rebuilds_the_fleet_and_the_restrictions():
    r = client.post("/api/sim/city", json={"city": "mumbai"})
    assert r.status_code == 200, r.text
    snap = r.json()
    assert snap["city"]["label"] == "Mumbai"
    assert snap["clock"] == "06:00", snap["clock"]

    codes = [v["code"] for v in client.get("/api/fleet/vehicles").json()]
    assert codes and all(c.startswith("MH-") for c in codes), codes

    overlays = [o["name"] for o in client.get("/api/constraints").json()]
    assert any("Island city" in n for n in overlays), overlays

    customers = [s["customer_name"] for s in client.get("/api/shipments").json()]
    assert "Crawford Market" in customers, customers[:5]


def test_an_unknown_city_is_a_404_not_a_silent_default():
    r = client.post("/api/sim/city", json={"city": "atlantis"})
    assert r.status_code == 404, r.status_code


def test_start_the_day_plans_and_loads_the_scripted_shift():
    """The judge button: one call to a dispatched day with a playbook loaded."""
    assert client.post("/api/sim/city", json={"city": "bengaluru"}).status_code == 200
    snap = client.post(f"/api/sim/demo?solve_seconds={FAST}").json()
    assert snap["clock"] == "06:00", snap["clock"]
    assert snap.get("run_id"), snap.get("notes")
    assert snap.get("scenario", {}).get("id") == "full_day", snap.get("scenario")
    assert snap["scenario"]["beats"], "demo playbook has no beats"
    routes = client.get("/api/optimization/routes/committed").json()
    assert routes, "demo dispatched nothing"
    assert any(rt["stops"] for rt in routes), "committed routes have no stops"
    board = client.get("/api/network/board").json()
    assert board["dock"] and board["drivers"], board["ps2"]


def test_the_operating_day_runs_end_to_end():
    assert client.post("/api/sim/city", json={"city": "bengaluru"}).status_code == 200

    prepared = client.post(f"/api/sim/prepare?solve_seconds={FAST}").json()
    assert prepared["clock"] == "06:00"
    assert prepared.get("run_id"), prepared.get("notes")

    r = client.get("/api/optimization/routes/committed")
    assert r.status_code == 200, r.text
    routes = r.json()
    assert routes, "prepare dispatched nothing"
    assert any(rt["stops"] for rt in routes), "committed routes have no stops"

    first = client.post("/api/sim/tick").json()
    assert first["clock"] > "06:00"
    assert first["day_pct"] > 0

    # A playbook is a script, so loading one must be visible in the snapshot.
    loaded = client.post("/api/sim/playbook", json={"scenario": "full_day"}).json()
    assert loaded["scenario"]["id"] == "full_day", loaded.get("scenario")
    assert loaded["scenario"]["beats"], "a playbook with no beats is not a script"
    assert client.post("/api/sim/playbook", json={"scenario": "nope"}).status_code == 404

    # An injected disruption has to change the world, not just log a line.
    before = client.get("/api/sim/state").json()["weather"]["state"]
    after = client.post("/api/sim/inject", json={"kind": "storm"}).json()
    assert after["weather"]["state"] == "storm", (before, after["weather"])
    assert after["traffic"]["speed_kmh"] < 40

    kinds = {e["kind"] for e in after["events"]}
    assert "weather" in kinds, kinds

    assert client.post("/api/sim/inject", json={"kind": "nonsense"}).status_code == 400

    # Rewind puts the day back to a state worth demonstrating again.
    rewound = client.post("/api/sim/reset").json()
    assert rewound["clock"] == "06:00"
    assert rewound["weather"]["state"] == "clear"
    assert rewound["scorecard"]["delivered"] == 0


def test_the_network_board_is_the_same_plan_on_every_desk():
    """PS2: dispatcher, dock, driver and consignee read one committed plan."""
    assert client.post("/api/sim/city", json={"city": "bengaluru"}).status_code == 200
    prepared = client.post(f"/api/sim/prepare?solve_seconds={FAST}").json()
    assert prepared.get("run_id"), prepared.get("notes")

    board = client.get("/api/network/board").json()
    assert board["ps2"]["optimize"]["routes"] >= 1, board["ps2"]
    assert board["dock"], "dock has no loads after dispatch"
    assert board["drivers"], "driver slice empty after dispatch"
    assert any(c["vehicle_code"] for c in board["consignments"]), board["consignments"][:3]
    assert board["ps2"]["allocate"]["all_loadable"] is True

    # One consignment, two desks: the truck on the track page is the truck
    # on the dock page.
    tracked = next(c for c in board["consignments"] if c["vehicle_code"])
    pub = client.get(f"/api/network/track/{tracked['code']}").json()
    assert pub["code"] == tracked["code"]
    assert pub["vehicle_code"] == tracked["vehicle_code"]
    dock_codes = {d["code"] for d in board["dock"]}
    assert tracked["vehicle_code"] in dock_codes
    assert any(d.get("placements") for d in board["dock"]), "dock has no packed cartons"

    missing = client.get("/api/network/track/NO-SUCH")
    assert missing.status_code == 404


def test_reset_demo_keeps_the_city_you_were_in():
    assert client.post("/api/sim/city", json={"city": "hyderabad"}).status_code == 200
    r = client.post("/api/events/reset-demo")
    assert r.status_code == 200, r.text
    assert r.json()["city"] == "hyderabad", r.json()
    codes = [v["code"] for v in client.get("/api/fleet/vehicles").json()]
    assert all(c.startswith("TS-") for c in codes), codes


if __name__ == "__main__":
    setup_module_once()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
