"""Distance matrices and road geometry.

Two providers, one interface:

  * **OSRM** — real road network. Used for the cost matrix the solver optimises
    against, and for the polyline the dispatcher sees on the map. Both must come
    from the same source, or the picture disagrees with the plan.
  * **Haversine** — straight-line fallback. Only ever a degraded mode; it
    understates urban distance badly (Bengaluru depot to Koramangala is 5.2 km
    straight and 8.3 km by road), so a plan costed on it is optimistic.

`matrix_source` is reported all the way up to the UI so nobody mistakes a
fallback plan for a road-costed one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import httpx

from app.core.config import settings


@dataclass(frozen=True)
class LatLon:
    lat: float
    lon: float


def haversine_km(a: LatLon, b: LatLon) -> float:
    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dphi = math.radians(b.lat - a.lat)
    dlmb = math.radians(b.lon - a.lon)
    h = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(h))


def build_haversine_matrices(
    points: list[LatLon], speed_kmh: float = 30.0
) -> tuple[list[list[int]], list[list[float]]]:
    """Time matrix in seconds, distance matrix in km.

    Distances carry a detour factor: real road distance between two urban
    points runs well above the straight line, and pretending otherwise makes
    every fallback plan look cheaper than it can possibly be.
    """
    n = len(points)
    dist = [[0.0] * n for _ in range(n)]
    time_s = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            d = haversine_km(points[i], points[j]) * settings.haversine_detour_factor
            dist[i][j] = d
            time_s[i][j] = int((d / max(speed_kmh, 1e-3)) * 3600)
    return time_s, dist


def _coords(points: list[LatLon]) -> str:
    return ";".join(f"{p.lon:.6f},{p.lat:.6f}" for p in points)


def build_osrm_table(points: list[LatLon]) -> tuple[list[list[int]], list[list[float]]]:
    """OSRM /table/v1 — road-network durations and distances."""
    if len(points) < 2:
        return [[0]], [[0.0]]
    url = f"{settings.osrm_url.rstrip('/')}/table/v1/driving/{_coords(points)}"
    with httpx.Client(timeout=settings.osrm_timeout_s) as client:
        resp = client.get(url, params={"annotations": "duration,distance"})
        resp.raise_for_status()
        data = resp.json()
    if data.get("code") != "Ok":
        raise ValueError(f"OSRM table: {data.get('code')}")
    # Unreachable pairs come back null. A huge finite cost keeps the model
    # solvable while making the solver avoid them.
    time_s = [[int(x) if x is not None else 10**7 for x in row] for row in data["durations"]]
    dist_km = [[(x / 1000.0) if x is not None else 1e5 for x in row] for row in data["distances"]]
    return time_s, dist_km


@lru_cache(maxsize=64)
def _cached_matrices(key: tuple[LatLon, ...]) -> tuple[list[list[int]], list[list[float]], str]:
    points = list(key)
    if settings.use_haversine:
        t, d = build_haversine_matrices(points)
        return t, d, "haversine"
    try:
        t, d = build_osrm_table(points)
        return t, d, "osrm"
    except Exception:  # noqa: BLE001
        t, d = build_haversine_matrices(points)
        return t, d, "haversine_fallback"


def build_matrices(points: list[LatLon]) -> tuple[list[list[int]], list[list[float]], str]:
    """Cached because a demo re-plans the same stop set over and over, and the
    public OSRM instance is a shared courtesy, not an entitlement."""
    return _cached_matrices(tuple(points))


@lru_cache(maxsize=128)
def _cached_geometry(key: tuple[LatLon, ...]) -> tuple[tuple[float, float], ...] | None:
    points = list(key)
    if len(points) < 2 or settings.use_haversine:
        return None
    url = f"{settings.osrm_url.rstrip('/')}/route/v1/driving/{_coords(points)}"
    try:
        with httpx.Client(timeout=settings.osrm_timeout_s) as client:
            resp = client.get(
                url,
                params={
                    "overview": "full",
                    "geometries": "geojson",
                    "steps": "false",
                    "continue_straight": "true",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            return None
        return tuple(
            (c[0], c[1]) for c in data["routes"][0]["geometry"]["coordinates"]
        )
    except Exception:  # noqa: BLE001
        return None


def _simplify(coords: list[tuple[float, float]], epsilon_deg: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker. OSRM returns a point every few metres; at city
    zoom that is ~80 KB of JSON nobody can see. Iterative to avoid blowing the
    stack on a long route."""
    if len(coords) < 3:
        return coords
    keep = [False] * len(coords)
    keep[0] = keep[-1] = True
    stack = [(0, len(coords) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        ax, ay = coords[first]
        bx, by = coords[last]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        worst, worst_i = -1.0, first
        for i in range(first + 1, last):
            px, py = coords[i]
            if norm == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > worst:
                worst, worst_i = d, i
        if worst > epsilon_deg:
            keep[worst_i] = True
            stack.append((first, worst_i))
            stack.append((worst_i, last))
    return [c for c, k in zip(coords, keep) if k]


def road_geometry(points: list[LatLon]) -> list[list[float]] | None:
    """The driven path through these stops as [lon, lat] pairs.

    Legs are fetched stop-to-stop and stitched. One OSRM call through a long
    Delhi/Mumbai tour will happily take a ring-road shortcut that misses the
    dedicated streets between consecutive drops; stitching keeps each hop on
    the road the truck actually drives.

    None when OSRM is unavailable or disabled — callers fall back to drawing
    stop-to-stop lines and must say so, rather than passing straight lines off
    as a road route.
    """
    if len(points) < 2 or settings.use_haversine:
        return None
    coords: list[tuple[float, float]] = []
    for a, b in zip(points, points[1:]):
        leg = _cached_geometry((a, b))
        if not leg:
            whole = _cached_geometry(tuple(points))
            if not whole:
                return None
            coords = list(whole)
            break
        if not coords:
            coords.extend(leg)
        else:
            coords.extend(leg[1:])
    if len(coords) < 2:
        return None
    # ~1e-4 deg is about 11 m: invisible at dispatch zoom, and it cuts the
    # payload by roughly an order of magnitude.
    return [list(c) for c in _simplify(coords, 1e-4)]
