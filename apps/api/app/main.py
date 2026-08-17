from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.db import init_db
from app.modules.analytics.router import router as analytics_router
from app.modules.constraints.router import router as constraints_router
from app.modules.events.router import router as events_router
from app.modules.fleet.router import router as fleet_router
from app.modules.network.router import router as network_router
from app.modules.optimize.router import router as optimize_router
from app.modules.shipments.router import router as shipments_router
from app.modules.assistant.router import router as assistant_router
from app.modules.sim.router import router as sim_router
from app.modules.tracking.router import router as tracking_router
from app.schemas import HealthOut


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="SthiraRoute API",
    description="Smart Fleet Coordination — decision & optimization system",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(fleet_router, prefix="/api")
app.include_router(shipments_router, prefix="/api")
app.include_router(optimize_router, prefix="/api")
app.include_router(tracking_router, prefix="/api")
app.include_router(events_router, prefix="/api")
app.include_router(constraints_router, prefix="/api")
app.include_router(analytics_router, prefix="/api")
app.include_router(network_router, prefix="/api")
app.include_router(sim_router, prefix="/api")
app.include_router(assistant_router, prefix="/api")


@app.get("/health", response_model=HealthOut)
def health():
    return HealthOut(
        status="ok",
        service="sthiraroute-api",
        database=settings.database_url.split("://")[0],
        use_haversine=settings.use_haversine,
        optimize_in_process=settings.optimize_in_process,
    )
