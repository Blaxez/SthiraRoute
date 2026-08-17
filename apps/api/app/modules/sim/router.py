"""Shift simulation control — the operating day the dispatcher drives."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.services.cities import CITIES, city_list
from app.services.events import hub
from app.services.simulation import (
    SHIFT_END_MIN,
    get_config,
    get_state,
    inject,
    load_playbook,
    prepare_shift,
    reset_clock,
    set_config,
    snapshot,
    switch_city,
    tick,
)

router = APIRouter(prefix="/sim", tags=["simulation"])


class SimConfigBody(BaseModel):
    minutes_per_tick: int | None = None
    free_flow_kmh: float | None = None
    service_variance: float | None = None
    breakdown_per_vehicle_hour: float | None = None
    congestion_per_hour: float | None = None
    new_order_per_hour: float | None = None
    cancel_per_hour: float | None = None
    gps_dropout_per_vehicle_hour: float | None = None
    weather_change_per_hour: float | None = None
    warehouse_delay_per_hour: float | None = None
    sla_risk_margin_min: int | None = None
    reopt_cooldown_min: int | None = None
    seed: int | None = None


class InjectBody(BaseModel):
    kind: str


class PlaybookBody(BaseModel):
    scenario: str


class CityBody(BaseModel):
    city: str


@router.get("/state")
def state(db: Session = Depends(get_db)):
    return snapshot(db)


@router.get("/cities")
def cities():
    """The metros the demo can run in, with what makes each one different."""
    return city_list()


@router.post("/city")
async def set_city(body: CityBody, db: Session = Depends(get_db)):
    """Rebuild the demo in another metro and rewind to 06:00."""
    if body.city not in CITIES:
        raise HTTPException(404, f"unknown city: {body.city}")
    snap = switch_city(db, body.city)
    await hub.publish("fleet", {"type": "demo_reset", "city": body.city})
    return snap


@router.post("/prepare")
async def prepare(solve_seconds: int = 8, db: Session = Depends(get_db)):
    """Reset the clock, plan, and dispatch — one call to a demo-ready 06:00."""
    snap = prepare_shift(db, solve_seconds=solve_seconds)
    await hub.publish("fleet", {"type": "sim_prepared", "run_id": snap.get("run_id")})
    return snap


@router.post("/demo")
async def start_demo(solve_seconds: int = 8, db: Session = Depends(get_db)):
    """The judge button: plan, dispatch, and load the scripted operating day."""
    snap = prepare_shift(db, solve_seconds=solve_seconds)
    load_playbook(db, "full_day")
    out = snapshot(db)
    out["run_id"] = snap.get("run_id")
    await hub.publish("fleet", {"type": "sim_prepared", "run_id": out.get("run_id")})
    return out


@router.post("/tick")
async def step(db: Session = Depends(get_db)):
    snap = tick(db)
    # A dispatched re-plan replaces committed routes, so the UI has to reload
    # them rather than just moving markers.
    decision = snap.get("decision") or {}
    await hub.publish(
        "fleet",
        {
            "type": "sim_tick",
            "clock": snap["clock"],
            "replanned": decision.get("action") == "dispatched",
        },
    )
    return snap


@router.post("/run")
def run(running: bool = True, db: Session = Depends(get_db)):
    """Mark the clock as running. The dispatcher UI drives the tick cadence."""
    st = get_state(db)
    st.running = running
    db.commit()
    return snapshot(db)


@router.post("/autopilot")
def autopilot(on: bool = True, db: Session = Depends(get_db)):
    """When on, the simulation re-plans in response to its own disruptions."""
    st = get_state(db)
    st.autopilot = on
    db.commit()
    return snapshot(db)


@router.post("/inject")
async def inject_event(body: InjectBody, db: Session = Depends(get_db)):
    """Fire one disruption now, instead of waiting for probability to oblige."""
    try:
        snap = inject(db, body.kind)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if "error" in snap:
        raise HTTPException(409, snap["error"])
    await hub.publish(
        "fleet", {"type": "sim_injected", "kind": body.kind, "clock": snap["clock"]}
    )
    return snap


@router.post("/playbook")
def playbook(body: PlaybookBody, db: Session = Depends(get_db)):
    """Load a scripted sequence of disruptions timed across the shift."""
    try:
        load_playbook(db, body.scenario)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return snapshot(db)


@router.post("/jump")
def jump(to_min: int, db: Session = Depends(get_db)):
    """Skip the quiet stretch: tick forward until the clock reaches `to_min`.

    Ticking rather than setting the clock keeps the day causal — a jump still
    delivers stops, still burns fuel, and still fires scripted beats.
    """
    st = get_state(db)
    if to_min <= st.clock_min:
        raise HTTPException(400, "jump target must be later than the current clock")
    cfg = get_config(st)
    target = min(to_min, SHIFT_END_MIN)
    # Bounded so a bad request cannot spin the solver for minutes.
    for _ in range(max(1, (target - st.clock_min) // max(1, cfg.minutes_per_tick)) + 1):
        st = get_state(db)
        if st.clock_min >= target:
            break
        tick(db)
    return snapshot(db)


@router.post("/reset")
def reset(db: Session = Depends(get_db)):
    reset_clock(db)
    return snapshot(db)


@router.patch("/config")
def config(body: SimConfigBody, db: Session = Depends(get_db)):
    set_config(db, **body.model_dump(exclude_none=True))
    return snapshot(db)
