"""
FTS5 Feature Detection — Probe whether SQLite build supports FTS5.
"""

from __future__ import annotations

import sqlite3


def get_fts5_available(conn: sqlite3.Connection) -> bool:
    """
    Check if the current SQLite runtime supports FTS5.

    Creates a temporary virtual table to probe for fts5 support,
    then drops it immediately.
    """
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE _lcm_fts5_probe USING fts5(content)"
        )
        conn.execute("DROP TABLE _lcm_fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False
