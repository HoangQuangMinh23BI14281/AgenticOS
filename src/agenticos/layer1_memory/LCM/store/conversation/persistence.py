"""
Conversation Persistence — CRUD for conversations, messages, and message parts.
Part of the LCM ConversationStore decomposition.
"""

from __future__ import annotations

import sqlite3
from typing import Any
from uuid import uuid4

from ...types import ConversationRecord, MessagePartRecord, MessageRecord, MessageRole
from .mapping import _to_conversation, _to_message, _to_part


def insert_conversation(
    conn: sqlite3.Connection,
    session_id: str,
    session_key: str | None = None,
    title: str | None = None,
) -> ConversationRecord:
    cur = conn.execute(
        "INSERT INTO conversations (session_id, session_key, title) VALUES (?, ?, ?)",
        (session_id, session_key, title),
    )
    row = conn.execute(
        """SELECT conversation_id, session_id, session_key, title,
                  bootstrapped_at, created_at, updated_at
           FROM conversations WHERE conversation_id = ?""",
        (cur.lastrowid,),
    ).fetchone()
    return _to_conversation(row)


def fetch_conversation_by_id(
    conn: sqlite3.Connection, conversation_id: int
) -> ConversationRecord | None:
    row = conn.execute(
        """SELECT conversation_id, session_id, session_key, title,
                  bootstrapped_at, created_at, updated_at
           FROM conversations WHERE conversation_id = ?""",
        (conversation_id,),
    ).fetchone()
    return _to_conversation(row) if row else None


def fetch_conversation_by_session_id(
    conn: sqlite3.Connection, session_id: str
) -> ConversationRecord | None:
    row = conn.execute(
        """SELECT conversation_id, session_id, session_key, title,
                  bootstrapped_at, created_at, updated_at
           FROM conversations
           WHERE session_id = ?
           ORDER BY created_at DESC LIMIT 1""",
        (session_id,),
    ).fetchone()
    return _to_conversation(row) if row else None


def fetch_conversation_by_session_key(
    conn: sqlite3.Connection, session_key: str
) -> ConversationRecord | None:
    row = conn.execute(
        """SELECT conversation_id, session_id, session_key, title,
                  bootstrapped_at, created_at, updated_at
           FROM conversations WHERE session_key = ? LIMIT 1""",
        (session_key,),
    ).fetchone()
    return _to_conversation(row) if row else None


def update_conversation_session_id(
    conn: sqlite3.Connection, conversation_id: int, session_id: str
) -> None:
    conn.execute(
        "UPDATE conversations SET session_id = ?, updated_at = datetime('now') WHERE conversation_id = ?",
        (session_id, conversation_id),
    )


def update_conversation_session_key(
    conn: sqlite3.Connection, conversation_id: int, session_key: str
) -> None:
    conn.execute(
        "UPDATE conversations SET session_key = ?, updated_at = datetime('now') WHERE conversation_id = ?",
        (session_key, conversation_id),
    )


# ── Messages ──────────────────────────────────────────────────────────


def insert_message(
    conn: sqlite3.Connection,
    conversation_id: int,
    seq: int,
    role: MessageRole,
    content: str,
    token_count: int,
) -> MessageRecord:
    cur = conn.execute(
        "INSERT INTO messages (conversation_id, seq, role, content, token_count) VALUES (?, ?, ?, ?, ?)",
        (conversation_id, seq, role.value, content, token_count),
    )
    msg_id = cur.lastrowid
    row = conn.execute(
        """SELECT message_id, conversation_id, seq, role, content, token_count, created_at
           FROM messages WHERE message_id = ?""",
        (msg_id,),
    ).fetchone()
    return _to_message(row)


