import React, { useEffect, useMemo, useRef, useState } from "react";

const VIEWS = {
  door: { yaw: -32, pitch: -26, label: "From the door" },
  top: { yaw: 0, pitch: -78, label: "From above" },
  side: { yaw: -82, pitch: -14, label: "From the kerb" },
};

/**
 * The load plan, drawn as the deck actually looks.
 *
 * The argument of this screen is LIFO + axle balance, shown rather than
 * asserted: colour is delivery order, the next drop slides out of the rear
 * door, and the CoG plane sits on the deck it claims to measure.
 */
export default function LoadPlan({ plan, vehicleCode, camera, onClose }) {
  const start = VIEWS[camera] ? camera : "door";
  const [yaw, setYaw] = useState(VIEWS[start].yaw);
  const [pitch, setPitch] = useState(VIEWS[start].pitch);
  const [view, setView] = useState(start);
  const [step, setStep] = useState(0);
  const [hover, setHover] = useState(null);
  const [dragging, setDragging] = useState(false);
  const drag = useRef(null);

  const deck = plan?.container || { l: 400, w: 200, h: 200 };
  const placements = plan?.placements || [];
  const drops = useMemo(
    () => [...new Set(placements.map((p) => p.seq))].sort((a, b) => a - b),
    [placements]
  );
  const scale = 300 / Math.max(deck.l, 1);
  const L = deck.l * scale;
  const W = deck.w * scale;
  const H = deck.h * scale;

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose?.();
      if (e.key === "ArrowRight") setStep((s) => Math.min(drops.length, s + 1));
      if (e.key === "ArrowLeft") setStep((s) => Math.max(0, s - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, drops.length]);

  const onDown = (e) => {
    if (e.button != null && e.button !== 0) return;
    drag.current = { x: e.clientX, y: e.clientY, yaw, pitch };
    setDragging(true);
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };
  const onMove = (e) => {
    if (!drag.current) return;
    setView("");
    setYaw(drag.current.yaw + (e.clientX - drag.current.x) * 0.4);
    setPitch(
      Math.max(-85, Math.min(8, drag.current.pitch - (e.clientY - drag.current.y) * 0.3))
    );
  };
  const onUp = () => {
    drag.current = null;
    setDragging(false);
  };

  const adopt = (id) => {
    const v = VIEWS[id];
    setView(id);
    setYaw(v.yaw);
    setPitch(v.pitch);
  };

  const delivered = drops.slice(0, step);
  const nextDrop = drops[step];

  return (
    <div className="lp-backdrop" onClick={(e) => e.target === e.currentTarget && onClose?.()}>
      <section className="lp" role="dialog" aria-label={`Load plan for ${vehicleCode}`}>
        <header className="lp-head">
          <span className="lp-title">Load plan</span>
          <span className="lp-veh">{vehicleCode}</span>
          <span className={`chip chip-${plan.feasible ? "completed" : "failed"}`}>
            {plan.feasible ? "loadable" : "not loadable"}
          </span>
          <span className={`chip ${plan.lifo_ok ? "chip-completed" : "chip-failed"}`}>
            LIFO {plan.lifo_ok ? "verified" : "violated"}
          </span>
          <span className={`chip ${plan.cog_ok ? "chip-completed" : "chip-failed"}`}>
            CoG {plan.cog_ok ? "in band" : "off axle"}
          </span>
          <button className="lp-close" onClick={onClose} aria-label="Close load plan">✕</button>
        </header>

        <div className="lp-body">
          <div
            className="lp-stage"
            onPointerDown={onDown}
            onPointerMove={onMove}
            onPointerUp={onUp}
            onPointerCancel={onUp}
            title="Drag to rotate"
          >
            <p className="lp-nowline">
              {step >= drops.length
                ? "Deck empty — every drop came out the door, nothing rehandled."
                : nextDrop != null
                ? `Drop ${nextDrop} comes out the rear door next. Warm cartons leave first.`
                : "Nothing on this deck."}
            </p>
            <div className="lp-views" onPointerDown={(e) => e.stopPropagation()}>
              {Object.entries(VIEWS).map(([id, v]) => (
                <button
                  key={id}
                  type="button"
                  className={`lp-view${view === id ? " on" : ""}`}
                  onClick={() => adopt(id)}
                >
                  {v.label}
                </button>
              ))}
            </div>
            <div className="lp-legend" onPointerDown={(e) => e.stopPropagation()}>
              <i><span className="swatch" style={{ background: hue(drops[0] || 1, drops.length || 1) }} /> first out</i>
              <i><span className="swatch" style={{ background: hue(drops[drops.length - 1] || 1, drops.length || 1) }} /> last out</i>
              <i><span className="swatch swatch-cog" /> CoG</i>
              <i><span className="swatch swatch-fragile" /> fragile</i>
            </div>
            <div className={`lp-scene${dragging ? " dragging" : ""}`}
                 style={{ transform: `rotateX(${pitch}deg) rotateY(${yaw}deg)` }}>
              <Truck L={L} W={W} H={H} />
              <CogPlane L={L} W={W} H={H} pct={plan.cog_x_pct} ok={plan.cog_ok} />
              {placements.map((p) => {
                const gone = delivered.includes(p.seq);
                const isNext = p.seq === nextDrop;
                return (
                  <Carton
                    key={p.code}
                    p={p}
                    deck={deck}
                    scale={scale}
                    dropCount={drops.length}
                    gone={gone}
                    highlight={isNext || hover === p.code}
                    dim={hover != null && hover !== p.code}
                    onEnter={() => setHover(p.code)}
                    onLeave={() => setHover(null)}
                  />
                );
              })}
            </div>
          </div>

          <aside className="lp-side">
            <dl className="readout lp-readout">
              <div><dt>Deck</dt><dd>{deck.l}×{deck.w}×{deck.h}<small>cm</small></dd></div>
              <div><dt>Volume used</dt><dd>{plan.volume_utilization_pct}<small>%</small></dd></div>
              <div><dt>Floor used</dt><dd>{plan.floor_utilization_pct}<small>%</small></dd></div>
              <div>
                <dt>CoG from cab</dt>
                <dd className={plan.cog_ok ? "g-good" : "g-bad"}>
                  {plan.cog_x_pct}<small>%</small>
                </dd>
              </div>
            </dl>

            <div className="lp-cog">
              <div className="lp-cog-track">
                <span className="lp-cog-safe" />
                <span className="lp-cog-pin" style={{ left: `${plan.cog_x_pct}%` }} />
              </div>
              <div className="lp-cog-labels">
                <span>cab</span><span>axle-safe 30–70%</span><span>door</span>
              </div>
            </div>

            <div className="lp-stepper">
              <div className="panel-title">Unload sequence</div>
              <div className="lp-controls">
                <button className="lp-btn" onClick={() => setStep(0)} disabled={step === 0}>
                  Reset
                </button>
                <button
                  className="lp-btn"
                  onClick={() => setStep((s) => Math.max(0, s - 1))}
                  disabled={step === 0}
                >
                  ← Back
                </button>
                <button
                  className="lp-btn lp-btn-go"
                  onClick={() => setStep((s) => Math.min(drops.length, s + 1))}
                  disabled={step >= drops.length}
                >
                  Unload drop {nextDrop ?? "—"} →
                </button>
              </div>
              <p className="lp-hint">
                {step === 0
                  ? "Every drop comes out of the door in order with nothing on top of it or in front of it."
                  : step >= drops.length
                  ? "Deck empty — the whole route unloaded in sequence, nothing rehandled."
                  : `${step} of ${drops.length} drops out the door. Nothing was moved to reach them.`}
              </p>
            </div>

            <div className="panel-title">Loading order — first in, last out</div>
            <ol className="lp-list">
              {(plan.load_order || []).map((code, i) => {
                const p = placements.find((x) => x.code === code);
                if (!p) return null;
                const gone = delivered.includes(p.seq);
                return (
                  <li
                    key={code}
                    className={`lp-row${gone ? " gone" : ""}${hover === code ? " on" : ""}`}
                    onMouseEnter={() => setHover(code)}
                    onMouseLeave={() => setHover(null)}
                  >
                    <span className="lp-row-n">{i + 1}</span>
                    <span className="lp-swatch" style={{ background: hue(p.seq, drops.length) }} />
                    <span className="lp-row-code">{code}</span>
                    <span className="lp-row-meta">
                      drop {p.seq} · {p.l}×{p.w}×{p.h} · {p.weight_kg}kg
                      {p.fragile && <b className="lp-fragile"> fragile</b>}
                    </span>
                  </li>
                );
              })}
            </ol>

            {(plan.unplaced || []).length > 0 && (
              <div className="panel panel-block">
                <div className="panel-title">Would not fit — {plan.unplaced.length}</div>
                {plan.unplaced.map((u) => (
                  <div key={u.code} className="unserved-row">
                    <span className="unserved-code">{u.code}</span> — {u.reason}
                  </div>
                ))}
              </div>
            )}

            {(plan.notes || []).map((n) => (
              <p key={n} className="prose lp-note">{n}</p>
            ))}
          </aside>
        </div>
      </section>
    </div>
  );
}

