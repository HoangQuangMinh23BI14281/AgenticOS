"""
Lineage Facade — DAG structure and message linking.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from .base import SummaryBaseFacade
from ..crud import SUMMARY_COLS, to_summary
from ..lineage import (
    fetch_subtree,
    link_summary_to_messages,
    link_summary_to_parents,
)

if TYPE_CHECKING:
    from ....types import SummaryRecord, SummarySubtreeNode


class LineageFacade(SummaryBaseFacade):
    """Mixin for DAG lineage operations."""

    def link_to_messages(self, summary_id: str, message_ids: list[int]) -> None:
        self._pool.execute_write(lambda c: link_summary_to_messages(c, summary_id, message_ids))

    def link_to_parents(self, summary_id: str, parent_ids: list[str]) -> None:
        def _link(conn: sqlite3.Connection) -> None:
            # summary_id here is the NEW summary (the Parent).
            # parent_ids are the nodes being merged (the CHILDREN from a DAG perspective).
            for idx, child_id in enumerate(parent_ids):
                link_summary_to_parents(conn, child_id, summary_id, idx)
            for cid in parent_ids:
                self._subtree_cache.pop(cid, None)

        self._pool.execute_write(_link)

    def get_summary_parents(self, summary_id: str) -> list[SummaryRecord]:
        def _get(conn: sqlite3.Connection) -> list[SummaryRecord]:
            rows = conn.execute(
                f"""SELECT {SUMMARY_COLS} FROM summaries s
                    JOIN summary_parents sp ON sp.parent_summary_id = s.summary_id
                    WHERE sp.summary_id = ? ORDER BY sp.ordinal""",
                (summary_id,),
            ).fetchall()
            return [to_summary(r) for r in rows]

        return self._pool.execute_read(_get)

    def get_summary_children(self, parent_summary_id: str) -> list[SummaryRecord]:
        def _get(conn: sqlite3.Connection) -> list[SummaryRecord]:
            rows = conn.execute(
                f"""SELECT {SUMMARY_COLS} FROM summaries s
                    JOIN summary_parents sp ON sp.summary_id = s.summary_id
                    WHERE sp.parent_summary_id = ? ORDER BY sp.ordinal""",
                (parent_summary_id,),
            ).fetchall()
            return [to_summary(r) for r in rows]

        return self._pool.execute_read(_get)

    def get_summary_messages(self, summary_id: str) -> list[int]:
        def _get(conn: sqlite3.Connection) -> list[int]:
            rows = conn.execute(
                "SELECT message_id FROM summary_messages WHERE summary_id = ? ORDER BY ordinal",
                (summary_id,),
            ).fetchall()
            return [r["message_id"] for r in rows]

        return self._pool.execute_read(_get)

    def get_subtree(self, summary_id: str) -> list[SummarySubtreeNode]:
        if summary_id in self._subtree_cache:
            return self._subtree_cache[summary_id]
        result = self._pool.execute_read(lambda c: fetch_subtree(c, summary_id))
        self._subtree_cache[summary_id] = result
        return result
