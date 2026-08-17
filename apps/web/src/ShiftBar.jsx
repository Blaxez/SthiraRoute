import React from "react";

const SHIFT_START = 6 * 60;
const SHIFT_END = 22 * 60;
const SPAN = SHIFT_END - SHIFT_START;

const SPEEDS = [
  { id: 1, label: "1×", ms: 2400 },
  { id: 2, label: "2×", ms: 1200 },
  { id: 4, label: "4×", ms: 600 },
];

// One glyph per disruption, so a beat is identifiable at pip size.
const BEAT_MARK = {
  congestion: "≡",
  storm: "☂",
  closure: "⛔",
  breakdown: "✖",
  new_order: "+",
  cancel: "−",
  gps_loss: "◌",
  warehouse_delay: "⏳",
};

const TRAFFIC_TONE = { clear: "good", moderate: "", heavy: "warn", gridlock: "bad" };

/**
 * The shift instrument strip: what time it is in the operating day, what the
 * city is doing to the fleet, and what the system decided about it.
 *
 * It sits above the map rather than inside a panel because it is the one thing
 * that must never be scrolled away from — everything else on screen is only
 * meaningful relative to the clock.
 */
export default function ShiftBar({
  sim, running, speed, onPlay, onStep, onSpeed, onAutopilot, onReset, decision, busy,
}) {
  if (!sim) return null;

  const pct = (m) => Math.max(0, Math.min(100, ((m - SHIFT_START) / SPAN) * 100));
  const beats = sim.scenario?.beats || [];
  const traffic = sim.traffic || {};
  const weather = sim.weather || {};
  const fleet = sim.fleet || {};

  return (
    <div className="shiftbar">
      {/* The clock, and nothing else. Tick size and shift span were printed
          under it permanently; both are settings, not readings, and the span
          is already the timeline's own axis. */}
      <div className="sb-clock-block">
        <div
          className="sb-clock"
          title={`Simulated shift time · ${sim.minutes_per_tick} min per tick · shift 06:00–22:00`}
        >
          {sim.clock}
          {sim.shift_over && <span className="sb-over">shift over</span>}
        </div>
      </div>

      <div className="sb-transport">
        <button
          className={`sb-play${running ? " on" : ""}`}
          onClick={onPlay}
          disabled={sim.shift_over && !running}
          title={running ? "Pause the day" : "Run the day (press space)"}
        >
          {running ? "❚❚ Pause" : "▶ Run day"}
        </button>
        <button className="sb-btn" onClick={onStep} disabled={busy || sim.shift_over}
                title="Advance one tick">
          ▸ Step
        </button>
        <div className="sb-speeds" role="group" aria-label="Playback speed">
          {SPEEDS.map((s) => (
            <button
              key={s.id}
              className={`sb-speed${speed === s.id ? " on" : ""}`}
              onClick={() => onSpeed(s.id)}
            >
              {s.label}
            </button>
          ))}
        </div>
        <button
          className={`sb-pill${sim.autopilot ? " on" : ""}`}
          onClick={onAutopilot}
          title="When on, the system re-plans in response to its own disruptions — subject to the churn gate"
        >
          <span className="sb-dot" /> Autopilot {sim.autopilot ? "on" : "off"}
        </button>
        <button className="sb-btn" onClick={onReset} disabled={busy} title="Rewind to 06:00">
          ↺ Rewind
        </button>
      </div>

      {/* The day, with every scripted disruption visible before it lands. */}
      <div className="sb-day">
        <div className="sb-track">
          <div className="sb-fill" style={{ width: `${pct(sim.clock_min)}%` }} />
          {beats.map((b) => (
            <span
              key={`${b.at_min}-${b.kind}`}
              className={`sb-beat${b.fired ? " fired" : ""}`}
              style={{ left: `${pct(b.at_min)}%` }}
              title={`${b.at} · ${b.note}${b.fired ? " (fired)" : ""}`}
            >
              {BEAT_MARK[b.kind] || "•"}
            </span>
          ))}
          <span className="sb-head" style={{ left: `${pct(sim.clock_min)}%` }} />
        </div>
        <div className="sb-day-labels">
          <span>06</span><span>10</span><span>14</span><span>18</span><span>22</span>
        </div>
      </div>

      {/* Conditions, by exception. Four boxes reporting "clear", "clear",
          "0 none" and a healthy fleet is four boxes of noise that train you to
          stop looking — which is exactly when the fifth one matters. Road speed
          is always here because every ETA on screen depends on it; the rest
          appear only when they are not nominal. */}
      <div className="sb-conditions">
        <Condition
          label={traffic.label}
          value={`${traffic.speed_kmh} km/h`}
          tone={TRAFFIC_TONE[traffic.label] ?? ""}
          title={
            traffic.incident
              ? `Incident on a corridor until ${traffic.incident_until}`
              : `Time-of-day congestion profile for ${sim.city?.label || "this city"}`
          }
        />
        {weather.state !== "clear" && (
          <Condition
            label={weather.state}
            value={weather.until ? `until ${weather.until}` : ""}
            tone={weather.state === "storm" ? "bad" : "warn"}
            title={weather.label}
          />
        )}
        {sim.closures?.length > 0 && (
          <Condition
            label={`${sim.closures.length} closed`}
            // Closure names read "<cause> — <corridor>"; the corridor is the
            // part that fits, and the cause is already in the weather chip.
            value={sim.closures[0].name.split("— ").pop()}
            tone="bad"
            title="Corridors closed to freight — a hard constraint, not a cost"
          />
        )}
        {fleet.down > 0 && (
          <Condition label={`${fleet.down} down`} tone="bad"
                     title="Trucks broken down and out of the plan" />
        )}
        {fleet.dark > 0 && (
          <Condition label={`${fleet.dark} dark`} tone="warn"
                     title="Trucks whose GPS has stopped reporting" />
        )}
      </div>

      {/* One sentence, always present, saying what just happened and what the
          system did about it. Without it the screen is forty numbers and no
          narrative, and a first-time viewer has nowhere to start reading. */}
      <NowLine sim={sim} decision={decision} running={running} />
    </div>
  );
}

