from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    """Bring an existing database file up to the current model.

    `create_all` only creates missing tables, so a demo database written before
    a column existed keeps working right up until the first query against it.
    Alembic is the production answer; for a single-file SQLite demo an additive
    ALTER is enough, and it means nobody has to delete their data to pull.
    """
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have or col.primary_key:
                    continue
                ddl = f"{col.name} {col.type.compile(engine.dialect)}"
                default = col.default.arg if col.default is not None else None
                if default is not None and not callable(default):
                    literal = f"'{default}'" if isinstance(default, str) else (
                        int(default) if isinstance(default, bool) else default
                    )
                    ddl += f" DEFAULT {literal}"
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
