import React, { useCallback, useEffect, useRef, useState } from "react";
import { C, ROUTE_COLORS } from "./palette.js";

const DECK_KEY = "sthira.timelineHeight";
const DECK_MIN = 96;
const DECK_MAX = 520;
const DECK_DEFAULT = 190;

const SHIFT_START = 6 * 60;
const SHIFT_END = 22 * 60;
const SPAN = SHIFT_END - SHIFT_START;

const pct = (min) => ((min - SHIFT_START) / SPAN) * 100;

export const hhmm = (min) =>
  min == null
    ? "--:--"
    : `${String(Math.floor(min / 60) % 24).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;

/**
 * The shift timeline: one lane per vehicle across a 06:00-22:00 axis, with
 * active municipal curfews drawn as hazard bands straight through every lane.
 *
 * This is the one view where the product's argument is visible rather than
 * asserted — you can see the stops sitting outside the red band, and watch
 * them move when the plan is re-optimised.
 */
export default function Timeline({
  routes, vehicles, overlays, selected, onSelect, shipments, nowMin,
}) {
  const [wallNow, setWallNow] = useState(nowMinutes);
  const [open, setOpen] = useState(true);
  // A fleet of five fits in the default deck; a fleet of twenty does not, so
  // the deck is draggable and remembers where it was left.
  const saved = Number(localStorage.getItem(DECK_KEY));
  const [deck, setDeck] = useState(() =>
    saved >= DECK_MIN && saved <= DECK_MAX ? saved : DECK_DEFAULT
  );
  // Until someone drags it, the deck is only as tall as the lanes need — an
  // empty 190px band under two trucks is wasted map. Once it has been sized by
  // hand, that height is honoured exactly, empty space included, because that
  // is what dragging a panel edge is supposed to mean.
  const [sized, setSized] = useState(saved >= DECK_MIN && saved <= DECK_MAX);
  const drag = useRef(null);

  const clamp = (h) => Math.max(DECK_MIN, Math.min(DECK_MAX, h));

  const commit = (h) => {
    localStorage.setItem(DECK_KEY, String(h));
    return h;
  };

  // Pointer events with capture: one code path for mouse, touch and pen, and
  // the drag keeps tracking even when the cursor outruns the 7px handle.
  const onPointerDown = (e) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture?.(e.pointerId);
    drag.current = { startY: e.clientY, startHeight: deck };
    setSized(true);
    document.body.classList.add("resizing-v");
  };

  const onPointerMove = (e) => {
    if (!drag.current) return;
    // Dragging up makes the deck taller, which is the direction the edge itself
    // moves — anything else feels inverted.
    setDeck(clamp(drag.current.startHeight + (drag.current.startY - e.clientY)));
  };

  const onPointerUp = (e) => {
    if (!drag.current) return;
    drag.current = null;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    document.body.classList.remove("resizing-v");
    setDeck(commit);
  };

  // Keyboard equivalent, because a drag handle alone is not accessible.
  const nudge = useCallback((delta) => {
    setSized(true);
    setDeck((h) => commit(clamp(h + delta)));
  }, []);

  const resetDeck = () => {
    localStorage.removeItem(DECK_KEY);
    setSized(false);
    setDeck(DECK_DEFAULT);
  };

  // The shift clock owns "now" whenever a simulated day is loaded. Drawing the
  // wall clock instead put the marker at 01:00 while the fleet was at 09:00,
  // which made every ETA on the lane look wrong.
  const now = nowMin ?? wallNow;

  useEffect(() => {
    if (nowMin != null) return undefined;
    const t = setInterval(() => setWallNow(nowMinutes()), 30000);
    return () => clearInterval(t);
  }, [nowMin]);

  const bans = (overlays || []).filter((o) => o.active);
  const shipmentById = new Map((shipments || []).map((s) => [s.id, s]));
  const vehicleById = new Map((vehicles || []).map((v) => [v.id, v]));
  const showNow = now >= SHIFT_START && now <= SHIFT_END;

  // A curfew is geographic as well as temporal: it only binds stops that sit
  // inside the zone. Without this the band would imply every stop under it is
  // a violation, which is false and would be read as a bug.
  const restricted = new Set(
    (shipments || [])
      .filter((s) => bans.some((b) => within(s, b)))
      .map((s) => s.id)
  );

  // Colour is vehicle identity, and it is assigned by position in the drawn
  // route list — the same rule the map uses, so a truck is the same colour in
  // both places. Look it up per vehicle rather than per lane, because the lane
  // order below is the whole fleet and the map's order is only the working part.
  const drawn = (routes || []).filter((r) => (r.stops || []).length > 1);
  const colorOf = new Map(
    drawn.map((r, i) => [r.vehicle_id, ROUTE_COLORS[i % ROUTE_COLORS.length]])
  );

  // Every truck gets a lane, including the ones with nothing to do. A fleet of
  // five that shows one row reads as a fleet of one, and an idle or broken
  // truck is exactly the thing a dispatcher needs to see.
  const routeOf = new Map(drawn.map((r) => [r.vehicle_id, r]));
  const lanes = (vehicles || [])
    .map((v) => ({ route: routeOf.get(v.id), vehicle: v }))
    .sort((a, b) => (b.route ? 1 : 0) - (a.route ? 1 : 0));
  const working = drawn.length;

  const hours = [];
  for (let h = 6; h <= 22; h += 2) hours.push(h);

  return (
    <section className={`timeline${open ? "" : " collapsed"}`} aria-label="Shift timeline" data-hl="timeline">
      {open && (
        <div
          className="tl-grip"
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize the shift timeline"
          aria-valuenow={sized ? deck : DECK_DEFAULT}
          aria-valuemin={DECK_MIN}
          aria-valuemax={DECK_MAX}
          tabIndex={0}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onDoubleClick={resetDeck}
          onKeyDown={(e) => {
            if (e.key === "ArrowUp") nudge(24);
            else if (e.key === "ArrowDown") nudge(-24);
            else return;
            e.preventDefault();
          }}
          title="Drag to resize · double-click to reset"
        />
      )}
      <div className="tl-head">
        <button className="tl-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          <span className="sect-caret">▶</span>
          Shift timeline · the playhead is now · each row is one truck
          {lanes.length > 0 &&
            ` · ${working} of ${lanes.length} truck${lanes.length === 1 ? "" : "s"} carrying work`}
        </button>
        <div className="tl-key">
          <i><span className="swatch swatch-ban" /> curfew window</i>
          <i><span className="swatch swatch-zoned" /> stop inside the zone</i>
          <i><span className="swatch" style={{ background: C.sage }} /> on time</i>
          <i><span className="swatch" style={{ background: C.vermilion }} /> late</i>
          <i><span className="swatch" style={{ background: C.text }} /> delivered</i>
        </div>
      </div>

      {open && (
      <div className="tl-scale">
        {hours.map((h) => (
          <span key={h} className="tl-tick" style={{ left: `${pct(h * 60)}%` }}>
            {String(h).padStart(2, "0")}
          </span>
        ))}
      </div>
      )}

      {open && (
        lanes.length === 0 ? (
          <div className="tl-empty">
            Start the day — each row becomes one truck. The playhead is the shift clock.
          </div>
        ) : (
          <div
            className="tl-lanes"
            style={sized ? { height: deck } : { maxHeight: DECK_DEFAULT }}
          >
            {lanes.map(({ route, vehicle }, laneIdx) => {
              const stops = [...(route?.stops || [])].sort((a, b) => a.seq - b.seq);
              const timed = stops.filter((s) => s.eta_min != null);
              const idle = !route;
              return (
                <div className={`tl-lane${idle ? " idle" : ""}`} key={vehicle?.id ?? route?.id}>
                  {/* Same colour as this vehicle's line, truck chip and stops
                      on the map: one truck, one colour, everywhere. */}
                    <span
                      className={`tl-name${vehicle?.status === "down" ? " down" : ""}`}
                      data-hl={vehicle?.code ? `vehicle:${vehicle.code}` : undefined}
                    >
                    <i
                      className="tl-chip"
                      style={{ background: colorOf.get(vehicle?.id) || "var(--line)" }}
                    />
                    {vehicle?.code || `V${route?.vehicle_id}`}
                  </span>
                  <div className="tl-track">
                    {idle && (
                      <span className="tl-idle">
                        {vehicle?.status === "down"
                          ? "broken down — its freight was re-planned onto other trucks"
                          : vehicle?.status === "en_route"
                            ? "running back to its depot"
                            : "no work assigned — spare capacity"}
                      </span>
                    )}
                    {bans.map((b) => (
                      <div
                        key={b.id}
                        className={`tl-ban${b.kind === "closure" ? " flood" : ""}`}
                        style={{
                          left: `${pct(b.ban_start_min)}%`,
                          width: `${((b.ban_end_min - b.ban_start_min) / SPAN) * 100}%`,
                        }}
                      >
                        {laneIdx === 0 && (
                          <span className="tl-ban-label">
                            {b.kind === "closure" ? "flooded" : "no entry"}
                          </span>
                        )}
                      </div>
                    ))}

                    {showNow && (
                      <div className="tl-now" style={{ left: `${pct(now)}%` }}>
                        {laneIdx === 0 && (
                          <span className="tl-now-label">{hhmm(now)}</span>
                        )}
                      </div>
                    )}

                    {timed.slice(0, -1).map((s, i) => {
                      const left = pct(s.eta_min);
                      return (
                        <div
                          key={`leg-${s.id}`}
                          className="tl-drive"
                          style={{
                            left: `${left}%`,
                            width: `${Math.max(0, pct(timed[i + 1].eta_min) - left)}%`,
                          }}
                        />
                      );
                    })}

                    {timed.map((s) => {
                      const ship = shipmentById.get(s.shipment_id);
                      const inZone = restricted.has(s.shipment_id);
                      const cls = [
                        "tl-stop",
                        s.kind === "depot" && "depot",
                        inZone && "zoned",
                        s.late_min > 0 && "late",
                        s.status === "completed" && "done",
                        s.shipment_id && selected === s.shipment_id && "sel",
                      ].filter(Boolean).join(" ");
                      return (
                        <button
                          key={s.id}
                          className={cls}
                          style={{ left: `${pct(s.eta_min)}%` }}
                          onClick={() => onSelect?.(s.shipment_id)}
                          title={
                            s.kind === "depot"
                              ? `Depot · ${hhmm(s.eta_min)}`
                              : `${ship?.code || `#${s.shipment_id}`} · ${ship?.customer_name || ""}\n` +
                                `ETA ${hhmm(s.eta_min)}` +
                                (ship ? ` · window ${hhmm(ship.tw_start_min)}–${hhmm(ship.tw_end_min)}` : "") +
                                (inZone ? "\nInside a restricted zone — scheduled around the curfew" : "") +
                                (s.late_min > 0 ? `\nLATE by ${s.late_min} min` : "")
                          }
                          aria-label={`${vehicle?.code || "vehicle"} stop ${s.seq}, ETA ${hhmm(s.eta_min)}`}
                        />
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )
      )}
    </section>
  );
}

function nowMinutes() {
  const d = new Date();
  return d.getHours() * 60 + d.getMinutes();
}

/** Is this shipment inside a curfew zone? */
function within(shipment, zone) {
  const r = 6371;
  const toRad = (x) => (x * Math.PI) / 180;
  const dLat = toRad(zone.center_lat - shipment.lat);
  const dLon = toRad(zone.center_lon - shipment.lon);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(shipment.lat)) * Math.cos(toRad(zone.center_lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(h)) <= zone.radius_km;
}
