import { useCallback, useRef, useState } from "react";

/** How much of a folded panel is left on screen — enough for its handle. */
const FOLDED_PX = 26;

/**
 * A dispatcher's side column: draggable width, foldable, and it remembers both
 * between sessions.
 *
 * The timeline deck already worked this way, so the rails now do too — same
 * pointer capture, same keyboard nudge, same double-click reset. A width is
 * only asserted once the user has actually sized it by hand; until then the
 * stylesheet's responsive defaults stay in charge, which is what keeps the
 * layout sane on a projector nobody measured beforehand.
 */
export default function useSidePanel(key, { side, min, max, def }) {
  const wKey = `sthira.${key}.w`;
  const fKey = `sthira.${key}.folded`;
  const saved = Number(localStorage.getItem(wKey));
  const valid = saved >= min && saved <= max;
  const [width, setWidth] = useState(valid ? saved : def);
  const [sized, setSized] = useState(valid);
  const [folded, setFolded] = useState(() => localStorage.getItem(fKey) === "1");
  const drag = useRef(null);

  const clamp = (w) => Math.max(min, Math.min(max, w));
  const commit = (w) => {
    localStorage.setItem(wKey, String(w));
    return w;
  };

  const fold = useCallback(
    (v) => {
      localStorage.setItem(fKey, v ? "1" : "0");
      setFolded(v);
    },
    [fKey]
  );

  // Pointer events with capture: one code path for mouse, touch and pen, and
  // the drag keeps tracking even when the cursor outruns the 7px handle.
  const onPointerDown = (e) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, w: width };
    setSized(true);
    document.body.classList.add("resizing-h");
  };

  const onPointerMove = (e) => {
    if (!drag.current) return;
    // Dragging the edge outward widens the panel, which direction that is
    // depends on which side of the screen the panel lives on.
    const grow = (e.clientX - drag.current.x) * (side === "left" ? 1 : -1);
    setWidth(clamp(drag.current.w + grow));
  };

  const onPointerUp = (e) => {
    if (!drag.current) return;
    drag.current = null;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    document.body.classList.remove("resizing-h");
    setWidth(commit);
  };

  const nudge = (d) => {
    setSized(true);
    setWidth((w) => commit(clamp(w + d)));
  };

  const reset = () => {
    localStorage.removeItem(wKey);
    setSized(false);
    setWidth(def);
  };

  return {
    folded,
    toggle: useCallback(() => fold(!folded), [fold, folded]),
    close: useCallback(() => fold(true), [fold]),
    // null means "no opinion — let the stylesheet decide".
    cssWidth: folded ? `${FOLDED_PX}px` : sized ? `${width}px` : null,
    gripProps: {
      // A left-hand panel is resized by the handle on its right edge.
      className: `grip grip-${side === "left" ? "right" : "left"}`,
      role: "separator",
      "aria-orientation": "vertical",
      "aria-label": `Resize the ${side} panel`,
      "aria-valuenow": width,
      "aria-valuemin": min,
      "aria-valuemax": max,
      tabIndex: 0,
      title: "Drag to resize · double-click to reset",
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel: onPointerUp,
      onDoubleClick: reset,
      onKeyDown: (e) => {
        if (e.key === "ArrowLeft") nudge(side === "left" ? -24 : 24);
        else if (e.key === "ArrowRight") nudge(side === "left" ? 24 : -24);
        else return;
        e.preventDefault();
      },
    },
  };
}
