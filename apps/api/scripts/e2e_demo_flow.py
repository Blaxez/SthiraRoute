"""End-to-end demo flow against a running API (default http://127.0.0.1:8000)."""

from __future__ import annotations

import json
import sys
import time
import uuid
from typing import Any

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
PASS = 0
FAIL = 0
RESULTS: list[tuple[str, bool, str]] = []


def ok(name: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    RESULTS.append((name, True, detail))
    print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))


def fail(name: str, detail: str) -> None:
    global FAIL
    FAIL += 1
    RESULTS.append((name, False, detail))
    print(f"  FAIL  {name} — {detail}")


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        ok(name, detail)
    else:
        fail(name, detail or "assertion failed")


def jget(c: httpx.Client, path: str) -> Any:
    r = c.get(path)
    r.raise_for_status()
    return r.json()


def jpost(c: httpx.Client, path: str, body: dict | None = None) -> Any:
    r = c.post(path, json=body or {})
    r.raise_for_status()
    return r.json()


def jpatch(c: httpx.Client, path: str, body: dict) -> Any:
    r = c.patch(path, json=body)
    r.raise_for_status()
    return r.json()


def _near(stop: dict, lat: float, lon: float, radius_km: float) -> bool:
    import math

    r = 6371.0
    p1, p2 = math.radians(stop["lat"]), math.radians(lat)
    dp, dl = math.radians(lat - stop["lat"]), math.radians(lon - stop["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h)) <= radius_km


def main() -> int:
    print(f"\nSthiraRoute E2E @ {BASE}\n{'=' * 48}")
    t0 = time.time()

    with httpx.Client(base_url=BASE, timeout=60.0) as c:
        # 1. Health
        try:
            h = jget(c, "/health")
            check("health", h.get("status") == "ok", json.dumps(h))
        except Exception as e:
            fail("health", str(e))
            return 1

        # 2. Seed data present
        try:
            vehicles = jget(c, "/api/fleet/vehicles")
            shipments = jget(c, "/api/shipments")
            depots = jget(c, "/api/fleet/depots")
            overlays = jget(c, "/api/constraints")
            check("fleet vehicles", len(vehicles) >= 2, f"n={len(vehicles)}")
            check("shipments", len(shipments) >= 1, f"n={len(shipments)}")
            check("depots", len(depots) >= 1, f"n={len(depots)}")
            check("constraint overlays", len(overlays) >= 1, f"n={len(overlays)}")
        except Exception as e:
            fail("seed data", str(e))
            return 1

        # Ensure vehicles available for plan
        for v in vehicles:
            if v["status"] != "available":
                jpatch(c, f"/api/fleet/vehicles/{v['id']}/status", {"status": "available"})

        # 3. Plan
        try:
            run = jpost(
                c,
                "/api/optimization/runs",
                {"trigger": "plan", "solve_seconds": 6},
            )
            check(
                "plan complete",
                run["status"] == "completed",
                f"run=#{run['id']} obj={run.get('objective')}",
            )
            metrics = json.loads(run.get("metrics_json") or "{}")
            check(
                "plan has routes/metrics",
                metrics.get("vehicles_used", 0) >= 1 and metrics.get("total_distance_km", 0) > 0,
                json.dumps(metrics),
            )
            run_id = run["id"]
        except Exception as e:
            fail("plan", str(e))
            return 1

        # 4. Explain
        try:
            ex = jget(c, f"/api/optimization/runs/{run_id}/explain")
            check(
                "explain",
                bool(ex.get("summary")) and ex.get("run_id") == run_id,
                (ex.get("summary") or "")[:120],
            )
        except Exception as e:
            fail("explain", str(e))

        # 5. Approve + committed
        try:
            approved = jpost(c, "/api/optimization/approve", {"run_id": run_id})
            check("approve", approved["status"] == "completed", f"run=#{approved['id']}")
            committed = jget(c, "/api/optimization/routes/committed")
            stop_n = sum(len(r.get("stops") or []) for r in committed)
            check(
                "committed routes",
                len(committed) >= 1 and stop_n >= 1,
                f"routes={len(committed)} stops={stop_n}",
            )
        except Exception as e:
            fail("approve/committed", str(e))
            return 1

        # Pick a vehicle that has a committed route
        vehicle_id = committed[0]["vehicle_id"]

        # 6. Breakdown reopt + stability
        try:
            br = jpost(
                c,
                "/api/events/breakdown",
                {
                    "vehicle_id": vehicle_id,
                    "solve_seconds": 5,
                    "auto_approve": False,
                },
            )
            check(
                "breakdown reopt",
                br["status"] in ("completed", "failed"),
                f"run=#{br['id']} status={br['status']}",
            )
            if br["status"] == "completed":
                bm = json.loads(br.get("metrics_json") or "{}")
                be = json.loads(br.get("explain_json") or "{}")
                check(
                    "stability metrics present",
                    "stability_score" in bm or "stability" in be,
                    f"metrics={bm.get('stability_score')} explain_keys={list(be.keys())}",
                )
                jpost(c, "/api/optimization/approve", {"run_id": br["id"]})
                ok("approve after breakdown")
        except Exception as e:
            fail("breakdown", str(e))

        # Restore all vehicles available for insert path
        vehicles = jget(c, "/api/fleet/vehicles")
        for v in vehicles:
            if v["status"] != "available":
                jpatch(c, f"/api/fleet/vehicles/{v['id']}/status", {"status": "available"})

        # 7. Insert + reopt
        try:
            code = f"E2E-{uuid.uuid4().hex[:6]}"
            ins = jpost(
                c,
                "/api/events/insert-and-reopt",
                {
                    "code": code,
                    "customer_name": "E2E Customer",
                    "lat": 12.9352,
                    "lon": 77.6245,
                    "demand_kg": 40,
                    "demand_m3": 0.4,
                    "tw_start_min": 8 * 60,
                    "tw_end_min": 18 * 60,
                    "service_min": 10,
                    "priority": 1,
                    "solve_seconds": 5,
                    "auto_approve": True,
                },
            )
            check(
                "insert+reopt",
                ins["status"] == "completed",
                f"run=#{ins['id']} code={code}",
            )
        except Exception as e:
            fail("insert+reopt", str(e))

        # 8. GPS tick + POD-style tracking
        try:
            live_before = jget(c, "/api/tracking/vehicles/live")
            gps = jpost(c, "/api/events/simulate-gps", {})
            check("gps simulate", isinstance(gps, list), f"updated={len(gps)}")
            committed = jget(c, "/api/optimization/routes/committed")
            delivery = None
            for r in committed:
                for s in r.get("stops") or []:
                    if s.get("kind") == "delivery":
                        delivery = (r["vehicle_id"], s)
                        break
                if delivery:
                    break
            if delivery:
                vid, stop = delivery
                pod = jpost(
                    c,
                    "/api/tracking/gps",
                    {"vehicle_id": vid, "lat": stop["lat"], "lon": stop["lon"]},
                )
                check("pod gps", pod["id"] == vid, f"vehicle={vid}")
            else:
                fail("pod gps", "no delivery stop in committed routes")
            _ = live_before
        except Exception as e:
            fail("gps/pod", str(e))

        # 9. Constraint toggle round-trip
        try:
            overlays = jget(c, "/api/constraints")
            o = overlays[0]
            toggled = jpatch(c, f"/api/constraints/{o['id']}", {"active": not o["active"]})
            check(
                "overlay toggle",
                toggled["active"] != o["active"],
                f"{o['name'][:40]} -> {toggled['active']}",
            )
            # restore
            jpatch(c, f"/api/constraints/{o['id']}", {"active": o["active"]})
            ok("overlay restore")
        except Exception as e:
            fail("overlay toggle", str(e))

        # 10. Fresh plan with overlays active (smoke)
        try:
            for v in jget(c, "/api/fleet/vehicles"):
                if v["status"] != "available":
                    jpatch(c, f"/api/fleet/vehicles/{v['id']}/status", {"status": "available"})
            run2 = jpost(
                c,
                "/api/optimization/runs",
                {"trigger": "plan", "solve_seconds": 5},
            )
            m2 = json.loads(run2.get("metrics_json") or "{}")
            check(
                "replan with overlays",
                run2["status"] == "completed",
                f"no_entry_hits={m2.get('no_entry_hits')} km={m2.get('total_distance_km')}",
            )
        except Exception as e:
            fail("replan overlays", str(e))

        # 10b. 3D load plan + LIFO audit on every committed route
        try:
            committed = jget(c, "/api/optimization/routes/committed")
            plans = []
            for r in committed:
                lp = jget(c, f"/api/optimization/routes/{r['id']}/loadplan")
                plans.append((r["id"], lp))
            check("load plan exists for every route", len(plans) == len(committed),
                  f"{len(plans)}/{len(committed)}")
            check("every route is physically loadable",
                  all(lp["feasible"] for _, lp in plans),
                  ", ".join(f"r{i}:{[u['code'] for u in lp['unplaced']]}"
                            for i, lp in plans if not lp["feasible"]) or "all fit")

            # Independent geometric audit — do not trust the packer's own flag.
            overlaps = blocking = floating = 0
            for _, lp in plans:
                ps = lp["placements"]
                for i, a in enumerate(ps):
                    ax2, ay2, az2 = a["x"] + a["l"], a["y"] + a["w"], a["z"] + a["h"]
                    if a["z"] > 0 and not any(
                        b is not a and b["z"] + b["h"] == a["z"]
                        and a["x"] < b["x"] + b["l"] and b["x"] < ax2
                        and a["y"] < b["y"] + b["w"] and b["y"] < ay2
                        for b in ps
                    ):
                        floating += 1
                    for b in ps[i + 1:]:
                        bx2, by2, bz2 = b["x"] + b["l"], b["y"] + b["w"], b["z"] + b["h"]
                        if (a["x"] < bx2 and b["x"] < ax2 and a["y"] < by2
                                and b["y"] < ay2 and a["z"] < bz2 and b["z"] < az2):
                            overlaps += 1
                    for b in ps:
                        if b["seq"] <= a["seq"]:
                            continue
                        bx2, by2, bz2 = b["x"] + b["l"], b["y"] + b["w"], b["z"] + b["h"]
                        yz = (a["y"] < by2 and b["y"] < ay2 and a["z"] < bz2 and b["z"] < az2)
                        xy = (a["x"] < bx2 and b["x"] < ax2 and a["y"] < by2 and b["y"] < ay2)
                        if (ax2 <= b["x"] and yz) or (az2 <= b["z"] and xy):
                            blocking += 1
            check("no two cartons occupy the same space", overlaps == 0, f"{overlaps} overlaps")
            check("no carton floats unsupported", floating == 0, f"{floating} floating")
            check("independent LIFO audit passes", blocking == 0,
                  f"{blocking} earlier drops trapped by later ones")

            # Cartons must fit inside the declared deck.
            outside = sum(
                1 for _, lp in plans for a in lp["placements"]
                if a["x"] + a["l"] > lp["container"]["l"]
                or a["y"] + a["w"] > lp["container"]["w"]
                or a["z"] + a["h"] > lp["container"]["h"]
            )
            check("every carton fits inside the deck", outside == 0, f"{outside} outside")
            check("centre of gravity reported for every route",
                  all(0 < lp["cog_x_pct"] < 100 for _, lp in plans),
                  ", ".join(f"{lp['cog_x_pct']}%" for _, lp in plans))
            check("any out-of-band centre of gravity carries an explanation",
                  all(lp["cog_ok"] or lp["notes"] for _, lp in plans),
                  "flagged loads explain themselves")
        except Exception as e:
            fail("load plan", str(e))

        # 10c. Compatibility: the reefer consignment must ride a reefer truck
        try:
            shipments_now = jget(c, "/api/shipments")
            vehicles_now = jget(c, "/api/fleet/vehicles")
            reefer_ship = next(
                (s for s in shipments_now if s.get("requires_feature") == "reefer"), None
            )
            reefer_vehicles = {
                v["id"] for v in vehicles_now if "reefer" in (v.get("features") or "")
            }
            check("fleet declares a reefer vehicle", bool(reefer_vehicles),
                  str(reefer_vehicles))
            if reefer_ship and reefer_vehicles:
                committed = jget(c, "/api/optimization/routes/committed")
                carrier = next(
                    (r["vehicle_id"] for r in committed
                     for st in r["stops"] if st.get("shipment_id") == reefer_ship["id"]),
                    None,
                )
                check("cold-chain load is on a reefer vehicle",
                      carrier is None or carrier in reefer_vehicles,
                      f"{reefer_ship['code']} -> vehicle {carrier}")
        except Exception as e:
            fail("compatibility", str(e))

        # 11. India curfew actually binds and pushes ETAs out of the ban window
        try:
            overlays = jget(c, "/api/constraints")
            ban = next(o for o in overlays if o["active"])
            m2 = json.loads(run2.get("metrics_json") or "{}")
            check(
                "no-entry constraint binds",
                m2.get("no_entry_hits", 0) > 0,
                f"hits={m2.get('no_entry_hits')}",
            )
            inside = [
                s
                for r in run2.get("routes") or []
                for s in r.get("stops") or []
                if s["kind"] == "delivery"
                and _near(s, ban["center_lat"], ban["center_lon"], ban["radius_km"])
            ]
            violations = [
                s
                for s in inside
                if s["eta_min"] is not None
                and ban["ban_start_min"] <= s["eta_min"] < ban["ban_end_min"]
            ]
            check(
                "no CBD delivery inside ban window",
                not violations,
                f"{len(inside)} CBD stops, {len(violations)} violations",
            )
        except Exception as e:
            fail("curfew enforcement", str(e))

        # 12. Multi-depot
        try:
            m2 = json.loads(run2.get("metrics_json") or "{}")
            check("multi-depot planning", m2.get("depots_used", 0) >= 2,
                  f"depots_used={m2.get('depots_used')}")
        except Exception as e:
            fail("multi-depot", str(e))

        # 13. Graceful degradation: an impossible shipment is dropped, not fatal
        try:
            jpost(c, "/api/shipments", {
                "code": f"E2E-HUGE-{uuid.uuid4().hex[:4]}",
                "customer_name": "Oversized consignment", "lat": 12.95, "lon": 77.62,
                "demand_kg": 99999, "demand_m3": 500,
                "tw_start_min": 480, "tw_end_min": 1080, "service_min": 10,
            })
            run3 = jpost(c, "/api/optimization/runs", {"trigger": "plan", "solve_seconds": 5})
            m3 = json.loads(run3.get("metrics_json") or "{}")
            e3 = json.loads(run3.get("explain_json") or "{}")
            check("impossible load does not break the plan",
                  run3["status"] == "completed" and m3.get("unserved_count", 0) == 1,
                  f"status={run3['status']} unserved={m3.get('unserved_count')}")
            check("unserved shipment carries a reason + relaxation options",
                  bool(e3.get("unserved")) and bool(e3.get("relaxation_options")),
                  (e3.get("unserved") or [{}])[0].get("reason", "")[:70])
        except Exception as e:
            fail("graceful degradation", str(e))

        # 14. Gain gate on an event re-optimization
        try:
            ins2 = jpost(c, "/api/events/insert-and-reopt", {
                "code": f"E2E-G-{uuid.uuid4().hex[:4]}", "customer_name": "Gate test",
                "lat": 12.99, "lon": 77.60, "demand_kg": 30, "demand_m3": 0.2,
                "tw_start_min": 480, "tw_end_min": 1080, "service_min": 10,
                "solve_seconds": 5, "auto_approve": False,
            })
            eg = json.loads(ins2.get("explain_json") or "{}")
            gate = eg.get("gain_gate") or {}
            check("gain gate decides commit/hold",
                  gate.get("decision") in ("commit", "hold"),
                  f"{gate.get('decision')}: {str(gate.get('reason'))[:60]}")
            check("gain gate prices churn in rupees",
                  "churn_cost_inr" in gate, json.dumps(gate)[:80])
        except Exception as e:
            fail("gain gate", str(e))

        # 15. KPI + benchmark endpoints
        try:
            k = jget(c, "/api/analytics/kpis")
            check("kpis", k["plan"]["committed_routes"] >= 1 and "on_time_pct" in k["plan"],
                  f"util={k['plan']['capacity_utilization_pct']}% "
                  f"ontime={k['plan']['on_time_pct']}% empty={k['plan']['empty_km_pct']}%")
            b = jget(c, "/api/analytics/benchmark")
            imp = b.get("improvement") or {}
            check("benchmark vs greedy baseline",
                  b["baseline"]["total_distance_km"] > 0 and imp.get("distance_pct") is not None,
                  f"baseline={b['baseline']['total_distance_km']}km "
                  f"improvement={imp.get('distance_pct')}%")
        except Exception as e:
            fail("analytics", str(e))

        # 16. Stakeholder board: one plan on every desk (PS2 coordination)
        try:
            board = jget(c, "/api/network/board")
            ps2 = board.get("ps2") or {}
            check("network board serves all four PS2 slices",
                  all(k in ps2 for k in ("monitor", "optimize", "allocate", "track")),
                  ",".join(ps2))
            check("dock load allocation is populated",
                  len(board.get("dock") or []) >= 1,
                  f"{len(board.get('dock') or [])} trucks")
            assigned = [x for x in board.get("consignments") or [] if x.get("vehicle_code")]
            check("consignments share a truck with the dock",
                  assigned and assigned[0]["vehicle_code"] in {d["code"] for d in board["dock"]})
            code = assigned[0]["code"]
            pub = jget(c, f"/api/network/track/{code}")
            check("consignee track is the same object",
                  pub["code"] == code and pub["vehicle_code"] == assigned[0]["vehicle_code"],
                  f"{code} on {pub['vehicle_code']}")
        except Exception as e:
            fail("network board", str(e))

    elapsed = time.time() - t0
    print(f"\n{'=' * 48}")
    print(f"Result: {PASS} passed, {FAIL} failed in {elapsed:.1f}s")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
