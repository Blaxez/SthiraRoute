/**
 * Four desks, one plan. PS2's "fragmented stakeholders" is a coordination
 * problem: the dock, the driver and the consignee must be looking at the
 * same committed routes the dispatcher just approved.
 */
export const ROLES = [
  { id: "dispatch", label: "Dispatch", k: null, hint: "Control tower — plan, dispatch, re-plan" },
  { id: "dock", label: "Dock", k: "W", hint: "Load allocation — what is on which truck" },
  { id: "driver", label: "Driver", k: "D", hint: "One truck, the next stop, proof of delivery" },
  { id: "track", label: "Track", k: "T", hint: "Consignment tracking — the customer's copy" },
];

export function RoleSwitch({ view, onChange }) {
  return (
    <div className="roles" role="tablist" aria-label="Stakeholder desk" data-hl="desks">
      {ROLES.map((r) => (
        <button
          key={r.id}
          type="button"
          role="tab"
          aria-selected={view === r.id}
          aria-label={r.label}
          data-role={r.id}
          className={`roles-btn${view === r.id ? " on" : ""}`}
          onClick={() => onChange(r.id)}
          title={r.hint + (r.k ? `  (press ${r.k})` : "")}
        >
          {r.k ? <span className="tool-key">{r.k}</span> : null}
          {r.label}
        </button>
      ))}
    </div>
  );
}

export function DeskChrome({ title, children }) {
  return (
    <div className="desk">
      {title ? <p className="desk-role">{title}</p> : null}
      {children}
    </div>
  );
}

export async function fetchBoard() {
  const res = await fetch("/api/network/board");
  if (!res.ok) throw new Error(`board → ${res.status}`);
  return res.json();
}