/**
 * The deck for the dock desk, drawn one layer at a time.
 *
 * This used to be a single top-down plan, and it was lying. Looking straight
 * down at a stacked deck, a carton sitting on another one covers it exactly —
 * on a real eight-drop load three cartons were painted over three others and
 * simply could not be seen. A loader reading it would have walked to the truck
 * expecting five boxes and found eight.
 *
 * Warehouses do not draw a stacked pack as one picture; they draw a course per
 * level. So does this. Every carton is visible in exactly one panel, the panels
 * read floor-first the way the truck is loaded, and each is captioned with the
 * height it sits at so the picture stays checkable against the real deck.
 */
export function DeckMap({ plan }) {
  const deck = plan?.container;
  const placements = plan?.placements || [];
  if (!deck?.l || !placements.length) return null;
  const n = Math.max(...placements.map((p) => p.seq), 1);

  const byLevel = new Map();
  for (const p of placements) {
    const z = Math.round(p.z || 0);
    if (!byLevel.has(z)) byLevel.set(z, []);
    byLevel.get(z).push(p);
  }
  const levels = [...byLevel.keys()].sort((a, b) => a - b);

  return (
    <div className="deckmap" aria-label="Deck, one panel per level">
      <div className="deckmap-levels">
        {levels.map((z, i) => (
          <figure className="deckmap-level" key={z}>
            <figcaption>
              {i === 0 ? "on the floor" : `at ${z} cm`}
              <span>{byLevel.get(z).length}</span>
            </figcaption>
            <div
              className="deckmap-bed"
              style={{ aspectRatio: `${deck.l} / ${Math.max(deck.w, 1)}` }}
            >
              {/* The axle window belongs to the whole deck, so it is drawn on
                  the floor panel only — repeating it on every level made it
                  read as a property of each course. */}
              {i === 0 && <div className="deckmap-safe" />}
              {/* The one thing splitting the deck into courses loses is what a
                  carton is standing on. The course below comes back as an
                  outline, so a box floating in an empty panel still shows its
                  footing. */}
              {i > 0 &&
                byLevel.get(levels[i - 1]).map((p) => (
                  <b
                    key={`under-${p.code}`}
                    className="deckmap-under"
                    style={{
                      left: `${(p.x / deck.l) * 100}%`,
                      top: `${(p.y / Math.max(deck.w, 1)) * 100}%`,
                      width: `${(p.l / deck.l) * 100}%`,
                      height: `${(p.w / Math.max(deck.w, 1)) * 100}%`,
                    }}
                  />
                ))}
              {i === 0 && plan.cog_x_pct != null && (
                <i
                  className={`deckmap-cog${plan.cog_ok === false ? " bad" : ""}`}
                  style={{ left: `${plan.cog_x_pct}%` }}
                />
              )}
              {byLevel.get(z).map((p) => {
                const w = (p.l / deck.l) * 100;
                const h = (p.w / Math.max(deck.w, 1)) * 100;
                return (
                  <b
                    key={p.code}
                    className={`deckmap-box${p.fragile ? " fragile" : ""}`}
                    style={{
                      left: `${(p.x / deck.l) * 100}%`,
                      top: `${(p.y / Math.max(deck.w, 1)) * 100}%`,
                      width: `${w}%`,
                      height: `${h}%`,
                      background: hue(p.seq, n),
                    }}
                    title={
                      `${p.code} · drop ${p.seq} · ${p.weight_kg ?? "?"} kg` +
                      (i ? ` · stacked at ${z} cm` : " · on the floor") +
                      (p.fragile ? " · fragile" : "")
                    }
                  >
                    {w > 14 && h > 26 && <span>{p.seq}</span>}
                  </b>
                );
              })}
            </div>
          </figure>
        ))}
      </div>
      <div className="deckmap-axis">
        <span>cab</span>
        <span className="deckmap-axis-note">
          {levels.length === 1
            ? "single course — nothing stacked"
            : `${levels.length} courses · lower number leaves first`}
        </span>
        <span>door</span>
      </div>
    </div>
  );
}

