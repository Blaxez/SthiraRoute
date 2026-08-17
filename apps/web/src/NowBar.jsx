import React from "react";
import { RoleSwitch } from "./Roles.jsx";
import { SPEEDS } from "./ShiftBar.jsx";

/**
 * The announcement board. This is the only chrome a first-time viewer should
 * have to read: what time it is in the shift, what just happened, and the
 * one button that makes the day run.
 */
export default function NowBar({
  sim, running, speed, busy, live, view, dispatched, kpis, decision,
  onStart, onPlay, onSpeed, onChangeView, onLab, labOpen, onPickCity, onKeys,
  onAsk, askOpen, voice,
}) {
  const city = sim?.city;
  const traffic = sim?.traffic;
  const plan = kpis?.plan;
  const waiting = kpis?.shipments?.unassigned ?? 0;

  return (
    <header className="nowbar">
      <div className="nowbar-brand">
        <span className="brand-mark">Sthira<span>Route</span></span>
        {city && (
          <CityPicker sim={sim} busy={busy} onPick={onPickCity} />
        )}
      </div>

      <div className="nowbar-clock" data-hl="clock" title="Simulated shift time 06:00–22:00 — not the time on your watch">
        <span className="nowbar-clock-label">shift time</span>
        <span className="nowbar-clock-time">{sim?.clock || "06:00"}</span>
      </div>
      {sim?.shift_over && <span className="sb-over">shift over</span>}

      <NowSentence sim={sim} running={running} dispatched={dispatched} decision={decision} />

      <div className="nowbar-facts" data-hl="kpis">
        {traffic && (
          <span className="nowbar-chip" title="Road speed the ETAs are using">
            {traffic.speed_kmh} km/h
          </span>
        )}
        {dispatched && plan && (
          <span className="nowbar-chip" title="Orders still waiting for a truck">
            {waiting === 0 ? "every order assigned" : `${waiting} waiting`}
          </span>
        )}
        {dispatched && plan?.on_time_pct != null && (
          <span className="nowbar-chip">
            {plan.on_time_pct}% on time
          </span>
        )}
        <div className={`live${live ? " on" : ""}`}>
          <span className="dot" />
          {live ? "live" : "offline"}
        </div>
      </div>

      <div className="nowbar-go">
        {!dispatched ? (
          <button
            className="demo-start"
            onClick={onStart}
            disabled={!!busy}
            title="Plan, load and dispatch the day  (press P)"
          >
            {busy === "demo" || busy === "prepare" ? "Planning…" : "Start the day"}
          </button>
        ) : (
          <>
            <button
              className={`sb-play${running ? " on" : ""}`}
              onClick={onPlay}
              disabled={sim?.shift_over && !running}
              title={running ? "Pause  (space)" : "Run the day  (space)"}
            >
              {running ? "Pause" : "Run the day"}
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
          </>
        )}
      </div>

      <RoleSwitch view={view} onChange={onChangeView} />

      <button
        className="nowbar-keys"
        onClick={onKeys}
        title="Keyboard shortcuts  (press ?)"
        aria-label="Keyboard shortcuts"
      >
        ?
      </button>

      {/* Typing to the officer is a header control, like Lab. It used to hang
          off the orb, which tied one job (write a question) to another (talk),
          and moved every time the orb dodged something. */}
      <button
        className={`ask-toggle${askOpen ? " on" : ""}${voice && voice !== "off" && voice !== "fallback" ? " voice" : ""}`}
        onClick={onAsk}
        aria-expanded={!!askOpen}
        data-hl="ask"
        title="Ask the briefing officer  (press /)"
      >
        <svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
          <path
            d="M2.6 2.4h10.8v8.2H6.9L3.7 13V10.6H2.6z"
            fill="none" stroke="currentColor" strokeWidth="1.3"
            strokeLinejoin="round"
          />
        </svg>
        Ask
      </button>

      <button
        className={`lab-toggle${labOpen ? " on" : ""}`}
        onClick={onLab}
        aria-expanded={labOpen}
        aria-label="Lab"
        data-hl="lab"
        title="What-if: plan, curfews, breakdowns  (press L)"
      >
        Lab
      </button>
    </header>
  );
}

function NowSentence({ sim, running, dispatched, decision }) {
  const latest = (sim?.events || [])[0];
  let tag = sim?.clock || "06:00";
  let text = "Start the day to watch the fleet assign loads, take the road, and recover when something breaks.";
  let tone = "idle";

  if (decision?.action === "dispatched") {
    tag = "re-planned";
    text = decision.reason || `Re-planned and dispatched run #${decision.run_id}.`;
    tone = "go";
  } else if (decision?.action === "held") {
    tag = "held";
    text = decision.reason || "A cheaper-looking reshuffle was held — it would churn the plan for too little gain.";
    tone = "warn";
  } else if (latest) {
    tag = `${latest.at} · ${latest.kind.replace(/_/g, " ")}`;
    text = latest.message;
    tone = TONE[latest.kind] || "event";
  } else if (dispatched && running) {
    tag = `${sim.clock} · moving`;
    text = "The day is running. Watch the playhead on the timeline — each row is one truck.";
    tone = "go";
  } else if (dispatched) {
    tag = `${sim?.clock || "06:00"} · ready`;
    text = "Plan is in force. Press Run the day and the fleet starts moving.";
    tone = "event";
  }

  return (
    <p className={`nowbar-now t-${tone}`}>
      <b>{tag}</b>
      <span>{text}</span>
    </p>
  );
}

const TONE = {
  breakdown: "bad", closure: "bad", late: "bad",
  traffic: "warn", weather: "warn", sla_risk: "warn", gps_lost: "warn",
  warehouse_delay: "warn", replan_held: "warn",
  delivered: "go", returned: "go", replan: "go", gps_back: "go", script: "go",
};

function CityPicker({ sim, busy, onPick }) {
  const [open, setOpen] = React.useState(false);
  const cities = sim?.cities || [];
  const current = sim?.city;
  if (!current) return null;
  return (
    <div className="citypick">
      <button
        className={`citypick-btn${open ? " on" : ""}`}
        onClick={() => setOpen((v) => !v)}
        disabled={!!busy}
        title="Run the demo in another metro"
      >
        <span className="citypick-name">{current.label}</span>
        <span className="citypick-caret">▾</span>
      </button>
      {open && (
        <>
          <div className="citypick-scrim" onClick={() => setOpen(false)} />
          <div className="citypick-menu">
            {cities.map((c) => (
              <button
                key={c.id}
                className={`citypick-item${c.id === current.id ? " on" : ""}`}
                onClick={() => {
                  setOpen(false);
                  if (c.id !== current.id) onPick(c.id);
                }}
              >
                <span className="citypick-item-head">
                  <b>{c.label}</b>
                  <span>{c.region}</span>
                </span>
                <span className="citypick-item-note">{c.notes}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
