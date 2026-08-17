import React, { useCallback, useEffect, useState } from "react";
import { DeskChrome } from "./Roles.jsx";
import { hhmm } from "./Timeline.jsx";

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) throw new Error((await res.text()).slice(0, 200));
  return res.json();
}

/**
 * The driver's screen. One job: what is my next stop, and mark it done.
 * Everything the dispatcher cares about is deliberately absent.
 */
export default function DriverView({ vehicles, onChanged, view, onChange, live, clock }) {
  const [vehicleId, setVehicleId] = useState(null);
  const [route, setRoute] = useState(null);
  const [shipments, setShipments] = useState([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(null);
  const [loading, setLoading] = useState(true);

  const withRoutes = vehicles || [];

  const load = useCallback(async (id) => {
    if (!id) return;
    setError("");
    setLoading(true);
    try {
      const [r, s] = await Promise.all([
        api(`/api/tracking/manifest/${id}`),
        api("/api/shipments"),
      ]);
      setRoute(r);
      setShipments(s);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }, []);

  // Default to a vehicle that actually has work, not just the first one.
  useEffect(() => {
    if (vehicleId || !withRoutes.length) return;
    (async () => {
      for (const v of withRoutes) {
        try {
          const r = await api(`/api/tracking/manifest/${v.id}`);
          if (r && (r.stops || []).some((s) => s.kind === "delivery")) {
            setVehicleId(v.id);
            return;
          }
        } catch {
          /* try the next vehicle */
        }
      }
      setVehicleId(withRoutes[0].id);
    })();
  }, [withRoutes, vehicleId]);

  useEffect(() => {
    load(vehicleId);
  }, [vehicleId, load]);

  const vehicle = withRoutes.find((v) => v.id === vehicleId);
  const shipmentById = new Map(shipments.map((s) => [s.id, s]));
  const stops = [...(route?.stops || [])]
    .filter((s) => s.kind === "delivery")
    .sort((a, b) => a.seq - b.seq);
  const done = stops.filter((s) => s.status === "completed").length;
  const nextStop = stops.find((s) => s.status !== "completed");

  const sign = async (stop) => {
    setSaving(stop.id);
    setError("");
    try {
      const updated = await api("/api/tracking/pod", {
        method: "POST",
        body: JSON.stringify({ stop_id: stop.id }),
      });
      setRoute(updated);
      await load(vehicleId);
      onChanged?.();
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setSaving(null);
    }
  };

  return (
    <DeskChrome
      view={view || "driver"}
      onChange={onChange || (() => {})}
      clock={clock}
      live={live}
      title="driver · manifest"
    >
      <div className="driver">
      <div className="driver-top">
        <span className="driver-veh">{vehicle?.code || "—"}</span>
        <span className={`chip chip-${vehicle?.status === "down" ? "failed" : "completed"}`}>
          {vehicle?.status || "unknown"}
        </span>
        <select
          value={vehicleId || ""}
          onChange={(e) => setVehicleId(Number(e.target.value))}
          className="driver-select"
          aria-label="Choose vehicle"
        >
          {withRoutes.map((v) => (
            <option key={v.id} value={v.id}>{v.code}</option>
          ))}
        </select>
      </div>

      <div className="driver-progress">
        <div className="driver-progress-head">
          <span>{done} of {stops.length} delivered</span>
          <span style={{ fontFamily: "var(--font-data)", color: "var(--text-2)" }}>
            {route ? `${Number(route.total_distance_km).toFixed(1)} km · ${route.total_load_kg} kg` : ""}
          </span>
        </div>
        <div className="driver-progress-bar">
          <div
            className="driver-progress-fill"
            style={{ width: `${stops.length ? (done / stops.length) * 100 : 0}%` }}
          />
        </div>
      </div>

      {error && <div className="alert" style={{ margin: 13 }}>{error}</div>}

      {/* A driver reads one thing at a time and it is always the next drop.
          The manifest below is the reference; this is the instruction. */}
      {!loading && nextStop && (
        <section className="driver-now">
          <span className="driver-now-kicker">Next drop</span>
          <b className="driver-now-cust">
            {shipmentById.get(nextStop.shipment_id)?.customer_name || `Stop #${nextStop.seq}`}
          </b>
          <span className="driver-now-code">
            {shipmentById.get(nextStop.shipment_id)?.code} · stop {nextStop.seq} of {stops.length}
          </span>
          <div className="driver-now-grid">
            <span><b>{hhmm(nextStop.eta_min)}</b>arrive</span>
            <span>
              <b>{hhmm(shipmentById.get(nextStop.shipment_id)?.tw_end_min)}</b>
              promised by
            </span>
            <span>
              <b>{shipmentById.get(nextStop.shipment_id)?.demand_kg ?? "—"} kg</b>
              to hand over
            </span>
            <span><b>{stops.length - done}</b>drops left</span>
          </div>
          {nextStop.late_min > 0 ? (
            <p className="driver-now-flag late">
              Running {nextStop.late_min} min past the promised window. Call the
              customer before you arrive.
            </p>
          ) : (
            <p className="driver-now-flag">
              You are inside the promised window. Nothing to call in.
            </p>
          )}
          <button
            className="pod driver-now-pod"
            disabled={saving === nextStop.id}
            onClick={() => sign(nextStop)}
          >
            {saving === nextStop.id ? "Saving…" : "Mark this one delivered"}
          </button>
        </section>
      )}

      {loading ? (
        <div className="empty" data-state="loading">Loading your manifest…</div>
      ) : stops.length === 0 ? (
        <div className="empty" data-state="empty">
          No stops assigned yet.<br />
          Your dispatcher needs to approve a plan.
        </div>
      ) : (
        <ol className="driver-stops">
          {stops.map((s) => {
            const ship = shipmentById.get(s.shipment_id);
            const isNext = nextStop?.id === s.id;
            const isDone = s.status === "completed";
            return (
              <li
                key={s.id}
                className={`driver-stop${isNext ? " next" : ""}${isDone ? " done" : ""}`}
              >
                <span className="driver-seq">{s.seq}</span>
                <div className="driver-info">
                  <div className="driver-cust">{ship?.customer_name || `Stop #${s.shipment_id}`}</div>
                  <div className="driver-meta">
                    {ship?.code} · {hhmm(s.eta_min)}
                    {ship && ` · by ${hhmm(ship.tw_end_min)}`}
                    {ship && ` · ${ship.demand_kg} kg`}
                  </div>
                  {s.late_min > 0 && (
                    <div className="driver-warn">Running {s.late_min} min past the window</div>
                  )}
                </div>
                {isDone ? (
                  <span className="pod-done">Delivered ✓</span>
                ) : isNext ? (
                  /* ponytail: the card above already carries this stop's button —
                     one action, one place. */
                  <span className="pod-here">Sign it on the card above ↑</span>
                ) : (
                  <button
                    className="pod"
                    disabled
                    title="Complete the stop above first"
                  >
                    Mark delivered
                  </button>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </div>
    </DeskChrome>
  );
}