function Truck({ L, W, H }) {
  const origin = { position: "absolute", left: 0, top: 0, transformOrigin: "top left" };
  return (
    <div className="lp-deck">
      <div
        className="lp-floor"
        style={{
          ...origin,
          width: W,
          height: L,
          transform: `translate3d(${-W / 2}px, 0px, ${-L / 2}px) rotateX(90deg)`,
        }}
      >
        <div className="lp-floor-safe" />
        <div className="lp-floor-line" />
      </div>
      <div
        className="lp-bulkhead"
        style={{
          ...origin,
          width: W,
          height: H,
          transform: `translate3d(${-W / 2}px, ${-H}px, ${-L / 2}px)`,
        }}
      >
        <span>cab</span>
      </div>
      <div
        className="lp-wall lp-wall-side"
        style={{
          ...origin,
          width: L,
          height: H,
          transform: `translate3d(${-W / 2}px, ${-H}px, ${-L / 2}px) rotateY(-90deg)`,
        }}
      />
      <div
        className="lp-wall lp-wall-side"
        style={{
          ...origin,
          width: L,
          height: H,
          transform: `translate3d(${W / 2}px, ${-H}px, ${-L / 2}px) rotateY(-90deg)`,
        }}
      />
      <div
        className="lp-door"
        style={{
          ...origin,
          width: W,
          height: H,
          transform: `translate3d(${-W / 2}px, ${-H}px, ${L / 2}px)`,
        }}
      >
        <span>unload</span>
      </div>
    </div>
  );
}

