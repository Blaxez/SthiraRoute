/**
 * What the guide is allowed to point at.
 *
 * One registry, three consumers: the overlay resolves a target to a live
 * element, the model is handed this list as its enum, and anything it invents
 * is dropped before it can highlight thin air. Previously the target names
 * lived in a Python docstring and the hooks lived in scattered data-hl
 * attributes, so half the names pointed at nothing on three of the four desks.
 */

/** desk: which desk owns it — the guide switches there first. */
export const TARGETS = {
  clock: {
    desk: "dispatch", sel: ".nowbar-clock",
    label: "shift clock",
    what: "Simulated operating day 06:00–22:00. Not wall time.",
  },
  headline: {
    desk: "dispatch", sel: ".nowbar-now",
    label: "the one-line status",
    what: "What just happened to the plan, in one sentence.",
  },
  chips: {
    desk: "dispatch", sel: ".nowbar-facts",
    label: "road speed and order chips",
    what: "Road speed the ETAs assume, orders still waiting, on-time percentage.",
  },
  scorecard: {
    desk: "dispatch", sel: ".ev",
    label: "the day so far",
    what: "Delivered, on time, minutes late, unassigned, cost, km, empty running, deck used.",
  },
  log: {
    desk: "dispatch", sel: ".ev-log",
    label: "shift log",
    what: "Every disruption and every response, stamped with the shift clock.",
  },
  map: {
    desk: "dispatch", sel: ".map-wrap", kind: "map",
    label: "the map",
    what: "Trucks on their road. Bright line still to go, pale already driven, dashed is an unapproved re-plan.",
  },
  legend: {
    desk: "dispatch", sel: ".map-legend",
    label: "the map key",
    what: "What each colour and line style on the map means.",
  },
  zones: {
    desk: "dispatch", sel: ".map-wrap", kind: "map",
    label: "the no-entry zones",
    what: "Red discs and hatching are municipal no-entry windows the solver plans around.",
  },
  timeline: {
    desk: "dispatch", sel: ".timeline",
    label: "the shift timeline",
    what: "One row per truck. The playhead is now, red bands are curfew windows, dots are stops.",
  },
  fleet: {
    desk: "dispatch", sel: ".fleet",
    label: "the fleet rail",
    what: "Every truck, its load, its next drop and whether it needs a human.",
  },
  desks: {
    desk: "dispatch", sel: ".roles",
    label: "the desk switcher",
    what: "Dispatch, Dock, Driver and Track are the same committed plan seen by four jobs.",
  },
  lab: {
    desk: "dispatch", sel: ".lab-toggle",
    label: "the Lab button",
    what: "Opens what-if: breakdowns, rush orders, curfew toggles, cost explain.",
  },
  labpanel: {
    desk: "dispatch", sel: ".lab",
    label: "the Lab panel",
    what: "The what-if controls themselves.",
  },
  start: {
    desk: "dispatch", sel: ".demo-start",
    label: "Start the day",
    what: "Plans, loads and dispatches the whole day in one press.",
  },
  play: {
    desk: "dispatch", sel: ".sb-play",
    label: "run/pause",
    what: "Runs or pauses the shift clock.",
  },
  speed: {
    desk: "dispatch", sel: ".sb-speeds",
    label: "playback speed",
    what: "1x, 2x, 4x speeds the simulated day only — never the trucks' real speed.",
  },
  city: {
    desk: "dispatch", sel: ".citypick",
    label: "the city picker",
    what: "Runs the same demo in another metro with its own curfews.",
  },
  gate: {
    desk: "dispatch", sel: ".gate",
    label: "the empty-map notice",
    what: "The map is empty until a plan is dispatched. This explains why.",
  },
  focus: {
    desk: "dispatch", sel: ".focus",
    label: "the selected consignment",
    what: "Who it is for, which truck has it, and when it is promised.",
  },
  benchmark: {
    desk: "dispatch", sel: ".ev-vs",
    label: "plan versus the simple way",
    what: "This plan against a naive nearest-neighbour route: stops served, km, legality.",
  },
  inspector: {
    desk: "dispatch", sel: ".lab-inspect",
    label: "the run inspector",
    what: "The solver run itself: status, cost breakdown, per-route detail.",
  },

  loadplan: {
    desk: "any", sel: ".lp-body",
    label: "the load plan",
    what: "The full deck viewer for one truck. Call open_deck to put it on screen.",
  },
  deck3d: {
    desk: "any", sel: ".lp-stage",
    label: "the 3D deck",
    what: "A rotatable 3D view of how the cartons actually stack, in LIFO drop order. open_deck opens it.",
  },
  deck_cog: {
    desk: "any", sel: ".lp-cog",
    label: "the balance readout",
    what: "Where the load's centre of gravity sits, and whether the axle balance is safe. Inside the deck viewer.",
  },
  deck_steps: {
    desk: "any", sel: ".lp-stepper",
    label: "the unload stepper",
    what: "Walks the deck empty one drop at a time, proving nothing is rehandled. Inside the deck viewer.",
  },

  ask: {
    desk: "dispatch", sel: ".ask-toggle",
    label: "the Ask button",
    what: "Opens the typed briefing. Tapping the orb instead talks to me out loud.",
  },

  dock_trucks: {
    desk: "dock", sel: ".dock-list",
    label: "the loading list",
    what: "Trucks at the bay and whether their deck actually packs.",
  },
  dock_deck: {
    desk: "dock", sel: ".dock-detail",
    label: "the deck plan",
    what: "Top-down view of the deck: what goes where, in LIFO drop order.",
  },

  driver_next: {
    desk: "driver", sel: ".driver-now",
    label: "the next drop",
    what: "The one stop the driver is doing now, with the promise time.",
  },
  driver_manifest: {
    desk: "driver", sel: ".driver-stops",
    label: "the manifest",
    what: "Every stop in order. Only the next one can be signed off.",
  },

  track_list: {
    desk: "track", sel: ".track-list",
    label: "the consignment list",
    what: "The customer's copy: every consignment and its state.",
  },
  track_journey: {
    desk: "track", sel: ".track-journey",
    label: "the journey",
    what: "One consignment's beats, from booked to delivered.",
  },
};

