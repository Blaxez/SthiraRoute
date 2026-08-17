import React, { useEffect, useRef, useState } from "react";
import { focusRect } from "./guide.js";
import { C } from "./palette.js";

/**
 * The officer's presence: a rotating shell of particles that breathes with the
 * radio.
 *
 * Canvas 2D and ~280 points — no WebGL library for one ornament. Points sit on
 * a Fibonacci lattice (even coverage, no polar clumping), a two-term field
 * deforms the radius, and everything is depth-sorted and drawn from a cached
 * sprite so a frame costs well under a millisecond.
 *
 * It is draggable, it remembers where it was parked, and it gets out of its
 * own way: when the guide cuts a hole in the screen the blob springs to the
 * nearest free side rather than sitting on top of the thing being explained.
 */

const N = 280;
const GOLDEN = Math.PI * (3 - Math.sqrt(5));
const DEPTH = 3.1; // projection distance — larger is a flatter, calmer sphere

/**
 * Live inputs from the radio. Mutated, never set through React — the blob
 * reads the analyser nodes straight out of the audio graph inside its own
 * frame loop, so the shell breathes on the actual waveform with no state
 * updates and no second animation loop.
 */
export const voice = { state: "idle", inNode: null, outNode: null };

// Debug handle, same idea as window.__map: lets the checks see what the radio
// is doing without reaching into React.
if (typeof window !== "undefined") window.__voice = voice;

/** Where the blob is right now, so the guide can draw a line to its target. */
export const blobAt = { x: 0, y: 0, r: 0 };

/**
 * White throughout, because the officer is chrome, not a status light. State
 * reads from motion and brightness instead of hue — the one exception is a
 * failure, which has to look like a failure.
 */
const MOODS = {
  idle:      { amp: 0.19, spin: 0.16, color: "#c2c2c2",   size: 1.00 },
  listening: { amp: 0.26, spin: 0.34, color: "#ffffff",   size: 1.20 },
  thinking:  { amp: 0.34, spin: 0.70, color: "#e0e0e0",   size: 1.05 },
  speaking:  { amp: 0.28, spin: 0.38, color: "#ffffff",   size: 1.30 },
  error:     { amp: 0.14, spin: 0.10, color: C.vermilion, size: 1.00 },
};

const BOX = 112; // css px of the canvas
const R = 33; // sphere radius on screen
const MARGIN = 14;

const sprites = new Map();
function sprite(hex) {
  const key = hex;
  const hit = sprites.get(key);
  if (hit) return hit;
  const s = document.createElement("canvas");
  s.width = s.height = 32;
  const g = s.getContext("2d");
  const grad = g.createRadialGradient(16, 16, 0, 16, 16, 16);
  grad.addColorStop(0, hex);
  grad.addColorStop(0.45, `${hex}b0`);
  grad.addColorStop(1, `${hex}00`);
  g.fillStyle = grad;
  g.fillRect(0, 0, 32, 32);
  sprites.set(key, s);
  return s;
}

const mix = (a, b, t) => {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  // Quantised so the sprite cache stays a handful of entries, not hundreds.
  const ch = pa.map((v, i) => Math.round((v + (pb[i] - v) * t) / 17) * 17);
  return `#${ch.map((v) => Math.min(255, v).toString(16).padStart(2, "0")).join("")}`;
};

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/**
 * Nearest point outside `zone`, still on screen. Returns `p` untouched when it
 * is already clear, so the blob only ever moves when it is actually in the way.
 */
function shove(p, zone) {
  if (!(p.x > zone.x0 && p.x < zone.x1 && p.y > zone.y0 && p.y < zone.y1)) return p;
  const outs = [
    { x: zone.x0, y: p.y, d: p.x - zone.x0 },
    { x: zone.x1, y: p.y, d: zone.x1 - p.x },
    { x: p.x, y: zone.y0, d: p.y - zone.y0 },
    { x: p.x, y: zone.y1, d: zone.y1 - p.y },
  ]
    .map((o) => ({
      x: clamp(o.x, BOX / 2 + MARGIN, innerWidth - BOX / 2 - MARGIN),
      y: clamp(o.y, BOX / 2 + MARGIN, innerHeight - BOX / 2 - MARGIN),
      d: o.d,
    }))
    // Only the sides that still clear the zone once clamped on screen.
    .filter((o) => o.x <= zone.x0 || o.x >= zone.x1 || o.y <= zone.y0 || o.y >= zone.y1)
    .sort((a, b) => a.d - b.d);
  return outs[0] || p;
}

const wave = new Uint8Array(128);
/** RMS of whatever that analyser is carrying, 0..1. */
function levelOf(node) {
  if (!node) return 0;
  node.getByteTimeDomainData(wave);
  let sum = 0;
  for (let i = 0; i < wave.length; i++) {
    const v = (wave[i] - 128) / 128;
    sum += v * v;
  }
  return Math.min(1, Math.sqrt(sum / wave.length) * 3.4);
}

