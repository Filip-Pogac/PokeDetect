"""One-off copy of an existing SQLite database into Postgres (e.g. Neon).

Run it once after pointing DATABASE_URL at the new database, to carry the
locally registered users and their collections over. Safe to skip entirely if
the deployment is starting from an empty database.

    cd backend
    python scripts/migrate_sqlite_to_postgres.py \
        --source sqlite:///./pokedetect.db \
        --target "postgresql://user:pass@ep-xxx-pooler.../neondb?sslmode=require"

The target is created if empty and must not already contain rows in the tables
being copied - this refuses to merge into a populated database rather than
risk duplicating or colliding on primary keys.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Import the app package when run as a plain script from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import Base, _normalize_url  # noqa: E402
from app.models import CollectionCard, User  # noqa: E402

# Order matters: users before the cards that reference them.
TABLES = [User, CollectionCard]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default="sqlite:///./pokedetect.db",
        help="SQLAlchemy URL to copy from (default: the local SQLite file)",
    )
    parser.add_argument(
        "--target",
        default=os.getenv("DATABASE_URL", ""),
        help="SQLAlchemy URL to copy into (default: $DATABASE_URL)",
    )
    args = parser.parse_args()

    if not args.target:
        parser.error("no target: pass --target or set DATABASE_URL")
    source_url = _normalize_url(args.source)
    target_url = _normalize_url(args.target)
    if source_url == target_url:
        parser.error("source and target are the same database")

    source = create_engine(source_url)
    target = create_engine(target_url, pool_pre_ping=True)

    Base.metadata.create_all(bind=target)

    with Session(source) as src, Session(target) as dst:
        for model in TABLES:
            existing = dst.scalar(select(func.count()).select_from(model))
            if existing:
                print(
                    f"refusing to copy: target already has {existing} row(s) in "
                    f"{model.__tablename__}",
                    file=sys.stderr,
                )
                return 1

        for model in TABLES:
            rows = src.scalars(select(model)).all()
            for row in rows:
                # Detach from the source session and re-add, keeping ids so the
                # owner_id foreign keys stay valid.
                values = {
                    c.name: getattr(row, c.name) for c in model.__table__.columns
                }
                dst.add(model(**values))
            dst.flush()
            print(f"copied {len(rows)} row(s) into {model.__tablename__}")

        dst.commit()

    # Postgres sequences do not know about the ids inserted above, so the next
    # signup would collide on the primary key. Fast-forward them.
    if target.dialect.name == "postgresql":
        with target.begin() as conn:
            for model in TABLES:
                table = model.__tablename__
                conn.exec_driver_sql(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}), 1), "
                    f"(SELECT MAX(id) IS NOT NULL FROM {table}))"
                )
        print("reset id sequences")

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
