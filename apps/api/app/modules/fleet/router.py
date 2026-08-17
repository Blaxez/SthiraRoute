from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Depot, Vehicle, VehicleStatus
from app.schemas import DepotOut, VehicleCreate, VehicleOut

router = APIRouter(prefix="/fleet", tags=["fleet"])


class VehicleStatusBody(BaseModel):
    status: VehicleStatus


@router.get("/depots", response_model=list[DepotOut])
def list_depots(db: Session = Depends(get_db)):
    return list(db.scalars(select(Depot)))


@router.get("/vehicles", response_model=list[VehicleOut])
def list_vehicles(db: Session = Depends(get_db)):
    return list(db.scalars(select(Vehicle)))


@router.post("/vehicles", response_model=VehicleOut)
def create_vehicle(body: VehicleCreate, db: Session = Depends(get_db)):
    if not db.get(Depot, body.depot_id):
        raise HTTPException(404, "depot not found")
    v = Vehicle(**body.model_dump())
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


@router.patch("/vehicles/{vehicle_id}/status", response_model=VehicleOut)
def set_status(vehicle_id: int, body: VehicleStatusBody, db: Session = Depends(get_db)):
    v = db.get(Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, "vehicle not found")
    v.status = body.status
    db.commit()
    db.refresh(v)
    return v
