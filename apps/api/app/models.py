import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class VehicleStatus(str, enum.Enum):
    available = "available"
    en_route = "en_route"
    loading = "loading"
    down = "down"
    off_duty = "off_duty"


class ShipmentStatus(str, enum.Enum):
    pending = "pending"
    assigned = "assigned"
    in_transit = "in_transit"
    delivered = "delivered"
    cancelled = "cancelled"


class RouteStatus(str, enum.Enum):
    candidate = "candidate"
    committed = "committed"
    superseded = "superseded"


class StopStatus(str, enum.Enum):
    pending = "pending"
    arrived = "arrived"
    completed = "completed"
    skipped = "skipped"


class RunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    infeasible = "infeasible"


class RunTrigger(str, enum.Enum):
    plan = "plan"
    insert = "insert"
    breakdown = "breakdown"
    traffic = "traffic"
    sla = "sla"
    manual = "manual"


class Depot(Base):
    __tablename__ = "depots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)

    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="depot")


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    depot_id: Mapped[int] = mapped_column(ForeignKey("depots.id"))
    capacity_kg: Mapped[float] = mapped_column(Float, default=1000)
    capacity_m3: Mapped[float] = mapped_column(Float, default=10)
    # Load bed geometry, for the 3D packer. Defaults ≈ a Tata Ace container.
    deck_length_cm: Mapped[int] = mapped_column(Integer, default=220)
    deck_width_cm: Mapped[int] = mapped_column(Integer, default=150)
    deck_height_cm: Mapped[int] = mapped_column(Integer, default=150)
    # Comma-separated capabilities a shipment can demand, e.g. "reefer,tail_lift"
    features: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[VehicleStatus] = mapped_column(
        Enum(VehicleStatus), default=VehicleStatus.available
    )
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    # How far along its committed route the vehicle has driven, in km.
    path_progress_km: Mapped[float] = mapped_column(Float, default=0)
    # Simulation clock time at which the truck finishes at the current bay.
    dwell_until_min: Mapped[int] = mapped_column(Integer, default=0)
    # Minutes since the last GPS fix. Non-zero means the marker on the map is a
    # last-known position, not a live one (Plan.md §13 scenario 6).
    gps_stale_min: Mapped[int] = mapped_column(Integer, default=0)

    depot: Mapped[Depot] = relationship(back_populates="vehicles")
    routes: Mapped[list["Route"]] = relationship(back_populates="vehicle")


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), nullable=True)


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    customer_name: Mapped[str] = mapped_column(String(120))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    demand_kg: Mapped[float] = mapped_column(Float, default=50)
    demand_m3: Mapped[float] = mapped_column(Float, default=0.5)
    # minutes from midnight for soft/hard TW demo
    tw_start_min: Mapped[int] = mapped_column(Integer, default=8 * 60)
    tw_end_min: Mapped[int] = mapped_column(Integer, default=18 * 60)
    service_min: Mapped[int] = mapped_column(Integer, default=10)
    priority: Mapped[int] = mapped_column(Integer, default=1)
    # Carton geometry + handling rules for the 3D packer.
    length_cm: Mapped[int] = mapped_column(Integer, default=60)
    width_cm: Mapped[int] = mapped_column(Integer, default=40)
    height_cm: Mapped[int] = mapped_column(Integer, default=40)
    fragile: Mapped[bool] = mapped_column(Boolean, default=False)
    stackable: Mapped[bool] = mapped_column(Boolean, default=True)
    # Vehicle capability this consignment demands, e.g. "reefer". Empty = any.
    requires_feature: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[ShipmentStatus] = mapped_column(
        Enum(ShipmentStatus), default=ShipmentStatus.pending
    )


class OptimizationRun(Base):
    __tablename__ = "optimization_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[RunTrigger] = mapped_column(Enum(RunTrigger), default=RunTrigger.plan)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.queued)
    solve_seconds: Mapped[int] = mapped_column(Integer, default=8)
    objective: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    explain_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    routes: Mapped[list["Route"]] = relationship(back_populates="optimization_run")


