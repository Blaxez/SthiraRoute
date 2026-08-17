import React from "react";

// Grouped by what a judge asks to see, not by how the backend implements it.
const INJECT_GROUPS = [
  {
    title: "Road & weather",
    kinds: ["congestion", "storm", "closure", "warehouse_delay"],
  },
  {
    title: "Fleet & demand",
    kinds: ["breakdown", "gps_loss", "new_order", "cancel"],
  },
];

const INJECT_LABEL = {
  congestion: "Jam a corridor",
  storm: "Break the monsoon",
  closure: "Flood & close a road",
  warehouse_delay: "Back up a bay",
  breakdown: "Break a loaded truck",
  gps_loss: "Lose a driver's GPS",
  new_order: "Ad-hoc order",
  cancel: "Customer cancels",
};

const INJECT_TONE = {
  breakdown: "danger",
  closure: "danger",
  storm: "warn",
  congestion: "warn",
  gps_loss: "warn",
};

/**
 * A scripted day, for when the demo should drive itself.
 *
 * Lives behind a fold: choosing a scenario happens once, before anything else,
 * and three cards with a sentence each is not what the rail should be showing
 * for the rest of the shift.
 */
export function Playbooks({ sim, busy, onPlaybook }) {
  const scenario = sim?.scenario;
  return (
    <>
      {(sim?.playbooks || []).map((p) => (
        <button
          key={p.id}
          className={`sp-book${scenario?.id === p.id ? " on" : ""}`}
          onClick={() => onPlaybook(p.id)}
          disabled={!!busy}
          title={p.blurb}
        >
          <span className="sp-book-head">
            <span className="sp-book-label">{p.label}</span>
            <span className="sp-book-count">{p.beats}</span>
          </span>
          <span className="sp-book-blurb">{p.blurb}</span>
        </button>
      ))}
      {scenario && (
        <div className="sp-next">
          {scenario.next ? (
            <>
              Next beat <b>{scenario.next.at}</b> — {scenario.next.note}
              <span className="sp-next-count">
                {scenario.step}/{scenario.total} fired
              </span>
            </>
          ) : (
            <>All {scenario.total} scripted beats have fired.</>
          )}
        </div>
      )}
    </>
  );
}

/**
 * One disruption, fired by hand, for when a judge asks "what if X happened
 * right now". Also folded away: these are eight things that might happen, not
 * eight things to do, and at rest they drowned the three that are.
 */
export function Disruptions({ sim, busy, onInject }) {
  const hints = sim?.injections || {};
  return INJECT_GROUPS.map((g) => (
    <div className="sp-group" key={g.title}>
      <div className="sp-group-title">{g.title}</div>
      <div className="sp-inject-grid">
        {g.kinds.map((k) => (
          <button
            key={k}
            className={`sp-inject${INJECT_TONE[k] ? ` i-${INJECT_TONE[k]}` : ""}`}
            onClick={() => onInject(k)}
            disabled={!!busy}
            title={hints[k] || INJECT_LABEL[k]}
          >
            {busy === `inject:${k}` ? "…" : INJECT_LABEL[k]}
          </button>
        ))}
      </div>
    </div>
  ));
}

/** End-of-day figures, for the Inspector's shift tab. */
export function Scorecard({ sim }) {
  const s = sim?.scorecard;
  if (!s) return null;

  const rows = [
    ["Delivered", s.delivered, null],
    ["On time", s.on_time_pct == null ? "—" : `${s.on_time_pct}%`,
      s.on_time_pct == null ? "" : s.on_time_pct >= 95 ? "g-good" : s.on_time_pct >= 85 ? "g-warn" : "g-bad"],
    ["Late stops", s.late, s.late ? "g-warn" : "g-good"],
    ["Minutes late", s.late_minutes, s.late_minutes ? "g-warn" : "g-good"],
    ["Still open", s.undelivered, s.undelivered ? "g-warn" : "g-good"],
    ["Distance driven", `${s.km_driven} km`, null],
  ];

  // Only what actually happened. Five rows of zeroes is a table you learn to
  // skip, and then you skip it on the day one of them is not zero.
  const disruptions = [
    ["Ad-hoc orders", s.adhoc_orders],
    ["Cancellations", s.cancellations],
    ["Breakdowns", s.breakdowns],
    ["GPS blackouts", s.gps_dropouts],
    ["Road closures", s.closures],
  ].filter(([, v]) => v > 0);

  return (
    <>
      <dl className="readout">
        {rows.map(([label, value, tone]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd className={tone || ""}>{value}</dd>
          </div>
        ))}
      </dl>

      {disruptions.length > 0 && (
        <div className="panel">
          <div className="panel-title">What the day threw at the plan</div>
          <table className="ledger">
            <tbody>
              {disruptions.map(([label, value]) => (
                <tr key={label}>
                  <td>{label}</td>
                  <td>{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="panel panel-gate commit">
        <div className="panel-title">How the system responded</div>
        <table className="ledger">
          <tbody>
            <tr><td>Re-plans dispatched</td><td>{s.reopts_committed}</td></tr>
            <tr><td>Held by the churn gate</td><td>{s.reopts_held}</td></tr>
            <tr><td>Watched, not re-planned</td><td>{s.monitor_only}</td></tr>
          </tbody>
        </table>
        <p className="prose" style={{ margin: "7px 0 0", fontSize: 11 }}>
          Restraint is the point: mild ETA drift refreshes projections and leaves
          the plan alone, because reshuffling a shift drivers are already running
          costs more than the minutes it saves.
        </p>
      </div>
    </>
  );
}

// Feed colouring by event class. Each hue already has a fixed operational
// meaning in the stylesheet; these reuse it rather than inventing more.
const EVENT_TONE = {
  breakdown: "bad", closure: "bad", late: "bad",
  traffic: "warn", weather: "warn", sla_risk: "warn", gps_lost: "warn",
  warehouse_delay: "warn", replan_held: "warn", script_skipped: "warn",
  delivered: "good", returned: "good", sla_clear: "good", closure_cleared: "good",
  gps_back: "good", replan: "silver", script: "silver",
};

/** The narrative record — the demo's transcript, newest first. */
export function ShiftFeed({ sim }) {
  const events = sim?.events || [];
  if (!events.length) {
    return (
      <div className="empty">
        Nothing has happened yet.
        <br />
        Prepare the shift and press run.
      </div>
    );
  }
  return (
    <div className="sf">
      {events.map((e) => (
        <div className={`sf-row t-${EVENT_TONE[e.kind] || "plain"}`} key={e.id}>
          <span className="sf-at">{e.at}</span>
          <span className="sf-kind">{e.kind.replace(/_/g, " ")}</span>
          <span className="sf-msg">{e.message}</span>
        </div>
      ))}
    </div>
  );
}
