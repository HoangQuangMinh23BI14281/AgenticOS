"""
Summary Context — Context window management (context_items).
Part of the LCM SummaryStore decomposition.
"""

from __future__ import annotations

import logging
import sqlite3

from ...types import ContextItemRecord, ContextItemType, SummaryKind
from .crud import to_context_item

logger = logging.getLogger("lcm.context")


def fetch_context_items(
    conn: sqlite3.Connection, conversation_id: int
) -> list[ContextItemRecord]:
    rows = conn.execute(
        """SELECT conversation_id, ordinal, item_type, message_id, summary_id, created_at
           FROM context_items WHERE conversation_id = ? ORDER BY ordinal""",
        (conversation_id,),
    ).fetchall()
    return [to_context_item(r) for r in rows]


def append_message_to_context(
    conn: sqlite3.Connection, conversation_id: int, message_id: int
) -> None:
    row = conn.execute(
        "SELECT COALESCE(MAX(ordinal), -1) AS max_ordinal FROM context_items WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    new_ordinal = row["max_ordinal"] + 1
    
    logger.debug("[lcm.context] appending message %d to conv %d at ordinal %d", message_id, conversation_id, new_ordinal)
    
    conn.execute(
        "INSERT INTO context_items (conversation_id, ordinal, item_type, message_id) VALUES (?, ?, 'message', ?)",
        (conversation_id, new_ordinal, message_id),
    )


def append_summary_to_context(
    conn: sqlite3.Connection, conversation_id: int, summary_id: str
) -> None:
    row = conn.execute(
        "SELECT COALESCE(MAX(ordinal), -1) AS max_ordinal FROM context_items WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    conn.execute(
        "INSERT INTO context_items (conversation_id, ordinal, item_type, summary_id) VALUES (?, ?, 'summary', ?)",
        (conversation_id, row["max_ordinal"] + 1, summary_id),
    )


def replace_context_range_atomic(
    conn: sqlite3.Connection,
    conversation_id: int,
    start_ordinal: int,
    end_ordinal: int,
    summary_id: str,
) -> None:
    """Atomic replacement of a range with a single summary node."""
    # Delete range
    conn.execute(
        "DELETE FROM context_items WHERE conversation_id = ? AND ordinal >= ? AND ordinal <= ?",
        (conversation_id, start_ordinal, end_ordinal),
    )
    # Insert summary at start position
    conn.execute(
        "INSERT INTO context_items (conversation_id, ordinal, item_type, summary_id) VALUES (?, ?, 'summary', ?)",
        (conversation_id, start_ordinal, summary_id),
    )
    # Resequence
    offset = end_ordinal - start_ordinal
    if offset > 0:
        conn.execute(
            "UPDATE context_items SET ordinal = ordinal - ? WHERE conversation_id = ? AND ordinal > ?",
            (offset, conversation_id, end_ordinal), # Sửa ordinal > end_ordinal để dồn hàng sau khi xóa dải
        )
    elif offset < 0:
        # Fallback security: logic error in Compactor, prevent corruption
        logger.error(f"[lcm] invalid context replacement range: {start_ordinal}-{end_ordinal}")
        raise ValueError(f"Invalid context replacement range: {start_ordinal}-{end_ordinal}")


def calculate_context_token_count(conn: sqlite3.Connection, conversation_id: int) -> int:
    row = conn.execute(
        """SELECT COALESCE(SUM(token_count), 0) AS total FROM (
               SELECT m.token_count
               FROM context_items ci
               JOIN messages m ON m.message_id = ci.message_id
               WHERE ci.conversation_id = ? AND ci.item_type = 'message'
               UNION ALL
               SELECT s.token_count
               FROM context_items ci
               JOIN summaries s ON s.summary_id = ci.summary_id
               WHERE ci.conversation_id = ? AND ci.item_type = 'summary'
           ) sub""",
        (conversation_id, conversation_id),
    ).fetchone()
    return row["total"] if row else 0
