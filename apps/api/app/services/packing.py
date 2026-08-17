"""3D load packing with LIFO unloading — Plan.md §9 geometric module.

Runs as a **validation subroutine after routing**, never inside the search
loop. Coupling a second NP-hard geometric packer into every route mutation is
what makes 3L-CVRP intractable; the routing model carries the cheap surrogates
(weight, volume, compatibility) and this module proves the chosen route can
actually be loaded.

Geometry, from the driver's point of view:

    x  0 = cab bulkhead ............... L = rear door   (depth)
    y  0 = kerb side .................. W = off side    (width)
    z  0 = deck floor ................. H = roof        (height)

Items are loaded in **reverse delivery order** — the last drop goes in first,
against the bulkhead — so every stop can be unloaded from the door without
touching freight for later stops. That is the LIFO rule from Gendreau et al.
(2006), and here it is enforced at placement time rather than checked
afterwards, so a returned plan is LIFO-correct by construction.

An item i (delivered earlier) is blocked by item j (delivered later) when:
  * j sits between i and the door and their y-z cross-sections overlap, or
  * j sits on top of i and their x-y footprints overlap.
Both are rejected as candidate placements.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Fraction of an item's base that must rest on something solid.
MIN_SUPPORT = 0.70
# Longitudinal centre of gravity must stay inside this band, as a fraction of
# deck length from the bulkhead, or the axle loading goes out of spec.
COG_SAFE_MIN = 0.30
COG_SAFE_MAX = 0.70
# ...but only once the truck is carrying enough to matter. LIFO forces the
# last drops hard against the bulkhead, so a lightly loaded deck always reads
# nose-heavy. Warning on that would be noise the crew learns to ignore.
COG_CHECK_MIN_PAYLOAD = 0.40


@dataclass
class PackItem:
    shipment_id: int
    code: str
    seq: int  # delivery sequence on the route; lower = dropped earlier
    length_cm: int
    width_cm: int
    height_cm: int
    weight_kg: float
    fragile: bool = False  # nothing may be stacked on top of it
    stackable: bool = True  # may itself carry load
    rotatable: bool = True  # may be yawed 90 degrees; never tipped

    @property
    def volume_cm3(self) -> int:
        return self.length_cm * self.width_cm * self.height_cm

    def orientations(self) -> list[tuple[int, int, int]]:
        """Upright orientations only — cartons are not tipped on their side."""
        base = [(self.length_cm, self.width_cm, self.height_cm)]
        if self.rotatable and self.width_cm != self.length_cm:
            base.append((self.width_cm, self.length_cm, self.height_cm))
        return base


@dataclass
class Placement:
    item: PackItem
    x: int
    y: int
    z: int
    length_cm: int
    width_cm: int
    height_cm: int

    @property
    def x2(self) -> int:
        return self.x + self.length_cm

    @property
    def y2(self) -> int:
        return self.y + self.width_cm

    @property
    def z2(self) -> int:
        return self.z + self.height_cm

    def overlaps_xy(self, o: "Placement") -> bool:
        return self.x < o.x2 and o.x < self.x2 and self.y < o.y2 and o.y < self.y2

    def overlaps_yz(self, o: "Placement") -> bool:
        return self.y < o.y2 and o.y < self.y2 and self.z < o.z2 and o.z < self.z2

    def intersects(self, o: "Placement") -> bool:
        return (
            self.x < o.x2 and o.x < self.x2
            and self.y < o.y2 and o.y < self.y2
            and self.z < o.z2 and o.z < self.z2
        )

    def as_dict(self) -> dict:
        return {
            "shipment_id": self.item.shipment_id,
            "code": self.item.code,
            "seq": self.item.seq,
            "x": self.x, "y": self.y, "z": self.z,
            "l": self.length_cm, "w": self.width_cm, "h": self.height_cm,
            "weight_kg": self.item.weight_kg,
            "fragile": self.item.fragile,
        }


@dataclass
class Unplaced:
    shipment_id: int
    code: str
    reason: str


@dataclass
class PackResult:
    feasible: bool
    placements: list[Placement] = field(default_factory=list)
    unplaced: list[Unplaced] = field(default_factory=list)
    volume_utilization_pct: float = 0.0
    floor_utilization_pct: float = 0.0
    cog_x_pct: float = 0.0
    cog_ok: bool = True
    lifo_ok: bool = True
    container: dict = field(default_factory=dict)
    load_order: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "feasible": self.feasible,
            "container": self.container,
            "placements": [p.as_dict() for p in self.placements],
            "unplaced": [
                {"shipment_id": u.shipment_id, "code": u.code, "reason": u.reason}
                for u in self.unplaced
            ],
            "volume_utilization_pct": self.volume_utilization_pct,
            "floor_utilization_pct": self.floor_utilization_pct,
            "cog_x_pct": self.cog_x_pct,
            "cog_ok": self.cog_ok,
            "lifo_ok": self.lifo_ok,
            "load_order": self.load_order,
            "notes": self.notes,
        }


def _supported(cand: Placement, placed: list[Placement]) -> tuple[bool, str]:
    """Is the item's base resting on the deck or on solid, stackable freight?"""
    if cand.z == 0:
        return True, ""
    base_area = cand.length_cm * cand.width_cm
    covered = 0
    for p in placed:
        if p.z2 != cand.z:
            continue
        ox = min(cand.x2, p.x2) - max(cand.x, p.x)
        oy = min(cand.y2, p.y2) - max(cand.y, p.y)
        if ox <= 0 or oy <= 0:
            continue
        if p.item.fragile:
            return False, f"would rest on fragile {p.item.code}"
        if not p.item.stackable:
            return False, f"{p.item.code} cannot be stacked on"
        covered += ox * oy
    if covered < MIN_SUPPORT * base_area:
        return False, "insufficient support underneath"
    return True, ""


