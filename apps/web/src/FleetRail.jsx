import React from "react";
import { hhmm } from "./Timeline.jsx";

/**
 * The fleet, always on stage.
 *
 * Before this existed the dispatcher could not answer "how many trucks do I
 * have and is any of them in trouble?" without opening a modal. A broken-down
 * truck was a dot that had stopped moving. One row per vehicle, ordered so the
 * ones that need a human float to the top.
 */

const STATE = {
  down:      { label: "broken down", tone: "bad",  rank: 0 },
  dark:      { label: "GPS dark",    tone: "warn", rank: 1 },
  en_route:  { label: "on the road", tone: "go",   rank: 2 },
  loading:   { label: "at the bay",  tone: "warn", rank: 3 },
  available: { label: "idle",        tone: "idle", rank: 4 },
};

export default function FleetRail({
  vehicles, routes, shipments, simVehicles, selected, onSelect, nowMin,
}) {
  const byId = new Map((simVehicles || []).map((v) => [v.id, v]));
  const rows = (vehicles || [])
    .map((v) => row(v, byId.get(v.id), routes, shipments, nowMin))
    .sort((a, b) => a.rank - b.rank || a.code.localeCompare(b.code));

  const out = rows.filter((r) => r.state !== "available").length;
  const trouble = rows.filter((r) => r.tone === "bad" || r.tone === "warn").length;

  return (
    <aside className="fleet" aria-label="Fleet" data-hl="fleet">
      <header className="fleet-head">
        <b>Fleet</b>
        <span>
          {out} of {rows.length} out
          {trouble > 0 && <em className="fleet-trouble"> · {trouble} need a look</em>}
        </span>
      </header>

      <div className="fleet-list">
        {rows.length === 0 && (
          <p className="fleet-empty">No trucks registered for this city yet.</p>
        )}
        {rows.map((r) => (
          <button
            key={r.id}
            className={`truck t-${r.tone}${r.selected ? " on" : ""}`}
            data-hl={`vehicle:${r.code}`}
            onClick={() => onSelect?.(r.nextShipmentId ?? r.anyShipmentId ?? null)}
            title={`${r.code} — ${r.label}`}
          >
            <span className="truck-top">
              <b className="truck-code">{r.code}</b>
              {r.feature && <span className="truck-feat">{r.feature}</span>}
              <span className="truck-state">{r.label}</span>
            </span>

            {r.total > 0 ? (
              <>
                <span className="truck-bar" aria-hidden="true">
                  <i style={{ width: `${r.pct}%` }} />
                </span>
                <span className="truck-mid">
                  <span className="truck-drops">{r.done}/{r.total} drops</span>
                  <span className="truck-load">{r.loadKg} / {r.capKg} kg</span>
                </span>
                <span className="truck-next">
                  {r.next
                    ? <>next <b>{r.nextName}</b> · {hhmm(r.next.eta_min)}
                        {r.next.late_min > 0 && <em className="truck-late"> {r.next.late_min}m late</em>}</>
                    : "all drops done — returning to depot"}
                </span>
              </>
            ) : (
              <span className="truck-next">{r.idleReason}</span>
            )}
          </button>
        ))}
      </div>
    </aside>
  );
}

function row(v, simV, routes, shipments, nowMin) {
  const dark = (simV?.gps_stale_min ?? 0) > 0;
  const state = dark ? "dark" : (simV?.status || v.status || "available");
  const meta = STATE[state] || STATE.available;

  const route = (routes || []).find((r) => r.vehicle_id === v.id);
  const stops = (route?.stops || []).filter((s) => s.shipment_id);
  const done = stops.filter((s) => s.status === "completed").length;
  const next = stops.find((s) => s.status !== "completed");
  const name = (id) => (shipments || []).find((s) => s.id === id)?.customer_name;

  // A stop the clock has already passed but the truck has not reached is the
  // thing a dispatcher must see first, so it outranks a merely idle truck.
  const slipping = next && nowMin != null && next.eta_min < nowMin;

  return {
    id: v.id,
    code: v.code,
    state,
    label: meta.label,
    tone: slipping && meta.tone === "go" ? "warn" : meta.tone,
    rank: slipping ? Math.min(meta.rank, 1.5) : meta.rank,
    feature: (v.features || "").split(",").filter(Boolean)[0]?.replace("_", " "),
    total: stops.length,
    done,
    pct: stops.length ? Math.round((done / stops.length) * 100) : 0,
    loadKg: Math.round(route?.total_load_kg ?? 0),
    capKg: Math.round(v.capacity_kg ?? 0),
    next,
    // "On the road with no work assigned" is a contradiction the dispatcher
    // should never have to resolve. A truck can be moving because it is
    // finishing a superseded run or heading home; say which.
    idleReason:
      state === "down" ? "freight re-planned onto other trucks"
      : state === "en_route" ? "running back to its depot"
      : state === "loading" ? "at the bay, nothing loaded yet"
      : "spare capacity — no work assigned",
    nextName: name(next?.shipment_id) || "—",
    nextShipmentId: next?.shipment_id,
    anyShipmentId: stops[0]?.shipment_id,
    selected: false,
  };
}
