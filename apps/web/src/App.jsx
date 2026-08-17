import React, { useCallback, useEffect, useRef, useState } from "react";
import Assistant from "./Assistant.jsx";
import Blob from "./Blob.jsx";
import DockView from "./DockView.jsx";
import DriverView from "./DriverView.jsx";
import Evidence from "./Evidence.jsx";
import FleetRail from "./FleetRail.jsx";
import Guide from "./Guide.jsx";
import Inspector from "./Inspector.jsx";
import Lab from "./Lab.jsx";
import LoadPlan from "./LoadPlan.jsx";
import MapPanel from "./MapPanel.jsx";
import NowBar from "./NowBar.jsx";
import { SPEEDS } from "./ShiftBar.jsx";
import Timeline, { hhmm } from "./Timeline.jsx";
import TrackView from "./TrackView.jsx";

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${path} → ${res.status}: ${body.slice(0, 300)}`);
  }
  return res.json();
}

export default function App() {
  const [view, setView] = useState("dispatch");
  const [vehicles, setVehicles] = useState([]);
  const [shipments, setShipments] = useState([]);
  const [depots, setDepots] = useState([]);
  const [overlays, setOverlays] = useState([]);
  const [committed, setCommitted] = useState([]);
  const [kpis, setKpis] = useState(null);
  const [benchmark, setBenchmark] = useState(null);
  const [run, setRun] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [live, setLive] = useState(false);
  const [feed, setFeed] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loadPlan, setLoadPlan] = useState(null);
  const [labOpen, setLabOpen] = useState(false);
  const [sim, setSim] = useState(null);
  const [simRunning, setSimRunning] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [decision, setDecision] = useState(null);
  const [keysOpen, setKeysOpen] = useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [guide, setGuide] = useState(null);
  const [liveVoice, setLiveVoice] = useState("off");
  const askRef = useRef(null);
  const simTimer = useRef(null);
  // Bumped whenever the dispatcher changes an overlay by hand, so a refresh
  // that started earlier knows its payload is stale.
  const overlayEdit = useRef(0);
  // A tick can run the solver, so it must never overlap itself: a queue of
  // pending ticks would make the clock lurch and the fleet teleport.
  const ticking = useRef(false);

  const log = useCallback((msg) => {
    const t = new Date().toLocaleTimeString("en-IN", { hour12: false });
    setFeed((f) => [{ t, msg, id: `${Date.now()}-${Math.random()}` }, ...f].slice(0, 40));
  }, []);

  const refresh = useCallback(async () => {
    const stamp = overlayEdit.current;
    const [v, s, d, o, k, c] = await Promise.all([
      api("/api/fleet/vehicles"),
      api("/api/shipments"),
      api("/api/fleet/depots"),
      api("/api/constraints"),
      api("/api/analytics/kpis"),
      api("/api/optimization/routes/committed"),
    ]);
    setVehicles(v);
    setShipments(s);
    setDepots(d);
    // A refresh already in flight when the dispatcher flips a curfew read the
    // overlays before that flip existed. Applying it would silently switch the
    // rule back on — which, mid re-plan, looks exactly like the toggle being
    // broken. Newer intent wins over older data.
    if (stamp === overlayEdit.current) setOverlays(o);
    setKpis(k);
    setCommitted(c);
  }, []);

  const refreshSim = useCallback(async () => {
    const s = await api("/api/sim/state");
    setSim(s);
    setSimRunning(s.running);
    return s;
  }, []);

  useEffect(() => {
    refresh().catch((e) => setError(String(e.message || e)));
    refreshSim().catch(() => {});
    // A reload mid-shift must not lose the baseline comparison: it is the one
    // number that says this is an optimiser rather than a tracker.
    api("/api/analytics/benchmark").then(setBenchmark).catch(() => {});
  }, [refresh, refreshSim]);

  // A reload mid-shift still has a plan in force; Lab should open on that run
  // rather than an empty Shift panel.
  useEffect(() => {
    const id = kpis?.last_run?.id;
    const live = kpis?.plan?.committed_routes ?? 0;
    if (!id || run || live < 1) return undefined;
    let on = true;
    api(`/api/optimization/runs/${id}`)
      .then((r) => { if (on) setRun(r); })
      .catch(() => {});
    return () => { on = false; };
  }, [kpis?.last_run?.id, kpis?.plan?.committed_routes, run]);

  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/api/events/ws`);
    ws.onopen = () => setLive(true);
    ws.onclose = () => setLive(false);
    ws.onerror = () => setLive(false);
    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.type === "gps_tick" && msg.vehicles) {
        setVehicles((prev) =>
          prev.map((v) => {
            const u = msg.vehicles.find((x) => x.id === v.id);
            if (!u) return v;
            return {
              ...v,
              lat: u.lat,
              lon: u.lon,
              status: u.status,
              path_progress_km: u.path_progress_km ?? v.path_progress_km,
            };
          })
        );
      }
      if (["breakdown_reopt", "insert_reopt", "constraint_reopt", "demo_reset",
           "sim_prepared"].includes(msg.type)) {
        refresh().catch(() => {});
      }
    };
    const ping = setInterval(() => ws.readyState === WebSocket.OPEN && ws.send("ping"), 20000);
    return () => {
      clearInterval(ping);
      // Closing a socket that has not finished opening logs an error on every
      // reload — and in dev React mounts effects twice, so it logged on every
      // single start. Wait for the handshake, then hang up.
      if (ws.readyState === WebSocket.CONNECTING) {
        ws.addEventListener("open", () => ws.close());
      } else {
        ws.close();
      }
    };
  }, [refresh]);

  // One wrapper so every action gets the same busy/error/refresh handling.
  const act = useCallback(
    async (key, label, fn) => {
      setBusy(key);
      setError("");
      try {
        const result = await fn();
        if (result?.id && result?.status) {
          setRun(result);
          const m = safeParse(result.metrics_json);
          log(
            `${label} → run #${result.id} ${result.status}` +
              (m.total_distance_km ? ` · ${m.total_distance_km} km` : "") +
              (m.unserved_count ? ` · ${m.unserved_count} unserved` : "")
          );
        } else {
          log(label);
        }
        await refresh();
        api("/api/analytics/benchmark").then(setBenchmark).catch(() => {});
        return result;
      } catch (e) {
        setError(String(e.message || e));
        log(`${label} failed`);
      } finally {
        setBusy("");
      }
    },
    [log, refresh]
  );

  // ---------------------------------------------------------------- plan --

  const runPlan = () =>
    act("plan", "Plan", () =>
      api("/api/optimization/runs", {
        method: "POST",
        body: JSON.stringify({ trigger: "plan", solve_seconds: 8 }),
      })
    );

  const approve = () =>
    run && act("approve", `Approved run #${run.id}`, () =>
      api("/api/optimization/approve", {
        method: "POST",
        body: JSON.stringify({ run_id: run.id }),
      })
    );

  const toggleOverlay = async (o) => {
    setError("");
    const next = !o.active;
    // Flip locally first: a curfew toggle should feel instant, and the
    // timeline band is the thing the dispatcher is watching.
    overlayEdit.current += 1;
    setOverlays((prev) => prev.map((x) => (x.id === o.id ? { ...x, active: next } : x)));
    try {
      await api(`/api/constraints/${o.id}`, {
        method: "PATCH",
        body: JSON.stringify({ active: next }),
      });
      log(`${next ? "Applied" : "Lifted"} ${shortName(o.name)} — re-plan to apply it to routes`);
      await refresh();
    } catch (e) {
      overlayEdit.current += 1;
      setOverlays((prev) => prev.map((x) => (x.id === o.id ? { ...x, active: o.active } : x)));
      setError(String(e.message || e));
    }
  };

  const replanForConstraints = () =>
    act("constraint", "Re-planned for constraint change", () =>
      api("/api/events/constraint-change", {
        method: "POST",
        body: JSON.stringify({ note: "Dispatcher changed a no-entry overlay.", solve_seconds: 6 }),
      })
    );

  // --------------------------------------------------------------- shift --

  // Whatever the autopilot decided, show the reasoning: the run it produced
  // carries the churn gate and the explain payload the inspector renders.
  const adoptDecision = useCallback(async (d) => {
    setDecision(d || null);
    if (!d?.run_id) return;
    try {
      setRun(await api(`/api/optimization/runs/${d.run_id}`));
      api("/api/analytics/benchmark").then(setBenchmark).catch(() => {});
    } catch {
      /* the decision line still says what happened */
    }
  }, []);

  const applySnapshot = useCallback(
    async (snap, { reload = true } = {}) => {
      setSim(snap);
      setSimRunning(snap.running);
      if (snap.decision !== undefined) await adoptDecision(snap.decision);
      if (reload) await refresh();
      return snap;
    },
    [adoptDecision, refresh]
  );

  // The cheap half of a refresh: markers and counters, no route geometry.
  const refreshFleetOnly = useCallback(async () => {
    try {
      const [v, s, k] = await Promise.all([
        api("/api/fleet/vehicles"),
        api("/api/shipments"),
        api("/api/analytics/kpis"),
      ]);
      setVehicles(v);
      setShipments(s);
      setKpis(k);
    } catch {
      /* a dropped poll is not worth an error banner */
    }
  }, []);

  const tick = useCallback(async () => {
    if (ticking.current) return null;
    ticking.current = true;
    try {
      const snap = await api("/api/sim/tick", { method: "POST", body: "{}" });
      const replanned = snap.decision?.action === "dispatched";
      // Positions change every tick; routes only change when a plan is
      // dispatched, so the expensive reload is conditional.
      setSim(snap);
      setSimRunning(snap.running);
      if (snap.decision) await adoptDecision(snap.decision);
      await (replanned ? refresh() : refreshFleetOnly());
      if (snap.shift_over) setSimRunning(false);
      return snap;
    } catch (e) {
      setError(String(e.message || e));
      setSimRunning(false);
      return null;
    } finally {
      ticking.current = false;
    }
  }, [adoptDecision, refresh, refreshFleetOnly]);

  useEffect(() => {
    if (simTimer.current) clearInterval(simTimer.current);
    if (!simRunning) return undefined;
    const ms = SPEEDS.find((s) => s.id === speed)?.ms ?? 1200;
    simTimer.current = setInterval(tick, ms);
    return () => clearInterval(simTimer.current);
  }, [simRunning, speed, tick]);

  const togglePlay = async () => {
    const next = !simRunning;
    setSimRunning(next);
    try {
      await api(`/api/sim/run?running=${next}`, { method: "POST", body: "{}" });
      log(next ? "Shift running" : "Shift paused");
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  const prepare = () =>
    act("prepare", "Shift prepared", async () => {
      const snap = await api("/api/sim/prepare?solve_seconds=8", { method: "POST", body: "{}" });
      setSimRunning(false);
      setDecision(null);
      await applySnapshot(snap, { reload: true });
      if (snap.run_id) setRun(await api(`/api/optimization/runs/${snap.run_id}`));
      return null;
    });

  const startDay = () =>
    act("demo", "Day started", async () => {
      const snap = await api("/api/sim/demo?solve_seconds=8", { method: "POST", body: "{}" });
      setDecision(null);
      setSpeed(4);
      await applySnapshot(snap, { reload: true });
      if (snap.run_id) setRun(await api(`/api/optimization/runs/${snap.run_id}`));
      api("/api/analytics/benchmark").then(setBenchmark).catch(() => {});
      setSimRunning(true);
      await api("/api/sim/run?running=true", { method: "POST", body: "{}" }).catch(() => {});
      return null;
    });

  const loadPlaybook = (id) =>
    act("playbook", `Loaded playbook: ${id.replace(/_/g, " ")}`, async () => {
      await applySnapshot(
        await api("/api/sim/playbook", { method: "POST", body: JSON.stringify({ scenario: id }) }),
        { reload: false }
      );
      return null;
    });

  const injectEvent = async (kind) => {
    setBusy(`inject:${kind}`);
    setError("");
    try {
      await applySnapshot(
        await api("/api/sim/inject", { method: "POST", body: JSON.stringify({ kind }) })
      );
      log(`Injected: ${kind.replace(/_/g, " ")}`);
    } catch (e) {
      const msg = String(e.message || e);
      // A 409 means the disruption cannot apply to the current state — there is
      // no loaded truck to break, nothing left to cancel. That is a note about
      // the world, not a failure, so it does not deserve the error banner.
      const conflict = msg.match(/→ 409.*?"detail":"([^"]+)"/);
      if (conflict) log(`Can't ${kind.replace(/_/g, " ")} now — ${conflict[1]}`);
      else setError(msg);
    } finally {
      setBusy("");
    }
  };

  const toggleAutopilot = async () => {
    const next = !sim?.autopilot;
    try {
      await applySnapshot(
        await api(`/api/sim/autopilot?on=${next}`, { method: "POST", body: "{}" }),
        { reload: false }
      );
      log(`Autopilot ${next ? "on" : "off"}`);
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  // Changing city rebuilds the dataset, so everything on screen is stale.
  const switchCity = (city) =>
    act(`city:${city}`, `Switched to ${city}`, async () => {
      stopClock();
      setRun(null);
      setBenchmark(null);
      setSelected(null);
      setDecision(null);
      await applySnapshot(
        await api("/api/sim/city", { method: "POST", body: JSON.stringify({ city }) })
      );
      return null;
    });

  const rewind = () =>
    act("rewind", "Shift rewound to 06:00", async () => {
      setSimRunning(false);
      setDecision(null);
      await applySnapshot(await api("/api/sim/reset", { method: "POST", body: "{}" }));
      return null;
    });

  const stopClock = useCallback(() => {
    if (simTimer.current) clearInterval(simTimer.current);
    simTimer.current = null;
    setSimRunning(false);
  }, []);

  useEffect(() => stopClock, [stopClock]);

  // Each open gets a ticket, and only the newest one is allowed to land. The
  // officer can be asked for MH-02 and then MH-03 inside a second; without
  // this, whichever fetch happened to finish last won — and a close that
  // arrived mid-flight was undone by the reply to a request nobody wanted.
  const deckTicket = useRef(0);

  const openLoadPlan = async (route, vehicleCode, camera) => {
    setError("");
    const mine = ++deckTicket.current;
    try {
      const plan = await api(`/api/optimization/routes/${route.id}/loadplan`);
      if (mine !== deckTicket.current) return;
      setLoadPlan({ plan, vehicleCode, camera, key: route.id });
    } catch (e) {
      if (mine === deckTicket.current) setError(String(e.message || e));
    }
  };

  const closeLoadPlan = useCallback(() => {
    deckTicket.current += 1;
    setLoadPlan(null);
  }, []);

  // The officer's version of the same thing: it knows a truck by its code, not
  // by a route object it never saw. Asking for MH-03 while MH-02 is open used
  // to do nothing at all, because there was no tool for it and no path from a
  // code to a route — so the officer said it could not switch trucks.
  const openDeckByCode = useCallback(
    (code, camera) => {
      const want = String(code || "").trim().toLowerCase();
      const v = vehicles.find((x) => String(x.code).toLowerCase() === want);
      if (!v) return false;
      const route = (committed.length ? committed : run?.routes || []).find(
        (r) => r.vehicle_id === v.id
      );
      if (!route) return false;
      openLoadPlan(route, v.code, camera);
      return true;
    },
    // openLoadPlan is stable enough for this: it only closes over setters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [committed, run, vehicles]
  );

  const resetDemo = () =>
    act("reset", "Demo data rebuilt", async () => {
      await api("/api/events/reset-demo", { method: "POST", body: "{}" });
      stopClock();
      setRun(null);
      setCommitted([]);
      setKpis((k) =>
        k
          ? {
              ...k,
              last_run: { ...(k.last_run || {}), id: null },
              plan: { ...(k.plan || {}), committed_routes: 0 },
            }
          : k
      );
      setBenchmark(null);
      setSelected(null);
      setDecision(null);
      await api("/api/sim/reset", { method: "POST", body: "{}" }).catch(() => {});
      await refreshSim().catch(() => {});
      return null;
    });

  // The key badges on the action rail are a promise; honour it.
  useEffect(() => {
    const onKey = (e) => {
      if (e.target.matches("input, select, textarea") || e.metaKey || e.ctrlKey || e.altKey) {
        if (e.key === "Escape" && labOpen && e.target.matches("input[type='checkbox']")) {
          setLabOpen(false);
          return e.preventDefault();
        }
        return;
      }
      if (loadPlan) return; // the overlay owns the keyboard while it is open
      if (e.key === "Escape") {
        if (askOpen) setAskOpen(false);
        else if (keysOpen) setKeysOpen(false);
        else if (labOpen) setLabOpen(false);
        else if (view !== "dispatch") setView("dispatch");
        else setSelected(null);
        return e.preventDefault();
      }
      if (e.key === "/") {
        if (!askOpen) setAskOpen(true);
        return e.preventDefault();
      }
      if (keysOpen && e.key !== "?") setKeysOpen(false);
      const map = {
        " ": togglePlay,
        p: startDay,
        "1": runPlan,
        "2": approve,
        "3": () => injectEvent("breakdown"),
        "4": () => injectEvent("new_order"),
        "5": () => injectEvent("closure"),
        "6": replanForConstraints,
        a: toggleAutopilot,
        l: () => setLabOpen((v) => !v),
        d: () => setView((v) => (v === "driver" ? "dispatch" : "driver")),
        w: () => setView((v) => (v === "dock" ? "dispatch" : "dock")),
        t: () => setView((v) => (v === "track" ? "dispatch" : "track")),
        r: resetDemo,
        "?": () => setKeysOpen((v) => !v),
      };
      const fn = map[e.key === " " ? " " : e.key.toLowerCase()];
      if (!fn) return;
      e.preventDefault();
      fn();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const goDesk = (id) => setView(id);

  // Highlights arrive one function call at a time while the officer is still
  // talking. Appending to a live queue lets one answer walk the screen;
  // replacing would mean only the last thing mentioned ever got pointed at.
  const pushGuide = useCallback((steps) => {
    const clean = (steps || []).filter((s) => s?.target);
    if (!clean.length) return;
    setGuide((g) => (g ? { key: g.key, steps: [...g.steps, ...clean] } : { key: Date.now(), steps: clean }));
  }, []);

  // Same debug handle as window.__map: the smoke suite drives the guide
  // through it, so pointing is testable without spending a Gemini call.
  useEffect(() => {
    window.__guide = pushGuide;
  }, [pushGuide]);

  const focusOnMap = useCallback((where) => {
    const m = typeof window !== "undefined" && window.__map;
    if (!m) return;
    // Returning to a desk remounts the map, which fits the fleet over about a
    // second. Without this the camera drifts back off whatever the officer
    // just asked you to look at.
    m.stop();
    if (where.kind === "shipment") {
      const s = shipments.find(
        (x) => String(x.code).toLowerCase() === String(where.code).toLowerCase()
      );
      if (s) {
        setSelected(s.id);
        m.easeTo({ center: [s.lon, s.lat], zoom: Math.max(m.getZoom(), 12.5), duration: 700 });
      }
      return;
    }
    const v = vehicles.find(
      (x) => String(x.code).toLowerCase() === String(where.code).toLowerCase()
    );
    if (v?.lat != null) {
      m.easeTo({ center: [v.lon, v.lat], zoom: Math.max(m.getZoom(), 12.5), duration: 700 });
    }
  }, [shipments, vehicles]);

  const onAskAction = useCallback((act) => {
    const name = act?.name;
    const args = act?.args || {};
    if (name === "highlight") pushGuide([{ target: args.target, note: args.note }]);
    if (name === "switch_desk" && args.desk) setView(args.desk);
    if (name === "open_lab") setLabOpen(!!args.open);
    if (name === "tour") {
      pushGuide(
        (args.steps || []).map((s) => ({ target: s.target, note: s.note }))
      );
    }
    if (name === "focus_map") {
      // The deck viewer is a full-screen overlay: moving the camera behind it
      // is a move nobody sees.
      closeLoadPlan();
      focusOnMap({ kind: args.kind || "vehicle", code: args.code });
      if (args.code) pushGuide([{ target: `${args.kind || "vehicle"}:${args.code}`, note: args.note }]);
    }
    if (name === "open_deck") {
      const ok = openDeckByCode(args.code, args.camera);
      if (!ok) setError(`No committed route for ${args.code} — nothing to load.`);
      else if (args.note) {
        pushGuide([{ target: args.camera === "top" ? "deck_cog" : "deck3d", note: args.note }]);
      }
    }
    if (name === "close_overlay") {
      closeLoadPlan();
      setLabOpen(false);
    }
  }, [focusOnMap, openDeckByCode, pushGuide]);

  // The officer's whole control surface, reachable without spending a Gemini
  // call. The smoke suite drives the tools through this, which is the only way
  // "asking for MH-03 while MH-02 is open" is a thing a test can assert.
  useEffect(() => {
    window.__act = onAskAction;
  }, [onAskAction]);

  const askContext = {
    view,
    selected,
    decision,
    kpis,
    vehicles: vehicles.map((v) => ({
      id: v.id, code: v.code, status: v.status, lat: v.lat, lon: v.lon,
      vehicle_type: v.vehicle_type || v.kind,
    })),
    shipments: shipments.map((s) => ({
      id: s.id, code: s.code, customer_name: s.customer_name, status: s.status,
      priority: s.priority, tw_start_min: s.tw_start_min, tw_end_min: s.tw_end_min,
      // The API calls it demand_kg. Sending weight_kg meant every weight the
      // officer was asked for came back "not specified".
      demand_kg: s.demand_kg, service_min: s.service_min, lat: s.lat, lon: s.lon,
    })),
    overlays: overlays.map((o) => ({
      id: o.id, name: o.name, active: o.active, kind: o.kind,
      ban_start_min: o.ban_start_min, ban_end_min: o.ban_end_min,
    })),
  };

  const plan = kpis?.plan;
  const dispatched = (plan?.committed_routes ?? 0) > 0 || committed.length > 0;
  const nextAction = (() => {
    if (!run && sim && !sim.running && sim.clock === "06:00" && !dispatched) return "prepare";
    if (!run || run.status !== "completed") return "plan";
    if (!committed.some((r) => r.optimization_run_id === run.id)) return "approve";
    return null;
  })();
  // The stage shows the roads trucks are actually driving. A candidate run
  // that has not been approved is a proposal — dashed on the map, solid in
  // Lab — otherwise the fleet appears to leave the coloured lines the moment
  // a re-plan lands.
  const awaitingApprove =
    !!run?.routes?.length &&
    committed.length > 0 &&
    !committed.some((r) => r.optimization_run_id === run.id);
  const liveRoutes = committed.length ? committed : (run?.routes || []);
  const proposalRoutes = awaitingApprove ? run.routes : [];
  const selectedShipment = shipments.find((s) => s.id === selected);

  const inspector = (
    <Inspector
      embedded
      run={run}
      error={error}
      benchmark={benchmark}
      shipments={shipments}
      vehicles={vehicles}
      selected={selected}
      onSelect={setSelected}
      onOpenLoad={openLoadPlan}
      feed={feed}
      sim={sim}
      kpis={kpis}
      open
      onClose={() => setLabOpen(false)}
    />
  );

  return (
    <>
    <div className={`shell${view !== "dispatch" ? " desk-mode" : ""}`}>
      <NowBar
        sim={sim}
        running={simRunning}
        speed={speed}
        busy={busy}
        live={live}
        view={view}
        dispatched={dispatched}
        kpis={kpis}
        decision={decision}
        onStart={startDay}
        onPlay={togglePlay}
        onSpeed={setSpeed}
        onChangeView={goDesk}
        onLab={() => setLabOpen((v) => !v)}
        labOpen={labOpen}
        onPickCity={switchCity}
        onKeys={() => setKeysOpen(true)}
        onAsk={() => setAskOpen((v) => !v)}
        askOpen={askOpen}
        voice={liveVoice}
      />

      {view === "driver" && (
        <DriverView
          vehicles={vehicles}
          view={view}
          onChange={goDesk}
          live={live}
          clock={sim?.clock}
          onChanged={refresh}
        />
      )}
      {view === "dock" && (
        <DockView
          view={view}
          onChange={goDesk}
          sim={sim}
          live={live}
          onOpenLoadPlan={openLoadPlan}
        />
      )}
      {view === "track" && (
        <TrackView view={view} onChange={goDesk} sim={sim} live={live} />
      )}

      {view === "dispatch" && (
        <>
          <div className="stage">
            <FleetRail
              vehicles={vehicles}
              routes={liveRoutes}
              shipments={shipments}
              simVehicles={sim?.vehicles}
              selected={selected}
              onSelect={setSelected}
              nowMin={sim?.clock_min}
            />
            <div className="stage-map">
            <MapPanel
              depots={depots}
              shipments={shipments}
              vehicles={vehicles}
              routes={liveRoutes}
              committed={committed}
              proposal={proposalRoutes}
              overlays={overlays}
              sim={sim}
              playbackMs={SPEEDS.find((s) => s.id === speed)?.ms ?? 2400}
              onSelect={setSelected}
              selected={selected}
              matrixSource={
                safeParse(run?.metrics_json).matrix_source || kpis?.last_run?.matrix_source
              }
            />
            {!dispatched && (
              <Gate
                city={sim?.city?.label}
                busy={busy}
                onStart={startDay}
              />
            )}
            {selectedShipment && (
              <FocusCard
                shipment={selectedShipment}
                routes={liveRoutes}
                vehicles={vehicles}
                clock={sim?.clock}
                onClear={() => setSelected(null)}
              />
            )}
            </div>
            <Evidence
              kpis={kpis}
              benchmark={benchmark}
              sim={sim}
              decision={decision}
              awaitingApprove={awaitingApprove}
            />
          </div>
          <Timeline
            routes={liveRoutes}
            vehicles={vehicles}
            overlays={overlays}
            shipments={shipments}
            selected={selected}
            onSelect={setSelected}
            nowMin={sim?.clock_min}
          />
        </>
      )}
    </div>

      {guide && (
        <Guide
          key={guide.key}
          steps={guide.steps}
          view={view}
          onDesk={setView}
          onFocusCode={focusOnMap}
          onIdle={() => setGuide(null)}
        />
      )}

      <Assistant
        ref={askRef}
        open={askOpen}
        // While the officer is walking the board, the transcript is in the way
        // of the thing being explained. It slides out and comes straight back
        // with the full answer still in it.
        aside={!!guide}
        onClose={() => setAskOpen(false)}
        onLive={setLiveVoice}
        context={askContext}
        onAction={onAskAction}
      />

      <Blob
        open={askOpen}
        listening={liveVoice !== "off" && liveVoice !== "fallback"}
        // The orb does one thing: talk. Typing lives on the header, so the orb
        // is free to be dragged and to dodge without carrying a control that
        // people then have to chase around the screen.
        onToggle={() => askRef.current?.toggleLive()}
      />

      <Lab
        open={labOpen}
        onClose={() => setLabOpen(false)}
        busy={busy}
        nextAction={nextAction}
        run={run}
        onPrepare={prepare}
        onPlan={runPlan}
        onApprove={approve}
        onReset={resetDemo}
        onRewind={rewind}
        sim={sim}
        onInject={injectEvent}
        onPlaybook={loadPlaybook}
        overlays={overlays}
        onToggleOverlay={toggleOverlay}
        onReplanConstraints={replanForConstraints}
        inspector={inspector}
      />

      {keysOpen && <Shortcuts onClose={() => setKeysOpen(false)} />}

      {loadPlan && (
        <LoadPlan
          // Keyed on the route so switching trucks rebuilds the deck instead of
          // animating cartons from one truck's pack into another's.
          key={loadPlan.key ?? loadPlan.vehicleCode}
          plan={loadPlan.plan}
          vehicleCode={loadPlan.vehicleCode}
          camera={loadPlan.camera}
          onClose={() => closeLoadPlan()}
        />
      )}
    </>
  );
}

function Gate({ city, busy, onStart }) {
  return (
    <div className="gate" role="dialog" aria-label="Start the day">
      <p className="gate-kicker">{city || "City"} · 06:00 · nothing dispatched yet</p>
      <h1>Watch one operating day</h1>
      <p>
        The map is empty because no truck has a job yet. Start the day to
        assign every order, load the fleet, and run the shift. The bright
        plate is the <b>shift clock</b> (06:00–22:00), not your watch. The
        playhead on the timeline is the same clock.
      </p>
      <button className="demo-start" onClick={onStart} disabled={!!busy}>
        {busy === "demo" || busy === "prepare" ? "Planning…" : "Start the day"}
      </button>
    </div>
  );
}

function FocusCard({ shipment, routes, vehicles, clock, onClear }) {
  const stop = (routes || [])
    .flatMap((r) => (r.stops || []).map((s) => ({ ...s, vehicle_id: r.vehicle_id })))
    .find((s) => s.shipment_id === shipment.id);
  const truck = vehicles.find((v) => v.id === stop?.vehicle_id);
  return (
    <aside className="focus" aria-label="Selected consignment" data-hl={`shipment:${shipment.code}`}>
      <header>
        <b>{shipment.code}</b>
        <button className="insp-close" onClick={onClear} aria-label="Clear selection">✕</button>
      </header>
      <p className="focus-who">{shipment.customer_name}</p>
      <dl>
        <div>
          <dt>Now</dt>
          <dd>{clock || "06:00"}</dd>
        </div>
        <div>
          <dt>Truck</dt>
          <dd>{truck?.code || "unassigned"}</dd>
        </div>
        <div>
          <dt>ETA</dt>
          <dd>{stop?.eta_min != null ? hhmm(stop.eta_min) : "—"}</dd>
        </div>
        <div>
          <dt>Window</dt>
          <dd>{hhmm(shipment.tw_start_min)}–{hhmm(shipment.tw_end_min)}</dd>
        </div>
      </dl>
      <p className="focus-status">{statusLine(shipment, stop)}</p>
    </aside>
  );
}

function statusLine(shipment, stop) {
  if (shipment.status === "delivered") return "Delivered.";
  if (stop?.late_min > 0) return `Running late by ${stop.late_min} min.`;
  if (shipment.status === "in_transit") return "On the truck, moving.";
  if (stop) return "On today's plan — waiting for this stop.";
  return "Not on a truck yet.";
}

const KEYS = [
  ["The day", [
    ["P", "start the day — plan, load, dispatch, run"],
    ["Space", "run or pause the clock"],
    ["A", "hand the shift to autopilot"],
  ]],
  ["Lab", [
    ["L", "open or close the lab"],
    ["1", "run a plan for everything unassigned"],
    ["2", "approve and dispatch it"],
    ["6", "re-plan for the current curfews"],
    ["3", "break a truck down"],
    ["4", "inject a priority order"],
    ["5", "close a road"],
  ]],
  ["Desks", [
    ["D", "driver"],
    ["W", "dock — load allocation"],
    ["T", "track a consignment"],
    ["/", "ask the briefing officer"],
    ["Esc", "back out"],
    ["R", "rebuild the demo data"],
    ["?", "this list"],
  ]],
];

function Shortcuts({ onClose }) {
  return (
    <div className="keys-scrim" onClick={onClose}>
      <div className="keys" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Keyboard shortcuts">
        <div className="keys-head">
          Keyboard
          <button className="insp-close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="keys-body">
          {KEYS.map(([group, rows]) => (
            <div className="keys-group" key={group}>
              <b>{group}</b>
              {rows.map(([k, what]) => (
                <div className="keys-row" key={k}>
                  <kbd>{k}</kbd>
                  <span>{what}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
        <div className="keys-foot">
          Click a truck on the map to keep the camera on it. The playhead
          on the timeline is the current shift time — not the time on your watch.
        </div>
      </div>
    </div>
  );
}

function safeParse(s) {
  try {
    return JSON.parse(s || "{}");
  } catch {
    return {};
  }
}

const shortName = (n) => n.replace(/\s*\(template\)\s*/i, "");

