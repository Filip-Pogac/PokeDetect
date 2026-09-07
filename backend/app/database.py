"""SQLAlchemy engine, session factory and declarative base."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

settings = get_settings()

# check_same_thread is only needed for SQLite when used across threads (FastAPI).
_connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columns added to a model after a database already exists. create_all() only
# creates missing *tables*, never missing columns, so an existing pokedetect.db
# would break on a new field without this. Kept as a simple additive list
# rather than pulling in a full migration tool.
_SQLITE_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    # (table, column, DDL type + default)
    ("collection_cards", "quantity", "INTEGER NOT NULL DEFAULT 1"),
    ("collection_cards", "variant", "TEXT NOT NULL DEFAULT ''"),
]


def _apply_sqlite_migrations() -> None:
    """Add any missing columns to existing SQLite tables (no-op on a fresh DB)."""
    if engine.dialect.name != "sqlite":
        return
    from sqlalchemy import inspect

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in _SQLITE_ADDED_COLUMNS:
            if table not in existing_tables:
                continue  # create_all() will build it with the column already
            columns = {c["name"] for c in inspector.get_columns(table)}
            if column in columns:
                continue
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db() -> None:
    """Create all tables. Imported models must be registered before calling."""
    from . import models  # noqa: F401  (ensures models are registered)

    _apply_sqlite_migrations()
    Base.metadata.create_all(bind=engine)