def insert_messages_bulk(
    conn: sqlite3.Connection, inputs: list[dict[str, Any]]
) -> list[MessageRecord]:
    records = []
    for inp in inputs:
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, seq, role, content, token_count) VALUES (?, ?, ?, ?, ?)",
            (
                inp["conversation_id"],
                inp["seq"],
                inp["role"],
                inp["content"],
                inp["token_count"],
            ),
        )
        msg_id = cur.lastrowid
        row = conn.execute(
            """SELECT message_id, conversation_id, seq, role, content, token_count, created_at
               FROM messages WHERE message_id = ?""",
            (msg_id,),
        ).fetchone()
        records.append(_to_message(row))
    return records


def update_message_content(
    conn: sqlite3.Connection, message_id: int, content: str, token_count: int
) -> None:
    conn.execute(
        "UPDATE messages SET content = ?, token_count = ? WHERE message_id = ?",
        (content, token_count, message_id),
    )


def fetch_messages(
    conn: sqlite3.Connection,
    conversation_id: int,
    after_seq: int = -1,
    limit: int | None = None,
) -> list[MessageRecord]:
    if limit is not None:
        rows = conn.execute(
            """SELECT message_id, conversation_id, seq, role, content, token_count, created_at
               FROM messages WHERE conversation_id = ? AND seq > ?
               ORDER BY seq LIMIT ?""",
            (conversation_id, after_seq, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT message_id, conversation_id, seq, role, content, token_count, created_at
               FROM messages WHERE conversation_id = ? AND seq > ?
               ORDER BY seq""",
            (conversation_id, after_seq),
        ).fetchall()
    return [_to_message(r) for r in rows]


def fetch_message_by_id(
    conn: sqlite3.Connection, message_id: int
) -> MessageRecord | None:
    row = conn.execute(
        """SELECT message_id, conversation_id, seq, role, content, token_count, created_at
           FROM messages WHERE message_id = ?""",
        (message_id,),
    ).fetchone()
    return _to_message(row) if row else None


def fetch_max_seq(conn: sqlite3.Connection, conversation_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM messages WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    return row["max_seq"] if row else 0


def fetch_message_count(conn: sqlite3.Connection, conversation_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM messages WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    return row["count"] if row else 0


# ── Message Parts ─────────────────────────────────────────────────────


def insert_message_parts(
    conn: sqlite3.Connection, message_id: int, parts: list[dict[str, Any]]
) -> None:
    for part in parts:
        conn.execute(
            """INSERT INTO message_parts (
                part_id, message_id, session_id, part_type, ordinal,
                text_content, tool_call_id, tool_name, tool_input, tool_output, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid4()),
                message_id,
                part["session_id"],
                part["part_type"],
                part["ordinal"],
                part.get("text_content"),
                part.get("tool_call_id"),
                part.get("tool_name"),
                part.get("tool_input"),
                part.get("tool_output"),
                part.get("metadata"),
            ),
        )


def fetch_message_parts(
    conn: sqlite3.Connection, message_id: int
) -> list[MessagePartRecord]:
    rows = conn.execute(
        """SELECT part_id, message_id, session_id, part_type, ordinal,
                  text_content, tool_call_id, tool_name, tool_input, tool_output, metadata
           FROM message_parts WHERE message_id = ? ORDER BY ordinal""",
        (message_id,),
    ).fetchall()
    return [_to_part(r) for r in rows]


# ── Deletion ──────────────────────────────────────────────────────────


def delete_unreferenced_messages(
    conn: sqlite3.Connection, message_ids: list[int]
) -> int:
    deleted = 0
    for mid in message_ids:
        # Skip if referenced by a summary
        ref = conn.execute(
            "SELECT 1 FROM summary_messages WHERE message_id = ? LIMIT 1",
            (mid,),
        ).fetchone()
        if ref:
            continue

        conn.execute(
            "DELETE FROM context_items WHERE item_type = 'message' AND message_id = ?",
            (mid,),
        )
        conn.execute("DELETE FROM messages WHERE message_id = ?", (mid,))
        deleted += 1
    return deleted
