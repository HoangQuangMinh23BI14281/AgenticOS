"""
Summary Store — Facade for summaries, context items, large files, and DAG lineage.
Uses Multiple Inheritance (Mixins) to decompose responsibilities while maintaining a flat API.

Main entrance for Memory Layer Summary/DAG operations.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from cachetools import TTLCache

from ..db.connection import ConnectionPool
from .summary import SUMMARY_COLS, to_summary
from .summary.facades.context_facade import ContextFacade
from .summary.facades.file_facade import FileFacade
from .summary.facades.lineage_facade import LineageFacade
from .summary.facades.summary_facade import SummaryFacade

if TYPE_CHECKING:
    from ..types import SummaryRecord


class SummaryStore(SummaryFacade, ContextFacade, LineageFacade, FileFacade):
    """
    Consolidated facade for LCM Summary storage.
    Delegates implementation to domain-specific mixins in facades/*.
    """

    def __init__(
        self,
        pool: ConnectionPool,
        fts5_available: bool = True,
        subtree_cache_size: int = 128,
        subtree_cache_ttl: int = 300,
    ) -> None:
        # 1. Initialize state shared across Mixins
        summary_cache: dict[str, SummaryRecord] = {}
        subtree_cache: TTLCache = TTLCache(
            maxsize=subtree_cache_size, ttl=subtree_cache_ttl
        )

        super().__init__(
            pool=pool,
            fts5_available=fts5_available,
            summary_cache=summary_cache,
            subtree_cache=subtree_cache,
        )

    def get_summary(self, summary_id: str) -> SummaryRecord | None:
        """
        Overlay Get Summary with LRU caching.
        Base CRUD in SummaryFacade handles INSERT, this handles cached FETCH.
        """
        if summary_id in self._summary_cache:
            return self._summary_cache[summary_id]

        def _get(conn: sqlite3.Connection) -> SummaryRecord | None:
            row = conn.execute(
                f"SELECT {SUMMARY_COLS} FROM summaries s WHERE s.summary_id = ?",
                (summary_id,),
            ).fetchone()
            return to_summary(row) if row else None

        record = self._pool.execute_read(_get)
        if record:
            self._summary_cache[summary_id] = record
        return record
