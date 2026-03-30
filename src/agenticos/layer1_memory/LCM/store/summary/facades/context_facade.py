"""
Context Facade — Context window management (context_items).
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from .base import SummaryBaseFacade
from ..context import (
    append_message_to_context,
    append_summary_to_context,
    calculate_context_token_count,
    fetch_context_items,
    replace_context_range_atomic,
)

if TYPE_CHECKING:
    from ....types import ContextItemRecord


class ContextFacade(SummaryBaseFacade):
    """Mixin for context item operations."""

    def get_context_items(self, conversation_id: int) -> list[ContextItemRecord]:
        return self._pool.execute_read(lambda c: fetch_context_items(c, conversation_id))

    def append_context_message(self, conversation_id: int, message_id: int, conn: sqlite3.Connection | None = None) -> None:
        if conn:
            append_message_to_context(conn, conversation_id, message_id)
        else:
            self._pool.execute_write(lambda c: append_message_to_context(c, conversation_id, message_id))

    def append_context_messages(self, conversation_id: int, message_ids: list[int]) -> None:
        def _append(conn: sqlite3.Connection) -> None:
            for mid in message_ids:
                append_message_to_context(conn, conversation_id, mid)

        self._pool.execute_write(_append)

    def append_context_summary(self, conversation_id: int, summary_id: str) -> None:
        self._pool.execute_write(lambda c: append_summary_to_context(c, conversation_id, summary_id))

    def replace_context_range_with_summary(
        self, conversation_id: int, start_ordinal: int, end_ordinal: int, summary_id: str
    ) -> None:
        self._pool.execute_write(
            lambda c: replace_context_range_atomic(c, conversation_id, start_ordinal, end_ordinal, summary_id)
        )

    def get_context_token_count(self, conversation_id: int) -> int:
        return self._pool.execute_read(lambda c: calculate_context_token_count(c, conversation_id))

    def get_distinct_depths_in_context(self, conversation_id: int, max_ordinal: int | None = None) -> list[int]:
        def _get(conn: sqlite3.Connection) -> list[int]:
            sql = """SELECT DISTINCT s.depth FROM context_items ci
                     JOIN summaries s ON s.summary_id = ci.summary_id
                     WHERE ci.conversation_id = ? AND ci.item_type = 'summary'"""
            args = [conversation_id]
            if max_ordinal is not None and max_ordinal != float("inf"):
                sql += " AND ci.ordinal < ?"
                args.append(int(max_ordinal))
            rows = conn.execute(sql + " ORDER BY s.depth ASC", args).fetchall()
            return [r["depth"] for r in rows]

        return self._pool.execute_read(_get)
