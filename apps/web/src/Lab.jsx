import React from "react";
import { Disruptions, Playbooks } from "./ShiftPanel.jsx";

/**
 * Everything a judge might ask to poke, kept off the stage until wanted.
 * Laid out as a control board (grid), not a long vertical form.
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
          <div>
            <b>Lab</b>
            <span>Plan · poke · inspect</span>
          </div>
          <button className="insp-close" onClick={onClose} aria-label="Close lab">✕</button>
        </header>
        <div className="lab-cols">
          <div className="lab-controls">
            <section className="lab-section">
              <h3>Day</h3>
              <div className="lab-acts">
                <Act k="P" label="Prepare shift" next={nextAction === "prepare"}
                     hint="Rewind, plan, dispatch"
                     busy={busy === "prepare"} disabled={!!busy} onClick={onPrepare} />
                <Act k="1" label="Run plan" next={nextAction === "plan"}
                     hint="Build routes"
                     busy={busy === "plan"} disabled={!!busy} onClick={onPlan} />
                <Act k="2" label="Approve" next={nextAction === "approve"}
                     hint="Commit to drivers"
                     busy={busy === "approve"}
                     disabled={!!busy || run?.status !== "completed"} onClick={onApprove} />
                <Act k="0" label="Rewind 06:00" hint="Keep plan, reset clock"
                     busy={busy === "rewind"} disabled={!!busy} onClick={onRewind} />
                <Act k="R" label="Reset demo" hint="Rebuild fleet + data"
                     busy={busy === "reset"} disabled={!!busy} onClick={onReset} />
              </div>
            </section>

            <section className="lab-section lab-section-split">
              <div>
                <h3>What if</h3>
                <Disruptions sim={sim} busy={busy} onInject={onInject} />
              </div>
              <div>
                <h3>Scripted day</h3>
                <Playbooks sim={sim} busy={busy} onPlaybook={onPlaybook} />
              </div>
            </section>

            <section className="lab-section">
              <h3>No-entry windows</h3>
              <div className="lab-overlays">
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
              </div>
              <Act k="6" label="Re-plan for constraints" busy={busy === "constraint"}
                   hint="Apply curfew changes to live routes"
                   disabled={!!busy} onClick={onReplanConstraints} />
            </section>
          </div>
          <div className="lab-inspect" data-hl="lab_inspect">
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
