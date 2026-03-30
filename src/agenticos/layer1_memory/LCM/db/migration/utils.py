"""
LCM Migration Utils — SQL helpers and date/time parsing.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger("lcm.db.migration.utils")


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    col_type: str,
    default: str | None = None,
) -> None:
    """Add a column if it doesn't exist."""
    if has_column(conn, table, column):
        return
    default_clause = f" DEFAULT {default}" if default is not None else ""
    conn.execute(
        f"ALTER TABLE {table} ADD COLUMN {column} {col_type}{default_clause}"
    )
    logger.debug("Added column %s.%s", table, column)


def parse_dt(value: str | None) -> datetime:
    """Parse an ISO timestamp string, defaulting to UTC now."""
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def iso_or_none(dt: datetime | None) -> str | None:
    """Convert datetime to ISO string or None."""
    return dt.isoformat() if dt else None
