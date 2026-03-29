"""
Summary Store — CRUD for summaries, context items, large files, and DAG lineage.

Features:
  - LRU cache on immutable get_summary() calls
  - TTLCache on expensive get_subtree() recursive CTE
  - ConnectionPool for thread-safe access

Ported from store/summary-store.ts.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from cachetools import TTLCache

from ..types import (
    ContextItemRecord,
    ContextItemType,
    LargeFileRecord,
    SummaryKind,
    SummaryRecord,
    SummarySubtreeNode,
)
from ..db.connection import ConnectionPool
from .fts5_sanitize import sanitize_fts5_query
from .fulltext_fallback import (
    build_like_search_plan,
    contains_cjk,
    create_fallback_snippet,
)

logger = logging.getLogger("lcm.store.summary")


# ── Row Mappers ───────────────────────────────────────────────────────────────


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        # Đảm bảo luôn có tzinfo kể cả khi chuỗi naive
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value == value and value >= 0:
        return int(value)
    return default


def _to_summary(row: sqlite3.Row) -> SummaryRecord:
    file_ids: list[str] = []
    try:
        file_ids = json.loads(row["file_ids"]) if row["file_ids"] else []
    except (json.JSONDecodeError, TypeError):
        pass

    return SummaryRecord(
        summary_id=row["summary_id"],
        conversation_id=row["conversation_id"],
        kind=SummaryKind(row["kind"]),
        depth=_safe_int(row["depth"]),
        content=row["content"],
        token_count=_safe_int(row["token_count"]),
        file_ids=file_ids,
        earliest_at=_parse_dt(row["earliest_at"]) if row["earliest_at"] else None,
        latest_at=_parse_dt(row["latest_at"]) if row["latest_at"] else None,
        descendant_count=_safe_int(row["descendant_count"]),
        descendant_token_count=_safe_int(row["descendant_token_count"]),
        source_message_token_count=_safe_int(row["source_message_token_count"]),
        model=row["model"] if isinstance(row["model"], str) else "unknown",
        created_at=_parse_dt(row["created_at"]),
    )


def _to_context_item(row: sqlite3.Row) -> ContextItemRecord:
    return ContextItemRecord(
        conversation_id=row["conversation_id"],
        ordinal=row["ordinal"],
        item_type=ContextItemType(row["item_type"]),
        message_id=row["message_id"],
        summary_id=row["summary_id"],
        created_at=_parse_dt(row["created_at"]),
    )


def _to_large_file(row: sqlite3.Row) -> LargeFileRecord:
    return LargeFileRecord(
        file_id=row["file_id"],
        conversation_id=row["conversation_id"],
        storage_uri=row["storage_uri"],
        file_name=row["file_name"],
        mime_type=row["mime_type"],
        byte_size=row["byte_size"],
        exploration_summary=row["exploration_summary"],
        created_at=_parse_dt(row["created_at"]),
    )


# Summary columns shared across queries
_SUMMARY_COLS = "s.summary_id, s.conversation_id, s.kind, s.depth, s.content, s.token_count, s.file_ids, s.earliest_at, s.latest_at, s.descendant_count, s.descendant_token_count, s.source_message_token_count, s.model, s.created_at"


# ── SummaryStore ──────────────────────────────────────────────────────────────


class SummaryStore:
    """
    CRUD for summaries, context items, large files, and DAG lineage.

    Caching strategy (per user review):
      - _summary_cache: dict — permanent cache for immutable summary records
      - _subtree_cache: TTLCache(ttl=300) — 5-minute cache for expensive recursive CTEs
      - invalidate_cache() — called after compaction creates new summaries
    """

    def __init__(
        self,
        pool: ConnectionPool,
        fts5_available: bool = True,
        subtree_cache_size: int = 128,
        subtree_cache_ttl: int = 300,
    ) -> None:
        self._pool = pool
        self._fts5 = fts5_available
        self._summary_cache: dict[str, SummaryRecord] = {}
        self._subtree_cache: TTLCache = TTLCache(
            maxsize=subtree_cache_size, ttl=subtree_cache_ttl
        )

    def _sanitize_content(self, text: str) -> str:
        """
        Ngăn chặn triệt để Prompt Leakage (như vụ Node 2595 tokens).
        Loại bỏ các thẻ XML và Header hệ thống trước khi lưu vào DB.
        """
        if not text: return ""
        
        if "</think>" in text:
            text = text.split("</think>")[-1]

        patterns = [
            r"<previous_context>.*?</previous_context>",
            r"<conversation_segment>.*?</conversation_segment>",
            r"<conversation_to_condense>.*?</conversation_to_condense>",
            r"<historical_logs>.*?</historical_logs>",
            r"<\w+>", r"</\w+>", 
            r"\*\*output requirements:\*\*",
            r"\*\*previous_context\*\*",
        ]
        for p in patterns:
            text = re.sub(p, "", text, flags=re.DOTALL | re.IGNORECASE)

        return text.strip()
    # ── Cache Management ──────────────────────────────────────────────────

    def invalidate_cache(self, summary_id: str | None = None) -> None:
        """
        Invalidate cache entries after compaction.

        Call with summary_id to invalidate a specific entry,
        or None to clear all caches.
        """
        if summary_id:
            self._summary_cache.pop(summary_id, None)
            self._subtree_cache.pop(summary_id, None)
        else:
            self._summary_cache.clear()
            self._subtree_cache.clear()

    # ── Summary CRUD ──────────────────────────────────────────────────────

    def insert_summary(
        self,
        summary_id: str,
        conversation_id: int,
        kind: SummaryKind,
        content: str,
        token_count: int,
        depth: int | None = None,
        file_ids: list[str] | None = None,
        earliest_at: datetime | None = None,
        latest_at: datetime | None = None,
        descendant_count: int = 0,
        descendant_token_count: int = 0,
        source_message_token_count: int = 0,
        model: str = "unknown",
    ) -> SummaryRecord:
        """Insert a new summary với bộ lọc Sanitizer tích hợp."""
        
        # CHỖ SỬA QUAN TRỌNG: Lọc nội dung trước khi insert
        clean_content = self._sanitize_content(content)
        
        resolved_depth = _safe_int(depth) if depth is not None else (0 if kind == SummaryKind.LEAF else 1)

        def _insert(conn: sqlite3.Connection) -> SummaryRecord:
            conn.execute(
                f"""INSERT INTO summaries (
                        summary_id, conversation_id, kind, depth, content, token_count,
                        file_ids, earliest_at, latest_at, descendant_count,
                        descendant_token_count, source_message_token_count, model
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    summary_id, conversation_id, kind.value, resolved_depth,
                    clean_content, # Lưu nội dung đã sạch
                    token_count, json.dumps(file_ids or []),
                    earliest_at.isoformat() if earliest_at else None,
                    latest_at.isoformat() if latest_at else None,
                    max(0, descendant_count), max(0, descendant_token_count),
                    max(0, source_message_token_count), model,
                ),
            )
            conn.commit()

            row = conn.execute(
                f"SELECT {_SUMMARY_COLS} FROM summaries s WHERE s.summary_id = ?",
                (summary_id,),
            ).fetchone()
            return _to_summary(row)

        record = self._pool.execute_write(_insert)
        self._summary_cache[summary_id] = record
        return record

    def get_summary(self, summary_id: str) -> SummaryRecord | None:
        """Get a summary by ID, with permanent cache for immutable data."""
        # Cache hit — O(1)
        cached = self._summary_cache.get(summary_id)
        if cached is not None:
            return cached

        # Cache miss — query DB
        def _get(conn: sqlite3.Connection) -> SummaryRecord | None:
            row = conn.execute(
                f"SELECT { _SUMMARY_COLS } FROM summaries s WHERE s.summary_id = ?",
                (summary_id,),
            ).fetchone()
            return _to_summary(row) if row else None

        record = self._pool.execute_read(_get)
        if record is not None:
            self._summary_cache[summary_id] = record
        return record

    def get_summaries_by_conversation(
        self, conversation_id: int
    ) -> list[SummaryRecord]:
        def _get(conn: sqlite3.Connection) -> list[SummaryRecord]:
            rows = conn.execute(
                f"SELECT { _SUMMARY_COLS } FROM summaries AS s WHERE s.conversation_id = ? ORDER BY s.created_at",
                (conversation_id,),
            ).fetchall()
            return [_to_summary(r) for r in rows]

        records = self._pool.execute_read(_get)
        for r in records:
            self._summary_cache[r.summary_id] = r
        return records

    # ── Lineage / DAG ─────────────────────────────────────────────────────

    def link_to_messages(self, summary_id: str, message_ids: list[int]) -> None:
        if not message_ids:
            return

        def _link(conn: sqlite3.Connection) -> None:
            for idx, mid in enumerate(message_ids):
                conn.execute(
                    """INSERT INTO summary_messages (summary_id, message_id, ordinal)
                       VALUES (?, ?, ?)
                       ON CONFLICT (summary_id, message_id) DO NOTHING""",
                    (summary_id, mid, idx),
                )
            conn.commit()

        self._pool.execute_write(_link)

    def link_to_parents(self, summary_id: str, parent_ids: list[str]) -> None:
        if not parent_ids:
            return

        def _link(conn: sqlite3.Connection) -> None:
            for idx, pid in enumerate(parent_ids):
                conn.execute(
                    """INSERT INTO summary_parents (summary_id, parent_summary_id, ordinal)
                       VALUES (?, ?, ?)
                       ON CONFLICT (summary_id, parent_summary_id) DO NOTHING""",
                    (summary_id, pid, idx),
                )
            conn.commit()

        self._pool.execute_write(_link)
        # Invalidate subtree caches for affected parents
        for pid in parent_ids:
            self._subtree_cache.pop(pid, None)

    def get_summary_parents(self, summary_id: str) -> list[SummaryRecord]:
        """Get the source summaries that were compacted into this summary."""
        def _get(conn: sqlite3.Connection) -> list[SummaryRecord]:
            rows = conn.execute(
                f"""SELECT s.{_SUMMARY_COLS.replace(chr(10), '')}
                    FROM summaries s
                    JOIN summary_parents sp ON sp.parent_summary_id = s.summary_id
                    WHERE sp.summary_id = ?
                    ORDER BY sp.ordinal""".replace(
                    f"s.{_SUMMARY_COLS.replace(chr(10), '')}",
                    ", ".join(f"s.{c.strip()}" for c in _SUMMARY_COLS.split(","))
                ),
                (summary_id,),
            ).fetchone()
            # Simplified query
            rows = conn.execute(
                """SELECT s.summary_id, s.conversation_id, s.kind, s.depth,
                          s.content, s.token_count, s.file_ids,
                          s.earliest_at, s.latest_at, s.descendant_count,
                          s.descendant_token_count, s.source_message_token_count,
                          s.model, s.created_at
                   FROM summaries s
                   JOIN summary_parents sp ON sp.parent_summary_id = s.summary_id
                   WHERE sp.summary_id = ?
                   ORDER BY sp.ordinal""",
                (summary_id,),
            ).fetchall()
            return [_to_summary(r) for r in rows]

        return self._pool.execute_read(_get)

    def get_summary_children(self, parent_summary_id: str) -> list[SummaryRecord]:
        """Get summaries that were created by compacting this summary."""
        def _get(conn: sqlite3.Connection) -> list[SummaryRecord]:
            rows = conn.execute(
                """SELECT s.summary_id, s.conversation_id, s.kind, s.depth,
                          s.content, s.token_count, s.file_ids,
                          s.earliest_at, s.latest_at, s.descendant_count,
                          s.descendant_token_count, s.source_message_token_count,
                          s.model, s.created_at
                   FROM summaries s
                   JOIN summary_parents sp ON sp.summary_id = s.summary_id
                   WHERE sp.parent_summary_id = ?
                   ORDER BY sp.ordinal""",
                (parent_summary_id,),
            ).fetchall()
            return [_to_summary(r) for r in rows]

        return self._pool.execute_read(_get)

    def get_summary_messages(self, summary_id: str) -> list[int]:
        """Get message IDs linked to a summary."""
        def _get(conn: sqlite3.Connection) -> list[int]:
            rows = conn.execute(
                "SELECT message_id FROM summary_messages WHERE summary_id = ? ORDER BY ordinal",
                (summary_id,),
            ).fetchall()
            return [r["message_id"] for r in rows]

        return self._pool.execute_read(_get)

    def get_subtree(self, summary_id: str) -> list[SummarySubtreeNode]:
        """
        Recursive subtree traversal using CTE.

        Cached with TTLCache (5-minute TTL) since this is an
        expensive query but the underlying data is mostly immutable.
        """
        cached = self._subtree_cache.get(summary_id)
        if cached is not None:
            return cached

        def _get(conn: sqlite3.Connection) -> list[SummarySubtreeNode]:
            sql = f"""WITH RECURSIVE subtree(summary_id, parent_summary_id, depth_from_root, path) AS (
                       SELECT ?, NULL, 0, ''
                       UNION ALL
                       SELECT sp.summary_id, sp.parent_summary_id,
                              subtree.depth_from_root + 1,
                              CASE
                                  WHEN subtree.path = '' THEN printf('%04d', sp.ordinal)
                                  ELSE subtree.path || '.' || printf('%04d', sp.ordinal)
                              END
                       FROM summary_parents sp
                       JOIN subtree ON sp.parent_summary_id = subtree.summary_id
                   )
                   SELECT { _SUMMARY_COLS },
                          subtree.depth_from_root, subtree.parent_summary_id,
                          subtree.path,
                          (SELECT COUNT(*) FROM summary_parents sp2
                           WHERE sp2.parent_summary_id = s.summary_id) AS child_count
                   FROM subtree
                   JOIN summaries s ON s.summary_id = subtree.summary_id
                   ORDER BY subtree.depth_from_root ASC, subtree.path ASC, s.created_at ASC"""
            
            rows = conn.execute(sql, (summary_id,)).fetchall()

            node_map: dict[str, SummarySubtreeNode] = {}
            for r in rows:
                sid = r["summary_id"]
                pid = r["parent_summary_id"]
                
                if sid not in node_map:
                    base = _to_summary(r)
                    node = SummarySubtreeNode(
                        **dataclasses.asdict(base),
                        depth_from_root=max(0, int(r["depth_from_root"] or 0)),
                        parent_ids=[pid] if pid else [],
                        parent_summary_id=pid,
                        path=r["path"] if isinstance(r["path"], str) else "",
                        child_count=_safe_int(r["child_count"]),
                    )
                    node_map[sid] = node
                else:
                    # Collect multi-parent lineage
                    if pid and pid not in node_map[sid].parent_ids:
                        node_map[sid].parent_ids.append(pid)
            return list(node_map.values())

        result = self._pool.execute_read(_get)
        self._subtree_cache[summary_id] = result
        return result

    # ── Context Items ─────────────────────────────────────────────────────

    def get_context_items(self, conversation_id: int) -> list[ContextItemRecord]:
        def _get(conn: sqlite3.Connection) -> list[ContextItemRecord]:
            rows = conn.execute(
                """SELECT conversation_id, ordinal, item_type, message_id, summary_id, created_at
                   FROM context_items WHERE conversation_id = ? ORDER BY ordinal""",
                (conversation_id,),
            ).fetchall()
            return [_to_context_item(r) for r in rows]

        return self._pool.execute_read(_get)

    def append_context_message(self, conversation_id: int, message_id: int) -> None:
        def _append(conn: sqlite3.Connection) -> None:
            row = conn.execute(
                "SELECT COALESCE(MAX(ordinal), -1) AS max_ordinal FROM context_items WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            conn.execute(
                "INSERT INTO context_items (conversation_id, ordinal, item_type, message_id) VALUES (?, ?, 'message', ?)",
                (conversation_id, row["max_ordinal"] + 1, message_id),
            )
            conn.commit()

        self._pool.execute_write(_append)

    def append_context_messages(self, conversation_id: int, message_ids: list[int]) -> None:
        if not message_ids:
            return

        def _append(conn: sqlite3.Connection) -> None:
            row = conn.execute(
                "SELECT COALESCE(MAX(ordinal), -1) AS max_ordinal FROM context_items WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            base = row["max_ordinal"] + 1
            for idx, mid in enumerate(message_ids):
                conn.execute(
                    "INSERT INTO context_items (conversation_id, ordinal, item_type, message_id) VALUES (?, ?, 'message', ?)",
                    (conversation_id, base + idx, mid),
                )
            conn.commit()

        self._pool.execute_write(_append)

    def append_context_summary(self, conversation_id: int, summary_id: str) -> None:
        def _append(conn: sqlite3.Connection) -> None:
            row = conn.execute(
                "SELECT COALESCE(MAX(ordinal), -1) AS max_ordinal FROM context_items WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            conn.execute(
                "INSERT INTO context_items (conversation_id, ordinal, item_type, summary_id) VALUES (?, ?, 'summary', ?)",
                (conversation_id, row["max_ordinal"] + 1, summary_id),
            )
            conn.commit()

        self._pool.execute_write(_append)

    def replace_context_range_with_summary(
        self,
        conversation_id: int,
        start_ordinal: int,
        end_ordinal: int,
        summary_id: str,
    ) -> None:
        """Replace a range of context items with a single summary, then resequence."""
        def _replace(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN")
            try:
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
                # 3. Resequence O(1) SQL Optimization (Audit Fix)
                # Tính toán khoảng cách (offset = số lượng items bị xóa - 1)
                offset = end_ordinal - start_ordinal
                if offset > 0:
                    conn.execute(
                        "UPDATE context_items SET ordinal = ordinal - ? WHERE conversation_id = ? AND ordinal > ?",
                        (offset, conversation_id, start_ordinal),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        self._pool.execute_write(_replace)

    def get_context_token_count(self, conversation_id: int) -> int:
        """Sum of token_count for all context items (messages + summaries)."""
        def _count(conn: sqlite3.Connection) -> int:
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

        return self._pool.execute_read(_count)

    def get_distinct_depths_in_context(
        self, conversation_id: int, max_ordinal: int | None = None
    ) -> list[int]:
        def _get(conn: sqlite3.Connection) -> list[int]:
            if max_ordinal is not None and max_ordinal != float("inf"):
                rows = conn.execute(
                    """SELECT DISTINCT s.depth
                       FROM context_items ci
                       JOIN summaries s ON s.summary_id = ci.summary_id
                       WHERE ci.conversation_id = ? AND ci.item_type = 'summary' AND ci.ordinal < ?
                       ORDER BY s.depth ASC""",
                    (conversation_id, int(max_ordinal)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT DISTINCT s.depth
                       FROM context_items ci
                       JOIN summaries s ON s.summary_id = ci.summary_id
                       WHERE ci.conversation_id = ? AND ci.item_type = 'summary'
                       ORDER BY s.depth ASC""",
                    (conversation_id,),
                ).fetchall()
            return [r["depth"] for r in rows]

        return self._pool.execute_read(_get)

    # ── Large Files ───────────────────────────────────────────────────────

    def insert_large_file(
        self,
        file_id: str,
        conversation_id: int,
        storage_uri: str,
        file_name: str | None = None,
        mime_type: str | None = None,
        byte_size: int | None = None,
        exploration_summary: str | None = None,
    ) -> LargeFileRecord:
        def _insert(conn: sqlite3.Connection) -> LargeFileRecord:
            conn.execute(
                """INSERT INTO large_files (file_id, conversation_id, file_name, mime_type,
                                            byte_size, storage_uri, exploration_summary)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (file_id, conversation_id, file_name, mime_type, byte_size, storage_uri, exploration_summary),
            )
            conn.commit()
            row = conn.execute(
                """SELECT file_id, conversation_id, file_name, mime_type, byte_size,
                          storage_uri, exploration_summary, created_at
                   FROM large_files WHERE file_id = ?""",
                (file_id,),
            ).fetchone()
            return _to_large_file(row)

        return self._pool.execute_write(_insert)

    def get_large_file(self, file_id: str) -> LargeFileRecord | None:
        def _get(conn: sqlite3.Connection) -> LargeFileRecord | None:
            row = conn.execute(
                """SELECT file_id, conversation_id, file_name, mime_type, byte_size,
                          storage_uri, exploration_summary, created_at
                   FROM large_files WHERE file_id = ?""",
                (file_id,),
            ).fetchone()
            return _to_large_file(row) if row else None

        return self._pool.execute_read(_get)

    def get_large_files_by_conversation(self, conversation_id: int) -> list[LargeFileRecord]:
        def _get(conn: sqlite3.Connection) -> list[LargeFileRecord]:
            rows = conn.execute(
                """SELECT file_id, conversation_id, file_name, mime_type, byte_size,
                          storage_uri, exploration_summary, created_at
                   FROM large_files WHERE conversation_id = ? ORDER BY created_at""",
                (conversation_id,),
            ).fetchall()
            return [_to_large_file(r) for r in rows]

        return self._pool.execute_read(_get)

    # ── Search ────────────────────────────────────────────────────────────

    def search_summaries(
        self,
        query: str,
        mode: str = "full_text",
        conversation_id: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if mode == "full_text":
            if contains_cjk(query):
                return self._search_like(query, limit, conversation_id)
            if self._fts5:
                try:
                    return self._search_fts(query, limit, conversation_id)
                except Exception:
                    return self._search_like(query, limit, conversation_id)
            return self._search_like(query, limit, conversation_id)
        return self._search_regex(query, limit, conversation_id)

    def _search_fts(
        self, query: str, limit: int, conversation_id: int | None
    ) -> list[dict[str, Any]]:
        def _search(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            where = ["summaries_fts MATCH ?"]
            args: list[Any] = [sanitize_fts5_query(query)]
            if conversation_id is not None:
                where.append("s.conversation_id = ?")
                args.append(conversation_id)
            args.append(limit)

            rows = conn.execute(
                f"""SELECT s.summary_id, s.conversation_id, s.kind, s.depth,
                           snippet(summaries_fts, 1, '', '', '...', 32) AS snippet,
                           s.created_at
                    FROM summaries_fts
                    JOIN summaries s ON s.summary_id = summaries_fts.summary_id
                    WHERE {' AND '.join(where)}
                    ORDER BY s.created_at DESC LIMIT ?""",
                args,
            ).fetchall()
            return [dict(r) for r in rows]
        return self._pool.execute_read(_search)

    def _search_like(
        self, query: str, limit: int, conversation_id: int | None
    ) -> list[dict[str, Any]]:
        plan = build_like_search_plan("content", query)
        if not plan["terms"]: return []

        def _search(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            where = list(plan["where"])
            args = list(plan["args"])
            if conversation_id is not None:
                where.append("conversation_id = ?")
                args.append(conversation_id)
            args.append(limit)

            # Đã thêm depth vào đây
            rows = conn.execute(
                f"""SELECT summary_id, conversation_id, kind, depth, content, created_at
                    FROM summaries WHERE {' AND '.join(where)}
                    ORDER BY created_at DESC LIMIT ?""",
                args,
            ).fetchall()
            return [
                {
                    "summary_id": r["summary_id"],
                    "conversation_id": r["conversation_id"],
                    "kind": r["kind"],
                    "depth": r["depth"],
                    "snippet": create_fallback_snippet(r["content"], query),
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
        return self._pool.execute_read(_search)

    def _search_regex(
        self, query: str, limit: int, conversation_id: int | None
    ) -> list[dict[str, Any]]:
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error:
            return []

        def _search(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            where = ["1=1"]
            args: list[Any] = []
            if conversation_id is not None:
                where.append("conversation_id = ?")
                args.append(conversation_id)

            rows = conn.execute(
                f"""SELECT summary_id, conversation_id, kind, content, created_at
                    FROM summaries WHERE {' AND '.join(where)}
                    ORDER BY created_at DESC""",
                args,
            ).fetchall()

            results = []
            for r in rows:
                if pattern.search(r["content"]):
                    results.append({
                        "summary_id": r["summary_id"],
                        "conversation_id": r["conversation_id"],
                        "kind": r["kind"],
                        "snippet": create_fallback_snippet(r["content"], query),
                        "created_at": r["created_at"],
                    })
                    if len(results) >= limit:
                        break
            return results

        return self._pool.execute_read(_search)
