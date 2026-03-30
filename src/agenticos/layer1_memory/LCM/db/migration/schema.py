"""
LCM Database Migration — Core Schema and Entry point.
"""

from __future__ import annotations

import logging
import sqlite3

from ..features import get_fts5_available
from .utils import ensure_column
from .backfill import (
    backfill_summary_depths,
    backfill_summary_metadata,
    backfill_tool_call_columns,
)
from .fts import setup_fts5

logger = logging.getLogger("lcm.db.migration.schema")

_CORE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS conversations (
        conversation_id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        session_key TEXT,
        title TEXT,
        bootstrapped_at TEXT,
        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')),
        updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))
    );

    CREATE TABLE IF NOT EXISTS messages (
        message_id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL
            REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        seq INTEGER NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
        content TEXT NOT NULL,
        token_count INTEGER NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')),
        UNIQUE (conversation_id, seq)
    );

    CREATE TABLE IF NOT EXISTS summaries (
        summary_id TEXT PRIMARY KEY,
        conversation_id INTEGER NOT NULL
            REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('leaf', 'condensed')),
        depth INTEGER NOT NULL DEFAULT 0,
        content TEXT NOT NULL,
        token_count INTEGER NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1,
        earliest_at TEXT,
        latest_at TEXT,
        descendant_count INTEGER NOT NULL DEFAULT 0,
        descendant_token_count INTEGER NOT NULL DEFAULT 0,
        source_message_token_count INTEGER NOT NULL DEFAULT 0,
        model TEXT NOT NULL DEFAULT 'unknown',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')),
        file_ids TEXT NOT NULL DEFAULT '[]'
    );

    CREATE TABLE IF NOT EXISTS message_parts (
        part_id TEXT PRIMARY KEY,
        message_id INTEGER NOT NULL
            REFERENCES messages(message_id) ON DELETE CASCADE,
        session_id TEXT NOT NULL,
        part_type TEXT NOT NULL CHECK (part_type IN (
            'text', 'reasoning', 'tool', 'patch', 'file',
            'subtask', 'compaction', 'step_start', 'step_finish',
            'snapshot', 'agent', 'retry'
        )),
        ordinal INTEGER NOT NULL,
        text_content TEXT,
        is_ignored INTEGER,
        is_synthetic INTEGER,
        tool_call_id TEXT,
        tool_name TEXT,
        tool_status TEXT,
        tool_input TEXT,
        tool_output TEXT,
        tool_error TEXT,
        tool_title TEXT,
        patch_hash TEXT,
        patch_files TEXT,
        file_mime TEXT,
        file_name TEXT,
        file_url TEXT,
        subtask_prompt TEXT,
        subtask_desc TEXT,
        subtask_agent TEXT,
        step_reason TEXT,
        step_cost REAL,
        step_tokens_in INTEGER,
        step_tokens_out INTEGER,
        snapshot_hash TEXT,
        compaction_auto INTEGER,
        metadata TEXT,
        UNIQUE (message_id, ordinal)
    );

    CREATE TABLE IF NOT EXISTS summary_messages (
        summary_id TEXT NOT NULL REFERENCES summaries(summary_id) ON DELETE CASCADE,
        message_id INTEGER NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL,
        PRIMARY KEY (summary_id, message_id)
    );

    CREATE TABLE IF NOT EXISTS summary_parents (
        summary_id TEXT NOT NULL
            REFERENCES summaries(summary_id) ON DELETE CASCADE,
        parent_summary_id TEXT NOT NULL
            REFERENCES summaries(summary_id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL,
        PRIMARY KEY (summary_id, parent_summary_id)
    );

    CREATE TABLE IF NOT EXISTS context_items (
        conversation_id INTEGER NOT NULL
            REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL,
        item_type TEXT NOT NULL CHECK (item_type IN ('message', 'summary')),
        message_id INTEGER
            REFERENCES messages(message_id) ON DELETE CASCADE,
        summary_id TEXT
            REFERENCES summaries(summary_id) ON DELETE CASCADE,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')),
        PRIMARY KEY (conversation_id, ordinal),
        CHECK (
            (item_type = 'message' AND message_id IS NOT NULL AND summary_id IS NULL) OR
            (item_type = 'summary' AND summary_id IS NOT NULL AND message_id IS NULL)
        )
    );

    CREATE TABLE IF NOT EXISTS large_files (
        file_id TEXT PRIMARY KEY,
        conversation_id INTEGER NOT NULL
            REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        file_name TEXT,
        mime_type TEXT,
        byte_size INTEGER,
        storage_uri TEXT NOT NULL,
        exploration_summary TEXT,
        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))
    );

    CREATE TABLE IF NOT EXISTS conversation_bootstrap_state (
        conversation_id INTEGER PRIMARY KEY
            REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        session_file_path TEXT NOT NULL,
        last_seen_size INTEGER NOT NULL,
        last_seen_mtime_ms INTEGER NOT NULL,
        last_processed_offset INTEGER NOT NULL,
        last_processed_entry_hash TEXT,
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))
    );

    -- Indexes
    CREATE INDEX IF NOT EXISTS messages_conv_seq_idx
        ON messages (conversation_id, seq);
    CREATE INDEX IF NOT EXISTS summaries_conv_created_idx
        ON summaries (conversation_id, created_at);
    CREATE INDEX IF NOT EXISTS message_parts_message_idx
        ON message_parts (message_id);
    CREATE INDEX IF NOT EXISTS message_parts_type_idx
        ON message_parts (part_type);
    CREATE INDEX IF NOT EXISTS summary_parents_parent_idx
        ON summary_parents (parent_summary_id);
    CREATE INDEX IF NOT EXISTS context_items_conv_idx
        ON context_items (conversation_id, ordinal);
    CREATE INDEX IF NOT EXISTS large_files_conv_idx
        ON large_files (conversation_id, created_at);
    CREATE INDEX IF NOT EXISTS bootstrap_state_path_idx
        ON conversation_bootstrap_state (session_file_path, updated_at);
    CREATE UNIQUE INDEX IF NOT EXISTS conversations_session_key_idx
        ON conversations (session_key);
