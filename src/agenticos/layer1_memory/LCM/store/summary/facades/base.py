"""
Summary Base Facade — Shared state for store mixins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cachetools import TTLCache
    from ....db.connection import ConnectionPool
    from ....types import SummaryRecord


class SummaryBaseFacade:
    """Base class providing shared state and pool access."""

    def __init__(
        self,
        pool: ConnectionPool,
        fts5_available: bool,
        summary_cache: dict[str, SummaryRecord],
        subtree_cache: TTLCache,
    ) -> None:
        self._pool = pool
        self._fts5 = fts5_available
        self._summary_cache = summary_cache
        self._subtree_cache = subtree_cache

    def invalidate_cache(self, summary_id: str | None = None) -> None:
        """Invalidate cache entries after compaction or updates."""
        if summary_id:
            self._summary_cache.pop(summary_id, None)
            self._subtree_cache.pop(summary_id, None)
        else:
            self._summary_cache.clear()
            self._subtree_cache.clear()
