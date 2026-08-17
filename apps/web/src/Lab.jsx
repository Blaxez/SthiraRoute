import React from "react";
import { Disruptions, Playbooks } from "./ShiftPanel.jsx";

/**
 * Everything a judge might ask to poke, kept off the stage until wanted.
 * The demo itself is Start the day + the map + the timeline.
 */
export default function Lab({
  open, onClose, busy, nextAction, run,
  onPrepare, onPlan, onApprove, onReset, onRewind,
  sim, onInject, onPlaybook,
  overlays, onToggleOverlay, onReplanConstraints,
  inspector,
}) {
  if (!open) return null;
  return (
    <div className="lab-scrim" onClick={onClose}>
      <aside className="lab" onClick={(e) => e.stopPropagation()} aria-label="Lab">
        <header className="lab-head">
          <b>Lab</b>
          <span>Plan, poke, and inspect — not the demo itself</span>
          <button className="insp-close" onClick={onClose} aria-label="Close lab">✕</button>
        </header>
        <div className="lab-cols">
          <div className="lab-controls">
            <h3>Plan</h3>
            <Act k="P" label="Prepare shift" next={nextAction === "prepare"}
                 hint="Rewind to 06:00, plan every order and dispatch it"
                 busy={busy === "prepare"} disabled={!!busy} onClick={onPrepare} />
            <Act k="1" label="Run plan" next={nextAction === "plan"}
                 hint="Build routes for every unassigned shipment"
                 busy={busy === "plan"} disabled={!!busy} onClick={onPlan} />
            <Act k="2" label="Approve & dispatch" next={nextAction === "approve"}
                 hint="Commit this plan and send it to the drivers"
                 busy={busy === "approve"}
                 disabled={!!busy || run?.status !== "completed"} onClick={onApprove} />
            <Act k="R" label="Reset demo" hint="Rebuild the fleet and rewind the clock"
                 busy={busy === "reset"} disabled={!!busy} onClick={onReset} />
            <Act k="0" label="Rewind to 06:00" hint="Keep today's plan, put the clock back"
                 busy={busy === "rewind"} disabled={!!busy} onClick={onRewind} />

            <h3>What if</h3>
            <Disruptions sim={sim} busy={busy} onInject={onInject} />

            <h3>Scripted day</h3>
            <Playbooks sim={sim} busy={busy} onPlaybook={onPlaybook} />

            <h3>No-entry windows</h3>
            {(overlays || []).map((o) => (
              <label key={o.id} className={`overlay-item${o.active ? " on" : ""}${o.kind === "closure" ? " closure" : ""}`}>
                <input type="checkbox" checked={o.active} onChange={() => onToggleOverlay(o)} />
                <span className="overlay-text">
                  <span className="overlay-name">{shortName(o.name)}</span>
                  <span className="overlay-win">
                    {hm(o.ban_start_min)}–{hm(o.ban_end_min)}
                  </span>
                </span>
              </label>
            ))}
            <Act k="6" label="Re-plan for constraints" busy={busy === "constraint"}
                 hint="Apply the curfew changes to the live routes"
                 disabled={!!busy} onClick={onReplanConstraints} />
          </div>
          <div className="lab-inspect">
            {inspector}
          </div>
        </div>
      </aside>
    </div>
  );
}

function Act({ k, label, hint, onClick, disabled, busy, next }) {
  return (
    <button
      className={`act${next ? " act-primary" : ""}`}
      onClick={onClick}
      disabled={disabled}
      title={hint ? `${hint}  (press ${k})` : undefined}
    >
      <span className="act-key">{k}</span>
      <span className="act-body">
        <span className="act-label">{busy ? "Working…" : label}</span>
        {hint && <span className="act-hint">{hint}</span>}
      </span>
      {next && <span className="act-next" aria-label="suggested next step">next</span>}
    </button>
  );
}

const hm = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const shortName = (n) => n.replace(/\s*\(template\)\s*/i, "");
