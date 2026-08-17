import React, { useEffect, useRef, useState } from "react";
import { blobAt } from "./Blob.jsx";
import { focusRect, resolveTarget } from "./guide.js";

/**
 * The pointing layer.
 *
 * A 2px outline pulsed three times was not an explanation — on a 1600px board
 * a judge never found what the officer meant. This dims the room, cuts a hole
 * around the thing being talked about, walks that hole from one target to the
 * next, and hangs the sentence off it. It never swallows clicks: the board
 * stays usable while the officer talks over it.
 *
 * Nothing here re-renders per frame. The hole is six numbers on five SVG
 * elements, and it moves by writing those numbers straight onto the nodes.
 * Routing them through state meant a full React render of the overlay sixty
 * times a second, on top of a simulator tick and a map redraw, for a rectangle
 * that had shifted four pixels. React is only asked to do something when the
 * sentence itself changes.
 */

const HOLD_MIN = 2600;
const HOLD_MAX = 9000;
const PAD = 8;
const MIN_HOLE = 52; // smallest hole that still reads as a highlight

const holdFor = (note) =>
  Math.min(HOLD_MAX, Math.max(HOLD_MIN, 1800 + (note || "").length * 48));

const reduced = () =>
  typeof matchMedia === "function" &&
  matchMedia("(prefers-reduced-motion: reduce)").matches;

