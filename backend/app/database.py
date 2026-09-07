"""SQLAlchemy engine, session factory and declarative base."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

settings = get_settings()


def _normalize_url(raw: str) -> str:
    """Make a hosted-Postgres URL usable by SQLAlchemy + psycopg 3.

    Neon (like Heroku, Render and Supabase) hands out `postgres://...` or
    `postgresql://...`, which SQLAlchemy resolves to psycopg2 - a driver this
    project does not install. Pin the psycopg 3 dialect explicitly instead, and
    require TLS if the URL did not already ask for it: Neon refuses plaintext
    connections, and the failure is otherwise a confusing handshake error.
    """
    url = make_url(raw)
    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername.startswith("postgresql") and "sslmode" not in url.query:
        url = url.update_query_dict({"sslmode": "require"}, append=True)
    return url.render_as_string(hide_password=False)


DATABASE_URL = _normalize_url(settings.database_url)
_IS_SQLITE = DATABASE_URL.startswith("sqlite")

if _IS_SQLITE:
    # check_same_thread is only needed for SQLite when used across threads (FastAPI).
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # Serverless-friendly pooling. On Vercel each instance handles few
    # concurrent requests but is reused across them, so a tiny pool beats
    # reconnecting per request (a fresh TLS handshake to Neon is not free).
    # pool_pre_ping discards connections Neon dropped while the instance was
    # idle - its compute auto-suspends - instead of failing the request, and
    # pool_recycle keeps them from getting old enough for that to be common.
    engine = create_engine(
        DATABASE_URL,
        pool_size=int(settings.db_pool_size),
        max_overflow=int(settings.db_max_overflow),
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"connect_timeout": int(settings.db_connect_timeout_seconds)},
    )

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
# creates missing *tables*, never missing columns, so an existing database
# would break on a new field without this. Kept as a simple additive list
# rather than pulling in a full migration tool. The DDL below is deliberately
# plain enough to be valid on both SQLite and Postgres.
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    # (table, column, DDL type + default)
    ("collection_cards", "quantity", "INTEGER NOT NULL DEFAULT 1"),
    ("collection_cards", "variant", "TEXT NOT NULL DEFAULT ''"),
]


def _apply_additive_migrations() -> None:
    """Add any missing columns to existing tables (no-op on a fresh database)."""
    from sqlalchemy import inspect

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            if table not in existing_tables:
                continue  # create_all() will build it with the column already
            columns = {c["name"] for c in inspector.get_columns(table)}
            if column in columns:
                continue
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db() -> None:
    """Create all tables. Imported models must be registered before calling."""
    from . import models  # noqa: F401  (ensures models are registered)

    _apply_additive_migrations()
    Base.metadata.create_all(bind=engine)
