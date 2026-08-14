import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


if settings.database_url.startswith("sqlite"):
    db_path = settings.database_url.removeprefix("sqlite:///")
    if db_path != ":memory:":
        Path(db_path).resolve().parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    """Create the schema and run any pending Step 10B migrations.

    ``Base.metadata.create_all`` only creates tables that do not yet exist
    and never alters existing tables, so a fresh database lands with the new
    persistent product-intelligence columns and an upgraded Step-9 database
    receives them through ``app.db.migration.run_migrations``.
    """
    from app.db import models  # noqa: F401 - register tables
    from app.db.migration import run_migrations

    Base.metadata.create_all(bind=engine)
    run_migrations(engine)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