export default function Guide({ steps, view, onDesk, onIdle, onFocusCode }) {
  const [i, setI] = useState(0);
  // Only what a person reads: the caption, and where to hang it. The geometry
  // lives on the nodes.
  const [cap, setCap] = useState(null);
  const raf = useRef(0);
  const timer = useRef(0);
  const seat = useRef(null); // the smoothed rect we are drawing
  const hole = useRef(null);
  const ring = useRef(null);
  const run = useRef(null);
  const pop = useRef(null);
  const link = useRef(null);
  const note = useRef(null);
  const side = useRef(null); // last caption placement, so it only flips on a real change
  // Steps grow while the officer is still talking. Reading them through a ref
  // keeps a late arrival from restarting the hold on the step being shown —
  // three highlights in one breath used to stall the walk on the first one.
  const all = useRef(steps);
  all.current = steps;

  const step = steps?.[i];

  useEffect(() => {
    if (!step) {
      focusRect.current = null;
      setCap(null);
      seat.current = null;
      if (all.current?.length) onIdle?.();
      return undefined;
    }

    let dead = false;
    const spec = resolveTarget(step.target);

    // The target may live on another desk. Switch, then wait for it to mount
    // rather than assuming one frame is enough on a cold render.
    // "any" means it is an overlay that sits on top of whatever desk is open —
    // switching desks would close the very thing being pointed at.
    if (spec?.desk && spec.desk !== "any" && spec.desk !== view) onDesk?.(spec.desk);
    if (spec?.code) onFocusCode?.(spec);

    const start = performance.now();
    let live = spec;

    const paint = (r) => {
      for (const el of [hole.current, ring.current, run.current, pop.current]) {
        if (!el) continue;
        el.setAttribute("x", r.x);
        el.setAttribute("y", r.y);
        el.setAttribute("width", r.w);
        el.setAttribute("height", r.h);
      }
      // The line from the officer to the thing it is naming.
      const line = link.current;
      if (line && blobAt.r) {
        // Nearest point on the hole's border, so the line lands on the edge of
        // the thing rather than crossing it.
        const ex = Math.max(r.x - 3, Math.min(blobAt.x, r.x + r.w + 3));
        const ey = Math.max(r.y - 3, Math.min(blobAt.y, r.y + r.h + 3));
        const dx = ex - blobAt.x;
        const dy = ey - blobAt.y;
        const len = Math.hypot(dx, dy) || 1;
        const gap = blobAt.r * 0.62;
        line.setAttribute("x1", blobAt.x + (dx / len) * gap);
        line.setAttribute("y1", blobAt.y + (dy / len) * gap);
        line.setAttribute("x2", ex);
        line.setAttribute("y2", ey);
        line.style.opacity = len > gap + 24 ? "1" : "0";
      }
      const n = note.current;
      if (n) {
        const below = r.y + r.h + 96 < innerHeight;
        n.style.left = `${Math.min(Math.max(8, r.x + r.w / 2 - 170), innerWidth - 348)}px`;
        n.style.top = `${below ? r.y + r.h + 10 : Math.max(8, r.y - 86)}px`;
        if (side.current !== below) {
          n.classList.toggle("above", !below);
          side.current = below;
        }
      }
    };

    const track = () => {
      if (dead) return;
      // Re-query only when the node has gone: a desk re-render replaces it, a
      // scroll does not, and querySelector sixty times a second is waste.
      if (!live?.el?.isConnected) live = resolveTarget(step.target);
      const el = live?.el;
      if (!el) {
        // Give a just-switched desk a moment; then give up on this step so a
        // stale target cannot wedge the queue.
        if (performance.now() - start > 1800) return advance();
        raf.current = requestAnimationFrame(track);
        return;
      }
      const r = el.getBoundingClientRect();
      if (r.width < 1 && r.height < 1) {
        if (performance.now() - start > 1800) return advance();
        raf.current = requestAnimationFrame(track);
        return;
      }

      // A truck chip on the map is ten pixels across; a ten-pixel hole reads as
      // a speck, not as "look here". Small targets get a halo instead.
      const w = Math.max(r.width + PAD * 2, MIN_HOLE);
      const h = Math.max(r.height + PAD * 2, MIN_HOLE);
      const want = {
        x: r.left + r.width / 2 - w / 2,
        y: r.top + r.height / 2 - h / 2,
        w,
        h,
      };
      // Ease the hole across the screen instead of teleporting it: the travel
      // is what tells you where you are being taken.
      // A hidden tab throttles rAF, which would leave the hole frozen halfway
      // between two targets when the dispatcher comes back. Snap instead.
      const s = seat.current;
      const k = !s || reduced() || document.hidden ? 1 : 0.22;
      seat.current = s
        ? {
            x: s.x + (want.x - s.x) * k,
            y: s.y + (want.y - s.y) * k,
            w: s.w + (want.w - s.w) * k,
            h: s.h + (want.h - s.h) * k,
          }
        : want;

      const cur = seat.current;
      focusRect.current = { ...cur, note: step.note || "" };
      paint(cur);

      if (performance.now() - start < 250) {
        const vh = innerHeight;
        if (r.top < 40 || r.bottom > vh - 40) {
          el.scrollIntoView({
            block: "center",
            behavior: reduced() ? "auto" : "smooth",
          });
        }
      }
      raf.current = requestAnimationFrame(track);
    };

    const advance = () => {
      if (dead) return;
      setI((n) => n + 1);
    };

    setCap({
      label: spec?.label || step.target,
      note: step.note || "",
      n: i + 1,
      of: all.current.length,
    });
    // Measure and paint on this tick, not the next frame: the hole should be
    // around the thing before the sentence about it is finished.
    track();
    timer.current = setTimeout(advance, step.ms || holdFor(step.note));

    return () => {
      dead = true;
      cancelAnimationFrame(raf.current);
      clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, i, view]);

  if (!cap) return null;

  const vw = innerWidth;
  const vh = innerHeight;

  return (
    <div className="guide" aria-live="polite">
      <svg className="guide-veil" width={vw} height={vh}>
        <defs>
          <mask id="guide-cut">
            <rect x="0" y="0" width={vw} height={vh} fill="#fff" />
            <rect ref={hole} rx="8" fill="#000" />
          </mask>
        </defs>
        <rect
          x="0" y="0" width={vw} height={vh}
          className="guide-scrim" mask="url(#guide-cut)"
        />
        <rect ref={ring} rx="8" className="guide-ring" />
        <rect ref={run} rx="8" className="guide-run" />
        {/* Keyed on the step so the arrival pulse restarts when the hole lands
            somewhere new, and never mid-travel. */}
        <rect key={`${cap.n}-pop`} ref={pop} rx="8" className="guide-pop" />
        <line ref={link} className="guide-link" />
      </svg>

      <div className="guide-note" ref={note}>
        <b>{cap.label}</b>
        {cap.note && <span>{cap.note}</span>}
        {cap.of > 1 && <i className="guide-step">{cap.n} of {cap.of}</i>}
      </div>
    </div>
  );
}
