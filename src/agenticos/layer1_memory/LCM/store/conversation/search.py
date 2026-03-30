"""
Conversation Search — FTS5, LIKE, and Regex search implementations.
Part of the LCM ConversationStore decomposition.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from ..fulltext_fallback import (
    build_like_search_plan,
    contains_cjk,
    create_fallback_snippet,
    sanitize_fts5_query,
)


def search_messages(
    conn: sqlite3.Connection,
    query: str,
    mode: str = "full_text",
    conversation_id: int | None = None,
    limit: int = 50,
    fts5_available: bool = True,
) -> list[dict[str, Any]]:
    """
    Search messages using FTS5 or LIKE fallback.
    """
    if mode == "full_text":
        if contains_cjk(query):
            return execute_like_search(conn, query, limit, conversation_id)
        if fts5_available:
            try:
                return execute_fts_search(conn, query, limit, conversation_id)
            except Exception:
                return execute_like_search(conn, query, limit, conversation_id)
        return execute_like_search(conn, query, limit, conversation_id)
    return execute_regex_search(conn, query, limit, conversation_id)


def execute_fts_search(
    conn: sqlite3.Connection, query: str, limit: int, conversation_id: int | None
) -> list[dict[str, Any]]:
    where = ["messages_fts MATCH ?"]
    args: list[Any] = [sanitize_fts5_query(query)]
    if conversation_id is not None:
        where.append("m.conversation_id = ?")
        args.append(conversation_id)
    args.append(limit)

    sql = f"""
        SELECT m.message_id, m.conversation_id, m.role,
               snippet(messages_fts, 0, '', '', '...', 32) AS snippet,
               m.created_at
        FROM messages_fts
        JOIN messages m ON m.message_id = messages_fts.rowid
        WHERE {' AND '.join(where)}
        ORDER BY m.created_at DESC
        LIMIT ?
    """
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def execute_like_search(
    conn: sqlite3.Connection, query: str, limit: int, conversation_id: int | None
) -> list[dict[str, Any]]:
    plan = build_like_search_plan("content", query)
    if not plan["terms"]:
        return []

    where = list(plan["where"])
    args = list(plan["args"])
    if conversation_id is not None:
        where.append("conversation_id = ?")
        args.append(conversation_id)
    args.append(limit)

    rows = conn.execute(
        f"""SELECT message_id, conversation_id, role, content, created_at
            FROM messages WHERE {' AND '.join(where)}
            ORDER BY created_at DESC LIMIT ?""",
        args,
    ).fetchall()

    return [
        {
            "message_id": r["message_id"],
            "conversation_id": r["conversation_id"],
            "role": r["role"],
            "snippet": create_fallback_snippet(r["content"], query),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def execute_regex_search(
    conn: sqlite3.Connection, query: str, limit: int, conversation_id: int | None
) -> list[dict[str, Any]]:
    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error:
        return []

    where = ["1=1"]
    args: list[Any] = []
    if conversation_id is not None:
        where.append("conversation_id = ?")
        args.append(conversation_id)

    rows = conn.execute(
        f"""SELECT message_id, conversation_id, role, content, created_at
            FROM messages WHERE {' AND '.join(where)}
            ORDER BY created_at DESC""",
        args,
    ).fetchall()

    results = []
    for r in rows:
        match = pattern.search(r["content"])
        if match:
            results.append(
                {
                    "message_id": r["message_id"],
                    "conversation_id": r["conversation_id"],
                    "role": r["role"],
                    "snippet": create_fallback_snippet(r["content"], query),
                    "created_at": r["created_at"],
                }
            )
            if len(results) >= limit:
                break
    return results
