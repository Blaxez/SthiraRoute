import React, { useEffect, useState } from "react";
import { DeckMap } from "./LoadPlan.jsx";
import { DeskChrome, fetchBoard } from "./Roles.jsx";
import { hhmm } from "./Timeline.jsx";

/**
 * The warehouse desk. Routing decided where the truck goes; this is whether
 * the load can physically go with it — utilization, LIFO, axle balance.
 */
export default function DockView({
  view, onChange, sim, live, onOpenLoadPlan,
}) {
  const [board, setBoard] = useState(null);
  const [sel, setSel] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let on = true;
    const load = () =>
      fetchBoard()
        .then((b) => {
          if (!on) return;
          setBoard(b);
          setError("");
        })
        .catch((e) => on && setError(String(e.message || e)));
    load();
    const t = setInterval(load, 2500);
    return () => {
      on = false;
      clearInterval(t);
    };
  }, []);

  const dock = board?.dock || [];
  const truck = dock.find((d) => d.vehicle_id === sel) || dock[0];
  const alloc = board?.ps2?.allocate;

  return (
    <DeskChrome
      view={view}
      onChange={onChange}
      clock={sim?.clock || board?.clock}
      live={live}
      title="dock · load allocation"
    >
      <div className="desk-body dock">
        <aside className="dock-list">
          <div className="desk-kicker">
            Loads on the floor
            {alloc ? (
              <span>
                {alloc.trucks_used} trucks · {alloc.avg_load_pct}% avg fill
                {alloc.all_loadable ? "" : " · a deck does not pack"}
              </span>
            ) : null}
          </div>
          {error ? <div className="alert">{error}</div> : null}
          {board == null ? (
            <div className="empty" data-state="loading">Reading the floor…</div>
          ) : dock.length === 0 ? (
            <div className="empty" data-state="empty">
              No loads on the floor.
              <br />
              Dispatch has to approve a plan first.
            </div>
          ) : (
            dock.map((d) => (
              <button
                key={d.vehicle_id}
                type="button"
                className={`dock-row${truck?.vehicle_id === d.vehicle_id ? " on" : ""}`}
                onClick={() => setSel(d.vehicle_id)}
              >
                <span className="dock-code">{d.code}</span>
                <span className="dock-depot">{d.depot}</span>
                <span className={`dock-fill${d.load_feasible ? "" : " bad"}`}>
                  {Math.round(d.load_pct)}%
                </span>
                <span className="dock-meta">
                  {d.delivered}/{d.drop_count} · {Math.round(d.kg)} kg
                </span>
              </button>
            ))
          )}
        </aside>

        <section className="dock-detail">
          {!truck ? (
            <div className="empty" data-state="empty">Select a truck to see its deck.</div>
          ) : (
            <>
              <header className="dock-head">
                <div>
                  <div className="dock-code lg">{truck.code}</div>
                  <div className="dock-depot">{truck.depot} · {truck.distance_km} km</div>
                </div>
                {/* Three physical checks, in the words a loader would use.
                    "CoG off" told nobody anything; "nose-heavy" is a thing you
                    can walk over to the truck and see. */}
                <div className="dock-chips">
                  <span
                    className={`chip chip-${truck.load_feasible ? "completed" : "failed"}`}
                    title="Does every carton physically fit on this deck?"
                  >
                    {truck.load_feasible ? "it all fits" : "does not fit"}
                  </span>
                  <span
                    className={`chip chip-${truck.lifo_ok ? "completed" : "failed"}`}
                    title="Last in, first out: each drop must come off the back with nothing on top of it or in front of it."
                  >
                    {truck.lifo_ok
                      ? "unloads in order"
                      : "blocked at the door"}
                  </span>
                  <span
                    className={`chip chip-${truck.cog_ok ? "completed" : "failed"}`}
                    title="Longitudinal centre of gravity — where the weight sits between the axles."
                  >
                    {truck.cog_ok ? "weight balanced" : "weight off the axle"}
                  </span>
                </div>
                <button
                  type="button"
                  className="tool dock-3d"
                  onClick={() => onOpenLoadPlan?.({ id: truck.route_id }, truck.code)}
                >
                  View 3D deck
                </button>
              </header>

              <div className="dock-util">
                <div>
                  <b>{Math.round(truck.load_pct)}%</b>
                  <span>weight fill</span>
                </div>
                <div>
                  <b>{Math.round(truck.volume_utilization_pct || 0)}%</b>
                  <span>volume fill</span>
                </div>
                <div>
                  <b>{truck.drop_count}</b>
                  <span>drops</span>
                </div>
              </div>

              <DeckMap plan={truck} />

              <ol className="dock-drops">
                {truck.drops.map((d) => (
                  <li
                    key={d.stop_id}
                    className={`dock-drop${d.status === "completed" ? " done" : ""}`}
                  >
                    <span className="driver-seq">{d.seq}</span>
                    <div className="driver-info">
                      <div className="driver-cust">{d.customer}</div>
                      <div className="driver-meta">
                        {d.code} · {d.eta || hhmm(d.eta_min)} · {d.kg} kg
                        {d.fragile ? " · fragile" : ""}
                      </div>
                    </div>
                    <span className="dock-drop-st">{d.status}</span>
                  </li>
                ))}
              </ol>
            </>
          )}
        </section>
      </div>
    </DeskChrome>
  );
}