/**
 * Where the weight sits, marked on the deck.
 *
 * This used to be a full-height translucent wall the width and height of the
 * container. On a lightly loaded truck it was the largest object on screen — a
 * dirty olive slab in front of the cartons it was supposed to annotate, and the
 * first thing anyone asked about. A centre of gravity is a position along the
 * deck, so it is now marked the way a position is: a bright line painted across
 * the floor, with a short fin so it still reads from directly above.
 */
function CogPlane({ L, W, H, pct, ok }) {
  if (pct == null) return null;
  const z = -L / 2 + (Number(pct) / 100) * L;
  const cls = `lp-cog-mark${ok === false ? " bad" : ""}`;
  const base = { position: "absolute", left: 0, top: 0, transformOrigin: "top left" };
  return (
    <>
      <div
        className={`${cls} floor`}
        style={{
          ...base,
          width: W,
          height: 5,
          transform: `translate3d(${-W / 2}px, 0px, ${z}px) rotateX(90deg)`,
        }}
      />
      <div
        className={`${cls} fin`}
        style={{
          ...base,
          width: W,
          height: Math.max(14, H * 0.22),
          transform: `translate3d(${-W / 2}px, ${-Math.max(14, H * 0.22)}px, ${z}px)`,
        }}
      >
        <span>CoG {pct}%</span>
      </div>
    </>
  );
}