function defaultPark() {
  // Sit on the map's lower-right, clear of the Evidence column (.ev).
  const ev = typeof document !== "undefined" ? document.querySelector(".ev") : null;
  const evW = ev ? Math.ceil(ev.getBoundingClientRect().width) : 320;
  const timeline = typeof document !== "undefined" ? document.querySelector(".timeline") : null;
  const tlH = timeline ? Math.ceil(timeline.getBoundingClientRect().height) : 96;
  return {
    x: Math.max(BOX / 2 + MARGIN, innerWidth - evW - BOX / 2 - 18),
    y: Math.max(BOX / 2 + MARGIN, innerHeight - tlH - BOX / 2 - 14),
  };
}

/** Has the dispatcher ever put it somewhere on purpose? */
function parked() {
  try {
    const raw = JSON.parse(localStorage.getItem("sr.blob") || "null");
    if (raw && Number.isFinite(raw.x) && Number.isFinite(raw.y)) return raw;
  } catch { /* a corrupt entry is not worth a crash */ }
  return null;
}

export default function Blob({ open, listening, onToggle, onOpenAsk, hint }) {
  const wrap = useRef(null);
  const cv = useRef(null);
  const hit = useRef(null);
  const park = useRef(parked() || defaultPark());
  const placed = useRef(!!parked());
  const pos = useRef({ ...park.current });
  const drag = useRef(null);
  const openRef = useRef(open);
  openRef.current = open;
  const [tip, setTip] = useState(() => {
    try { return localStorage.getItem("sr.askCoach") !== "1"; } catch { return true; }
  });

  const dismissTip = () => {
    setTip(false);
    try { localStorage.setItem("sr.askCoach", "1"); } catch { /* */ }
  };

  useEffect(() => {
    const canvas = cv.current;
    const g = canvas.getContext("2d", { alpha: true });
    const dpr = Math.min(2, devicePixelRatio || 1);
    canvas.width = BOX * dpr;
    canvas.height = BOX * dpr;
    g.scale(dpr, dpr);

    const pts = new Float32Array(N * 3);
    // A lattice on its own reads as a wireframe ball — too regular to be
    // alive. Each point carries its own phase and its own size, which breaks
    // the grid without scattering the shell.
    const phase = new Float32Array(N);
    const grain = new Float32Array(N);
    const jitter = new Float32Array(N);
    for (let i = 0; i < N; i++) {
      const y = 1 - (i / (N - 1)) * 2;
      const rad = Math.sqrt(Math.max(0, 1 - y * y));
      const th = i * GOLDEN;
      pts[i * 3] = Math.cos(th) * rad;
      pts[i * 3 + 1] = y;
      pts[i * 3 + 2] = Math.sin(th) * rad;
      phase[i] = Math.random() * Math.PI * 2;
      grain[i] = 0.6 + Math.random() * 0.8;
      // A permanent nudge in and out of the shell. Without it the lattice
      // rows stay visible from the front and it reads as a wireframe ball.
      jitter[i] = 0.93 + Math.random() * 0.14;
    }

    const order = new Int32Array(N).map((_, i) => i);
    const depth = new Float32Array(N);
    const sx = new Float32Array(N);
    const sy = new Float32Array(N);
    const ss = new Float32Array(N);

    const still =
      typeof matchMedia === "function" &&
      matchMedia("(prefers-reduced-motion: reduce)").matches;

    let yaw = 0.6;
    let tilt = 0.32;
    let t = 0;
    let amp = MOODS.idle.amp;
    let hue = MOODS.idle.color;
    let glow = 0;
    let live = 0;
    let last = performance.now();
    let raf = 0;
    let idleFrames = 0;
    // Cache panel rects — querying layout every frame thrashed the map + voice path.
    let askBox = null;
    let lpBox = null;
    let evBox = null;
    let boxAt = 0;

    const refreshBoxes = (now) => {
      if (now - boxAt < 200 && askBox !== undefined) return;
      boxAt = now;
      const askEl = openRef.current ? document.querySelector(".ask") : null;
      const lpEl = document.querySelector(".lp");
      const evEl = document.querySelector(".ev");
      askBox = askEl ? askEl.getBoundingClientRect() : null;
      lpBox = lpEl ? lpEl.getBoundingClientRect() : null;
      evBox = evEl ? evEl.getBoundingClientRect() : null;
    };

    const frame = (now) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;

      const mood = MOODS[voice.state] || MOODS.idle;
      const isIdle = voice.state === "idle" && !openRef.current && !drag.current && !focusRect.current;
      // Idle chrome: skip every other frame to free the map/voice path.
      if (isIdle) {
        idleFrames += 1;
        if (idleFrames % 2 === 1) {
          raf = requestAnimationFrame(frame);
          return;
        }
      } else {
        idleFrames = 0;
      }

      const drive =
        voice.state === "listening" ? levelOf(voice.inNode)
        : voice.state === "speaking" ? levelOf(voice.outNode)
        : 0;
      live += (drive - live) * 0.25;
      const wantAmp = mood.amp + live * 0.55;
      amp += (wantAmp - amp) * 0.14;
      glow += ((voice.state === "idle" ? 0.1 : 0.34 + live * 0.5) - glow) * 0.1;
      hue = mix(hue, mood.color, 0.1);

      if (!still) {
        yaw += dt * (mood.spin + live * 0.5);
        tilt = 0.3 + Math.sin(now / 4200) * 0.22;
        t = now / 1000;
      }

      // ---- where should it sit ----------------------------------------
      // Recompute home when the dispatcher never parked it — so Evidence /
      // timeline size changes do not leave the orb sitting on the KPIs.
      if (!placed.current && !drag.current) {
        park.current = defaultPark();
      }
      const f = focusRect.current;
      let want = park.current;
      if (f) {
        want = shove(want, {
          x0: f.x - R - 30,
          y0: f.y - R - 30,
          x1: f.x + f.w + R + 30,
          y1: f.y + f.h + R + 30 + 92, // + room for the callout
        });
      }
      refreshBoxes(now);
      for (const b of [askBox, lpBox, evBox]) {
        if (!b) continue;
        want = shove(want, {
          x0: b.left - R - 14,
          y0: b.top - R - 14,
          x1: b.right + R + 14,
          y1: b.bottom + R + 14,
        });
      }
      if (drag.current) want = drag.current;
      const k = drag.current ? 1 : still ? 1 : 0.12;
      pos.current.x += (want.x - pos.current.x) * k;
      pos.current.y += (want.y - pos.current.y) * k;
      const node = wrap.current;
      if (node) {
        node.style.transform = `translate(${Math.round(pos.current.x - BOX / 2)}px, ${Math.round(
          pos.current.y - BOX / 2
        )}px)`;
      }
      blobAt.x = pos.current.x;
      blobAt.y = pos.current.y;
      blobAt.r = R;

      // ---- draw --------------------------------------------------------
      g.clearRect(0, 0, BOX, BOX);
      const cx = BOX / 2;
      const cy = BOX / 2;

      const core = g.createRadialGradient(cx, cy, 0, cx, cy, R * 1.5);
      core.addColorStop(0, `${hue}${Math.round(38 + glow * 90).toString(16).padStart(2, "0")}`);
      core.addColorStop(1, `${hue}00`);
      g.fillStyle = core;
      g.fillRect(0, 0, BOX, BOX);

      const cyaw = Math.cos(yaw), syaw = Math.sin(yaw);
      const ctil = Math.cos(tilt), stil = Math.sin(tilt);
      const rr = R * (1 + live * 0.1);

      for (let i = 0; i < N; i++) {
        const x0 = pts[i * 3], y0 = pts[i * 3 + 1], z0 = pts[i * 3 + 2];
        const ph = phase[i];
        const n =
          Math.sin(x0 * 1.15 + t * 0.55) * Math.cos(y0 * 0.95 - t * 0.42) * 0.72 +
          Math.sin(z0 * 2.7 - t * 1.15 + ph) * 0.28;
        const rad = jitter[i] * (1 + amp * n);
        const x = x0 * rad, y = y0 * rad, z = z0 * rad;
        const X = x * cyaw + z * syaw;
        const Zr = -x * syaw + z * cyaw;
        const Y = y * ctil - Zr * stil;
        const Z = y * stil + Zr * ctil;
        const p = DEPTH / (DEPTH - Z);
        sx[i] = cx + X * rr * p;
        sy[i] = cy + Y * rr * p;
        ss[i] = mood.size * p * grain[i];
        depth[i] = Z;
      }
      // Skip full depth sort when nearly idle — order barely matters for a calm shell.
      if (!isIdle || idleFrames % 8 === 0) {
        order.sort((a, b) => depth[a] - depth[b]);
      }

      const img = sprite(hue);
      g.globalCompositeOperation = "lighter";
      for (let j = 0; j < N; j++) {
        const i = order[j];
        const near = (depth[i] + 1) / 2;
        g.globalAlpha = 0.1 + near * near * 0.8;
        const s = ss[i] * (1.6 + near * 2.2);
        g.drawImage(img, sx[i] - s / 2, sy[i] - s / 2, s, s);
      }
      g.globalAlpha = 1;
      g.globalCompositeOperation = "source-over";

      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  // ---- drag, and click that is not a drag ------------------------------
  useEffect(() => {
    const node = hit.current;
    if (!node) return undefined;
    let from = null;
    const down = (e) => {
      node.setPointerCapture(e.pointerId);
      from = { x: e.clientX, y: e.clientY, at: performance.now(), moved: false };
      drag.current = { x: pos.current.x, y: pos.current.y };
    };
    const move = (e) => {
      if (!from) return;
      const dx = e.clientX - from.x;
      const dy = e.clientY - from.y;
      if (Math.abs(dx) + Math.abs(dy) > 5) from.moved = true;
      drag.current = {
        x: clamp(pos.current.x + dx, BOX / 2 + MARGIN, innerWidth - BOX / 2 - MARGIN),
        y: clamp(pos.current.y + dy, BOX / 2 + MARGIN, innerHeight - BOX / 2 - MARGIN),
      };
      from.x = e.clientX;
      from.y = e.clientY;
    };
    const up = () => {
      if (!from) return;
      const tap = !from.moved && performance.now() - from.at < 500;
      if (drag.current && from.moved) {
        park.current = { ...drag.current };
        placed.current = true;
        try {
          localStorage.setItem("sr.blob", JSON.stringify(park.current));
        } catch { /* private mode: it just will not remember */ }
      }
      drag.current = null;
      from = null;
      if (tap) {
        dismissTip();
        onToggle?.();
        // Kill browser focus chrome on the hit button — it painted as a flat disc.
        try { node.blur(); } catch { /* */ }
      }
    };
    node.addEventListener("pointerdown", down);
    node.addEventListener("pointermove", move);
    node.addEventListener("pointerup", up);
    node.addEventListener("pointercancel", up);
    return () => {
      node.removeEventListener("pointerdown", down);
      node.removeEventListener("pointermove", move);
      node.removeEventListener("pointerup", up);
      node.removeEventListener("pointercancel", up);
    };
  }, [onToggle]);

  useEffect(() => {
    if (open || listening) dismissTip();
  }, [open, listening]);

  // Auto-clear the tip so it cannot sit on the board for a whole demo.
  useEffect(() => {
    if (!tip) return undefined;
    const t = setTimeout(dismissTip, 10000);
    return () => clearTimeout(t);
  }, [tip]);

  // One-time: forget a corner park that landed on the Evidence column.
  useEffect(() => {
    if (!placed.current) return;
    const ev = document.querySelector(".ev");
    if (!ev) return;
    const b = ev.getBoundingClientRect();
    if (
      park.current.x > b.left - R &&
      park.current.x < b.right + R &&
      park.current.y > b.top - R &&
      park.current.y < b.bottom + R
    ) {
      placed.current = false;
      park.current = defaultPark();
      try { localStorage.removeItem("sr.blob"); } catch { /* */ }
    }
  }, []);

  // Keep it on screen when the window resizes under it. A position the
  // dispatcher chose is theirs and only ever gets clamped; one nobody chose
  // goes back to its corner, instead of being left stranded mid-board by a
  // window that shrank and grew again.
  useEffect(() => {
    const fit = () => {
      park.current = placed.current
        ? {
            x: clamp(park.current.x, BOX / 2 + MARGIN, innerWidth - BOX / 2 - MARGIN),
            y: clamp(park.current.y, BOX / 2 + MARGIN, innerHeight - BOX / 2 - MARGIN),
          }
        : defaultPark();
    };
    addEventListener("resize", fit);
    return () => removeEventListener("resize", fit);
  }, []);

  const showTip = tip && !open && !listening;

  return (
    <div
      className={`blob${open ? " open" : ""}${listening ? " live" : ""}`}
      ref={wrap}
      style={{ width: BOX, height: BOX }}
    >
      <canvas ref={cv} width={BOX} height={BOX} aria-hidden="true" />
      {showTip && (
        <button
          type="button"
          className="blob-tip"
          onClick={() => {
            dismissTip();
            if (onOpenAsk) onOpenAsk();
            else onToggle?.();
          }}
        >
          <b>Talk to the board</b>
          Tap the orb to speak, or Ask to type.
        </button>
      )}
      <button
        ref={hit}
        className="blob-hit"
        // The tap is handled on pointerup so a drag that ends over the button
        // does not also count as a press. Keyboard still needs its own way in.
        onClick={(e) => e.preventDefault()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            dismissTip();
            onToggle?.();
            try { e.currentTarget.blur(); } catch { /* */ }
          }
        }}
        title={
          hint ||
          (listening
            ? "Listening — tap to stop · drag to move"
            : "Tap to talk · Ask in the header to type")
        }
        aria-label={listening ? "Stop listening" : "Talk to the briefing officer"}
        aria-pressed={!!listening}
      />
    </div>
  );
}
