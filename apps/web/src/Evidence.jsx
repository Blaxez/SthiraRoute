import React from "react";

/**
 * The argument, on stage.
 *
 * Every number here was already being computed and thrown away behind a modal.
 * A visitor who never opens Lab should still be able to tell that this is a
 * decision system and not a GPS tracker, so each block says what the figure is
 * *for* in one line of plain English underneath it.
 */
export default function Evidence({ kpis, benchmark, sim, decision, awaitingApprove }) {
  const plan = kpis?.plan || {};
  const score = sim?.scorecard || {};
  const ship = kpis?.shipments || {};
  const runInfo = kpis?.last_run || {};
  const events = (sim?.events || []).slice(0, 14);

  const delivered = score.delivered ?? (ship.by_status?.delivered ?? 0);
  const total = ship.total ?? 0;

  return (
    <aside className="ev" aria-label="How the plan is doing" data-hl="kpis">
      {awaitingApprove && (
        <p className="ev-flag">
          A re-plan is waiting for you. It is dashed on the map until you approve it.
        </p>
      )}
      {decision?.action === "held" && !awaitingApprove && (
        <p className="ev-flag hold">
          {decision.reason || "A reshuffle was held — too much churn for too little gain."}
        </p>
      )}

      <Block
        title="The day so far"
        why="Promises kept, not distance covered. This is the number a customer feels."
      >
        <Fig v={`${delivered}/${total}`} k="delivered" />
        <Fig v={pct(plan.on_time_pct)} k="on time" tone={tone(plan.on_time_pct, 95, 80)} />
        <Fig v={score.late_minutes ?? plan.late_stops ?? 0} k="min late" tone={(score.late_minutes ?? 0) > 0 ? "warn" : "go"} />
        <Fig v={ship.unassigned ?? 0} k="unassigned" tone={(ship.unassigned ?? 0) > 0 ? "bad" : "go"} />
      </Block>

      <Block
        title="What this plan costs"
        why="Empty running is freight moved for nothing. Deck used is how much truck you paid for and actually filled."
      >
        <Fig v={inr(plan.ops_cost_inr)} k="operating cost" />
        <Fig v={km(plan.total_distance_km)} k="road distance" />
        <Fig v={pct(plan.empty_km_pct)} k="running empty" tone={tone(100 - (plan.empty_km_pct ?? 0), 80, 65)} />
        <Fig v={pct(plan.capacity_utilization_pct)} k="deck used" tone={tone(plan.capacity_utilization_pct, 70, 50)} />
      </Block>

      {benchmark?.optimized && (
        <section className="ev-block">
          <h4>Against the simple way</h4>
          {/* Distance alone makes the greedy plan look better, which is exactly
              the trap. Lead with stops served and legality; show distance last,
              as the price of both. */}
          <div className="ev-vs">
            <div className="ev-vs-col">
              <span className="ev-vs-who">Nearest-neighbour</span>
              <b className="ev-vs-lead t-bad">{benchmark.baseline?.served ?? 0} stops served</b>
              <span className="ev-vs-tag t-bad">not legal to drive</span>
              <span className="ev-vs-sub">{km(benchmark.baseline?.total_distance_km)}</span>
            </div>
            <div className="ev-vs-col">
              <span className="ev-vs-who">This plan</span>
              <b className="ev-vs-lead t-go">{benchmark.optimized?.served ?? 0} stops served</b>
              <span className="ev-vs-tag t-go">
                windows + curfews kept{benchmark.optimized?.on_time_pct != null
                  ? ` · ${Math.round(benchmark.optimized.on_time_pct)}% on time`
                  : ""}
              </span>
              <span className="ev-vs-sub">{km(benchmark.optimized?.total_distance_km)}</span>
            </div>
          </div>
          <p className="ev-why">
            The greedy route is shorter only because it ignores the delivery
            windows and the municipal no-entry hours. It drops most of the day
            and could not lawfully be driven, so its distance is a floor for an
            impossible plan — not a target.
          </p>
        </section>
      )}

      <Block
        title="What the day threw at it"
        why="A plan that never met a disruption has not been tested."
      >
        <Row k="Ad-hoc orders" v={score.adhoc_orders ?? 0} />
        <Row k="Breakdowns" v={score.breakdowns ?? 0} />
        <Row k="GPS blackouts" v={score.gps_dropouts ?? 0} />
        <Row k="Road closures" v={score.closures ?? 0} />
        <Row k="Customer cancellations" v={score.cancellations ?? 0} />
      </Block>

      <Block
        title="What the system did back"
        why="Restraint is the point. Reshuffling a shift that drivers are already running costs more than the minutes it saves, so most drift is watched, not re-planned."
      >
        <Row k="Re-plans dispatched" v={score.reopts_committed ?? 0} tone="go" />
        <Row k="Held by the churn gate" v={score.reopts_held ?? 0} tone="warn" />
        <Row k="Watched, not re-planned" v={score.monitor_only ?? 0} />
        {runInfo.latency_s != null && (
          <Row k="Last solve" v={`${runInfo.latency_s}s · ${runInfo.matrix_source || "—"}`} />
        )}
      </Block>

      <div className="ev-log" data-hl="log">
        <h4>Shift log</h4>
        {events.length === 0 && <p className="ev-empty">Nothing has happened yet.</p>}
        {events.map((e) => (
          <p key={e.id ?? `${e.at}-${e.message}`} className={`ev-ev k-${e.kind}`}>
            <span className="ev-at">{e.at}</span>
            <span className="ev-kind">{(e.kind || "").replace(/_/g, " ")}</span>
            <span className="ev-msg">{e.message}</span>
          </p>
        ))}
      </div>
    </aside>
  );
}

function Block({ title, why, children }) {
  return (
    <section className="ev-block">
      <h4>{title}</h4>
      <div className="ev-figs">{children}</div>
      {why && <p className="ev-why">{why}</p>}
    </section>
  );
}

function Fig({ v, k, tone }) {
  return (
    <span className={`ev-fig${tone ? ` t-${tone}` : ""}`}>
      <b>{v}</b>
      <span>{k}</span>
    </span>
  );
}

function Row({ k, v, tone }) {
  return (
    <span className={`ev-row${tone ? ` t-${tone}` : ""}`}>
      <span>{k}</span>
      <b>{v}</b>
    </span>
  );
}

const pct = (n) => (n == null ? "—" : `${Math.round(n)}%`);
const km = (n) => (n == null ? "—" : `${Math.round(n)} km`);
const inr = (n) => (n == null ? "—" : `₹${Math.round(n).toLocaleString("en-IN")}`);
// Green above `good`, amber above `ok`, red below. Judged, not decorative.
const tone = (n, good, ok) => (n == null ? undefined : n >= good ? "go" : n >= ok ? "warn" : "bad");
