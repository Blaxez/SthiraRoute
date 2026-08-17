import React, { useEffect, useState } from "react";
import { DeskChrome, fetchBoard } from "./Roles.jsx";

const STEPS = ["pending", "assigned", "in_transit", "delivered"];

function stepIndex(status) {
  if (status === "cancelled") return -1;
  const i = STEPS.indexOf(status);
  return i < 0 ? 0 : i;
}

/**
 * The consignee's copy of one shipment. Same code, same truck, same ETA as
 * the dispatcher and the driver — that is the coordination claim.
 */
export default function TrackView({ view, onChange, sim, live }) {
  const [board, setBoard] = useState(null);
  const [code, setCode] = useState("");
  const [picked, setPicked] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let on = true;
    const load = () =>
      fetchBoard()
        .then((b) => {
          if (!on) return;
          setBoard(b);
          setError("");
          setCode((c) => {
            if (c) return c;
            const first = (b.consignments || []).find((x) => x.vehicle_code)
              || (b.consignments || [])[0];
            return first?.code || "";
          });
        })
        .catch((e) => on && setError(String(e.message || e)));
    load();
    const t = setInterval(load, 2500);
    return () => {
      on = false;
      clearInterval(t);
    };
  }, []);

  const list = board?.consignments || [];
  const row = picked || list.find((c) => c.code === code) || list[0];
  const idx = stepIndex(row?.status);

  const pull = async (c) => {
    setCode(c);
    setPicked(null);
    if (!c) return;
    try {
      const res = await fetch(`/api/network/track/${encodeURIComponent(c)}`);
      if (!res.ok) throw new Error(res.status === 404 ? "No consignment with that code." : `track → ${res.status}`);
      setPicked(await res.json());
      setError("");
    } catch (e) {
      setError(String(e.message || e));
    }
  };

  return (
    <DeskChrome
      view={view}
      onChange={onChange}
      clock={sim?.clock || board?.clock}
      live={live}
      title="track · consignment"
    >
      <div className="desk-body track">
        <aside className="track-list">
          <div className="desk-kicker">
            Consignments
            <span>{list.length} on the network</span>
          </div>
          <label className="track-find">
            <span>Track a code</span>
            <input
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === "Enter" && pull(code)}
              placeholder="SHP-01"
              aria-label="Consignment code"
            />
            <button type="button" className="tool" onClick={() => pull(code)}>Find</button>
          </label>
          {error ? <div className="alert">{error}</div> : null}
          {board == null ? (
            <div className="empty" data-state="loading">Loading consignments…</div>
          ) : (
            list.map((c) => (
            <button
              key={c.code}
              type="button"
              className={`track-row${row?.code === c.code ? " on" : ""}`}
              onClick={() => {
                setCode(c.code);
                setPicked(null);
                setError("");
              }}
            >
              <span className="track-code">{c.code}</span>
              <span className="track-cust">{c.customer}</span>
              <span className={`track-st st-${c.status}`}>{c.status.replace("_", " ")}</span>
            </button>
            ))
          )}
        </aside>

        <section className="track-detail">
          {!row ? (
            <div className="empty" data-state="empty">
              No consignments yet. Seed the demo from Dispatch.
            </div>
          ) : (
            <>
              <header className="track-head">
                <div>
                  <div className="track-code lg">{row.code}</div>
                  <div className="track-cust">{row.customer}</div>
                </div>
                <span className={`chip chip-${row.status === "delivered" ? "completed" : "queued"}`}>
                  {row.status.replace("_", " ")}
                </span>
              </header>

              <ol className="track-steps" aria-label="Shipment lifecycle">
                {STEPS.map((s, i) => (
                  <li
                    key={s}
                    className={
                      idx < 0 ? "" : i < idx ? "done" : i === idx ? "now" : ""
                    }
                  >
                    <b>{s.replace("_", " ")}</b>
                  </li>
                ))}
              </ol>

              <dl className="track-facts">
                <div>
                  <dt>Promised window</dt>
                  <dd>{row.window}</dd>
                </div>
                <div>
                  <dt>ETA</dt>
                  <dd className={row.late_min > 0 ? "late" : ""}>
                    {row.eta || "—"}
                    {row.late_min > 0 ? ` · ${row.late_min} min late` : ""}
                  </dd>
                </div>
                <div>
                  <dt>Assigned truck</dt>
                  <dd>{row.vehicle_code || "unassigned"}</dd>
                </div>
                <div>
                  <dt>Stops ahead</dt>
                  <dd>{row.stops_ahead ?? "—"}</dd>
                </div>
                <div>
                  <dt>Weight</dt>
                  <dd>{row.kg} kg</dd>
                </div>
                <div>
                  <dt>Same plan as dispatch</dt>
                  <dd>{row.vehicle_code ? "yes — one committed route" : "waiting on a plan"}</dd>
                </div>
              </dl>

              <Journey row={row} events={sim?.events} />
            </>
          )}
        </section>
      </div>
    </DeskChrome>
  );
}

/**
 * What actually happened to this parcel today.
 *
 * The four-step bar says which of four states it is in; it cannot say that the
 * truck carrying it sat out a curfew or lost GPS for twelve minutes. That is
 * the difference between a status page and knowing where your goods are, and
 * the events are already on the wire.
 */
function Journey({ row, events }) {
  const mine = (events || []).filter(
    (e) =>
      (row.code && e.message?.includes(row.code)) ||
      (row.vehicle_code && e.message?.includes(row.vehicle_code))
  );
  return (
    <section className="track-journey">
      <h4>What happened to this consignment</h4>
      {mine.length === 0 ? (
        <p className="track-journey-empty">
          Nothing has happened to {row.code} yet today. It is on the plan and
          waiting for its truck.
        </p>
      ) : (
        mine.slice(0, 10).map((e) => (
          <p key={e.id ?? `${e.at}-${e.message}`} className={`track-beat k-${e.kind}`}>
            <span className="track-beat-at">{e.at}</span>
            <span>{e.message}</span>
          </p>
        ))
      )}
    </section>
  );
}
