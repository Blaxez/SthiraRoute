from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Shipment
from app.schemas import ShipmentCreate, ShipmentOut

router = APIRouter(prefix="/shipments", tags=["shipments"])


@router.get("", response_model=list[ShipmentOut])
def list_shipments(db: Session = Depends(get_db)):
    return list(db.scalars(select(Shipment)))


@router.post("", response_model=ShipmentOut)
def create_shipment(body: ShipmentCreate, db: Session = Depends(get_db)):
    s = Shipment(**body.model_dump())
    db.add(s)
    db.commit()
    db.refresh(s)
    return s
