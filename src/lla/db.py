from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from lla.config import require_env


def get_engine() -> Engine:
    return create_engine(require_env("DATABASE_URL"), pool_pre_ping=True)


def verify_postgis(engine: Engine | None = None) -> dict[str, str]:
    engine = engine or get_engine()
    with engine.connect() as conn:
        version = conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'postgis';")
        ).scalar_one()
        srid = conn.execute(
            text("SELECT Find_SRID('lla', 'parcels', 'geom');")
        ).scalar()
    return {"postgis_version": version, "parcels_geom_srid": str(srid)}
