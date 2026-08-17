from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.models import (
    Route,
    RouteStatus,
    Shipment,
    ShipmentStatus,
    Stop,
    StopStatus,
    Vehicle,
    VehicleStatus,
)
from app.schemas import GpsUpdate, RouteOut, VehicleOut

router = APIRouter(prefix="/tracking", tags=["tracking"])


class PodRequest(BaseModel):
    stop_id: int
    note: str | None = None


@router.get("/manifest/{vehicle_id}", response_model=RouteOut | None)
def manifest(vehicle_id: int, db: Session = Depends(get_db)):
    """The driver's copy of the plan: the committed route for one vehicle."""
    return db.execute(
        select(Route)
        .options(selectinload(Route.stops))
        .where(Route.vehicle_id == vehicle_id, Route.status == RouteStatus.committed)
        .order_by(Route.id.desc())
        .limit(1)
    ).scalar_one_or_none()


@router.post("/pod", response_model=RouteOut)
def proof_of_delivery(body: PodRequest, db: Session = Depends(get_db)):
    """Complete a stop: the driver confirms the drop actually happened."""
    stop = db.get(Stop, body.stop_id)
    if not stop:
        raise HTTPException(404, "stop not found")
    if stop.kind != "delivery":
        raise HTTPException(400, "only delivery stops take a proof of delivery")
    if stop.status == StopStatus.completed:
        raise HTTPException(409, "this stop is already signed off")

    stop.status = StopStatus.completed
    route = db.get(Route, stop.route_id)
    if stop.shipment_id:
        ship = db.get(Shipment, stop.shipment_id)
        if ship:
            ship.status = ShipmentStatus.delivered
    if route:
        vehicle = db.get(Vehicle, route.vehicle_id)
        if vehicle:
            vehicle.lat, vehicle.lon = stop.lat, stop.lon
            remaining = [
                s for s in route.stops
                if s.kind == "delivery" and s.status != StopStatus.completed
            ]
            vehicle.status = (
                VehicleStatus.available if not remaining else VehicleStatus.en_route
            )
    db.commit()
    return db.execute(
        select(Route).options(selectinload(Route.stops)).where(Route.id == stop.route_id)
    ).scalar_one()


@router.post("/gps", response_model=VehicleOut)
def update_gps(body: GpsUpdate, db: Session = Depends(get_db)):
    v = db.get(Vehicle, body.vehicle_id)
    if not v:
        raise HTTPException(404, "vehicle not found")
    v.lat = body.lat
    v.lon = body.lon
    db.commit()
    db.refresh(v)
    return v


@router.get("/vehicles/live", response_model=list[VehicleOut])
def live_vehicles(db: Session = Depends(get_db)):
    return list(db.scalars(select(Vehicle)))
