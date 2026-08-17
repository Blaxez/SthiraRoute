"""Measure every number the SIH deck quotes, so no figure on a slide is folklore.

Plans a shift and then runs the whole simulated day for each city pack, and
writes the planned KPIs, the greedy-baseline comparison and the end-of-day
scorecard to PPT/measured.json.

Point it at a scratch database so the live demo is left alone:

    $env:DATABASE_URL = "sqlite:///./deck_measure.db"
    python scripts/measure_deck.py [city ...]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from app.core.db import SessionLocal, init_db
from app.modules.analytics.router import benchmark, kpis
from app.services import simulation as sim
from app.services.demo_seed import seed

CITIES = ["mumbai", "bengaluru", "delhi", "hyderabad"]
OUT = Path(__file__).resolve().parents[3] / "PPT" / "measured.json"


def measure(city_id: str) -> dict:
    city = seed(do_reset=True, city_id=city_id)
    db = SessionLocal()
    try:
        sim.get_state(db).city = city
        db.commit()

        t0 = time.time()
        snap = sim.prepare_shift(db, solve_seconds=8)
        solve_wall_s = round(time.time() - t0, 1)
        if not snap.get("run_id"):
            return {"city": city, "error": "planning failed"}

        planned = kpis(db)
        bench = benchmark(db)

        state = sim.get_state(db)
        state.autopilot = True
        db.commit()
        sim.load_playbook(db, "full_day")

        ticks = 0
        while True:
            snap = sim.tick(db)
            ticks += 1
            if snap["shift_over"]:
                break

        return {
            "city": city,
            "solve_wall_s": solve_wall_s,
            "ticks": ticks,
            "planned": planned,
            "benchmark": bench,
            "day_scorecard": snap["scorecard"],
            "day_shipments": snap["shipments"],
        }
    finally:
        db.close()


def main() -> int:
    wanted = [a for a in sys.argv[1:] if not a.startswith("--")] or CITIES
    init_db()

    results = {}
    for city_id in wanted:
        print(f"\n=== {city_id} ===", flush=True)
        r = measure(city_id)
        results[city_id] = r
        if "error" in r:
            print(f"  {r['error']}", flush=True)
            continue
        p, b = r["planned"]["plan"], r["benchmark"]
        print(f"  solve wall time      {r['solve_wall_s']}s")
        print(f"  matrix source        {r['planned']['last_run']['matrix_source']}")
        print(f"  committed routes     {p['committed_routes']} of "
              f"{r['planned']['fleet']['total']} trucks")
        print(f"  planned distance     {p['total_distance_km']} km")
        print(f"  greedy baseline      {b['baseline']['total_distance_km']} km")
        print(f"  improvement          {b['improvement']}")
        print(f"  empty running        {p['empty_km_pct']}%  ({p['empty_km']} km)")
        print(f"  payload utilisation  {p['capacity_utilization_pct']}%")
        print(f"  on time (planned)    {p['on_time_pct']}%  late stops {p['late_stops']}")
        print(f"  shift cost           Rs {p['ops_cost_inr']}")
        print(f"  unassigned           {r['planned']['shipments']['unassigned']}")
        print(f"  full day scorecard   {r['day_scorecard']}")

    OUT.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
