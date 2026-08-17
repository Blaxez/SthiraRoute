import React, { useEffect, useState } from "react";
import { Scorecard, ShiftFeed } from "./ShiftPanel.jsx";
import { hhmm } from "./Timeline.jsx";
import { ROUTE_COLORS } from "./palette.js";

const rs = (n) => (n == null ? "—" : `₹${Math.round(n).toLocaleString("en-IN")}`);
const fmt = (n) =>
  n == null ? "—" : Number(n).toLocaleString("en-IN", { maximumFractionDigits: 1 });
const tone = (v, good, ok) => (v == null ? "" : v >= good ? "g-good" : v >= ok ? "g-warn" : "g-bad");
const color = (i) => ROUTE_COLORS[i % ROUTE_COLORS.length];

function safeParse(s) {
  try {
    return JSON.parse(s || "{}");
  } catch {
    return {};
  }
}

/**
 * The panel chrome: fold handle, resize grip, and whatever the panel is
 * currently showing. Both states below share it, so a folded inspector still
 * has something to click on to get itself back.
 */
function Shell({ open, folded, onToggle, gripProps, embedded, children }) {
  const inner = <div className="insp-inner">{children}</div>;
  if (embedded) return <div className="inspector embedded">{inner}</div>;
  return (
    <aside className={`inspector${open ? " open" : ""}${folded ? " folded" : ""}`}>
      {folded ? (
        <button className="side-tab" onClick={onToggle} aria-expanded="false"
                title="Show plan details">‹</button>
      ) : (
        gripProps && <div {...gripProps} />
      )}
      {inner}
    </aside>
  );
}

/**
 * Why the plan looks the way it does.
 *
 * Split into tabs rather than one long column: stacked, this content pushed the
 * route cards — and the only way into the load plan — six hundred pixels below
 * the fold at 1366x768, where nobody found them.
 */
