import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.models import OptimizationRun, Route
from app.schemas import (
    ApproveRequest,
    ExplainOut,
    OptimizationRunOut,
    OptimizeRequest,
    RouteOut,
)
from app.services.explain import explain_run_text
from app.services.optimize import approve_run, create_and_run_optimization

router = APIRouter(prefix="/optimization", tags=["optimization"])


@router.post("/runs", response_model=OptimizationRunOut)
def start_run(body: OptimizeRequest, db: Session = Depends(get_db)):
    run = create_and_run_optimization(
        db,
        trigger=body.trigger,
        solve_seconds=body.solve_seconds,
        depot_id=body.depot_id,
    )
    return run


@router.get("/runs/{run_id}", response_model=OptimizationRunOut)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.execute(
        select(OptimizationRun)
        .options(selectinload(OptimizationRun.routes).selectinload(Route.stops))
        .where(OptimizationRun.id == run_id)
    ).scalar_one_or_none()
    if not run:
        raise HTTPException(404, "run not found")
    return run


@router.get("/runs", response_model=list[OptimizationRunOut])
def list_runs(db: Session = Depends(get_db)):
    runs = db.execute(
        select(OptimizationRun)
        .options(selectinload(OptimizationRun.routes).selectinload(Route.stops))
        .order_by(OptimizationRun.id.desc())
        .limit(20)
    ).scalars()
    return list(runs)


@router.post("/approve", response_model=OptimizationRunOut)
def approve(body: ApproveRequest, db: Session = Depends(get_db)):
    try:
        return approve_run(db, body.run_id)
    except ValueError as e:
        raise HTTPException(404 if "not found" in str(e) else 400, str(e)) from e


@router.get("/runs/{run_id}/explain", response_model=ExplainOut)
def explain(run_id: int, db: Session = Depends(get_db)):
    run = db.get(OptimizationRun, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    details = json.loads(run.explain_json or "{}")
    summary = explain_run_text(run.explain_json, run.metrics_json, run.error)
    details["explanation"] = summary
    return ExplainOut(run_id=run_id, summary=summary, details=details)


@router.get("/routes/{route_id}/loadplan")
def load_plan(route_id: int, db: Session = Depends(get_db)):
    """The 3D load plan for one route: geometry, load order, LIFO audit."""
    route = db.get(Route, route_id)
    if not route:
        raise HTTPException(404, "route not found")
    if not route.load_plan_json:
        raise HTTPException(404, "no load plan stored for this route")
    plan = json.loads(route.load_plan_json)
    plan["route_id"] = route.id
    plan["vehicle_id"] = route.vehicle_id
    return plan


@router.get("/routes/committed", response_model=list[RouteOut])
def committed_routes(db: Session = Depends(get_db)):
    from app.models import RouteStatus

    routes = db.execute(
        select(Route)
        .options(selectinload(Route.stops))
        .where(Route.status == RouteStatus.committed)
    ).scalars()
    return list(routes)
