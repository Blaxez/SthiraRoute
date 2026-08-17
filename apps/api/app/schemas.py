from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import (
    RouteStatus,
    RunStatus,
    RunTrigger,
    ShipmentStatus,
    StopStatus,
    VehicleStatus,
)


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DepotOut(OrmModel):
    id: int
    name: str
    lat: float
    lon: float


class VehicleCreate(BaseModel):
    code: str
    depot_id: int
    capacity_kg: float = 1000
    capacity_m3: float = 10
    deck_length_cm: int = 220
    deck_width_cm: int = 150
    deck_height_cm: int = 150
    features: str = ""
    status: VehicleStatus = VehicleStatus.available
    lat: float | None = None
    lon: float | None = None


class VehicleOut(OrmModel):
    id: int
    code: str
    depot_id: int
    capacity_kg: float
    capacity_m3: float
    deck_length_cm: int
    deck_width_cm: int
    deck_height_cm: int
    features: str
    status: VehicleStatus
    lat: float | None
    lon: float | None
    path_progress_km: float = 0


class ShipmentCreate(BaseModel):
    code: str
    customer_name: str
    lat: float
    lon: float
    demand_kg: float = 50
    demand_m3: float = 0.5
    tw_start_min: int = 8 * 60
    tw_end_min: int = 18 * 60
    service_min: int = 10
    priority: int = 1
    length_cm: int = 60
    width_cm: int = 40
    height_cm: int = 40
    fragile: bool = False
    stackable: bool = True
    requires_feature: str = ""


class ShipmentOut(OrmModel):
    id: int
    code: str
    customer_name: str
    lat: float
    lon: float
    demand_kg: float
    demand_m3: float
    tw_start_min: int
    tw_end_min: int
    service_min: int
    priority: int
    length_cm: int
    width_cm: int
    height_cm: int
    fragile: bool
    stackable: bool
    requires_feature: str
    status: ShipmentStatus


class StopOut(OrmModel):
    id: int
    seq: int
    kind: str
    lat: float
    lon: float
    eta_min: int | None
    late_min: int = 0
    status: StopStatus
    shipment_id: int | None


class RouteOut(OrmModel):
    id: int
    vehicle_id: int
    optimization_run_id: int
    version: int
    status: RouteStatus
    total_distance_km: float
    total_load_kg: float
    total_load_m3: float = 0
    load_pct: float = 0
    load_feasible: bool = True
    geometry: list[list[float]] | None = None
    stops: list[StopOut] = Field(default_factory=list)


class OptimizeRequest(BaseModel):
    trigger: RunTrigger = RunTrigger.plan
    solve_seconds: int | None = None
    depot_id: int | None = None


class OptimizationRunOut(OrmModel):
    id: int
    trigger: RunTrigger
    status: RunStatus
    solve_seconds: int
    objective: float | None
    metrics_json: str | None
    explain_json: str | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    routes: list[RouteOut] = Field(default_factory=list)


class GpsUpdate(BaseModel):
    vehicle_id: int
    lat: float
    lon: float


class ApproveRequest(BaseModel):
    run_id: int


class HealthOut(BaseModel):
    status: str
    service: str
    database: str
    use_haversine: bool
    optimize_in_process: bool


class ExplainOut(BaseModel):
    run_id: int
    summary: str
    details: dict[str, Any]
