"""Gemini briefing officer — explains the live shift. Never routes."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from app.core.config import settings
from app.services.simulation import snapshot as sim_snapshot

log = logging.getLogger("sthiraroute.assistant")

GEMINI_GENERATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_STREAM = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent"
)
LIVE_WS = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)

# Seconds a key sits out after 429 / 401. Chat and voice share this map so a
# quota hit on talk does not keep hammering the same key from the next Ask.
_KEY_SKIP_UNTIL: dict[str, float] = {}
_RATE_LIMIT_COOLDOWN_S = 90.0
_BAD_KEY_COOLDOWN_S = 3600.0


def gemini_keys() -> list[str]:
    """Primary key plus extras. Order is the failover order."""
    chunks = [settings.gemini_api_key or "", settings.gemini_api_keys or ""]
    out: list[str] = []
    for chunk in chunks:
        for part in chunk.replace("\n", ",").replace(";", ",").split(","):
            k = part.strip()
            if k and k not in out:
                out.append(k)
    return out


def mark_key_limited(key: str, *, bad: bool = False) -> None:
    seconds = _BAD_KEY_COOLDOWN_S if bad else _RATE_LIMIT_COOLDOWN_S
    _KEY_SKIP_UNTIL[key] = time.monotonic() + seconds
    log.warning("gemini key …%s cooling for %.0fs (%s)", key[-6:], seconds, "rejected" if bad else "rate-limited")


def ordered_keys() -> list[str]:
    """Keys that are not cooling first; exhausted keys last, as a last try."""
    now = time.monotonic()
    keys = gemini_keys()
    fresh = [k for k in keys if _KEY_SKIP_UNTIL.get(k, 0) <= now]
    tired = [k for k in keys if _KEY_SKIP_UNTIL.get(k, 0) > now]
    return fresh + tired


def _is_rate_limit(status: int, body: str = "") -> bool:
    low = (body or "").lower()
    return status == 429 or "resource_exhausted" in low or "quota" in low or "rate limit" in low


def _is_bad_key(status: int, body: str = "") -> bool:
    low = (body or "").lower()
    return status in (401, 403) or "api key not valid" in low or "api_key_invalid" in low

# A named model goes down for an afternoon; a chain does not. Order is fastest
# measured first — the tail is only there so a question still gets answered.
FALLBACK_MODELS = ["gemini-3.7-flash", "gemini-flash-latest", "gemini-3.5-flash"]

# Voice is a different catalogue: only a handful of models speak bidi at all.
# gemini-3.1-flash-live-preview is the one Gemini 3 general-purpose live model —
# native audio end to end (no transcribe/synthesise hop) and built for exactly
# the thing this officer does, driving tools from speech. The 2.5 native-audio
# pair behind it is the older generation, kept so a preview model going dark
# for an afternoon does not take the voice down with it.
#
# Every field below was probed against the live endpoint rather than read off a
# blog: thinkingLevel is NOT a generationConfig field (it is rejected outright,
# it lives under thinkingConfig), and proactivity / enableAffectiveDialog are
# rejected by 3.1 altogether. Sending any of those closes the socket with 1007
# before the officer says a word.
LIVE_FALLBACK_MODELS = [
    "gemini-3.1-flash-live-preview",
    "gemini-2.5-flash-native-audio-latest",
    "gemini-2.5-flash-native-audio-preview-12-2025",
]


def live_model_chain() -> list[str]:
    chain = [settings.gemini_live_model, *LIVE_FALLBACK_MODELS]
    out: list[str] = []
    for m in chain:
        if m and m not in out:
            out.append(m)
    return out

# Names the board actually answers to. The browser sends its own registry on
# every call; this is only the floor for a request that arrives without one.
BASE_TARGETS = [
    "clock", "headline", "chips", "scorecard", "log", "map", "legend", "zones",
    "timeline", "fleet", "desks", "lab", "start", "play", "speed", "gate",
]


def model_chain() -> list[str]:
    chain = [settings.gemini_model, *FALLBACK_MODELS]
    out: list[str] = []
    for m in chain:
        if m and m not in out:
            out.append(m)
    return out


def tools_for(targets: list[str] | None = None) -> list[dict]:
    """Tool schema built around the targets that exist on screen right now."""
    names = [t for t in (targets or []) if isinstance(t, str)] or BASE_TARGETS
    return [
        {
            "functionDeclarations": [
                {
                    "name": "highlight",
                    "description": (
                        "Point the dispatcher at one part of the screen while you explain "
                        "it. Call this whenever you mention a control, a number, a truck "
                        "or a consignment. It only moves the screen and returns no facts. "
                        "Always speak a sentence in the same turn."
                    ),
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "target": {
                                "type": "STRING",
                                "description": (
                                    "One of: " + ", ".join(names)
                                    + " — or vehicle:CODE / shipment:CODE."
                                ),
                            },
                            "note": {
                                "type": "STRING",
                                "description": "One short line shown beside the highlight.",
                            },
                        },
                        "required": ["target"],
                    },
                },
                {
                    "name": "tour",
                    "description": (
                        "Walk the dispatcher through several things in order, one "
                        "highlight after another. Use this instead of many separate "
                        "highlight calls when you are explaining how the board works."
                    ),
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "steps": {
                                "type": "ARRAY",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "target": {"type": "STRING"},
                                        "note": {"type": "STRING"},
                                    },
                                    "required": ["target"],
                                },
                            }
                        },
                        "required": ["steps"],
                    },
                },
                {
                    "name": "focus_map",
                    "description": (
                        "Centre and zoom the map on one truck or consignment, and "
                        "select it. This only moves the screen — it returns no facts. "
                        "Use look_up for the numbers."
                    ),
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "kind": {"type": "STRING", "enum": ["shipment", "vehicle"]},
                            "code": {"type": "STRING"},
                            "note": {"type": "STRING"},
                        },
                        "required": ["kind", "code"],
                    },
                },
                {
                    "name": "look_up",
                    "description": (
                        "Fetch the detail for one truck, consignment or zone: window, "
                        "weight, customer, stops, status, hours. The snapshot only "
                        "carries a one-line summary, so call this before quoting any "
                        "number about a specific thing."
                    ),
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "kind": {"type": "STRING", "enum": ["vehicle", "shipment", "zone"]},
                            "code": {"type": "STRING", "description": "MH-01, SHP-13, or a zone name."},
                        },
                        "required": ["kind", "code"],
                    },
                },
                {
                    "name": "shift_log",
                    "description": "The last things that happened to the plan, newest first.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {"limit": {"type": "INTEGER"}},
                    },
                },
                {
                    "name": "plan_numbers",
                    "description": (
                        "Cost, distance, empty running, deck used, on-time, and how this "
                        "plan compares with the naive nearest-neighbour route."
                    ),
                    "parameters": {"type": "OBJECT", "properties": {}},
                },
                {
                    "name": "list_zones",
                    "description": "Every municipal no-entry window, its hours and whether it is in force.",
                    "parameters": {"type": "OBJECT", "properties": {}},
                },
                {
                    "name": "switch_desk",
                    "description": "Open another stakeholder desk that shares the same committed plan.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "desk": {
                                "type": "STRING",
                                "enum": ["dispatch", "dock", "driver", "track"],
                            }
                        },
                        "required": ["desk"],
                    },
                },
                {
                    "name": "open_lab",
                    "description": "Open or close the What-if lab (plan, curfews, disruptions).",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {"open": {"type": "BOOLEAN"}},
                        "required": ["open"],
                    },
                },
                {
                    "name": "open_deck",
                    "description": (
                        "Open the 3D deck viewer for one truck — the rotatable load "
                        "plan showing how its cartons actually stack, the unload "
                        "sequence and the centre of gravity. Call this whenever the "
                        "dispatcher asks about loading, stacking, LIFO, balance, or "
                        "'show me MH-03'. If the viewer is already open on a "
                        "different truck this switches it to the one you name, so "
                        "never say you cannot change trucks — just call it again."
                    ),
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "code": {
                                "type": "STRING",
                                "description": "Truck code, e.g. MH-03.",
                            },
                            "camera": {
                                "type": "STRING",
                                "enum": ["door", "top", "side"],
                                "description": (
                                    "Where to stand: door (see the unload face), "
                                    "top (see the floor plan), side (see the stacking)."
                                ),
                            },
                            "note": {"type": "STRING"},
                        },
                        "required": ["code"],
                    },
                },
                {
                    "name": "close_overlay",
                    "description": (
                        "Close whatever is open over the board — the deck viewer or "
                        "the Lab — and go back to the desk underneath. Use this "
                        "before you point at something on the board itself."
                    ),
                    "parameters": {"type": "OBJECT", "properties": {}},
                },
            ]
        }
    ]


# Kept for the callers that do not carry a live registry (tests, Live setup
# before the first context frame lands).
TOOLS = tools_for()


def system_prompt(target_brief: str = "") -> str:
    screen = (
        f"\nWhat you may point at, and what each one is:\n{target_brief}\n"
        if target_brief
        else ""
    )
    return (
        "You are the shift briefing officer for SthiraRoute, a dispatcher product "
        "for Indian city logistics (SIH Problem Statement 2).\n"
        "The product is a prescriptive decision system: Google OR-Tools plans "
        "heterogeneous-fleet CVRPTW routes under municipal no-entry windows, "
        "capacity, compatibility and LIFO loading. An LLM never invents a route.\n"
        "Your job is to explain what is on screen right now in plain language so a "
        "first-time visitor (or a hackathon judge) understands the operating day.\n"
        "Rules:\n"
        "- The snapshot is a summary, not the whole board. When a detail is not in "
        "it — a consignment's window or weight, a truck's load, a zone's hours, the "
        "cost breakdown, what happened earlier — call look_up, shift_log, "
        "plan_numbers or list_zones and use the answer. Never tell the dispatcher "
        "something is missing until you have called the tool that would fetch it.\n"
        "- Invent nothing. If a tool says it is not there, say that plainly.\n"
        "- The large clock is SHIFT TIME (06:00–22:00 simulated), not the wall clock.\n"
        "- Playback 1×/2×/4× speeds the simulator; trucks follow the same road polyline the map paints.\n"
        "- Solid coloured lines = committed plan the drivers are on. Dashed = a proposal waiting for approval.\n"
        "- The bright playhead on the timeline is 'now'. Red bands are no-entry / curfew windows. "
        "Stops sit outside those bands because the solver waits out the ban.\n"
        "- Sage/green = on time / healthy. Red = blocked, late, or broken. Amber = needs a human.\n"
        "- Desks (Dispatch / Dock / Driver / Track) are the same committed plan, four jobs.\n"
        "- Lab is for 'what if': breakdowns, rush orders, curfew toggles, cost explain.\n"
        "You are driving this screen, not describing a screenshot of it. Move it:\n"
        "- When you mention a control, call highlight so the board points at it. "
        "Use tour when you are walking someone through more than two things.\n"
        "- Loading, stacking, LIFO, axle balance, 'show me MH-03', 'what is on that "
        "truck' — open_deck(code). It opens the 3D deck viewer, and calling it again "
        "with another code switches the truck it is showing. There is no truck you "
        "cannot open and no need to close one first.\n"
        "- open_deck also takes a camera: 'top' to argue about the floor plan, "
        "'side' to argue about stacking, 'door' to argue about unload order.\n"
        "- The deck viewer covers the board. Call close_overlay before you point at "
        "anything on the desk behind it.\n"
        "- A question about a truck's position or route is focus_map, not open_deck.\n"
        "- Never say you are unable to open, switch or show something. You have a "
        "tool for every one of those. Call it, then say what is now on screen.\n"
        "- Never end a turn on a tool call alone. The dispatcher needs the sentence "
        "as well as the pointer.\n"
        "- Speak like a depot clerk, not a chatbot. Short sentences. No buzzwords, no emojis.\n"
        "- Markdown is fine for structure: **bold** for a number that matters, "
        "'- ' bullets for a list. Never a table, never a heading.\n"
        "- Hindi is fine if the user writes in Hindi; otherwise English.\n"
        + screen
    )


def compact_snapshot(db, extra: dict | None = None) -> dict[str, Any]:
    """Lean context the model can actually use — not the whole database."""
    sim = sim_snapshot(db)
    extra = extra or {}
    events = (sim.get("events") or [])[:8]
    score = sim.get("scorecard") or {}
    city = sim.get("city") or {}
    traffic = sim.get("traffic") or {}
    decision = extra.get("decision") or sim.get("decision")
    kpis = extra.get("kpis") or {}
    plan = kpis.get("plan") or {}
    selected = extra.get("selected")
    view = extra.get("view") or "dispatch"
    vehicles = extra.get("vehicles") or []
    shipments = extra.get("shipments") or []
    overlays = extra.get("overlays") or []

    # One line per truck is what most questions actually need. Everything
    # heavier — windows, weights, customer names, zone hours, the shift log —
    # is a tool call away, so the officer pulls detail instead of us pushing
    # the whole database into every single turn.
    fleet = [
        f"{v.get('code')} {v.get('status')}"
        + (f" {v.get('vehicle_type') or v.get('kind')}" if (v.get("vehicle_type") or v.get("kind")) else "")
        for v in vehicles[:14]
    ]
    trouble = [
        v.get("code") for v in vehicles
        if v.get("status") in ("down", "broken_down") or v.get("gps_stale_min")
    ]
    late = [s_.get("code") for s_ in shipments if (s_.get("late_min") or 0) > 0][:8]

    return {
        "shift_clock": sim.get("clock"),
        "shift_running": sim.get("running"),
        "shift_over": sim.get("shift_over"),
        "autopilot": sim.get("autopilot"),
        "city": city.get("label") or city.get("id"),
        "traffic_kmh": traffic.get("speed_kmh"),
        "traffic_label": traffic.get("label") or traffic.get("band"),
        "desk": view,
        "selected": selected,
        "kpis": {
            "on_time_pct": plan.get("on_time_pct"),
            "committed_routes": plan.get("committed_routes"),
            "unassigned": (kpis.get("shipments") or {}).get("unassigned"),
            "delivered": score.get("delivered"),
            "late": score.get("late"),
        },
        "last_decision": (decision or {}).get("reason") if isinstance(decision, dict) else decision,
        "latest_event": (events[0] or {}).get("message") if events else None,
        "fleet": fleet,
        "needs_a_human": trouble,
        "late_orders": late,
        "orders_total": len(shipments),
        "zones_total": len(overlays),
        "more": "Call look_up, shift_log, plan_numbers or list_zones for anything not listed here.",
        "how_to_read_the_screen": {
            "clock": "Simulated operating day 06:00–22:00, not wall time.",
            "map": "Coloured dot = truck on its road. Bright line = still to go. Pale = already driven. Dashed = unapproved re-plan.",
            "timeline": "One row per truck. Yellow playhead = now. Red band = no-entry window.",
            "desks": "Dispatch plans; Dock loads; Driver delivers; Track is the customer's copy.",
        },
    }


def _hm(m) -> str:
    if m is None:
        return "--:--"
    try:
        m = int(m)
    except (TypeError, ValueError):
        return "--:--"
    return f"{m // 60:02d}:{m % 60:02d}"


# Tools that answer with data instead of moving the screen. These run here and
# their result goes back to the model; everything else is a UI action and is
# forwarded to the browser with a bare acknowledgement.
DATA_TOOLS = {"look_up", "shift_log", "plan_numbers", "list_zones"}


def _clean(d: dict) -> dict:
    """Drop nulls before a tool answer goes back to the model.

    A field sitting there as null reads to the model as "this is unknown", and
    it will happily tell the dispatcher the weight is not specified while the
    number is right there in the next key.
    """
    return {k: v for k, v in d.items() if v is not None}


def run_tool(db, name: str, args: dict, extra: dict | None = None) -> dict:
    """Answer a data tool from the live board. Read-only, always."""
    extra = extra or {}
    sim = sim_snapshot(db)
    vehicles = extra.get("vehicles") or []
    shipments = extra.get("shipments") or []
    overlays = extra.get("overlays") or []
    kpis = extra.get("kpis") or {}
    code = str(args.get("code") or "").strip().lower()

    def hit(rows):
        return next((r for r in rows if str(r.get("code") or r.get("name") or "").lower() == code), None)

    if name == "shift_log":
        limit = int(args.get("limit") or 8)
        return {
            "events": [
                {"at": e.get("at"), "kind": e.get("kind"), "message": e.get("message")}
                for e in (sim.get("events") or [])[: max(1, min(limit, 25))]
            ]
        }

    if name == "plan_numbers":
        plan = kpis.get("plan") or {}
        return _clean({
            "on_time_pct": plan.get("on_time_pct"),
            "committed_routes": plan.get("committed_routes"),
            "cost": plan.get("operating_cost") or plan.get("cost"),
            "distance_km": plan.get("total_distance_km") or plan.get("distance_km"),
            "empty_running_pct": plan.get("empty_running_pct"),
            "deck_used_pct": plan.get("deck_used_pct"),
            "scorecard": sim.get("scorecard"),
            "unassigned": (kpis.get("shipments") or {}).get("unassigned"),
        })

    if name == "list_zones":
        return {
            "zones": [
                {
                    "name": (o.get("name") or "").replace("(template)", "").strip(),
                    "in_force": o.get("active"),
                    "kind": o.get("kind"),
                    "window": f"{_hm(o.get('ban_start_min'))}–{_hm(o.get('ban_end_min'))}",
                }
                for o in overlays
            ]
        }

    if name == "look_up":
        kind = args.get("kind")
        if kind == "vehicle":
            v = hit(vehicles)
            if not v:
                return {"found": False, "known": [x.get("code") for x in vehicles][:14]}
            live = next((x for x in (sim.get("vehicles") or []) if x.get("code") == v.get("code")), {})
            return _clean({
                "found": True, "code": v.get("code"), "status": v.get("status"),
                "type": v.get("vehicle_type") or v.get("kind"),
                "capacity_kg": v.get("capacity_kg"),
                "gps_stale_min": v.get("gps_stale_min") or live.get("gps_stale_min"),
                "at": [round(v["lat"], 4), round(v["lon"], 4)] if v.get("lat") is not None else None,
                "carrying": [
                    s.get("code") for s in shipments if s.get("vehicle_id") == v.get("id")
                ][:12],
            })
        if kind == "shipment":
            s_ = hit(shipments)
            if not s_:
                return {"found": False, "known": [x.get("code") for x in shipments][:20]}
            return _clean({
                "found": True, "code": s_.get("code"), "customer": s_.get("customer_name"),
                "status": s_.get("status"), "priority": s_.get("priority"),
                "window": f"{_hm(s_.get('tw_start_min'))}–{_hm(s_.get('tw_end_min'))}",
                "kg": s_.get("weight_kg") or s_.get("demand_kg"),
                "late_min": s_.get("late_min"),
            })
        o = hit(overlays)
        if not o:
            return {"found": False, "known": [x.get("name") for x in overlays][:12]}
        return _clean({
            "found": True, "name": o.get("name"), "in_force": o.get("active"),
            "kind": o.get("kind"),
            "window": f"{_hm(o.get('ban_start_min'))}–{_hm(o.get('ban_end_min'))}",
        })

    return {"error": f"no such tool: {name}"}


def extract_actions(parts: list) -> tuple[str, list[dict]]:
    text_bits = []
    actions = []
    for p in parts or []:
        if isinstance(p, dict) and p.get("text"):
            text_bits.append(p["text"])
        fc = p.get("functionCall") if isinstance(p, dict) else None
        if fc:
            args = fc.get("args") or {}
            actions.append({"name": fc.get("name"), "args": args})
    return "".join(text_bits).strip(), actions


def _body(snap: dict, message: str, targets, brief: str) -> dict:
    return {
        "systemInstruction": {"parts": [{"text": system_prompt(brief)}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            "Live shift snapshot (JSON). Treat this as ground truth:\n"
                            + json.dumps(snap, ensure_ascii=False, default=str)
                            + "\n\nDispatcher says:\n"
                            + message
                        )
                    }
                ],
            }
        ],
        "tools": tools_for(targets),
        "generationConfig": {
            "temperature": 0.4,
            # Gemini 3 spends part of this budget thinking before it writes
            # anything. At 1024 a tool-calling turn could burn the lot on
            # thoughts and return an empty candidate.
            "maxOutputTokens": 2048,
        },
    }


async def _once(client, body: dict, stream: bool):
    """One turn, walking keys then models. Yields ('parts', parts) / ('delta', text).

    A 429 on one key fails over to the next key on the same model before we
    give up and try a slower model. A 404/503 is a model problem: skip the
    remaining keys for that model.
    """
    last_err = "No Gemini model answered."
    keys = ordered_keys()
    if not keys:
        yield ("error", "Gemini key is not configured on the API. Put GEMINI_API_KEY in apps/api/.env.")
        return
    sweeps = [(m, s) for s in range(2) for m in model_chain()]
    for model, sweep in sweeps:
        if sweep and model == sweeps[0][0]:
            await asyncio.sleep(1.2)
        tmpl = GEMINI_STREAM if stream else GEMINI_GENERATE
        url = tmpl.format(model=model)
        model_gone = False
        for key in keys:
            params = {"key": key}
            if stream:
                params["alt"] = "sse"
            parts: list[dict] = []
            try:
                if stream:
                    async with client.stream("POST", url, params=params, json=body) as resp:
                        body_txt = ""
                        if resp.status_code >= 400:
                            await resp.aread()
                            body_txt = resp.text
                        if _is_rate_limit(resp.status_code, body_txt):
                            mark_key_limited(key)
                            last_err = _friendly_http(resp.status_code, body_txt)
                            continue
                        if _is_bad_key(resp.status_code, body_txt):
                            mark_key_limited(key, bad=True)
                            last_err = _friendly_http(resp.status_code, body_txt)
                            continue
                        if resp.status_code in (404, 503):
                            last_err = _friendly_http(resp.status_code, body_txt)
                            model_gone = True
                            break
                        if resp.status_code >= 400:
                            yield ("error", _friendly_http(resp.status_code, body_txt))
                            return
                        async for line in resp.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            chunk = json.loads(line[5:].strip() or "{}")
                            cand = (chunk.get("candidates") or [{}])[0]
                            got = ((cand.get("content") or {}).get("parts")) or []
                            for p in got:
                                parts.append(p)
                                if p.get("text"):
                                    yield ("delta", p["text"])
                                elif p.get("functionCall"):
                                    fc = p["functionCall"]
                                    yield ("action", {"name": fc.get("name"), "args": fc.get("args") or {}})
                else:
                    resp = await client.post(url, params=params, json=body)
                    if _is_rate_limit(resp.status_code, resp.text):
                        mark_key_limited(key)
                        last_err = _friendly_http(resp.status_code, resp.text)
                        continue
                    if _is_bad_key(resp.status_code, resp.text):
                        mark_key_limited(key, bad=True)
                        last_err = _friendly_http(resp.status_code, resp.text)
                        continue
                    if resp.status_code in (404, 503):
                        last_err = _friendly_http(resp.status_code, resp.text)
                        model_gone = True
                        break
                    if resp.status_code >= 400:
                        yield ("error", _friendly_http(resp.status_code, resp.text))
                        return
                    cand = (resp.json().get("candidates") or [{}])[0]
                    parts = ((cand.get("content") or {}).get("parts")) or []
                    text, acts = extract_actions(parts)
                    if text:
                        yield ("delta", text)
                    for a in acts:
                        yield ("action", a)
            except httpx.HTTPError as e:
                last_err = f"Could not reach Gemini ({e.__class__.__name__})."
                continue
            if not parts:
                last_err = "Gemini returned an empty answer."
                continue
            yield ("parts", parts)
            yield ("model", model)
            return
        if model_gone:
            continue
    yield ("error", last_err)


async def converse(db, message: str, extra: dict | None = None, stream: bool = False):
    """The whole exchange as events: action / delta / done / error.

    Gemini answers a "what am I looking at" with tool calls first and the
    sentence second, so a single round is not an answer. Three rounds is the
    ceiling; the tools only paint the screen, so a loop cannot do damage.
    """
    if not gemini_keys():
        yield {
            "type": "error",
            "message": "Gemini key is not configured on the API. Put GEMINI_API_KEY in apps/api/.env.",
        }
        return

    extra = extra or {}
    snap = compact_snapshot(db, extra)
    body = _body(snap, message, extra.get("targets"), extra.get("target_brief") or "")
    convo = list(body["contents"])
    model_used = None

    async with httpx.AsyncClient(timeout=60.0) as client:
        for _round in range(4):
            parts: list[dict] = []
            said = False
            failed = False
            async for kind, payload in _once(client, {**body, "contents": convo}, stream):
                if kind == "delta" and payload:
                    said = True
                    yield {"type": "delta", "text": payload}
                elif kind == "action":
                    # Data tools are answered here; only screen actions travel
                    # to the browser.
                    if payload.get("name") not in DATA_TOOLS:
                        yield {"type": "action", **payload}
                elif kind == "parts":
                    parts = payload
                elif kind == "model":
                    model_used = payload
                elif kind == "error":
                    failed = True
                    yield {"type": "error", "message": payload}
            if failed:
                return
            if said:
                yield {"type": "done", "model": model_used}
                return
            calls = [p["functionCall"] for p in parts if isinstance(p, dict) and p.get("functionCall")]
            if not calls:
                # An empty candidate is almost always the free tier: the model
                # returns 200 with nothing in it once the day's quota is spent.
                # Saying "nothing at all" sent people looking through the code.
                yield {
                    "type": "error",
                    "message": (
                        "Gemini returned an empty answer. That is usually the API key's "
                        "quota or a busy model — check the key's limits in AI Studio, or "
                        "ask again in a minute."
                    ),
                }
                return
            # Hand the tool results back — including the model's own parts, which
            # carry the thought signatures Gemini 3 requires on the way in.
            convo = convo + [
                {"role": "model", "parts": parts},
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                # Gemini 3 matches a response to its call by id;
                                # without it a multi-call turn can attach the
                                # answer to the wrong question.
                                **({"id": c["id"]} if c.get("id") else {}),
                                "name": c.get("name"),
                                "response": (
                                    run_tool(db, c.get("name"), c.get("args") or {}, extra)
                                    if c.get("name") in DATA_TOOLS
                                    else {"ok": True}
                                ),
                            }
                        }
                        for c in calls
                    ],
                },
            ]
    yield {
        "type": "error",
        "message": "Gemini kept pointing at the screen without ever explaining it.",
    }


async def chat(db, message: str, extra: dict | None = None) -> dict[str, Any]:
    """Non-streaming shape, collected from the same exchange."""
    text: list[str] = []
    actions: list[dict] = []
    model = None
    err = None
    async for ev in converse(db, message, extra, stream=False):
        if ev["type"] == "delta":
            text.append(ev["text"])
        elif ev["type"] == "action":
            actions.append({"name": ev.get("name"), "args": ev.get("args") or {}})
        elif ev["type"] == "done":
            model = ev.get("model")
        elif ev["type"] == "error":
            err = ev["message"]
    joined = "".join(text).strip()
    if err and not joined:
        return {"ok": False, "text": err, "actions": actions}
    return {"ok": True, "text": joined, "actions": actions, "model": model}


def _friendly_http(status: int, body: str) -> str:
    low = (body or "").lower()
    if status in (401, 403) or "api key" in low or "permission" in low:
        return "Gemini rejected the API key. Check GEMINI_API_KEY in apps/api/.env (AI Studio key)."
    if status == 404:
        return f"Gemini model not found. Current model: {settings.gemini_model}."
    if status == 503:
        return (
            "Gemini is busy right now (every model in the chain returned 503). "
            "Ask again in a few seconds."
        )
    if status == 429:
        n = len(gemini_keys())
        if n > 1:
            return (
                "Gemini rate-limited a key; another key is configured and will be "
                "tried automatically. If you still see this, every key is over quota."
            )
        return (
            "Gemini says this key is over its quota. Free-tier keys run out; "
            "add another key as GEMINI_API_KEYS, or wait a minute."
        )
    snippet = (body or "")[:240]
    return f"Gemini error {status}: {snippet}"


def live_setup_message(
    snap: dict,
    targets: list[str] | None = None,
    brief: str = "",
    resume: str | None = None,
    model: str | None = None,
) -> dict:
    setup = {
        "model": f"models/{model or settings.gemini_live_model}",
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Puck"}}
            },
            "temperature": 0.35,
            # A guided tour is recall and pointing, not reasoning. Left on the
            # default the officer spends a beat thinking before every sentence,
            # and in a voice conversation a beat of silence reads as a hang.
            "thinkingConfig": {"thinkingLevel": "LOW"},
        },
        "systemInstruction": {
            "parts": [
                {
                    "text": (
                        system_prompt(brief)
                        + "\nYou speak out loud. Keep turns under 20 seconds. "
                        "Call highlight while you talk, so the dispatcher's eye lands "
                        "on the thing you are naming.\n"
                        "Current snapshot:\n"
                        + json.dumps(snap, ensure_ascii=False, default=str)[:6000]
                    )
                }
            ]
        },
        "tools": tools_for(targets),
        # Without these the log stays empty: in AUDIO modality the model emits
        # no text parts at all, so "what did it just say" had nowhere to come
        # from.
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
        # An audio session is capped at 15 minutes. A handle plus a sliding
        # window is the difference between a demo that keeps talking and one
        # that goes quiet halfway through with no error on screen.
        "sessionResumption": {"handle": resume} if resume else {},
        "contextWindowCompression": {"slidingWindow": {}},
        # The officer's own voice comes back through the dispatcher's microphone.
        # On the default sensitivity that echo reads as the dispatcher starting
        # to talk, so the model interrupts itself and the conversation dies after
        # one answer. LOW start sensitivity means it takes a real voice to cut
        # in; LOW end sensitivity plus a longer silence window means a thinking
        # pause mid-question is not mistaken for the end of the question.
        "realtimeInputConfig": {
            "automaticActivityDetection": {
                "startOfSpeechSensitivity": "START_SENSITIVITY_LOW",
                "endOfSpeechSensitivity": "END_SENSITIVITY_LOW",
                "prefixPaddingMs": 60,
                "silenceDurationMs": 900,
            }
        },
    }
    return {"setup": setup}
