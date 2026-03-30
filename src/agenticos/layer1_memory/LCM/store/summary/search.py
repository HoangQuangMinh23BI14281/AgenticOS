"""
Summary Search — FTS5 and LIKE search for summaries.
Part of the LCM SummaryStore decomposition.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .crud import SUMMARY_COLS, to_summary


def execute_fts_search(
    conn: sqlite3.Connection, query: str, limit: int = 50, conversation_id: int | None = None
) -> list[dict[str, Any]]:
    # Simple FTS search on summaries table (assuming summaries_fts exists or integrated)
    # For now, let's stick to the common pattern used in SummaryStore
    where = ["summaries_fts MATCH ?"]
    args = [query]
    if conversation_id:
        where.append("s.conversation_id = ?")
        args.append(conversation_id)
    args.append(limit)

    sql = f"""
        SELECT s.{SUMMARY_COLS}, snippet(summaries_fts, 4, '', '', '...', 32) AS snippet
        FROM summaries_fts
        JOIN summaries s ON s.summary_id = summaries_fts.rowid
        WHERE {' AND '.join(where)}
        ORDER BY s.created_at DESC
        LIMIT ?
    """
    rows = conn.execute(sql, args).fetchall()
    return [
        {
            "record": to_summary(r),
            "snippet": r["snippet"]
        }
        for r in rows
    ]


def execute_like_search(
    conn: sqlite3.Connection, query: str, limit: int = 50, conversation_id: int | None = None
) -> list[dict[str, Any]]:
    where = ["s.content LIKE ?"]
    args = [f"%{query}%"]
    if conversation_id:
        where.append("s.conversation_id = ?")
        args.append(conversation_id)
    args.append(limit)

    sql = f"""
        SELECT {SUMMARY_COLS} FROM summaries s
        WHERE {' AND '.join(where)}
        ORDER BY s.created_at DESC
        LIMIT ?
    """
    rows = conn.execute(sql, args).fetchall()
    return [
        {
            "record": to_summary(r),
            "snippet": r["content"][:200] + "..." # Simplified snippet
        }
        for r in rows
    ]
