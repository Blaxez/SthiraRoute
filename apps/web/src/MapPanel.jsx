import React, { useCallback, useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import { C, ROUTE_COLORS } from "./palette.js";

const hhmm = (min) =>
  `${String(Math.floor(min / 60) % 24).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;

/** Trucks nearer than this to their own hub are drawn as a depot count. */
const AT_DEPOT_KM = 0.25;
/** Further than this off the polyline and the marker is treated as off-road. */
const OFF_ROAD_KM = 0.08;

// Raster basemap, no API key. Street labels are left off on purpose: our
// route, stop and depot labels are the content, and Carto's POIs were
// competing with them. The road casings stay so a coloured line can sit on
// a real street.
const STYLE = {
  version: 8,
  sources: {
    base: {
      type: "raster",
      tiles: [
        "https://a.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
        "https://b.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
        "https://c.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
      ],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors © CARTO",
    },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": C.ink } },
    {
      id: "base",
      type: "raster",
      source: "base",
      paint: {
        "raster-opacity": 0.5,
        "raster-saturation": -0.35,
        "raster-contrast": 0.18,
      },
    },
  ],
};

/**
 * Routes that are worth drawing, in a fixed order so a vehicle keeps the same
 * colour everywhere it appears: the line, its stops, its truck and its lane.
 * Colour is vehicle identity on this map — nothing else may use it.
 */
function drawnRoutes(routes) {
  return (routes || [])
    .filter((r) => (r.stops || []).length > 1)
    .map((r, i) => ({ route: r, color: ROUTE_COLORS[i % ROUTE_COLORS.length] }));
}

function colorOfVehicle(routes, extra) {
  const map = new Map();
  drawnRoutes(routes).forEach(({ route: r, color }) => map.set(r.vehicle_id, color));
  let i = map.size;
  (extra || []).forEach((r) => {
    if (!map.has(r.vehicle_id)) {
      map.set(r.vehicle_id, ROUTE_COLORS[i++ % ROUTE_COLORS.length]);
    }
  });
  return map;
}

export default function MapPanel({
  depots, shipments, vehicles, routes, committed, proposal, overlays, sim,
  onSelect, selected, matrixSource, playbackMs = 2400,
}) {
  const holder = useRef(null);
  const map = useRef(null);
  // Depots, stops and zone labels are rebuilt whenever their content changes.
  // Trucks are not: they are kept and moved. A marker destroyed and recreated
  // on every GPS tick cannot be animated, and that is what made the fleet look
  // like it was teleporting across the city.
  const statics = useRef([]);
  const depotCounts = useRef(new Map());
  const parkedAt = useRef(new Map());
  const trucks = useRef(new Map());
  const raf = useRef(0);
  const lastPaint = useRef(0);
  const lastPaintKm = useRef(new Map());
  const followFrame = useRef(0);
  const lastFollowAt = useRef(0);
  const progressKm = useRef(new Map());
  const followRef = useRef(null);
  const paintRoutes = useRef(() => {});
  // The map is built once; these keep the handlers it registered then current.
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const [ready, setReady] = useState(false);
  const [tilesDown, setTilesDown] = useState(false);
  const [straight, setStraight] = useState(false);
  const [legendOpen, setLegendOpen] = useState(
    () => localStorage.getItem("sthira.mapkey") === "1"
  );
  const toggleLegend = () =>
    setLegendOpen((v) => {
      localStorage.setItem("sthira.mapkey", v ? "0" : "1");
      return !v;
    });
  const [follow, setFollow] = useState(null);

  const nowMin = sim?.clock_min;
  /** A rule that is switched on, versus a rule that is biting at this minute. */
  const inForce = (o) =>
    nowMin != null && nowMin >= o.ban_start_min && nowMin < o.ban_end_min;

  const focusVid = focusVehicle(follow, selected, routes, committed);

  useEffect(() => {
    if (map.current || !holder.current) return;
    const m = new maplibregl.Map({
      container: holder.current,
      style: STYLE,
      center: [77.62, 12.97],
      zoom: 10.2,
      attributionControl: { compact: true },
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.on("load", () => setReady(true));
    m.on("error", (e) => {
      if (String(e?.error?.message || "").match(/tile|fetch|network/i)) setTilesDown(true);
    });
    // Panning by hand is a statement that you want to look somewhere else.
    m.on("dragstart", () => setFollow(null));
    // Clicking empty map is how every map app clears a selection. Marker
    // clicks land on their own DOM elements, never on the canvas, so this
    // cannot swallow them.
    m.on("click", () => onSelectRef.current?.(null));
    // The rails either side of the map are resizable, and a grid column that
    // changes width fires no window resize — without this the canvas keeps its
    // old size and the map sits offset inside its own container.
    const ro = new ResizeObserver(() => m.resize());
    ro.observe(holder.current);
    map.current = m;
    // Handle for the smoke test to inspect what actually got drawn.
    if (typeof window !== "undefined") window.__map = m;
    return () => {
      ro.disconnect();
      cancelAnimationFrame(raf.current);
      raf.current = 0;
      trucks.current.clear();
      statics.current = [];
      m.remove();
      map.current = null;
      setReady(false);
    };
  }, []);

  useEffect(() => {
    followRef.current = follow;
  }, [follow]);

  // Frame the work, not the city. A fixed zoom on the centroid left half of
  // every plan outside the viewport, which is the fastest way to make a map
  // look broken.
  const fit = useCallback((animate = true) => {
    const m = map.current;
    if (!m) return;
    const pts = [
      ...(depots || []),
      ...(shipments || []),
      ...(vehicles || []),
    ].filter((p) => p?.lat != null && p?.lon != null);
    if (!pts.length) return;
    setFollow(null);
    const b = pts.reduce(
      (acc, p) => acc.extend([p.lon, p.lat]),
      new maplibregl.LngLatBounds([pts[0].lon, pts[0].lat], [pts[0].lon, pts[0].lat])
    );
    m.fitBounds(b, {
      padding: { top: 46, right: 60, bottom: 88, left: 24 },
      maxZoom: 13,
      duration: animate ? 700 : 0,
    });
  }, [depots, shipments, vehicles]);

  // Refit when the city changes or the first plan lands — never on a routine
  // refresh, which would yank the camera out from under the dispatcher.
  const framed = useRef(null);
  useEffect(() => {
    if (!ready) return;
    const key = `${sim?.city?.id || "-"}:${(routes || []).length > 0}`;
    if (framed.current === key) return;
    const first = framed.current === null;
    framed.current = key;
    fit(!first);
  }, [sim?.city?.id, routes, ready, fit]);

  // -------------------------------------------------------- lines + zones --

  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;

    const zoneFeatures = (overlays || [])
      .filter((o) => o.active)
      .map((o) => ({
        type: "Feature",
        properties: {
          name: o.name,
          // The zone is a large translucent fill, so it keeps the saturated
          // sign red rather than the lightened text red.
          color: o.kind === "closure" ? C.ochre : C.noentry,
          live: inForce(o) ? 1 : 0,
        },
        geometry: { type: "Polygon", coordinates: [circle(o.center_lon, o.center_lat, o.radius_km)] },
      }));

    setGeoJson(m, "zones", { type: "FeatureCollection", features: zoneFeatures }, () => {
      m.addLayer({
        id: "zones-fill",
        type: "fill",
        source: "zones",
        paint: {
          "fill-color": ["get", "color"],
          "fill-opacity": ["case", ["==", ["get", "live"], 1], 0.2, 0.055],
        },
      });
      m.addLayer({
        id: "zones-line",
        type: "line",
        source: "zones",
        filter: ["==", ["get", "live"], 1],
        paint: { "line-color": ["get", "color"], "line-width": 2.4 },
      });
      m.addLayer({
        id: "zones-line-idle",
        type: "line",
        source: "zones",
        filter: ["==", ["get", "live"], 0],
        paint: {
          "line-color": ["get", "color"],
          "line-width": 1.2,
          "line-dasharray": [3, 2],
          "line-opacity": 0.65,
        },
      });
    });
  }, [overlays, nowMin, ready]);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;

    const colors = colorOfVehicle(routes, proposal);
    const live = drawnRoutes(routes);
    setStraight(live.length > 0 && live.every(({ route: r }) => !(r.geometry?.length > 1)));

    const paint = () => {
      if (!map.current) return;
      const features = [];
      live.forEach(({ route: r, color }) => {
        const path = coordsOf(r);
        if (!path || path.length < 2) return;
        const road = r.geometry?.length > 1 ? 1 : 0;
        const dim = focusVid != null && focusVid !== r.vehicle_id ? 1 : 0;
        const cum = cumOf(path);
        const kmNow = progressKm.current.get(r.vehicle_id) ?? 0;
        if (road) {
          const { trail, ahead } = splitAtKm(path, cum, kmNow);
          if (trail?.length > 1) {
            features.push(lineFeat(trail, { color, road: 1, role: "trail", dim }));
          }
          if (ahead?.length > 1) {
            features.push(lineFeat(ahead, { color, road: 1, role: "ahead", dim }));
          }
        } else {
          features.push(lineFeat(path, { color, road: 0, role: "ahead", dim }));
        }
      });

      setGeoJson(m, "routes", { type: "FeatureCollection", features }, () => {});
      ensureRouteLayers(m);

      const planFeats = (proposal || [])
        .filter((r) => coordsOf(r)?.length > 1)
        .map((r) => {
          const path = coordsOf(r);
          return lineFeat(path, {
            color: colors.get(r.vehicle_id) || C.silver,
            road: r.geometry?.length > 1 ? 1 : 0,
            role: "plan",
            dim: 0,
          });
        });
      setGeoJson(m, "plan", { type: "FeatureCollection", features: planFeats }, () => {});
      ensurePlanLayer(m);
    };

    paintRoutes.current = paint;
    paint();
  }, [routes, proposal, focusVid, ready]);

  // ------------------------------------------------------- static markers --

  const depotSig = (depots || []).map((d) => `${d.id}:${d.lat}:${d.lon}`).join("|");
  const stopSig = (shipments || [])
    .map((s) => `${s.id}:${s.status}:${s.lat}:${s.lon}`).join("|");
  const zoneSig = (overlays || [])
    .map((o) => `${o.id}:${o.active ? 1 : 0}:${inForce(o) ? 1 : 0}`).join("|");
  const routeSig = drawnRoutes(routes)
    .map(({ route: r, color }) =>
      `${r.vehicle_id}:${color}:${(r.stops || [])
        .map((s) => `${s.seq},${s.shipment_id},${s.late_min || 0}`).join(";")}`)
    .join("|");

  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    statics.current.forEach((x) => x.remove());
    statics.current = [];
    depotCounts.current.clear();

    const place = (lon, lat, el, onClick, z = 0) => {
      if (lon == null || lat == null) return;
      if (onClick) {
        el.addEventListener("click", onClick);
        el.style.cursor = "pointer";
      }
      const marker = new maplibregl.Marker({ element: el, anchor: "center" })
        .setLngLat([lon, lat])
        .addTo(m);
      el.style.zIndex = String(z);
      statics.current.push(marker);
    };

    // Which truck serves which stop, and in what order. Without this the map
    // shows twenty identical dots and the plan it is meant to explain is
    // invisible: you cannot see a route, only a scatter.
    const stopMeta = new Map();
    drawnRoutes(routes).forEach(({ route: r, color }) => {
      const code = (vehicles || []).find((v) => v.id === r.vehicle_id)?.code || "";
      [...(r.stops || [])]
        .filter((s) => s.shipment_id)
        .sort((a, b) => a.seq - b.seq)
        .forEach((s, i) => {
          stopMeta.set(s.shipment_id, {
            color, code, seq: i + 1,
            eta: s.eta_min, late: s.late_min || 0,
            vehicleId: r.vehicle_id,
          });
        });
    });

    const bans = (overlays || []).filter((o) => o.active);
    const zonedBy = new Map();
    (shipments || []).forEach((s) => {
      const hit =
        bans.find((b) => inForce(b) && km(s.lat, s.lon, b.center_lat, b.center_lon) <= b.radius_km)
        || bans.find((b) => km(s.lat, s.lon, b.center_lat, b.center_lon) <= b.radius_km);
      if (hit) zonedBy.set(s.id, hit);
    });

    (depots || []).forEach((d) => {
      const el = node("mk mk-depot", null, `Depot · ${d.name}`);
      el.appendChild(node("mk-tag", d.name));
      const count = node("mk-count", "");
      count.style.display = "none";
      el.appendChild(count);
      depotCounts.current.set(d.id, { el, count, name: d.name });
      place(d.lon, d.lat, el, null, 2);
    });
    applyParked();

    (shipments || []).forEach((s) => {
      const meta = stopMeta.get(s.id);
      const done = s.status === "delivered";
      const orphan = !meta && !done;
      const late = meta?.late > 0;
      const zone = done ? null : zonedBy.get(s.id);
      const dim = focusVid != null && meta && meta.vehicleId !== focusVid;

      const cls = [
        "mk",
        done ? "mk-done" : orphan ? "mk-orphan" : "mk-stop",
        late ? "is-late" : "",
        zone ? (inForce(zone) ? "is-zoned is-shut" : "is-zoned") : "",
        selected === s.id ? "is-selected" : "",
        dim ? "is-dim" : "",
      ].filter(Boolean).join(" ");

      const el = node(
        cls,
        done ? null : orphan ? "!" : meta.seq,
        [
          `${s.code} · ${s.customer_name}`,
          `${s.demand_kg} kg · ${s.status.replace("_", " ")}`,
          meta ? `stop ${meta.seq} on ${meta.code}` : "no vehicle assigned yet",
          late ? `projected ${meta.late} min late` : "",
          zone
            ? `inside ${plain(zone.name)} — ${
                inForce(zone)
                  ? `no entry until ${hhmm(zone.ban_end_min)}`
                  : `no entry ${hhmm(zone.ban_start_min)}–${hhmm(zone.ban_end_min)}`
              }`
            : "",
        ].filter(Boolean).join("\n")
      );
      if (meta) el.style.setProperty("--mk", meta.color);
      place(s.lon, s.lat, el, () => onSelect?.(s.id), selected === s.id ? 8 : 4);
    });

    const zoneTags = new Map();
    bans.forEach((o) => {
      const key = `${o.center_lat.toFixed(4)},${o.center_lon.toFixed(4)},${o.kind}`;
      const at = zoneTags.get(key);
      const tag = at || {
        lat: o.center_lat, lon: o.center_lon, kind: o.kind,
        radius: o.radius_km || 0,
        windows: [], names: [], notes: o.notes, live: null,
      };
      tag.windows.push(`${hhmm(o.ban_start_min)}–${hhmm(o.ban_end_min)}`);
      tag.names.push(o.name);
      if (inForce(o)) tag.live = o;
      zoneTags.set(key, tag);
    });

    zoneTags.forEach((z) => {
      const closure = z.kind === "closure";
      const what = closure ? "ROAD CLOSED" : "NO ENTRY";
      const el = node(
        `mk-zonetag${closure ? " closure" : ""}${z.live ? " is-live" : ""}`,
        z.live
          ? `${what} NOW · until ${hhmm(z.live.ban_end_min)}`
          : `${what} ${z.windows.join(" · ")}`,
        [...z.names, z.notes].filter(Boolean).join("\n")
      );
      place(z.lon, z.lat + z.radius / 110.574, el, null, 3);
    });
  }, [depotSig, stopSig, zoneSig, routeSig, ready, selected, onSelect, focusVid, vehicles]);

  function applyParked() {
    depotCounts.current.forEach(({ el, count, name }, id) => {
      const codes = parkedAt.current.get(id) || [];
      count.style.display = codes.length ? "" : "none";
      count.textContent = String(codes.length);
      el.title = codes.length
        ? `Depot · ${name}\n${codes.length} at the hub: ${codes.join(", ")}`
        : `Depot · ${name}`;
    });
  }

  // ------------------------------------------------------------- the fleet --

  // A reported GPS fix is a point on the dedicated path, not a licence to
  // fly the marker there in a straight line. Between ticks the chip is walked
  // along the same polyline the map paints — otherwise a 3 km jump chords
  // across parks, lakes and the wrong carriageway.
  //
  // Duration is the playback slot, not "time since this effect last ran".
  // Shipments, the clock object and a follow-click used to retrigger this
  // effect a few milliseconds later, which compressed the hop into 260 ms
  // and made the fleet look like it was flying.
  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;

    const liveByVid = new Map();
    (routes || []).forEach((r) => {
      if (coordsOf(r)?.length > 1) liveByVid.set(r.vehicle_id, r);
    });
    const colors = colorOfVehicle(routes, proposal);
    const staleOf = new Map(
      (sim?.vehicles || []).filter((v) => v.gps_stale_min).map((v) => [v.id, v.gps_stale_min])
    );
    const depotById = new Map((depots || []).map((d) => [d.id, d]));
    const shipById = new Map((shipments || []).map((s) => [s.id, s]));
    const slot = Math.max(900, (playbackMs || 2400) - 60);

    const parked = new Map();
    const seen = new Set();
    let moved = false;

    (vehicles || []).forEach((v) => {
      if (v.lat == null || v.lon == null) return;
      seen.add(v.id);
      const stale = staleOf.get(v.id);
      const down = v.status === "down";
      const home = depotById.get(v.depot_id);
      const atHome = !!home && !down && km(v.lat, v.lon, home.lat, home.lon) < AT_DEPOT_KM;
      if (atHome) parked.set(home.id, [...(parked.get(home.id) || []), v.code]);

      const route = liveByVid.get(v.id);
      const path = route ? coordsOf(route) : null;
      const cum = path && path.length > 1 ? cumOf(path) : null;
      const toKm = targetKm(v, path, cum);

      let t = trucks.current.get(v.id);
      if (!t) {
        const el = truckNode(v.code);
        const start = cum ? pointAt(path, cum, toKm) : [v.lon, v.lat, null];
        const marker = new maplibregl.Marker({ element: el, anchor: "center" })
          .setLngLat([start[0], start[1]])
          .addTo(m);
        el.style.zIndex = "12";
        t = {
          el, marker,
          at: [start[0], start[1]],
          km: toKm,
          toKm,
          pathKey: pathKey(route),
        };
        el.dataset.lon = String(start[0]);
        el.dataset.lat = String(start[1]);
        if (start[2] != null) el.style.setProperty("--dir", `${start[2]}deg`);
        trucks.current.set(v.id, t);
      }

      t.el.classList.toggle("is-down", !!down);
      t.el.classList.toggle("is-dark", !!stale);
      t.el.classList.toggle("is-loading", v.status === "loading");
      t.el.classList.toggle("is-followed", followRef.current === v.id);
      t.el.classList.toggle("is-dim", focusVid != null && focusVid !== v.id);
      t.el.style.zIndex = followRef.current === v.id ? "24" : "12";
      t.el.title =
        (stale
          ? `${v.code} · GPS lost ${stale} min ago · last-known position`
          : `${v.code} · ${v.status.replace("_", " ")} · ${v.capacity_kg} kg`) +
        "\nclick to keep the camera on this truck";
      t.el.style.setProperty(
        "--mk",
        down ? C.vermilion : stale ? C.ochre : colors.get(v.id) || C.steel
      );
      t.el.style.cursor = "pointer";
      t.el.style.display = atHome ? "none" : "";
      t.el.onclick = () => setFollow((f) => (f === v.id ? null : v.id));
      const nextEl = t.el.querySelector(".mk-next");
      if (nextEl) nextEl.textContent = route ? nextStopLabel(route, shipById) : "";

      const key = pathKey(route);
      const newPath = key !== t.pathKey;
      t.pathKey = key;

      if (!cum) {
        // No dedicated road: sit on the GPS fix. Never lerp — that chord is
        // what put trucks in the middle of their own loop.
        const target = [v.lon, v.lat];
        t.marker.setLngLat(target);
        t.at = target;
        t.km = toKm;
        t.toKm = toKm;
        t.anim = null;
        t.el.dataset.lon = String(v.lon);
        t.el.dataset.lat = String(v.lat);
        progressKm.current.set(v.id, toKm);
        return;
      }

      if (newPath || down || toKm < t.km - 0.05) {
        placeOnPath(t, path, cum, toKm);
        t.toKm = toKm;
        t.anim = null;
        progressKm.current.set(v.id, t.km);
        return;
      }

      // Already walking this hop — leave the in-flight animation alone.
      if (t.anim?.cum && Math.abs((t.anim.toKm ?? t.toKm) - toKm) < 0.04) {
        progressKm.current.set(v.id, t.km);
        return;
      }

      const here = projectOnPath(path, cum, t.at[0], t.at[1]);
      if (here.offsetKm > OFF_ROAD_KM) {
        placeOnPath(t, path, cum, here.km);
      } else {
        t.km = here.km;
      }
      if (Math.abs(toKm - t.km) < 0.004) {
        placeOnPath(t, path, cum, toKm);
        t.toKm = toKm;
        t.anim = null;
        progressKm.current.set(v.id, t.km);
        return;
      }
      t.toKm = toKm;
      t.anim = { path, cum, fromKm: t.km, toKm, t0: performance.now(), dur: slot };
      moved = true;
      progressKm.current.set(v.id, t.km);
    });

    trucks.current.forEach((t, id) => {
      if (seen.has(id)) return;
      t.marker.remove();
      trucks.current.delete(id);
      progressKm.current.delete(id);
    });

    parkedAt.current = parked;
    applyParked();
    lastPaintKm.current = new Map(progressKm.current);
    paintRoutes.current?.();
    paintFleet(m, trucks.current);

    if (!moved) return;
    if (raf.current) return;

    const frame = () => {
      const at = performance.now();
      let running = false;
      trucks.current.forEach((t, id) => {
        const a = t.anim;
        if (!a?.cum) {
          if (a) t.anim = null;
          return;
        }
        const p = Math.min(1, (at - a.t0) / a.dur);
        if (p < 1) running = true;
        else t.anim = null;
        t.km = a.fromKm + (a.toKm - a.fromKm) * p;
        const [lon, lat, dir] = pointAt(a.path, a.cum, t.km);
        t.at = [lon, lat];
        if (dir != null) t.el.style.setProperty("--dir", `${dir}deg`);
        progressKm.current.set(id, t.km);
        t.el.dataset.lon = String(lon);
        t.el.dataset.lat = String(lat);
        t.marker.setLngLat(t.at);
      });
      if (at - lastPaint.current > 180) {
        lastPaint.current = at;
        // Skip setData unless some truck advanced enough to change the trail split.
        if (routeProgressMoved(progressKm.current, lastPaintKm.current, 0.02)) {
          lastPaintKm.current = new Map(progressKm.current);
          paintRoutes.current?.();
        }
        paintFleet(m, trucks.current);
      }
      const lead = followRef.current != null && trucks.current.get(followRef.current);
      // Marker moves every frame; camera follow is cheaper at ~3rd frame / 100ms.
      if (lead) {
        followFrame.current += 1;
        if (followFrame.current >= 3 || at - lastFollowAt.current >= 100) {
          followFrame.current = 0;
          lastFollowAt.current = at;
          m.setCenter(lead.at);
        }
      }
      raf.current = running ? requestAnimationFrame(frame) : 0;
      if (!running) {
        lastPaintKm.current = new Map(progressKm.current);
        paintRoutes.current?.();
        paintFleet(m, trucks.current);
      }
    };
    raf.current = requestAnimationFrame(frame);
  }, [vehicles, routes, proposal, depots, sim, shipments, ready, focusVid, playbackMs]);

  useEffect(() => {
    const m = map.current;
    const t = follow != null && trucks.current.get(follow);
    if (!m || !ready || !t) return;
    m.easeTo({ center: t.at, zoom: Math.max(m.getZoom(), 12.5), duration: 700 });
  }, [follow, ready]);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready || selected == null) return;
    const s = (shipments || []).find((x) => x.id === selected);
    if (!s) return;
    setFollow(null);
    m.easeTo({ center: [s.lon, s.lat], zoom: Math.max(m.getZoom(), 12.5), duration: 600 });
  }, [selected, ready]);

  const followed = follow != null && (vehicles || []).find((v) => v.id === follow);
  const hasProposal = (proposal || []).some((r) => coordsOf(r)?.length > 1);

  return (
    <div className="map-wrap" data-hl="map">
      <div ref={holder} className="map" />
      <div className="map-tools">
        <button className="map-fit" onClick={() => fit(true)} title="Frame the whole plan">
          ⤢ Fit
        </button>
        {followed && (
          <button
            className="map-fit on"
            onClick={() => setFollow(null)}
            title="Stop following this truck and free the camera"
          >
            ◎ following {followed.code} ✕
          </button>
        )}
      </div>
      <div className="map-notes">
        {tilesDown && (
          <div className="map-note">
            Basemap tiles unavailable — routes and stops still shown.
          </div>
        )}
        {straight && (
          <div className="map-note">
            Straight-line preview: the routing engine is unreachable, so these
            join stops rather than follow roads.
          </div>
        )}
      </div>
      <div className={`map-legend${legendOpen ? "" : " folded"}`} data-hl="legend">
        <p className="map-read">
          <b>Coloured dot</b> = the truck on its road.
          {" "}Bright line = still to go. Pale = already driven. Playback speed only moves the shift clock — not wall time.
          {hasProposal && (
            <>
              {" "}<b>Dashed</b> = a proposed re-plan. Trucks stay on the solid road until you approve it.
            </>
          )}
        </p>
        <button
          className="legend-toggle"
          onClick={toggleLegend}
          aria-expanded={legendOpen}
        >
          <span className="sect-caret">▶</span> Map key
        </button>
        {matrixSource && (
          <span className={`map-source ${matrixSource === "osrm" ? "ok" : "warn"}`}>
            {matrixSource === "osrm"
              ? "distances: OSRM road network"
              : `distances: ${matrixSource.replace(/_/g, " ")}`}
          </span>
        )}
        {legendOpen && (
        <div className="legend-body">
        <div className="legend-group">
          <b>Path</b>
          <span className="legend-item">
            <i className="legend-path ahead" /> still to go
          </span>
          <span className="legend-item">
            <i className="legend-path trail" /> already driven
          </span>
          <span className="legend-item">
            <i className="legend-path plan" /> proposed re-plan
          </span>
        </div>
        <div className="legend-group">
          <b>Stops</b>
          <span className="legend-item">
            <i className="mk mk-stop lg" style={{ "--mk": C.steel }}>3</i>
            on a truck, in order
          </span>
          <span className="legend-item"><i className="mk mk-orphan lg">!</i> nobody assigned</span>
          <span className="legend-item"><i className="mk mk-done lg" /> delivered</span>
          <span className="legend-item">
            <i className="mk mk-stop lg is-late" style={{ "--mk": C.steel }}>5</i>
            running late
          </span>
          <span className="legend-item">
            <i className="mk mk-stop lg is-zoned is-shut" style={{ "--mk": C.steel }}>7</i>
            inside a restricted zone
          </span>
        </div>
        <div className="legend-group">
          <b>Fleet</b>
          <span className="legend-item">
            <i className="legend-truck-dot" style={{ background: C.steel }} /> on its road
          </span>
          <span className="legend-item">
            <i className="mk mk-truck lg" style={{ "--mk": C.steel }}>TRK</i> name
          </span>
          <span className="legend-item">
            <i className="mk mk-truck lg is-dark" style={{ "--mk": C.ochre }}>TRK</i> GPS lost
          </span>
          <span className="legend-item">
            <i className="mk mk-truck lg is-down" style={{ "--mk": C.vermilion }}>TRK</i> broken down
          </span>
          <span className="legend-item"><i className="mk mk-depot lg" /> depot</span>
        </div>
        <div className="legend-group">
          <b>Zones</b>
          <span className="legend-item"><i className="legend-zone live" /> shut right now</span>
          <span className="legend-item"><i className="legend-zone" /> no-entry, another hour</span>
          <span className="legend-item"><i className="legend-zone closure" /> weather closure</span>
        </div>
        </div>
        )}
      </div>
    </div>
  );
}

function focusVehicle(follow, selected, routes, committed) {
  if (follow != null) return follow;
  if (selected == null) return null;
  for (const r of [...(routes || []), ...(committed || [])]) {
    if ((r.stops || []).some((s) => s.shipment_id === selected)) return r.vehicle_id;
  }
  return null;
}

function pathKey(route) {
  if (!route) return null;
  const path = coordsOf(route);
  if (!path) return String(route.id);
  const a = path[0];
  const b = path[path.length - 1];
  return `${route.id}:${path.length}:${a[0].toFixed(4)},${a[1].toFixed(4)}:${b[0].toFixed(4)}`;
}

/** True if any vehicle’s path-km moved enough to warrant a route GeoJSON rewrite. */
function routeProgressMoved(now, prev, minDelta) {
  if (now.size !== prev.size) return true;
  for (const [id, km] of now) {
    const was = prev.get(id);
    if (was == null || Math.abs(km - was) > minDelta) return true;
  }
  return false;
}

function targetKm(v, path, cum) {
  if (!cum) return v.path_progress_km || 0;
  const projected = projectOnPath(path, cum, v.lon, v.lat);
  const reported = v.path_progress_km;
  if (reported != null && Math.abs(reported - projected.km) < 2) return reported;
  return projected.km;
}

function placeOnPath(t, path, cum, atKm) {
  const [lon, lat, dir] = pointAt(path, cum, atKm);
  t.at = [lon, lat];
  t.km = atKm;
  t.el.dataset.lon = String(lon);
  t.el.dataset.lat = String(lat);
  t.marker.setLngLat(t.at);
  if (dir != null) t.el.style.setProperty("--dir", `${dir}deg`);
}

function nextStopLabel(route, shipById) {
  const pending = [...(route.stops || [])]
    .filter((s) => s.shipment_id && s.status !== "completed" && s.status !== "skipped")
    .sort((a, b) => a.seq - b.seq);
  const s = pending[0];
  if (!s) return "returning to depot";
  const code = shipById.get(s.shipment_id)?.code || "stop";
  return s.eta_min != null ? `next ${code} · ${hhmm(s.eta_min)}` : `next ${code}`;
}

function truckNode(code) {
  const el = document.createElement("div");
  el.className = "mk mk-truck";
  // The officer points at trucks by code, and the one worth pointing at is the
  // one on the road — the guide tracks this node as it moves.
  el.dataset.hl = `vehicle:${code}`;
  const body = document.createElement("i");
  body.className = "mk-body";
  const label = document.createElement("b");
  label.className = "mk-code";
  label.textContent = code;
  const next = document.createElement("span");
  next.className = "mk-next";
  el.append(body, label, next);
  return el;
}

function lineFeat(coords, properties) {
  return {
    type: "Feature",
    properties,
    geometry: { type: "LineString", coordinates: coords },
  };
}

function splitAtKm(path, cum, atKm) {
  const end = cum[cum.length - 1] || 0;
  const t = Math.max(0, Math.min(atKm || 0, end));
  if (t <= 0.03) return { trail: null, ahead: path };
  if (t >= end - 0.03) return { trail: path, ahead: null };
  const [lon, lat] = pointAt(path, cum, t);
  const split = [lon, lat];
  let i = 1;
  while (i < cum.length - 1 && cum[i] < t) i++;
  return {
    trail: path.slice(0, i).concat([split]),
    ahead: [split].concat(path.slice(i)),
  };
}

const fallbackPath = new WeakMap();

function coordsOf(route) {
  if (!route) return null;
  if (route.geometry?.length > 1) return route.geometry;
  let cached = fallbackPath.get(route);
  if (cached) return cached;
  const stops = [...(route.stops || [])].sort((a, b) => a.seq - b.seq);
  if (stops.length < 2) return null;
  cached = stops.map((s) => [s.lon, s.lat]);
  fallbackPath.set(route, cached);
  return cached;
}

function node(cls, text, title) {
  const el = document.createElement("div");
  el.className = cls;
  if (text != null) el.textContent = String(text);
  if (title) el.title = title;
  return el;
}

function setGeoJson(map, id, data, addLayers) {
  const src = map.getSource(id);
  if (src) {
    src.setData(data);
    return;
  }
  map.addSource(id, { type: "geojson", data });
  addLayers();
}

function ensureRouteLayers(m) {
  const dim = ["case", ["==", ["get", "dim"], 1], 0.22, 1];
  if (!m.getLayer("routes-trail")) {
    m.addLayer({
      id: "routes-trail",
      type: "line",
      source: "routes",
      filter: ["==", ["get", "role"], "trail"],
      paint: {
        "line-color": ["get", "color"],
        "line-width": 3,
        "line-opacity": ["case", ["==", ["get", "dim"], 1], 0.12, 0.38],
      },
      layout: { "line-cap": "round", "line-join": "round" },
    });
  }
  if (!m.getLayer("routes-casing")) {
    m.addLayer({
      id: "routes-casing",
      type: "line",
      source: "routes",
      filter: ["==", ["get", "role"], "ahead"],
      paint: { "line-color": "#0a0906", "line-width": 9, "line-opacity": 0.9 },
      layout: { "line-cap": "round", "line-join": "round" },
    });
  }
  if (!m.getLayer("routes-line")) {
    m.addLayer({
      id: "routes-line",
      type: "line",
      source: "routes",
      filter: ["all", ["==", ["get", "road"], 1], ["==", ["get", "role"], "ahead"]],
      paint: {
        "line-color": ["get", "color"],
        "line-width": 5,
        "line-opacity": dim,
      },
      layout: { "line-cap": "round", "line-join": "round" },
    });
  }
  if (!m.getLayer("routes-line-straight")) {
    m.addLayer({
      id: "routes-line-straight",
      type: "line",
      source: "routes",
      filter: ["==", ["get", "road"], 0],
      paint: {
        "line-color": ["get", "color"],
        "line-width": 2.4,
        "line-dasharray": [2, 1.6],
        "line-opacity": 0.85,
      },
      layout: { "line-cap": "round", "line-join": "round" },
    });
  }
}

function ensurePlanLayer(m) {
  if (m.getLayer("routes-plan")) return;
  if (!m.getSource("plan")) return;
  const spec = {
    id: "routes-plan",
    type: "line",
    source: "plan",
    paint: {
      "line-color": ["get", "color"],
      "line-width": 2.2,
      "line-dasharray": [2.2, 2],
      "line-opacity": 0.55,
    },
    layout: { "line-cap": "round", "line-join": "round" },
  };
  if (m.getLayer("routes-trail")) m.addLayer(spec, "routes-trail");
  else m.addLayer(spec);
}

/** A circle in the same coordinate space as the route line — HTML chips at
 *  city zoom are a kilometre across and look like they have left the road. */
function paintFleet(m, truckMap) {
  if (!m?.getStyle?.()) return;
  const features = [];
  truckMap.forEach((t) => {
    if (t.el.style.display === "none") return;
    features.push({
      type: "Feature",
      properties: {
        color: t.el.style.getPropertyValue("--mk") || C.steel,
        dim: t.el.classList.contains("is-dim") ? 1 : 0,
      },
      geometry: { type: "Point", coordinates: t.at },
    });
  });
  setGeoJson(m, "fleet", { type: "FeatureCollection", features }, () => {});
  if (!m.getLayer("fleet-dot") && m.getSource("fleet")) {
    m.addLayer({
      id: "fleet-dot",
      type: "circle",
      source: "fleet",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 3.5, 11, 5, 14, 7],
        "circle-color": ["get", "color"],
        "circle-stroke-width": 1.6,
        "circle-stroke-color": "#0a0906",
        "circle-opacity": ["case", ["==", ["get", "dim"], 1], 0.3, 1],
      },
    });
  }
}

const plain = (n) => n.replace(/\s*\(template\)\s*/i, "");

function km(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const p = Math.PI / 180;
  const dLat = (lat2 - lat1) * p;
  const dLon = (lon2 - lon1) * p;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * p) * Math.cos(lat2 * p) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

const cumCache = new WeakMap();

function cumOf(path) {
  let cum = cumCache.get(path);
  if (cum) return cum;
  cum = [0];
  for (let i = 1; i < path.length; i++) {
    cum.push(cum[i - 1] + km(path[i - 1][1], path[i - 1][0], path[i][1], path[i][0]));
  }
  cumCache.set(path, cum);
  return cum;
}

/**
 * The point `d` km along the path, and the heading there in CSS degrees
 * (0 = pointing right, which is what an arrow glyph does unrotated).
 */
function pointAt(path, cum, d) {
  const end = cum[cum.length - 1];
  const t = Math.max(0, Math.min(d, end));
  let i = 1;
  while (i < cum.length - 1 && cum[i] < t) i++;
  const a = path[i - 1];
  const b = path[i] || a;
  const span = cum[i] - cum[i - 1];
  const f = span > 0 ? (t - cum[i - 1]) / span : 0;
  const dx = (b[0] - a[0]) * Math.cos((a[1] * Math.PI) / 180);
  const dy = b[1] - a[1];
  const dir = dx || dy ? -(Math.atan2(dy, dx) * 180) / Math.PI : null;
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, dir];
}

/** Nearest point on the polyline, as path-kilometres and how far off it is. */
function projectOnPath(path, cum, lon, lat) {
  let best = {
    km: 0,
    lon: path[0][0],
    lat: path[0][1],
    offsetKm: km(lat, lon, path[0][1], path[0][0]),
  };
  const cos = Math.cos((lat * Math.PI) / 180);
  for (let i = 1; i < path.length; i++) {
    const ax = path[i - 1][0];
    const ay = path[i - 1][1];
    const bx = path[i][0];
    const by = path[i][1];
    const dx = (bx - ax) * cos;
    const dy = by - ay;
    const px = (lon - ax) * cos;
    const py = lat - ay;
    const len2 = dx * dx + dy * dy;
    const u = len2 > 0 ? Math.max(0, Math.min(1, (px * dx + py * dy) / len2)) : 0;
    const qx = ax + (bx - ax) * u;
    const qy = ay + (by - ay) * u;
    const d = km(lat, lon, qy, qx);
    if (d < best.offsetKm) {
      const span = cum[i] - cum[i - 1];
      best = { km: cum[i - 1] + span * u, lon: qx, lat: qy, offsetKm: d };
    }
  }
  return best;
}

function circle(lon, lat, radiusKm, steps = 64) {
  const coords = [];
  const dx = radiusKm / (111.32 * Math.cos((lat * Math.PI) / 180));
  const dy = radiusKm / 110.574;
  for (let i = 0; i <= steps; i++) {
    const a = (i / steps) * 2 * Math.PI;
    coords.push([lon + dx * Math.cos(a), lat + dy * Math.sin(a)]);
  }
  return coords;
}
