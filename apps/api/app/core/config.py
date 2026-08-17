from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_API_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_API_DIR / ".env",
        extra="ignore",
    )

    app_name: str = "SthiraRoute"
    # Default SQLite so the project runs with zero DB install.
    # For Postgres: postgresql+psycopg://sthira:sthira@localhost:5432/sthiraroute
    database_url: str = "sqlite:///./sthiraroute.db"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://rohensingh.in",
        "https://www.rohensingh.in",
        "http://rohensingh.in",
        "http://www.rohensingh.in",
    ]
    jwt_secret: str = "dev-change-me"
    # Public OSRM demo instance: real road network, no key, but shared and
    # rate-limited. Point this at a self-hosted OSRM for production.
    osrm_url: str = "https://router.project-osrm.org"
    osrm_timeout_s: float = 20.0
    # Straight-line fallback only. Leave False so plans are costed on roads.
    use_haversine: bool = False
    # Urban road distance vs straight line. Applied only in fallback mode so a
    # degraded plan is not also an optimistic one.
    haversine_detour_factor: float = 1.4
    # Run OR-Tools inside API process (no Redis worker required)
    optimize_in_process: bool = True
    default_solve_seconds: int = 8
    # Distance a simulated vehicle covers per GPS tick.
    gps_step_km: float = 4.0
    # Optional briefing chat / live voice (never used for routing).
    gemini_api_key: str = ""
    gemini_api_keys: str = ""
    gemini_model: str = "gemini-3.7-flash"
    gemini_live_model: str = "gemini-3.1-flash-live-preview"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
