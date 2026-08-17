"""Run a whole simulated operating day headless, and print what happened.

    python scripts/run_shift.py [playbook] [--city delhi] [--quiet-autopilot]

This is the fastest way to see whether the day reads like an operating day
rather than a random walk: it prepares a plan, runs the clock to 22:00, and
prints the narrative plus the end-of-day scorecard. Use it before a demo — if
the log here is boring or nonsensical, the live demo will be too.
"""

from __future__ import annotations

import sys

from app.core.db import SessionLocal, init_db
from app.services import simulation as sim
from app.services.demo_seed import seed


def main() -> int:
    argv = sys.argv[1:]
    city = "bengaluru"
    if "--city" in argv:
        city = argv[argv.index("--city") + 1]
        argv = [a for i, a in enumerate(argv) if a != "--city" and argv[i - 1] != "--city"]
    args = [a for a in argv if not a.startswith("--")]
    playbook = args[0] if args else "full_day"
    autopilot = "--quiet-autopilot" not in sys.argv

    init_db()
    city = seed(do_reset=True, city_id=city)

    db = SessionLocal()
    try:
        sim.get_state(db).city = city
        db.commit()
        print(f"City: {city}")
        print(f"\nPreparing the shift (plan + dispatch)…")
        snap = sim.prepare_shift(db, solve_seconds=6)
        if not snap.get("run_id"):
            print("  planning failed — nothing to run")
            return 1
        print(f"  dispatched run #{snap['run_id']}")

        st = sim.get_state(db)
        st.autopilot = autopilot
        db.commit()
        if playbook != "none":
            sim.load_playbook(db, playbook)
            print(f"  playbook: {sim.PLAYBOOKS[playbook]['label']}")

        # Read the log from the database rather than the snapshot: the snapshot
        # keeps only the most recent events, so a whole-day print built from it
        # silently loses the afternoon.
        from sqlalchemy import select

        from app.models import SimEvent

        last_id = 0
        print(f"\n{'time':>6}  {'kind':<16} what happened")
        print("-" * 96)
        while True:
            snap = sim.tick(db)
            fresh = db.scalars(
                select(SimEvent).where(SimEvent.id > last_id).order_by(SimEvent.id)
            ).all()
            for e in fresh:
                print(f"{sim.hhmm(e.clock_min):>6}  {e.kind:<16} {e.message}")
                last_id = e.id
            if snap["shift_over"]:
                break

        sc = snap["scorecard"]
        print("\nEnd of shift")
        print("-" * 96)
        for k, v in sc.items():
            print(f"  {k:<20} {v}")
        print(f"  {'shipments':<20} {snap['shipments']}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
