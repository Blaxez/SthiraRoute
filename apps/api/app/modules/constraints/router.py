from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import ConstraintOverlay

router = APIRouter(prefix="/constraints", tags=["constraints"])


class OverlayOut(BaseModel):
    id: int
    name: str
    kind: str
    center_lat: float
    center_lon: float
    radius_km: float
    ban_start_min: int
    ban_end_min: int
    active: bool
    notes: str | None

    class Config:
        from_attributes = True


class OverlayToggle(BaseModel):
    active: bool


@router.get("", response_model=list[OverlayOut])
def list_overlays(db: Session = Depends(get_db)):
    return list(db.scalars(select(ConstraintOverlay)))


@router.patch("/{overlay_id}", response_model=OverlayOut)
def toggle_overlay(overlay_id: int, body: OverlayToggle, db: Session = Depends(get_db)):
    o = db.get(ConstraintOverlay, overlay_id)
    if not o:
        raise HTTPException(404, "overlay not found")
    o.active = body.active
    db.commit()
    db.refresh(o)
    return o