def _lifo_ok(cand: Placement, placed: list[Placement]) -> tuple[bool, str]:
    """Reject placements that would trap earlier freight behind later freight.

    `placed` only ever holds items with a delivery sequence at or after the
    candidate's, because packing runs in reverse delivery order.
    """
    for p in placed:
        if p.item.seq <= cand.item.seq:
            continue  # same stop, or already handled — no ordering to violate
        # p is delivered later, so it must not stand between cand and the door
        if cand.x2 <= p.x and cand.overlaps_yz(p):
            return False, f"{p.item.code} (later drop) would block the door side"
        # ...nor sit on top of it
        if cand.z2 <= p.z and cand.overlaps_xy(p):
            return False, f"{p.item.code} (later drop) would sit on top of it"
    return True, ""


def pack_route(
    items: list[PackItem],
    deck_length_cm: int,
    deck_width_cm: int,
    deck_height_cm: int,
    payload_kg: float | None = None,
) -> PackResult:
    """Pack one vehicle's load, LIFO-correct by construction.

    Corner-point placement with a deepest-first scoring rule, walked in
    reverse delivery order. Candidate positions are the faces of already-loaded
    freight, which is where an optimal placement always sits.
    """
    container = {
        "l": deck_length_cm, "w": deck_width_cm, "h": deck_height_cm,
        "payload_kg": payload_kg,
    }
    if not items:
        return PackResult(
            feasible=True, container=container, notes=["Nothing to load."]
        )

    deck_volume = deck_length_cm * deck_width_cm * deck_height_cm
    # Last drop loads first, hard against the bulkhead.
    queue = sorted(items, key=lambda i: (-i.seq, -i.volume_cm3))

    placed: list[Placement] = []
    unplaced: list[Unplaced] = []
    total_weight = 0.0

    for item in queue:
        best: Placement | None = None
        best_score: tuple | None = None
        last_reason = "no free space with a legal unloading order"

        # Candidate corners are the faces of what is already loaded, plus the
        # deck origin. An optimal placement always abuts something, so this
        # covers every position worth trying — and unlike a maintained
        # extreme-point list it cannot go stale and strand usable space.
        xs = sorted({0, *(p.x2 for p in placed)})
        ys = sorted({0, *(p.y2 for p in placed)})
        zs = sorted({0, *(p.z2 for p in placed)})

        for px, py, pz in ((x, y, z) for x in xs for y in ys for z in zs):
            for length, width, height in item.orientations():
                if px + length > deck_length_cm:
                    continue
                if py + width > deck_width_cm:
                    continue
                if pz + height > deck_height_cm:
                    continue
                cand = Placement(item, px, py, pz, length, width, height)
                if any(cand.intersects(p) for p in placed):
                    continue
                ok, why = _supported(cand, placed)
                if not ok:
                    last_reason = why
                    continue
                ok, why = _lifo_ok(cand, placed)
                if not ok:
                    last_reason = why
                    continue
                # Deepest, then lowest, then kerb-side: pushes each drop's
                # freight as far from the door as its ordering allows.
                score = (px, pz, py)
                if best_score is None or score < best_score:
                    best, best_score = cand, score

        if best is None:
            unplaced.append(Unplaced(item.shipment_id, item.code, last_reason))
            continue
        if payload_kg is not None and total_weight + item.weight_kg > payload_kg + 1e-6:
            unplaced.append(
                Unplaced(item.shipment_id, item.code, "would exceed the payload limit")
            )
            continue

        placed.append(best)
        total_weight += item.weight_kg

    used_volume = sum(p.length_cm * p.width_cm * p.height_cm for p in placed)
    floor_used = sum(p.length_cm * p.width_cm for p in placed if p.z == 0)

    cog_x_pct = 0.0
    if total_weight > 0:
        moment = sum(
            (p.x + p.length_cm / 2) * p.item.weight_kg for p in placed
        )
        cog_x_pct = round(100 * moment / total_weight / deck_length_cm, 1)
    # Only judge the balance once the deck carries enough for axle load to bite.
    payload_ratio = total_weight / payload_kg if payload_kg else 1.0
    cog_checked = bool(placed) and payload_ratio >= COG_CHECK_MIN_PAYLOAD
    cog_ok = (
        COG_SAFE_MIN * 100 <= cog_x_pct <= COG_SAFE_MAX * 100 if cog_checked else True
    )

    # Independent audit of the invariant the placement rule is meant to hold.
    lifo_ok, lifo_notes = verify_lifo(placed)

    notes = list(lifo_notes)
    if not cog_ok:
        notes.append(
            f"Centre of gravity sits {cog_x_pct}% down the deck — outside the "
            f"{int(COG_SAFE_MIN * 100)}-{int(COG_SAFE_MAX * 100)}% axle band at "
            f"{round(payload_ratio * 100)}% payload. Redistribute before dispatch."
        )
    elif placed and not cog_checked:
        notes.append(
            f"Centre of gravity {cog_x_pct}% — not axle-critical at "
            f"{round(payload_ratio * 100)}% payload."
        )

    return PackResult(
        feasible=not unplaced and lifo_ok,
        placements=placed,
        unplaced=unplaced,
        volume_utilization_pct=round(100 * used_volume / deck_volume, 1) if deck_volume else 0.0,
        floor_utilization_pct=round(
            100 * floor_used / (deck_length_cm * deck_width_cm), 1
        ) if deck_length_cm and deck_width_cm else 0.0,
        cog_x_pct=cog_x_pct,
        cog_ok=cog_ok,
        lifo_ok=lifo_ok,
        container=container,
        # Loading order is the order the crew actually stacks the truck.
        load_order=[p.item.code for p in placed],
        notes=notes,
    )


def verify_lifo(placements: list[Placement]) -> tuple[bool, list[str]]:
    """Audit every pair. Cheap at route scale, and it keeps the packer honest."""
    problems: list[str] = []
    for i in placements:
        for j in placements:
            if j.item.seq <= i.item.seq:
                continue
            if i.x2 <= j.x and i.overlaps_yz(j):
                problems.append(
                    f"{i.item.code} (drop {i.item.seq}) is trapped behind "
                    f"{j.item.code} (drop {j.item.seq})"
                )
            elif i.z2 <= j.z and i.overlaps_xy(j):
                problems.append(
                    f"{i.item.code} (drop {i.item.seq}) is buried under "
                    f"{j.item.code} (drop {j.item.seq})"
                )
    return not problems, problems
