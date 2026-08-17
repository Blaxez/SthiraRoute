"""Self-check for the 3D packer. Run: python tests/test_packing.py

The LIFO invariant is the whole point of this module, so it is asserted from
two directions: the placement rule must produce it, and the independent
verifier must agree.
"""

from app.services.packing import PackItem, Placement, pack_route, verify_lifo


def item(seq, code=None, l=80, w=60, h=70, kg=50, fragile=False, stackable=True):
    return PackItem(
        shipment_id=seq, code=code or f"SHP-{seq:02d}", seq=seq,
        length_cm=l, width_cm=w, height_cm=h, weight_kg=kg,
        fragile=fragile, stackable=stackable,
    )


DECK = (430, 200, 200)  # Eicher 14ft


def test_empty_load_is_trivially_feasible():
    r = pack_route([], *DECK)
    assert r.feasible and not r.placements


def test_everything_fits_in_a_big_truck():
    items = [item(i) for i in range(1, 7)]
    r = pack_route(items, *DECK, payload_kg=2000)
    assert r.feasible, [u.reason for u in r.unplaced]
    assert len(r.placements) == 6
    assert r.volume_utilization_pct > 0


def test_lifo_holds_by_construction():
    """Earlier drops must never be trapped behind or under later drops."""
    items = [item(i) for i in range(1, 9)]
    r = pack_route(items, *DECK, payload_kg=2000)
    ok, problems = verify_lifo(r.placements)
    assert ok, problems
    assert r.lifo_ok


def test_first_drop_is_nearest_the_door():
    """The whole point: drop 1 must be reachable without moving anything."""
    items = [item(i) for i in range(1, 6)]
    r = pack_route(items, *DECK, payload_kg=2000)
    by_seq = {p.item.seq: p for p in r.placements}
    # Every later drop sits deeper (lower x) than, or clear of, the first.
    first = by_seq[1]
    for seq, p in by_seq.items():
        if seq == 1:
            continue
        blocks = p.x >= first.x2 and first.overlaps_yz(p)
        assert not blocks, f"drop {seq} at x={p.x} blocks drop 1 at x={first.x}"


def test_load_order_is_reverse_delivery_order():
    items = [item(i) for i in range(1, 6)]
    r = pack_route(items, *DECK, payload_kg=2000)
    seqs = [p.item.seq for p in r.placements]
    assert seqs == sorted(seqs, reverse=True), seqs


def test_nothing_stacks_on_a_fragile_carton():
    """A fragile item must never end up load-bearing."""
    items = [item(1, fragile=True, l=200, w=200, h=50)] + [item(i) for i in range(2, 6)]
    r = pack_route(items, *DECK, payload_kg=2000)
    fragile = next(p for p in r.placements if p.item.fragile)
    for p in r.placements:
        if p is fragile:
            continue
        resting_on = p.z == fragile.z2 and p.overlaps_xy(fragile)
        assert not resting_on, f"{p.item.code} is stacked on fragile {fragile.item.code}"


def test_non_stackable_carries_nothing():
    items = [item(1, stackable=False, l=200, w=200, h=50)] + [item(i) for i in range(2, 5)]
    r = pack_route(items, *DECK, payload_kg=2000)
    base = next(p for p in r.placements if not p.item.stackable)
    for p in r.placements:
        if p is base:
            continue
        assert not (p.z == base.z2 and p.overlaps_xy(base)), "stacked on a no-stack item"


def test_oversize_carton_is_reported_not_crammed():
    items = [item(1), item(2, l=9999)]
    r = pack_route(items, *DECK, payload_kg=2000)
    assert not r.feasible
    assert [u.code for u in r.unplaced] == ["SHP-02"]
    assert len(r.placements) == 1


def test_payload_limit_is_respected():
    items = [item(i, kg=300) for i in range(1, 6)]
    r = pack_route(items, *DECK, payload_kg=700)
    carried = sum(p.item.weight_kg for p in r.placements)
    assert carried <= 700, carried
    assert r.unplaced


def test_tiny_deck_overflows_gracefully():
    items = [item(i) for i in range(1, 10)]
    r = pack_route(items, 100, 100, 100, payload_kg=5000)
    assert not r.feasible
    assert r.unplaced
    assert all(u.reason for u in r.unplaced)


def test_nothing_floats_in_mid_air():
    items = [item(i, l=100, w=100, h=60) for i in range(1, 9)]
    r = pack_route(items, 200, 200, 200, payload_kg=5000)
    for p in r.placements:
        if p.z == 0:
            continue
        supporters = [
            q for q in r.placements if q is not p and q.z2 == p.z and q.overlaps_xy(p)
        ]
        assert supporters, f"{p.item.code} floats at z={p.z}"


def test_no_two_cartons_occupy_the_same_space():
    items = [item(i, l=90, w=70, h=60) for i in range(1, 12)]
    r = pack_route(items, *DECK, payload_kg=5000)
    for i, a in enumerate(r.placements):
        for b in r.placements[i + 1:]:
            assert not a.intersects(b), f"{a.item.code} intersects {b.item.code}"


def test_centre_of_gravity_is_reported():
    items = [item(i) for i in range(1, 7)]
    r = pack_route(items, *DECK, payload_kg=2000)
    assert 0 < r.cog_x_pct < 100
    assert isinstance(r.cog_ok, bool)


def test_verifier_catches_a_deliberately_bad_layout():
    """Sanity: the auditor must fail a layout that is actually trapped."""
    early, late = item(1), item(2)
    placements = [
        # drop 1 sits at the bulkhead, drop 2 sits between it and the door
        Placement(early, 0, 0, 0, 80, 60, 70),
        Placement(late, 80, 0, 0, 80, 60, 70),
    ]
    ok, problems = verify_lifo(placements)
    assert not ok
    assert "trapped behind" in problems[0]


def test_verifier_catches_burial():
    early, late = item(1), item(2)
    placements = [
        Placement(early, 0, 0, 0, 80, 60, 70),
        Placement(late, 0, 0, 70, 80, 60, 70),  # drop 2 on top of drop 1
    ]
    ok, problems = verify_lifo(placements)
    assert not ok
    assert "buried under" in problems[0]


def test_same_stop_items_do_not_constrain_each_other():
    """Two cartons for one customer come off together — no ordering rule."""
    items = [item(1, code="A"), item(1, code="B"), item(2, code="C")]
    for i, it in enumerate(items):
        it.shipment_id = i + 1
    r = pack_route(items, *DECK, payload_kg=2000)
    assert r.feasible, [u.reason for u in r.unplaced]
    assert len(r.placements) == 3


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