class Route(Base):
    __tablename__ = "routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"))
    optimization_run_id: Mapped[int] = mapped_column(ForeignKey("optimization_runs.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[RouteStatus] = mapped_column(
        Enum(RouteStatus), default=RouteStatus.candidate
    )
    total_distance_km: Mapped[float] = mapped_column(Float, default=0)
    total_load_kg: Mapped[float] = mapped_column(Float, default=0)
    total_load_m3: Mapped[float] = mapped_column(Float, default=0)
    load_pct: Mapped[float] = mapped_column(Float, default=0)
    # 3D load plan from the packer — geometry, load order, LIFO audit.
    load_plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    load_feasible: Mapped[bool] = mapped_column(Boolean, default=True)
    # Driven path as [[lon,lat],...] from OSRM. Null when only straight-line
    # distances were available — the map says so rather than faking a road.
    geometry_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    vehicle: Mapped[Vehicle] = relationship(back_populates="routes")
    optimization_run: Mapped[OptimizationRun] = relationship(back_populates="routes")
    stops: Mapped[list["Stop"]] = relationship(
        back_populates="route", order_by="Stop.seq", cascade="all, delete-orphan"
    )

    @property
    def geometry(self) -> list[list[float]] | None:
        """Decoded driven path, for the API layer."""
        if not self.geometry_json:
            return None
        import json

        try:
            return json.loads(self.geometry_json)
        except ValueError:
            return None


class Stop(Base):
    __tablename__ = "stops"
    __table_args__ = (UniqueConstraint("route_id", "seq", name="uq_route_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id"))
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id"), nullable=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32), default="delivery")  # depot|delivery
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    eta_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    late_min: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[StopStatus] = mapped_column(Enum(StopStatus), default=StopStatus.pending)

    route: Mapped[Route] = relationship(back_populates="stops")
    shipment: Mapped[Shipment | None] = relationship()


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), unique=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id"))
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ConstraintOverlay(Base):
    """India municipal / operational constraint packs (templates — verify locally)."""

    __tablename__ = "constraint_overlays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(64), default="no_entry")  # no_entry|closure
    # Circular zone for Phase 3 (PostGIS polygons later)
    center_lat: Mapped[float] = mapped_column(Float)
    center_lon: Mapped[float] = mapped_column(Float)
    radius_km: Mapped[float] = mapped_column(Float, default=3.0)
    # Banned window minutes from midnight (e.g. 480–660 = 08:00–11:00)
    ban_start_min: Mapped[int] = mapped_column(Integer)
    ban_end_min: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class SimState(Base):
    """Single-row clock for the shift simulation."""

    __tablename__ = "sim_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clock_min: Mapped[int] = mapped_column(Integer, default=6 * 60)
    running: Mapped[bool] = mapped_column(Boolean, default=False)
    # Extra slowdown from a live incident, 0..1, and when it clears.
    congestion: Mapped[float] = mapped_column(Float, default=0)
    congestion_until_min: Mapped[int] = mapped_column(Integer, default=0)
    # Let the system respond to its own events rather than waiting for a click.
    autopilot: Mapped[bool] = mapped_column(Boolean, default=True)
    last_reopt_min: Mapped[int] = mapped_column(Integer, default=0)
    # Which city pack the day is running: traffic profile, hazard corridors
    # and ad-hoc geography all follow from it.
    city: Mapped[str] = mapped_column(String(32), default="bengaluru")
    # clear | rain | storm, and when the spell breaks.
    weather: Mapped[str] = mapped_column(String(16), default="clear")
    weather_until_min: Mapped[int] = mapped_column(Integer, default=0)
    # Scripted demo: which playbook is loaded and how far through it we are.
    scenario: Mapped[str | None] = mapped_column(String(32), nullable=True)
    script_step: Mapped[int] = mapped_column(Integer, default=0)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Day-to-date tallies, so the scorecard survives a page reload.
    runtime_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class SimEvent(Base):
    """What happened, and at what shift time. The demo's narrative record."""

    __tablename__ = "sim_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clock_min: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), nullable=True)
    shipment_id: Mapped[int | None] = mapped_column(ForeignKey("shipments.id"), nullable=True)
