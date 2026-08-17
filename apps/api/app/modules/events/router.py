from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import RunTrigger, VehicleStatus
from app.schemas import OptimizationRunOut, ShipmentCreate, ShipmentOut, VehicleOut
from app.services.dynamic import (
    reoptimize_after_breakdown,
    reoptimize_after_constraint_change,
    reoptimize_after_insert,
    simulate_gps_step,
)
from app.services.events import hub
from app.services.optimize import approve_run
from app.models import Shipment

router = APIRouter(prefix="/events", tags=["events"])


class BreakdownRequest(BaseModel):
    vehicle_id: int
    solve_seconds: int | None = 5
    auto_approve: bool = False


class InsertAndReoptRequest(ShipmentCreate):
    solve_seconds: int | None = 5
    auto_approve: bool = False


@router.post("/breakdown", response_model=OptimizationRunOut)
async def breakdown(body: BreakdownRequest, db: Session = Depends(get_db)):
    try:
        run = reoptimize_after_breakdown(
            db, body.vehicle_id, solve_seconds=body.solve_seconds
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    if body.auto_approve and run.status.value == "completed":
        run = approve_run(db, run.id)

    await hub.publish(
        "fleet",
        {
            "type": "breakdown_reopt",
            "vehicle_id": body.vehicle_id,
            "run_id": run.id,
            "status": run.status.value,
        },
    )
    return run


@router.post("/insert-and-reopt", response_model=OptimizationRunOut)
async def insert_and_reopt(body: InsertAndReoptRequest, db: Session = Depends(get_db)):
    data = body.model_dump()
    solve_seconds = data.pop("solve_seconds")
    auto_approve = data.pop("auto_approve")
    ship = Shipment(**data)
    db.add(ship)
    db.commit()
    db.refresh(ship)

    run = reoptimize_after_insert(
        db, solve_seconds=solve_seconds, shipment_code=ship.code
    )
    if auto_approve and run.status.value == "completed":
        run = approve_run(db, run.id)

    await hub.publish(
        "fleet",
        {
            "type": "insert_reopt",
            "shipment_id": ship.id,
            "run_id": run.id,
            "status": run.status.value,
        },
    )
    return run


@router.post("/reset-demo")
async def reset_demo(db: Session = Depends(get_db)):
    """Rebuild the demo dataset so a run can be repeated from a clean slate."""
    from app.services.demo_seed import seed
    from app.services.simulation import get_state

    # Rebuild the city currently loaded. Silently dropping a dispatcher back to
    # Bengaluru because they pressed reset in Delhi would be its own bug.
    city = get_state(db).city
    db.close()
    city = seed(do_reset=True, city_id=city)
    await hub.publish("fleet", {"type": "demo_reset", "city": city})
    return {"status": "ok", "message": "Demo data rebuilt.", "city": city}


class ConstraintChangeRequest(BaseModel):
    note: str = "Constraint overlay changed."
    solve_seconds: int | None = 5


@router.post("/constraint-change", response_model=OptimizationRunOut)
async def constraint_change(
    body: ConstraintChangeRequest, db: Session = Depends(get_db)
):
    """Monsoon closure / curfew toggle → corridor re-route (Plan.md D13)."""
    run = reoptimize_after_constraint_change(
        db, solve_seconds=body.solve_seconds, note=body.note
    )
    await hub.publish(
        "fleet",
        {"type": "constraint_reopt", "run_id": run.id, "status": run.status.value},
    )
    return run


@router.post("/simulate-gps", response_model=list[VehicleOut])
async def simulate_gps(db: Session = Depends(get_db)):
    updated = simulate_gps_step(db)
    await hub.publish(
        "fleet",
        {
            "type": "gps_tick",
            "vehicles": [
                {
                    "id": v.id,
                    "lat": v.lat,
                    "lon": v.lon,
                    "status": v.status.value,
                    "path_progress_km": v.path_progress_km or 0,
                }
                for v in updated
            ],
        },
    )
    return updated


@router.websocket("/ws")
async def websocket_fleet(ws: WebSocket):
    await ws.accept()
    await hub.subscribe("fleet", ws)
    try:
        await ws.send_json({"type": "connected", "channel": "fleet"})
        while True:
            # keep alive; client may send pings
            await ws.receive_text()
    except WebSocketDisconnect:
        await hub.unsubscribe("fleet", ws)