export default function Inspector({
  run, error, benchmark, shipments, vehicles, selected, onSelect, onOpenLoad, feed, sim,
  kpis, open, onClose, folded, onToggle, gripProps, embedded,
}) {
  const [tab, setTab] = useState("shift");
  const chrome = { open, folded, onToggle, gripProps, embedded };

  // A plan the dispatcher asked for should be on screen when it lands. While
  // the shift is running it must not: autopilot re-plans every few minutes and
  // yanking the panel off the shift log each time is worse than useless.
  const running = sim?.running;
  useEffect(() => {
    if (run?.id && !running) setTab("summary");
  }, [run?.id, running]);

  // Before there is a plan the shift log is still worth reading — it is where
  // "prepare the shift" reports what it did, including a failure to plan.
  if (!run) {
    return (
      <Shell {...chrome}>
        <div className="insp-head">
          <span className="run-id">Shift</span>
          <span className="chip chip-plain">{sim?.clock || "--:--"}</span>
          {onClose && (
            <button className="insp-close" onClick={onClose}
                  aria-label={embedded ? "Close lab" : "Fold this panel away"}
                  title={embedded ? "Close lab" : "Fold this panel away"}>✕</button>
          )}
        </div>
        <div className="insp-body">
          {error && <div className="alert">{error}</div>}
          {sim?.scorecard?.delivered ? <Scorecard sim={sim} /> : null}
          <ShiftFeed sim={sim} />
        </div>
      </Shell>
    );
  }

  const metrics = safeParse(run.metrics_json);
  const explain = safeParse(run.explain_json);
  const gate = explain.gain_gate;
  const unserved = explain.unserved || [];
  const routes = run.routes || [];
  const shipmentById = new Map((shipments || []).map((s) => [s.id, s]));
  const vehicleById = new Map((vehicles || []).map((v) => [v.id, v]));

  const issueCount =
    unserved.length + (explain.load_warning ? 1 : 0) + (metrics.total_late_min > 0 ? 1 : 0);

  const TABS = [
    { id: "shift", label: "Shift", badge: sim?.scorecard?.delivered || 0, quiet: true },
    { id: "summary", label: "Plan" },
    { id: "routes", label: "Routes", badge: routes.length, quiet: true },
    { id: "issues", label: "Issues", badge: issueCount },
  ];

  return (
    <Shell {...chrome}>
      <div className="insp-head">
        <span className="run-id">#{run.id}</span>
        <span className={`chip chip-${run.status}`}>{run.status}</span>
        <span className="chip chip-plain">{run.trigger}</span>
        {onClose && (
          <button className="insp-close" onClick={onClose}
                  aria-label={embedded ? "Close lab" : "Fold this panel away"}
                  title={embedded ? "Close lab" : "Fold this panel away"}>✕</button>
        )}
      </div>

      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className={`tab${tab === t.id ? " on" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
            {t.badge > 0 && (
              <span className={`tab-badge${t.quiet ? " quiet" : ""}`}>{t.badge}</span>
            )}
          </button>
        ))}
      </div>

      <div className="insp-body">
        {error && <div className="alert">{error}</div>}
        {run.error && <div className="alert">{run.error}</div>}

        {tab === "summary" && (
          <>
            <dl className="readout">
              <div>
                <dt>Distance</dt>
                <dd>{fmt(metrics.total_distance_km)} <small>km</small></dd>
              </div>
              <div>
                <dt>Operating cost</dt>
                <dd>{rs(metrics.ops_cost_inr)}</dd>
              </div>
              <div>
                <dt>Capacity used</dt>
                <dd className={tone(metrics.capacity_utilization_pct, 75, 60)}>
                  {fmt(metrics.capacity_utilization_pct)}<small>%</small>
                </dd>
              </div>
              <div>
                <dt>On time</dt>
                <dd className={tone(metrics.on_time_pct, 95, 85)}>
                  {fmt(metrics.on_time_pct)}<small>%</small>
                </dd>
              </div>
              <div>
                <dt>Vehicles</dt>
                <dd>
                  {metrics.vehicles_used ?? "—"}
                  <small>/{metrics.vehicles_available ?? "—"}</small>
                </dd>
              </div>
              <div>
                <dt>Not served</dt>
                <dd className={metrics.unserved_count ? "g-bad" : "g-good"}>
                  {metrics.unserved_count ?? 0}
                </dd>
              </div>
              {/* Measured over the routes actually in force, not this proposal:
                  the empty leg is a fact about the running fleet. */}
              {kpis?.plan?.committed_routes > 0 && (
                <div>
                  <dt>Running empty</dt>
                  <dd className={tone(20 - kpis.plan.empty_km_pct, 5, 0)}>
                    {fmt(kpis.plan.empty_km_pct)}<small>%</small>
                  </dd>
                </div>
              )}
              {metrics.avg_volume_utilization_pct != null && (
                <div>
                  <dt>Deck fill</dt>
                  <dd className={metrics.load_infeasible_routes ? "g-bad" : ""}>
                    {fmt(metrics.avg_volume_utilization_pct)}<small>%</small>
                  </dd>
                </div>
              )}
              {metrics.load_infeasible_routes != null && (
                <div>
                  <dt>Loadable routes</dt>
                  <dd className={metrics.load_infeasible_routes ? "g-bad" : "g-good"}>
                    {metrics.load_feasible_routes}
                    <small>
                      /{(metrics.load_feasible_routes ?? 0) + metrics.load_infeasible_routes}
                    </small>
                  </dd>
                </div>
              )}
            </dl>

            {explain.summary && <p className="prose">{explain.summary}</p>}

            {benchmark?.improvement?.distance_pct != null && (
              <div className="panel">
                <div className="panel-title">Measured against a greedy plan</div>
                <table className="ledger">
                  <tbody>
                    <tr>
                      <td>Greedy nearest-neighbour</td>
                      <td>{fmt(benchmark.baseline.total_distance_km)} km</td>
                    </tr>
                    <tr>
                      <td>This plan</td>
                      <td>{fmt(benchmark.optimized.total_distance_km)} km</td>
                    </tr>
                    <tr className="total">
                      <td>Distance saved</td>
                      <td className={benchmark.improvement.distance_pct > 0 ? "g-good" : "g-bad"}>
                        {benchmark.improvement.distance_pct > 0 ? "−" : "+"}
                        {Math.abs(benchmark.improvement.distance_pct)}%
                      </td>
                    </tr>
                  </tbody>
                </table>
                <p className="prose" style={{ fontSize: 10.5, margin: "6px 0 0" }}>
                  The baseline ignores delivery windows and curfews, so it is an
                  optimistic figure for a plan that would not be legal to drive.
                </p>
              </div>
            )}

            {gate && (
              <div className={`panel panel-gate panel-churn ${gate.decision === "commit" ? "commit" : ""}`}>
                <div className="panel-title">
                  Churn gate · {gate.decision === "commit" ? "worth committing" : "recommend holding"}
                </div>
                <p className="prose" style={{ margin: "0 0 6px" }}>{gate.reason}</p>
                {gate.churn_cost_inr != null && (
                  <table className="ledger">
                    <tbody>
                      <tr><td>Plan in force</td><td>{rs(gate.incumbent_ops_inr)}</td></tr>
                      <tr><td>Proposed</td><td>{rs(gate.proposed_ops_inr)}</td></tr>
                      <tr><td>Cost of reshuffling</td><td>{rs(gate.churn_cost_inr)}</td></tr>
                      <tr className="total">
                        <td>Net</td>
                        <td className={gate.net_inr < 0 ? "g-good" : "g-warn"}>
                          {gate.net_inr > 0 ? "+" : ""}{rs(gate.net_inr)}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                )}
              </div>
            )}

            {explain.load_retry && (
              <div className="panel panel-gate panel-loadfeedback commit">
                <div className="panel-title">Load feedback</div>
                <p className="prose" style={{ margin: 0, fontSize: 11.5 }}>{explain.load_retry}</p>
              </div>
            )}
          </>
        )}

        {tab === "routes" &&
          (routes.length === 0 ? (
            <div className="empty">This run produced no routes.</div>
          ) : (
            routes.map((r, i) => {
              const v = vehicleById.get(r.vehicle_id);
              const stops = [...(r.stops || [])].sort((a, b) => a.seq - b.seq);
              return (
                <div className="route-item" key={r.id} style={{ borderLeftColor: color(i) }}>
                  <button className="route-bar" onClick={() => onSelect?.(null)}>
                    <span className="route-code">{v?.code || `V${r.vehicle_id}`}</span>
                    <span className="route-facts">
                      {fmt(r.total_distance_km)} km · {r.total_load_kg} kg · {fmt(r.load_pct)}%
                    </span>
                  </button>
                  <div className="fill">
                    <div
                      className={`fill-bar${r.load_pct > 92 ? " hot" : ""}`}
                      style={{ width: `${Math.min(100, r.load_pct || 0)}%` }}
                    />
                  </div>
                  <button
                    className="route-load"
                    onClick={() => onOpenLoad?.(r, v?.code || `V${r.vehicle_id}`)}
                  >
                    <span className={r.load_feasible ? "good" : "bad"}>
                      {r.load_feasible ? "◧" : "⚠"}
                    </span>
                    <span>
                      Load plan · <b>{fmt(r.total_load_m3)} m³</b>
                      {r.load_feasible ? " · LIFO verified" : " · cannot be loaded"}
                    </span>
                    <span style={{ marginLeft: "auto" }}>view 3D →</span>
                  </button>
                  <ol className="stop-list">
                    {stops.map((s) => {
                      const ship = shipmentById.get(s.shipment_id);
                      return (
                        <li
                          key={s.id}
                          className="stop-row"
                          style={{
                            background:
                              selected && selected === s.shipment_id ? "var(--raised-2)" : undefined,
                          }}
                        >
                          <span className="stop-seq">{s.seq}</span>
                          <span className="stop-eta">{hhmm(s.eta_min)}</span>
                          <span className="stop-name">
                            {s.kind === "depot" ? "Depot" : ship?.customer_name || `#${s.shipment_id}`}
                          </span>
                          {s.late_min > 0 && <span className="stop-late">+{s.late_min}m</span>}
                          {s.status === "completed" && <span className="stop-done">✓</span>}
                        </li>
                      );
                    })}
                  </ol>
                </div>
              );
            })
          ))}

        {tab === "issues" && (
          <>
            {issueCount === 0 && !explain.no_entry_applied?.length && (
              <div className="empty">
                Nothing to flag.
                <br />
                Every shipment is served, on time and loadable.
              </div>
            )}

            {unserved.length > 0 && (
              <div className="panel panel-block">
                <div className="panel-title">Could not be served — {unserved.length}</div>
                {unserved.map((u) => (
                  <div key={u.code} className="unserved-row">
                    <span className="unserved-code">{u.code}</span> — {u.reason}
                  </div>
                ))}
                {explain.relaxation_options && (
                  <>
                    <div className="panel-title" style={{ marginTop: 9 }}>Ways to fix it</div>
                    <ul className="fix-list">
                      {explain.relaxation_options.map((o) => <li key={o}>{o}</li>)}
                    </ul>
                  </>
                )}
              </div>
            )}

            {explain.load_warning && (
              <div className="panel panel-block">
                <div className="panel-title">Load check failed</div>
                <p className="prose" style={{ margin: 0, fontSize: 11.5 }}>{explain.load_warning}</p>
              </div>
            )}

            {metrics.total_late_min > 0 && (
              <div className="panel panel-gate">
                <div className="panel-title">Running late</div>
                <p className="prose" style={{ margin: 0, fontSize: 11.5 }}>
                  {metrics.total_late_min} minutes past the promised windows in total.
                  Lateness is priced, not forbidden — the plan still delivers.
                </p>
              </div>
            )}

            {explain.no_entry_applied?.length > 0 && (
              <div className="panel">
                <div className="panel-title">
                  Municipal curfews enforced — {explain.no_entry_applied.length}
                </div>
                <p className="prose" style={{ margin: 0, fontSize: 11.5 }}>
                  {[...new Set(explain.no_entry_applied.map((t) => t.split(" -> ")[1]))].join(", ")}
                  {" "}rescheduled outside their restricted window.
                </p>
              </div>
            )}

            {explain.incompatible?.length > 0 && (
              <div className="panel panel-block">
                <div className="panel-title">No compatible vehicle</div>
                <p className="prose" style={{ margin: 0, fontSize: 11.5 }}>
                  {explain.incompatible.join(", ")} need equipment no available truck has.
                </p>
              </div>
            )}
          </>
        )}

        {tab === "shift" && (
          <>
            <Scorecard sim={sim} />
            <div className="panel-title" style={{ marginTop: 4 }}>Shift log</div>
            <ShiftFeed sim={sim} />
            {(feed || []).length > 0 && (
              <>
                <div className="panel-title" style={{ marginTop: 12 }}>
                  This session's actions
                </div>
                <div className="feed">
                  {feed.map((f) => (
                    <div className="feed-row" key={f.id}>
                      <span className="feed-time">{f.t}</span>
                      <span>{f.msg}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </div>
    </Shell>
  );
}
