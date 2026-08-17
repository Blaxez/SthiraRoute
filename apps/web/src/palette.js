/**
 * Operational palette — the same hues the stylesheet defines, exported for the
 * places that must hand raw colours to a canvas (MapLibre) or inline styles.
 * Keep this in step with :root in styles.css; nothing here is decorative.
 */
export const C = {
  ink: "#0e141c",
  surface: "#151c26",
  line: "#2c3a4d",
  text: "#e8edf2",
  text2: "#9aabbc",
  text3: "#8698ab",
  silver: "#f0c014",
  sage: "#3bb37e",
  vermilion: "#e8555a",
  /* Sign red. Large fills only — it is unreadable as text on this ground. */
  noentry: "#c1121f",
  ochre: "#e07a1f",
  steel: "#5b8fad",
};

/** Route line colours, in assignment order. Steel first: vehicles are steel. */
export const ROUTE_COLORS = [
  "#5b8fad", "#2d9a6a", "#8b6fa8", "#e07a1f", "#c45c7a", "#3d9a8c",
];

/** Shipment marker colour by lifecycle state. */
export const shipmentColor = (status) =>
  status === "delivered" ? C.text3
  : status === "in_transit" ? C.ochre
  : status === "assigned" ? C.sage
  : C.vermilion;
