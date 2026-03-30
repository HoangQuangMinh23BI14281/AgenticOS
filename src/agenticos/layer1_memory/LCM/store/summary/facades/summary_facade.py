"""
Summary Facade — Core summary CRUD and search.
"""

from __future__ import annotations

import sqlite3
from typing import Any, TYPE_CHECKING

from .base import SummaryBaseFacade
from ..crud import (
    SUMMARY_COLS,
    insert_summary_and_replace_context_atomic,
    insert_summary_record,
    safe_int,
    sanitize_summary_content,
    to_summary,
)
from ..search import execute_fts_search, execute_like_search
from ...fulltext_fallback import contains_cjk
from ....types import SummaryKind

if TYPE_CHECKING:
    from ....types import SummaryRecord


class SummaryFacade(SummaryBaseFacade):
    """Mixin for core summary CRUD operations."""

    def insert_summary(self, **kwargs) -> SummaryRecord:
        """Insert a new summary with sanitization."""
        kind = kwargs.get("kind")
        depth = kwargs.get("depth")
        kwargs["depth"] = safe_int(depth) if depth is not None else (0 if kind == SummaryKind.LEAF else 1)
        kwargs["content"] = sanitize_summary_content(kwargs.get("content", ""))

        record = self._pool.execute_write(lambda c: insert_summary_record(c, **kwargs))
        self._summary_cache[record.summary_id] = record
        return record

    def insert_summary_and_replace_context_atomic(
        self,
        summary_id: str,
        conversation_id: int,
        content: str,
        kind: SummaryKind,
        depth: int,
        token_count: int,
        source_message_token_count: int = 0,
        model: str = "unknown",
        source_message_ids: list[int] = None,
        source_summary_ids: list[str] = None,
        start_ordinal: int = 0,
        end_ordinal: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Atomic ingestion of a summary and context replacement."""

        def _atomic_op(conn: sqlite3.Connection) -> None:
            insert_summary_and_replace_context_atomic(
                conn,
                summary_id=summary_id,
                conversation_id=conversation_id,
                content=content,
                kind=kind,
                depth=depth,
                token_count=token_count,
                source_message_token_count=source_message_token_count,
                model=model,
                source_message_ids=source_message_ids,
                source_summary_ids=source_summary_ids,
                start_ordinal=start_ordinal,
                end_ordinal=end_ordinal,
            )

        self._pool.execute_in_transaction(_atomic_op)
        self.invalidate_cache(summary_id)

    def get_summaries_by_conversation(self, conversation_id: int) -> list[SummaryRecord]:
        def _get(conn: sqlite3.Connection) -> list[SummaryRecord]:
            rows = conn.execute(
                f"SELECT {SUMMARY_COLS} FROM summaries s WHERE s.conversation_id = ? ORDER BY s.created_at",
                (conversation_id,),
            ).fetchall()
            return [to_summary(r) for r in rows]

        return self._pool.execute_read(_get)

    def search_summaries(
        self,
        query: str,
        mode: str = "full_text",
        conversation_id: int | None = None,
        limit: int = 50,
        **kwargs,
    ) -> list[dict[str, Any]]:
        is_fts = (mode == "full_text" and self._fts5 and not contains_cjk(query))
        if is_fts:
            try:
                return self._pool.execute_read(lambda c: execute_fts_search(c, query, **kwargs))
            except Exception:
                pass
        return self._pool.execute_read(lambda c: execute_like_search(c, query, **kwargs))
