"""
Conversation Store — Facade for conversations, messages, and message parts.
Delegates to specialized submodules in store/conversation/.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from ..types import (
    ConversationRecord,
    MessagePartRecord,
    MessageRecord,
    MessageRole,
)
from ..db.connection import ConnectionPool
from .conversation import (
    delete_unreferenced_messages,
    fetch_conversation_by_id,
    fetch_conversation_by_session_id,
    fetch_conversation_by_session_key,
    fetch_max_seq,
    fetch_message_by_id,
    fetch_message_count,
    fetch_message_parts,
    fetch_messages,
    insert_conversation,
    insert_message,
    insert_message_parts,
    insert_messages_bulk,
    search_messages,
    update_conversation_session_id,
    update_conversation_session_key,
    update_message_content,
)

logger = logging.getLogger("lcm.store.conversation")


class ConversationStore:
    """
    Facade for conversations, messages, and message parts.
    Thread-safe via ConnectionPool.
    """

    def __init__(self, pool: ConnectionPool, fts5_available: bool = True) -> None:
        self._pool = pool
        self._fts5 = fts5_available

    # ── Conversation ops ──────────────────────────────────────────────────

    def create_conversation(
        self, session_id: str, session_key: str | None = None, title: str | None = None
    ) -> ConversationRecord:
        return self._pool.execute_write(
            lambda c: insert_conversation(c, session_id, session_key, title)
        )

    def get_conversation(self, conversation_id: int) -> ConversationRecord | None:
        return self._pool.execute_read(
            lambda c: fetch_conversation_by_id(c, conversation_id)
        )

    def get_conversation_by_session_id(self, session_id: str) -> ConversationRecord | None:
        return self._pool.execute_read(
            lambda c: fetch_conversation_by_session_id(c, session_id)
        )

    def get_conversation_by_session_key(self, session_key: str) -> ConversationRecord | None:
        return self._pool.execute_read(
            lambda c: fetch_conversation_by_session_key(c, session_key)
        )

    def get_or_create_conversation(
        self, session_id: str, session_key: str | None = None, title: str | None = None
    ) -> ConversationRecord:
        if session_key:
            existing = self.get_conversation_by_session_key(session_key)
            if existing:
                if existing.session_id != session_id:
                    self._pool.execute_write(
                        lambda c: update_conversation_session_id(c, existing.conversation_id, session_id)
                    )
                return existing

        existing = self.get_conversation_by_session_id(session_id)
        if existing:
            if session_key and not existing.session_key:
                self._pool.execute_write(
                    lambda c: update_conversation_session_key(c, existing.conversation_id, session_key)
                )
            return existing

        return self.create_conversation(session_id, session_key, title)

    # ── Message ops ───────────────────────────────────────────────────────

    def create_message(
        self, conversation_id: int, seq: int, role: MessageRole, content: str, token_count: int, conn: sqlite3.Connection | None = None
    ) -> MessageRecord:
        if conn:
            return insert_message(conn, conversation_id, seq, role, content, token_count)
        return self._pool.execute_write(
            lambda c: insert_message(c, conversation_id, seq, role, content, token_count)
        )

    def create_messages_bulk(self, inputs: list[dict[str, Any]]) -> list[MessageRecord]:
        return self._pool.execute_write(lambda c: insert_messages_bulk(c, inputs))

    def update_message(self, message_id: int, content: str, token_count: int) -> None:
        self._pool.execute_write(
            lambda c: update_message_content(c, message_id, content, token_count)
        )

    def get_messages(
        self, conversation_id: int, after_seq: int = -1, limit: int | None = None
    ) -> list[MessageRecord]:
        return self._pool.execute_read(
            lambda c: fetch_messages(c, conversation_id, after_seq, limit)
        )

    def get_message_by_id(self, message_id: int) -> MessageRecord | None:
        return self._pool.execute_read(lambda c: fetch_message_by_id(c, message_id))

    def get_max_seq(self, conversation_id: int) -> int:
        return self._pool.execute_read(lambda c: fetch_max_seq(c, conversation_id))

    def get_message_count(self, conversation_id: int) -> int:
        return self._pool.execute_read(lambda c: fetch_message_count(c, conversation_id))

    # ── Message Parts ─────────────────────────────────────────────────────

    def create_message_parts(self, message_id: int, parts: list[dict[str, Any]], conn: sqlite3.Connection | None = None) -> None:
        if conn:
            insert_message_parts(conn, message_id, parts)
        else:
            self._pool.execute_write(lambda c: insert_message_parts(c, message_id, parts))

    def get_message_parts(self, message_id: int) -> list[MessagePartRecord]:
        return self._pool.execute_read(lambda c: fetch_message_parts(c, message_id))

    # ── Search ────────────────────────────────────────────────────────────

    def search_messages(
        self, query: str, mode: str = "full_text", conversation_id: int | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        return self._pool.execute_read(
            lambda c: search_messages(c, query, mode, conversation_id, limit, self._fts5)
        )

    # ── Deletion ──────────────────────────────────────────────────────────

    def delete_messages(self, message_ids: list[int]) -> int:
        return self._pool.execute_write(
            lambda c: delete_unreferenced_messages(c, message_ids)
        )