function NowLine({ sim, decision, running }) {
  if (decision) {
    return (
      <div className={`sb-decision d-${decision.action}`} title={decision.gate_reason || ""}>
        <span className="sb-decision-tag">{DECISION_TAG[decision.action] || decision.action}</span>
        <span className="sb-decision-text">
          {decision.action === "dispatched" && `Re-planned and dispatched run #${decision.run_id} — ${decision.reason}.`}
          {decision.action === "held" && `Proposed run #${decision.run_id} but the churn gate said hold — ${decision.reason}.`}
          {decision.action === "monitor" && decision.reason}
          {decision.action === "cooldown" && decision.reason}
        </span>
      </div>
    );
  }

  const latest = (sim.events || [])[0];
  if (latest) {
    return (
      <div className={`sb-decision d-event k-${latest.kind}`}>
        <span className="sb-decision-tag">{latest.at} · {latest.kind.replace("_", " ")}</span>
        <span className="sb-decision-text">{latest.message}</span>
      </div>
    );
  }

  return (
    <div className="sb-decision d-idle">
      <span className="sb-decision-tag">{sim.clock} · ready</span>
      <span className="sb-decision-text">
        {sim.shift_over
          ? "The shift is over. Rewind to run the day again."
          : running
          ? "The day is running. Disruptions will appear here as they land."
          : "Nothing has happened yet — press Run day, or fire a disruption from the left rail."}
      </span>
    </div>
  );
}

const DECISION_TAG = {
  dispatched: "re-planned",
  held: "held for dispatcher",
  monitor: "monitoring",
  cooldown: "cooldown",
};

function Condition({ label, value, tone, title }) {
  return (
    <span className={`sb-cond${tone ? ` g-${tone}` : ""}`} title={title}>
      <b>{label}</b>
      {value ? <i>{value}</i> : null}
    </span>
  );
}

export { SPEEDS };
