/**
 * Writes the deck's semantic icons from Lucide, tinted to the palette so an
 * icon always matches the accent of the block it sits in.
 * The output is committed, so this only needs re-running when the set changes:
 *   npm install lucide-static --no-save && node make-icons.mjs
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";

const SRC = "node_modules/lucide-static/icons";
const OUT = "../../PPT/deck/img/icons";
mkdirSync(OUT, { recursive: true });

const C = {
  navy: "#1f3864",
  blue: "#0070c0",
  steel: "#3e6b9a",
  green: "#14804a",
  saffron: "#d9691a",
  red: "#b8362c",
  white: "#ffffff",
  muted: "#6b7a8a",
};

// name → [lucide slug, colour key]. Suffix the file so one glyph can appear in
// two colours without a clash.
const WANT = [
  // slide 2 — the four failures and the four mechanisms
  ["fill-red", "package", "red"],
  ["route-red", "route", "red"],
  ["blind-red", "wifi-off", "red"],
  ["split-red", "split", "red"],
  ["boxes-green", "boxes", "green"],
  ["route-green", "route", "green"],
  ["radar-green", "activity", "green"],
  ["desks-green", "users", "green"],
  // differentiators
  ["rupee-saffron", "indian-rupee", "saffron"],
  ["cube-green", "boxes", "green"],
  ["ban-blue", "ban", "blue"],
  ["check-red", "circle-check-big", "red"],
  // slide 4 — risks
  ["server-navy", "server", "navy"],
  ["map-navy", "map-pin", "navy"],
  ["clock-navy", "clock", "navy"],
  ["shield-navy", "shield-check", "navy"],
  ["bot-navy", "bot", "navy"],
  ["scale-navy", "scale", "navy"],
  ["laptop-blue", "monitor", "blue"],
  ["rocket-blue", "rocket", "blue"],
  ["flask-green", "flask-conical", "green"],
  // slide 5 — who feels it, and the benefit themes
  ["dispatcher", "clipboard-list", "navy"],
  ["driver", "truck", "navy"],
  ["dock", "warehouse", "navy"],
  ["customer", "handshake", "navy"],
  ["city", "building-2", "navy"],
  ["fuel-green", "fuel", "green"],
  ["leaf-green", "leaf", "green"],
  ["gavel-saffron", "scale", "saffron"],
  ["resil-blue", "refresh-cw", "blue"],
  // white, for use on dark fills
  ["truck-w", "truck", "white"],
  ["alert-w", "triangle-alert", "white"],
  ["cpu-w", "cpu", "white"],
  ["db-w", "database", "white"],
  ["layers-w", "layers", "white"],
  ["check-w", "circle-check-big", "white"],
  ["gauge-w", "gauge", "white"],
  ["mic-w", "mic", "white"],
  ["snow-w", "thermometer-snowflake", "white"],
  ["pin-w", "map-pin", "white"],
  ["rupee-w", "indian-rupee", "white"],
  ["down-w", "trending-down", "white"],
];

for (const [name, slug, colour] of WANT) {
  const raw = readFileSync(`${SRC}/${slug}.svg`, "utf8");
  // Lucide ships stroke="currentColor"; bake the colour in so an <img> works.
  const svg = raw
    .replace(/stroke="currentColor"/, `stroke="${C[colour]}"`)
    .replace(/stroke-width="2"/, 'stroke-width="2.1"');
  if (!svg.includes(C[colour])) throw new Error(`${slug}: colour not applied`);
  writeFileSync(`${OUT}/${name}.svg`, svg);
  console.log(`${name.padEnd(16)} ${slug.padEnd(24)} ${C[colour]}`);
}

console.log(`\n${WANT.length} icons → ${OUT}`);
