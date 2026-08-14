"""Idempotent SQLite migration utilities (Step 10B).

SQLAlchemy's ``create_all`` is a no-op when a table already exists, so it
cannot upgrade an existing Step-9 database to add the new persistent
product-intelligence columns. ``run_migrations`` performs a small, explicit,
idempotent migration that:

1. Inspects the current schema with ``PRAGMA table_info``.
2. Computes the set of missing columns.
3. Adds each missing column with a safe ``DEFAULT`` so existing rows are
   preserved without any data backfill.

The same code path works for both fresh and upgraded databases:

* Fresh DB  -- ``create_all`` already created the table with the new columns
  in the model, so the migration loop finds no missing columns and does
  nothing.
* Step-9 DB -- the table exists with the legacy 7 columns; the migration
  adds the 9 new columns with safe defaults. Existing rows are preserved
  and the new structured columns are filled with their defaults
  (empty strings / JSON / NULL / 30).

Alembic is intentionally not introduced: a hackathon-weight explicit
migration is enough for this single schema bump.

No secrets, API keys or Authorization headers are ever persisted.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine


# Definition of the Step 10B columns. Kept here (and not on the model)
# so the migration stays the single source of truth even if a model
# attribute is later renamed or removed: the migration only ever asks
# "does the table have this column?" and adds it when missing.
PRODUCT_RECORD_NEW_COLUMNS: list[tuple[str, str, str]] = [
    ("raw_description", "TEXT", "''"),
    ("sources_json", "TEXT", "'[]'"),
    ("evidence_json", "TEXT", "'{}'"),
    ("attributes_json", "TEXT", "'{}'"),
    ("descriptions_json", "TEXT", "'{}'"),
    ("validation_json", "TEXT", "'{}'"),
    ("enrichment_history_json", "TEXT", "'[]'"),
    ("last_enriched_at", "DATETIME", "NULL"),
    ("source_freshness_days", "INTEGER", "30"),
]


def _existing_columns(conn, table: str) -> set[str]:
    """Return the set of column names currently present on ``table``."""
    result = conn.execute(text(f"PRAGMA table_info({table})"))
    return {row[1] for row in result}


def _table_exists(conn, table: str) -> bool:
    result = conn.execute(
        text(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = :name"
        ),
        {"name": table},
    )
    return result.first() is not None


def migrate_product_records(engine: Engine) -> None:
    """Add Step 10B columns to ``product_records`` if they are missing.

    Idempotent: safe to call any number of times. A no-op when every column
    already exists (fresh-DB case after ``create_all``). Existing rows are
    preserved; the new columns receive their declared defaults.
    """
    with engine.begin() as conn:
        if not _table_exists(conn, "product_records"):
            # Fresh DB without the table: ``create_all`` will create it with
            # every column already declared in the model. Nothing to do.
            return
        existing = _existing_columns(conn, "product_records")
        for col_name, col_type, default_sql in PRODUCT_RECORD_NEW_COLUMNS:
            if col_name in existing:
                continue
            # DEFAULT literal is a SQL expression already; string defaults
            # are wrapped in single quotes and NULL/integers pass through.
            conn.execute(
                text(
                    f"ALTER TABLE product_records "
                    f"ADD COLUMN {col_name} {col_type} DEFAULT {default_sql}"
                )
            )


def run_migrations(engine: Engine) -> None:
    """Run every Step 10B migration in order.

    Currently only ``product_records`` has new columns. The orchestrator is
    the single entry point so future migrations just add new helper calls.
    """
    migrate_product_records(engine)
