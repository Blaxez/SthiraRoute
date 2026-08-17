"""Stakeholder coordination board — the PS2 'fragmented desks' fix."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.services.network import board, track_code

router = APIRouter(prefix="/network", tags=["network"])


@router.get("/board")
def network_board(db: Session = Depends(get_db)):
    """One committed plan, sliced for dispatch / dock / driver / consignee."""
    return board(db)


@router.get("/track/{code}")
def track_consignment(code: str, db: Session = Depends(get_db)):
    """Public-style track: the same object the dispatcher and driver see."""
    row = track_code(db, code)
    if not row:
        raise HTTPException(404, "consignment not found")
    return row
