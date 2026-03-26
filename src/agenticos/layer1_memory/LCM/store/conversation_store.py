"""
Conversation Store — CRUD for conversations, messages, and message parts.

Uses ConnectionPool for thread-safe SQLite access.
Ported from store/conversation-store.ts.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..types import (
    ConversationRecord,
    MessagePartRecord,
    MessagePartType,
    MessageRecord,
    MessageRole,
)
from ..db.connection import ConnectionPool
from .fts5_sanitize import sanitize_fts5_query
from .fulltext_fallback import (
    build_like_search_plan,
    contains_cjk,
    create_fallback_snippet,
)

logger = logging.getLogger("lcm.store.conversation")


# ── Row Mappers ───────────────────────────────────────────────────────────────


def _parse_dt(value: str | None) -> datetime:
    """Parse an ISO timestamp string, defaulting to UTC now."""
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _to_conversation(row: sqlite3.Row) -> ConversationRecord:
    return ConversationRecord(
        conversation_id=row["conversation_id"],
        session_id=row["session_id"],
        session_key=row["session_key"],
        title=row["title"],
        bootstrapped_at=_parse_dt(row["bootstrapped_at"]) if row["bootstrapped_at"] else None,
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _to_message(row: sqlite3.Row) -> MessageRecord:
    return MessageRecord(
        message_id=row["message_id"],
        conversation_id=row["conversation_id"],
        seq=row["seq"],
        role=MessageRole(row["role"]),
        content=row["content"],
        token_count=row["token_count"],
        created_at=_parse_dt(row["created_at"]),
    )


def _to_part(row: sqlite3.Row) -> MessagePartRecord:
    return MessagePartRecord(
        part_id=row["part_id"],
        message_id=row["message_id"],
        session_id=row["session_id"],
        part_type=MessagePartType(row["part_type"]),
        ordinal=row["ordinal"],
        text_content=row["text_content"],
        tool_call_id=row["tool_call_id"],
        tool_name=row["tool_name"],
        tool_input=row["tool_input"],
        tool_output=row["tool_output"],
        metadata=row["metadata"],
    )


def _normalize_content_for_fts(content: str) -> str | None:
    """Normalize message content for FTS indexing."""
    trimmed = content.strip()
    if not trimmed:
        return None
    # Skip externalized file references
    if trimmed.startswith("[LCM File:") or trimmed.startswith("[LCM Tool Output:"):
        lines = [l.strip() for l in trimmed.split("\n") if l.strip()]
        if not lines:
            return None
        header = lines[0]
        summary_lines = []
        in_summary = False
        for line in lines[1:]:
            if line == "Exploration Summary:":
                in_summary = True
                continue
            if line.startswith("Use lcm_describe"):
                continue
            if in_summary:
                summary_lines.append(line)
        normalized = "\n".join([header] + summary_lines)
        return normalized if normalized else None
    return content


# ── ConversationStore ─────────────────────────────────────────────────────────


class ConversationStore:
    """
    CRUD operations for conversations, messages, and message parts.

    Thread-safe via ConnectionPool.
    """

    def __init__(self, pool: ConnectionPool, fts5_available: bool = True) -> None:
        self._pool = pool
        self._fts5 = fts5_available

    # ── Conversation ops ──────────────────────────────────────────────────

    def create_conversation(
        self,
        session_id: str,
        session_key: str | None = None,
        title: str | None = None,
    ) -> ConversationRecord:
        def _create(conn: sqlite3.Connection) -> ConversationRecord:
            cur = conn.execute(
                "INSERT INTO conversations (session_id, session_key, title) VALUES (?, ?, ?)",
                (session_id, session_key, title),
            )
            conn.commit()
            row = conn.execute(
                """SELECT conversation_id, session_id, session_key, title,
                          bootstrapped_at, created_at, updated_at
                   FROM conversations WHERE conversation_id = ?""",
                (cur.lastrowid,),
            ).fetchone()
            return _to_conversation(row)

        return self._pool.execute_write(_create)

    def get_conversation(self, conversation_id: int) -> ConversationRecord | None:
        def _get(conn: sqlite3.Connection) -> ConversationRecord | None:
            row = conn.execute(
                """SELECT conversation_id, session_id, session_key, title,
                          bootstrapped_at, created_at, updated_at
                   FROM conversations WHERE conversation_id = ?""",
                (conversation_id,),
            ).fetchone()
            return _to_conversation(row) if row else None

        return self._pool.execute_read(_get)

    def get_conversation_by_session_id(self, session_id: str) -> ConversationRecord | None:
        def _get(conn: sqlite3.Connection) -> ConversationRecord | None:
            row = conn.execute(
                """SELECT conversation_id, session_id, session_key, title,
                          bootstrapped_at, created_at, updated_at
                   FROM conversations
                   WHERE session_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (session_id,),
            ).fetchone()
            return _to_conversation(row) if row else None

        return self._pool.execute_read(_get)

    def get_conversation_by_session_key(self, session_key: str) -> ConversationRecord | None:
        def _get(conn: sqlite3.Connection) -> ConversationRecord | None:
            row = conn.execute(
                """SELECT conversation_id, session_id, session_key, title,
                          bootstrapped_at, created_at, updated_at
                   FROM conversations WHERE session_key = ? LIMIT 1""",
                (session_key,),
            ).fetchone()
            return _to_conversation(row) if row else None

        return self._pool.execute_read(_get)

    def get_or_create_conversation(
        self,
        session_id: str,
        session_key: str | None = None,
        title: str | None = None,
    ) -> ConversationRecord:
        """Find by session_key → session_id → create new."""
        if session_key:
            existing = self.get_conversation_by_session_key(session_key)
            if existing:
                if existing.session_id != session_id:
                    self._pool.execute_write(
                        lambda conn: (
                            conn.execute(
                                "UPDATE conversations SET session_id = ?, updated_at = datetime('now') WHERE conversation_id = ?",
                                (session_id, existing.conversation_id),
                            ),
                            conn.commit(),
                        )
                    )
                return existing

        existing = self.get_conversation_by_session_id(session_id)
        if existing:
            if session_key and not existing.session_key:
                self._pool.execute_write(
                    lambda conn: (
                        conn.execute(
                            "UPDATE conversations SET session_key = ?, updated_at = datetime('now') WHERE conversation_id = ?",
                            (session_key, existing.conversation_id),
                        ),
                        conn.commit(),
                    )
                )
            return existing

        return self.create_conversation(session_id, session_key, title)

    # ── Message ops ───────────────────────────────────────────────────────

    def create_message(
        self,
        conversation_id: int,
        seq: int,
        role: MessageRole,
        content: str,
        token_count: int,
    ) -> MessageRecord:
        def _create(conn: sqlite3.Connection) -> MessageRecord:
            cur = conn.execute(
                "INSERT INTO messages (conversation_id, seq, role, content, token_count) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, seq, role.value, content, token_count),
            )
            msg_id = cur.lastrowid
            self._index_fts(conn, msg_id, content)
            conn.commit()
            row = conn.execute(
                """SELECT message_id, conversation_id, seq, role, content, token_count, created_at
                   FROM messages WHERE message_id = ?""",
                (msg_id,),
            ).fetchone()
            return _to_message(row)

        return self._pool.execute_write(_create)

    def create_messages_bulk(
        self,
        inputs: list[dict[str, Any]],
    ) -> list[MessageRecord]:
        """Bulk insert messages. Each dict: {conversation_id, seq, role, content, token_count}."""
        if not inputs:
            return []

        def _bulk(conn: sqlite3.Connection) -> list[MessageRecord]:
            records = []
            for inp in inputs:
                cur = conn.execute(
                    "INSERT INTO messages (conversation_id, seq, role, content, token_count) VALUES (?, ?, ?, ?, ?)",
                    (inp["conversation_id"], inp["seq"], inp["role"], inp["content"], inp["token_count"]),
                )
                msg_id = cur.lastrowid
                self._index_fts(conn, msg_id, inp["content"])
                row = conn.execute(
                    """SELECT message_id, conversation_id, seq, role, content, token_count, created_at
                       FROM messages WHERE message_id = ?""",
                    (msg_id,),
                ).fetchone()
                records.append(_to_message(row))
            conn.commit()
            return records

        return self._pool.execute_write(_bulk)

    def get_messages(
        self,
        conversation_id: int,
        after_seq: int = -1,
        limit: int | None = None,
    ) -> list[MessageRecord]:
        def _get(conn: sqlite3.Connection) -> list[MessageRecord]:
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

        return self._pool.execute_read(_get)

    def get_message_by_id(self, message_id: int) -> MessageRecord | None:
        def _get(conn: sqlite3.Connection) -> MessageRecord | None:
            row = conn.execute(
                """SELECT message_id, conversation_id, seq, role, content, token_count, created_at
                   FROM messages WHERE message_id = ?""",
                (message_id,),
            ).fetchone()
            return _to_message(row) if row else None

        return self._pool.execute_read(_get)

    def get_max_seq(self, conversation_id: int) -> int:
        def _get(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            return row["max_seq"] if row else 0

        return self._pool.execute_read(_get)

    def get_message_count(self, conversation_id: int) -> int:
        def _get(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            return row["count"] if row else 0

        return self._pool.execute_read(_get)

    # ── Message Parts ─────────────────────────────────────────────────────

    def create_message_parts(
        self, message_id: int, parts: list[dict[str, Any]]
    ) -> None:
        if not parts:
            return

        def _create(conn: sqlite3.Connection) -> None:
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
            conn.commit()

        self._pool.execute_write(_create)

    def get_message_parts(self, message_id: int) -> list[MessagePartRecord]:
        def _get(conn: sqlite3.Connection) -> list[MessagePartRecord]:
            rows = conn.execute(
                """SELECT part_id, message_id, session_id, part_type, ordinal,
                          text_content, tool_call_id, tool_name, tool_input, tool_output, metadata
                   FROM message_parts WHERE message_id = ? ORDER BY ordinal""",
                (message_id,),
            ).fetchall()
            return [_to_part(r) for r in rows]

        return self._pool.execute_read(_get)

    # ── Search ────────────────────────────────────────────────────────────

    def search_messages(
        self,
        query: str,
        mode: str = "full_text",
        conversation_id: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Search messages using FTS5 or LIKE fallback.

        Returns list of dicts with: message_id, conversation_id, role, snippet, created_at.
        """
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

        return self._pool.execute_read(_search)

    def _search_like(
        self, query: str, limit: int, conversation_id: int | None
    ) -> list[dict[str, Any]]:
        plan = build_like_search_plan("content", query)
        if not plan["terms"]:
            return []

        def _search(conn: sqlite3.Connection) -> list[dict[str, Any]]:
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
                f"""SELECT message_id, conversation_id, role, content, created_at
                    FROM messages WHERE {' AND '.join(where)}
                    ORDER BY created_at DESC""",
                args,
            ).fetchall()

            results = []
            for r in rows:
                match = pattern.search(r["content"])
                if match:
                    results.append({
                        "message_id": r["message_id"],
                        "conversation_id": r["conversation_id"],
                        "role": r["role"],
                        "snippet": create_fallback_snippet(r["content"], query),
                        "created_at": r["created_at"],
                    })
                    if len(results) >= limit:
                        break
            return results

        return self._pool.execute_read(_search)

    # ── FTS helpers ───────────────────────────────────────────────────────

    def _index_fts(
        self, conn: sqlite3.Connection, message_id: int, content: str
    ) -> None:
        """Index a message in FTS5 (best-effort)."""
        if not self._fts5:
            return
        normalized = _normalize_content_for_fts(content)
        if not normalized:
            return
        try:
            conn.execute(
                "INSERT INTO messages_fts(rowid, content) VALUES (?, ?)",
                (message_id, normalized),
            )
        except Exception:
            pass  # FTS indexing is optional

    # ── Deletion ──────────────────────────────────────────────────────────

    def delete_messages(self, message_ids: list[int]) -> int:
        """Delete messages not referenced by summaries."""
        if not message_ids:
            return 0

        def _delete(conn: sqlite3.Connection) -> int:
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
                if self._fts5:
                    try:
                        conn.execute("DELETE FROM messages_fts WHERE rowid = ?", (mid,))
                    except Exception:
                        pass
                conn.execute("DELETE FROM messages WHERE message_id = ?", (mid,))
                deleted += 1
            conn.commit()
            return deleted

        return self._pool.execute_write(_delete)