function Carton({ p, deck, scale, dropCount, gone, highlight, dim, onEnter, onLeave }) {
  const l = p.l * scale;
  const w = p.w * scale;
  const h = p.h * scale;
  const cx = (p.y + p.w / 2) * scale - (deck.w * scale) / 2;
  const cy = -((p.z + p.h / 2) * scale);
  const cz = (p.x + p.l / 2) * scale - (deck.l * scale) / 2;
  const out = (deck.l * scale) * 0.7;
  const fill = parts(p.seq, dropCount);

  const faces = [
    { k: "f", w, h, t: `translateZ(${l / 2}px)`, lgt: 0 },
    { k: "b", w, h, t: `rotateY(180deg) translateZ(${l / 2}px)`, lgt: -18 },
    { k: "r", w: l, h, t: `rotateY(90deg) translateZ(${w / 2}px)`, lgt: -10 },
    { k: "l", w: l, h, t: `rotateY(-90deg) translateZ(${w / 2}px)`, lgt: -14 },
    { k: "t", w, h: l, t: `rotateX(90deg) translateZ(${h / 2}px)`, lgt: 8 },
    { k: "d", w, h: l, t: `rotateX(-90deg) translateZ(${h / 2}px)`, lgt: -24 },
  ];

  return (
    <div
      className={`lp-carton${gone ? " gone" : ""}${highlight ? " hot" : ""}${dim ? " dim" : ""}`}
      style={{
        transform: `translate3d(${cx}px, ${cy}px, ${cz}px)${gone ? ` translate3d(0px, 0px, ${out}px)` : ""}`,
      }}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
      title={`${p.code} · drop ${p.seq} · ${p.l}×${p.w}×${p.h} cm · ${p.weight_kg} kg${p.fragile ? " · FRAGILE" : ""}`}
    >
      {faces.map((f) => (
        <div
          key={f.k}
          className={`lp-face${p.fragile ? " fragile" : ""}`}
          style={{
            width: f.w,
            height: f.h,
            left: -f.w / 2,
            top: -f.h / 2,
            transform: f.t,
            backgroundColor: hsl(fill.h, fill.s, fill.l + f.lgt),
          }}
        >
          {f.k === "f" && <span className="lp-tag">{p.seq}</span>}
        </div>
      ))}
    </div>
  );
}

/**
 * Sequential ramp for drop order.
 *
 * This used to sweep 38°→228°, which put saturated blue, cyan and kelly green
 * on a deck that sits inside a monochrome product — the one screen that looked
 * like it came from a different application. It then swept through amber,
 * which turned the deck brown against a black ground.
 *
 * It is now a near-neutral steel ramp: the first drop is almost white because
 * it is the one at the door, and each later drop sits deeper and cooler toward
 * the cab. Vermilion and sage are deliberately not reached — on this deck they
 * already mean "fragile" and "inside the axle window", and a colour may not
 * carry two meanings on the same picture.
 */
export function hue(seq, total) {
  const p = parts(seq, total);
  return hsl(p.h, p.s, p.l);
}

function parts(seq, total) {
  const t = total > 1 ? (seq - 1) / (total - 1) : 0;
  return {
    h: Math.round(210 - t * 6),    // a hint of steel, never a saturated blue
    s: Math.round(6 + t * 16),
    l: Math.round(86 - t * 46),    // door: near white → cab: deep slate
  };
}

function hsl(h, s, l) {
  return `hsl(${h} ${s}% ${Math.max(14, Math.min(78, l))}%)`;
}