"""


def run_lcm_migrations(
    conn: sqlite3.Connection,
    *,
    fts5_available: bool | None = None,
) -> None:
    """
    Run all LCM database migrations.
    """
    logger.info("Running LCM migrations...")

    # 1. Core schema
    conn.executescript(_CORE_SCHEMA)

    conn.execute("BEGIN")
    try:
        # 2. Forward-compatible column additions
        ensure_column(conn, "conversations", "bootstrapped_at", "TEXT")
        ensure_column(conn, "conversations", "session_key", "TEXT")
        ensure_column(conn, "summaries", "is_active", "INTEGER NOT NULL", "1")
        ensure_column(conn, "messages", "is_active", "INTEGER NOT NULL", "1")
        ensure_column(conn, "summaries", "depth", "INTEGER NOT NULL", "0")
        ensure_column(conn, "summaries", "earliest_at", "TEXT")
        ensure_column(conn, "summaries", "latest_at", "TEXT")
        ensure_column(conn, "summaries", "descendant_count", "INTEGER NOT NULL", "0")
        ensure_column(conn, "summaries", "descendant_token_count", "INTEGER NOT NULL", "0")
        ensure_column(conn, "summaries", "source_message_token_count", "INTEGER NOT NULL", "0")
        ensure_column(conn, "summaries", "model", "TEXT NOT NULL", "'unknown'")
        ensure_column(conn, "summary_parents", "ordinal", "INTEGER NOT NULL", "0")

        # 3. Backfill data
        backfill_summary_depths(conn)
        backfill_summary_metadata(conn)
        backfill_tool_call_columns(conn)

        # 4. FTS5 (optional)
        if fts5_available is None:
            fts5_available = get_fts5_available(conn)

        if fts5_available:
            setup_fts5(conn)
            logger.info("LCM migrations complete (FTS5 enabled)")
        else:
            logger.info("LCM migrations complete (FTS5 not available)")

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