/**
 * Older names the model has been trained on by earlier prompts, and the words
 * a person would reach for. Pointing at nothing is worse than pointing at the
 * next best thing.
 */
const ALIAS = {
  kpis: "scorecard",
  numbers: "scorecard",
  evidence: "scorecard",
  shiftlog: "log",
  events: "log",
  key: "legend",
  roles: "desks",
  now: "headline",
  status: "headline",
  speed: "speed",
  zone: "zones",
  curfew: "timeline",
  deck: "dock_deck",
  manifest: "driver_manifest",
  driver: "driver_next",
  dock: "dock_trucks",
  track: "track_list",
};

/** Targets the model may also build itself, by code. */
export const DYNAMIC = {
  vehicle: "vehicle:MH-01 — that truck's row, and the map follows it",
  shipment: "shipment:SHP-13 — that consignment, selected and centred",
};

const CODE_DESK = { vehicle: "dispatch", shipment: "dispatch" };

/**
 * id → { el, desk, label, kind } or null when it is not on screen.
 * Dynamic ids (vehicle:MH-01) come off the data-hl attribute the rails emit.
 */
export function resolveTarget(id) {
  if (!id) return null;
  const raw = String(id).trim();
  const key = TARGETS[raw] ? raw : ALIAS[raw.toLowerCase()] || raw;
  const spec = TARGETS[key];
  if (spec) {
    return {
      id: key,
      desk: spec.desk,
      label: spec.label,
      kind: spec.kind || "dom",
      el: document.querySelector(spec.sel),
    };
  }
  const [kind, ...rest] = key.split(":");
  const code = rest.join(":").trim();
  if (!code || !CODE_DESK[kind]) return null;
  // The truck on the map beats the truck's row in the rail: when someone says
  // "look at MH-01" they mean the one that is moving.
  let el = null;
  const pick = (sel) => {
    try { return document.querySelector(sel); } catch { return null; }
  };
  const esc = (v) => (window.CSS?.escape ? CSS.escape(v) : v);
  const tag = `[data-hl="${esc(kind)}:${esc(code)}"]`;
  el = pick(`.mk${tag}`) || pick(tag);
  return {
    id: key, desk: CODE_DESK[kind], label: code, kind, code, el,
  };
}

/**
 * Where the guide is pointing right now, in viewport pixels, or null.
 * A plain mutable box on purpose: the overlay writes it every frame and the
 * blob reads it inside its own animation loop, so neither one re-renders the
 * app sixty times a second to say "the rectangle moved four pixels".
 */
export const focusRect = { current: null };

/** The enum handed to guide — it can only point at things that exist. */
export const targetIds = () => Object.keys(TARGETS);

/** One compact block for the system prompt, so names carry their meaning. */
export function targetBrief() {
  const lines = Object.entries(TARGETS).map(
    ([id, t]) => `${id} (${t.desk}) — ${t.what}`
  );
  return lines.concat(Object.values(DYNAMIC)).join("\n");
}
