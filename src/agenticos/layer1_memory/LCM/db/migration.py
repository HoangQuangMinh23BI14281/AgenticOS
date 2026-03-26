"""
LCM Database Migration — Schema creation, FTS5, and backfill.

Creates 9 core tables, 7 indexes, and optional FTS5 virtual tables.
Ported from db/migration.ts in lossless-claw.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from .features import get_fts5_available

logger = logging.getLogger("lcm.db.migration")


# ── Core Schema ───────────────────────────────────────────────────────────────

_CORE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS conversations (
        conversation_id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        session_key TEXT,
        title TEXT,
        bootstrapped_at TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS messages (
        message_id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL
            REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        seq INTEGER NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
        content TEXT NOT NULL,
        token_count INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
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
        earliest_at TEXT,
        latest_at TEXT,
        descendant_count INTEGER NOT NULL DEFAULT 0,
        descendant_token_count INTEGER NOT NULL DEFAULT 0,
        source_message_token_count INTEGER NOT NULL DEFAULT 0,
        model TEXT NOT NULL DEFAULT 'unknown',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
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
        summary_id TEXT NOT NULL
            REFERENCES summaries(summary_id) ON DELETE CASCADE,
        message_id INTEGER NOT NULL
            REFERENCES messages(message_id) ON DELETE RESTRICT,
        ordinal INTEGER NOT NULL,
        PRIMARY KEY (summary_id, message_id)
    );

    CREATE TABLE IF NOT EXISTS summary_parents (
        summary_id TEXT NOT NULL
            REFERENCES summaries(summary_id) ON DELETE CASCADE,
        parent_summary_id TEXT NOT NULL
            REFERENCES summaries(summary_id) ON DELETE RESTRICT,
        ordinal INTEGER NOT NULL,
        PRIMARY KEY (summary_id, parent_summary_id)
    );

    CREATE TABLE IF NOT EXISTS context_items (
        conversation_id INTEGER NOT NULL
            REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL,
        item_type TEXT NOT NULL CHECK (item_type IN ('message', 'summary')),
        message_id INTEGER
            REFERENCES messages(message_id) ON DELETE RESTRICT,
        summary_id TEXT
            REFERENCES summaries(summary_id) ON DELETE RESTRICT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
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
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS conversation_bootstrap_state (
        conversation_id INTEGER PRIMARY KEY
            REFERENCES conversations(conversation_id) ON DELETE CASCADE,
        session_file_path TEXT NOT NULL,
        last_seen_size INTEGER NOT NULL,
        last_seen_mtime_ms INTEGER NOT NULL,
        last_processed_offset INTEGER NOT NULL,
        last_processed_entry_hash TEXT,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
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
    CREATE INDEX IF NOT EXISTS context_items_conv_idx
        ON context_items (conversation_id, ordinal);
    CREATE INDEX IF NOT EXISTS large_files_conv_idx
        ON large_files (conversation_id, created_at);
    CREATE INDEX IF NOT EXISTS bootstrap_state_path_idx
        ON conversation_bootstrap_state (session_file_path, updated_at);
    CREATE UNIQUE INDEX IF NOT EXISTS conversations_session_key_idx
        ON conversations (session_key);
"""


# ── Column Helpers ────────────────────────────────────────────────────────────


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    col_type: str,
    default: str | None = None,
) -> None:
    """Add a column if it doesn't exist."""
    if _has_column(conn, table, column):
        return
    default_clause = f" DEFAULT {default}" if default is not None else ""
    conn.execute(
        f"ALTER TABLE {table} ADD COLUMN {column} {col_type}{default_clause}"
    )
    logger.debug("Added column %s.%s", table, column)


# ── Backfill Functions ────────────────────────────────────────────────────────


def _parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO timestamp string into a datetime."""
    if not value or not value.strip():
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(value.strip(), fmt).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
    return None


def _iso_or_none(dt: datetime | None) -> str | None:
    """Convert datetime to ISO string or None."""
    return dt.isoformat() if dt else None


def _backfill_summary_depths(conn: sqlite3.Connection) -> None:
    """Compute and set correct depth values for all summaries."""
    # Leaves are always depth 0.
    conn.execute("UPDATE summaries SET depth = 0 WHERE kind = 'leaf'")

    rows = conn.execute(
        "SELECT DISTINCT conversation_id FROM summaries WHERE kind = 'condensed'"
    ).fetchall()

    if not rows:
        return

    for row in rows:
        conv_id = row["conversation_id"]

        summaries = conn.execute(
            "SELECT summary_id, kind, depth FROM summaries WHERE conversation_id = ?",
            (conv_id,),
        ).fetchall()

        depth_by_id: dict[str, int] = {}
        unresolved = set()

        for s in summaries:
            if s["kind"] == "leaf":
                depth_by_id[s["summary_id"]] = 0
            else:
                unresolved.add(s["summary_id"])

        edges = conn.execute(
            """SELECT summary_id, parent_summary_id
               FROM summary_parents
               WHERE summary_id IN (
                   SELECT summary_id FROM summaries
                   WHERE conversation_id = ? AND kind = 'condensed'
               )""",
            (conv_id,),
        ).fetchall()

        parents_map: dict[str, list[str]] = {}
        for e in edges:
            parents_map.setdefault(e["summary_id"], []).append(
                e["parent_summary_id"]
            )

        while unresolved:
            progressed = False
            for sid in list(unresolved):
                parent_ids = parents_map.get(sid, [])
                if not parent_ids:
                    depth_by_id[sid] = 1
                    unresolved.discard(sid)
                    progressed = True
                    continue

                parent_depths = [depth_by_id.get(pid) for pid in parent_ids]
                if any(d is None for d in parent_depths):
                    continue

                depth_by_id[sid] = max(d for d in parent_depths if d is not None) + 1
                unresolved.discard(sid)
                progressed = True

            if not progressed:
                for sid in unresolved:
                    depth_by_id[sid] = 1
                unresolved.clear()

        for s in summaries:
            depth = depth_by_id.get(s["summary_id"])
            if depth is not None:
                conn.execute(
                    "UPDATE summaries SET depth = ? WHERE summary_id = ?",
                    (depth, s["summary_id"]),
                )

    logger.debug("Backfilled summary depths")


def _backfill_summary_metadata(conn: sqlite3.Connection) -> None:
    """Compute time ranges and descendant counts for summaries."""
    conv_rows = conn.execute(
        "SELECT DISTINCT conversation_id FROM summaries"
    ).fetchall()

    if not conv_rows:
        return

    for conv_row in conv_rows:
        conv_id = conv_row["conversation_id"]

        summaries = conn.execute(
            """SELECT summary_id, kind, depth, token_count, created_at
               FROM summaries
               WHERE conversation_id = ?
               ORDER BY depth ASC, created_at ASC""",
            (conv_id,),
        ).fetchall()

        if not summaries:
            continue

        # Leaf time ranges from linked messages
        leaf_ranges = conn.execute(
            """SELECT sm.summary_id,
                      MIN(m.created_at) AS earliest_at,
                      MAX(m.created_at) AS latest_at,
                      COALESCE(SUM(m.token_count), 0) AS source_message_token_count
               FROM summary_messages sm
               JOIN messages m ON m.message_id = sm.message_id
               JOIN summaries s ON s.summary_id = sm.summary_id
               WHERE s.conversation_id = ? AND s.kind = 'leaf'
               GROUP BY sm.summary_id""",
            (conv_id,),
        ).fetchall()

        leaf_range_map = {
            r["summary_id"]: {
                "earliest": r["earliest_at"],
                "latest": r["latest_at"],
                "src_tokens": r["source_message_token_count"] or 0,
            }
            for r in leaf_ranges
        }

        # Parent edges
        edges = conn.execute(
            """SELECT summary_id, parent_summary_id
               FROM summary_parents
               WHERE summary_id IN (
                   SELECT summary_id FROM summaries WHERE conversation_id = ?
               )""",
            (conv_id,),
        ).fetchall()

        parents_map: dict[str, list[str]] = {}
        for e in edges:
            parents_map.setdefault(e["summary_id"], []).append(
                e["parent_summary_id"]
            )

        token_by_id = {
            s["summary_id"]: max(0, int(s["token_count"] or 0))
            for s in summaries
        }

        meta: dict[str, dict] = {}

        for s in summaries:
            sid = s["summary_id"]
            fallback = _parse_timestamp(s["created_at"])

            if s["kind"] == "leaf":
                lr = leaf_range_map.get(sid)
                meta[sid] = {
                    "earliest": _parse_timestamp(lr["earliest"] if lr else s["created_at"]) or fallback,
                    "latest": _parse_timestamp(lr["latest"] if lr else s["created_at"]) or fallback,
                    "desc_count": 0,
                    "desc_tokens": 0,
                    "src_tokens": max(0, lr["src_tokens"]) if lr else 0,
                }
                continue

            parent_ids = parents_map.get(sid, [])
            if not parent_ids:
                meta[sid] = {
                    "earliest": fallback,
                    "latest": fallback,
                    "desc_count": 0,
                    "desc_tokens": 0,
                    "src_tokens": 0,
                }
                continue

            earliest = None
            latest = None
            desc_count = 0
            desc_tokens = 0
            src_tokens = 0

            for pid in parent_ids:
                pm = meta.get(pid)
                if not pm:
                    continue
                pe = pm["earliest"]
                if pe and (earliest is None or pe < earliest):
                    earliest = pe
                pl = pm["latest"]
                if pl and (latest is None or pl > latest):
                    latest = pl
                desc_count += max(0, pm["desc_count"]) + 1
                desc_tokens += max(0, token_by_id.get(pid, 0)) + max(0, pm["desc_tokens"])
                src_tokens += max(0, pm["src_tokens"])

            meta[sid] = {
                "earliest": earliest or fallback,
                "latest": latest or fallback,
                "desc_count": max(0, desc_count),
                "desc_tokens": max(0, desc_tokens),
                "src_tokens": max(0, src_tokens),
            }

        for s in summaries:
            m = meta.get(s["summary_id"])
            if not m:
                continue
            conn.execute(
                """UPDATE summaries
                   SET earliest_at = ?, latest_at = ?,
                       descendant_count = ?, descendant_token_count = ?,
                       source_message_token_count = ?
                   WHERE summary_id = ?""",
                (
                    _iso_or_none(m["earliest"]),
                    _iso_or_none(m["latest"]),
                    max(0, m["desc_count"]),
                    max(0, m["desc_tokens"]),
                    max(0, m["src_tokens"]),
                    s["summary_id"],
                ),
            )

    logger.debug("Backfilled summary metadata")


def _backfill_tool_call_columns(conn: sqlite3.Connection) -> None:
    """
    Backfill tool_call_id, tool_name, tool_input from metadata JSON.

    Covers legacy rows where tool info was only stored in metadata.
    """
    conn.execute("""
        UPDATE message_parts
        SET tool_call_id = COALESCE(
            json_extract(metadata, '$.toolCallId'),
            json_extract(metadata, '$.raw.id'),
            json_extract(metadata, '$.raw.call_id'),
            json_extract(metadata, '$.raw.toolCallId'),
            json_extract(metadata, '$.raw.tool_call_id')
        )
        WHERE tool_call_id IS NULL
          AND metadata IS NOT NULL
          AND COALESCE(
              json_extract(metadata, '$.toolCallId'),
              json_extract(metadata, '$.raw.id'),
              json_extract(metadata, '$.raw.call_id'),
              json_extract(metadata, '$.raw.toolCallId'),
              json_extract(metadata, '$.raw.tool_call_id')
          ) IS NOT NULL
    """)

    conn.execute("""
        UPDATE message_parts
        SET tool_name = COALESCE(
            json_extract(metadata, '$.toolName'),
            json_extract(metadata, '$.raw.name'),
            json_extract(metadata, '$.raw.toolName'),
            json_extract(metadata, '$.raw.tool_name')
        )
        WHERE tool_name IS NULL
          AND metadata IS NOT NULL
          AND COALESCE(
              json_extract(metadata, '$.toolName'),
              json_extract(metadata, '$.raw.name'),
              json_extract(metadata, '$.raw.toolName'),
              json_extract(metadata, '$.raw.tool_name')
          ) IS NOT NULL
    """)

    conn.execute("""
        UPDATE message_parts
        SET tool_input = COALESCE(
            json_extract(metadata, '$.raw.input'),
            json_extract(metadata, '$.raw.arguments'),
            json_extract(metadata, '$.raw.toolInput')
        )
        WHERE tool_input IS NULL
          AND metadata IS NOT NULL
          AND COALESCE(
              json_extract(metadata, '$.raw.input'),
              json_extract(metadata, '$.raw.arguments'),
              json_extract(metadata, '$.raw.toolInput')
          ) IS NOT NULL
    """)

    logger.debug("Backfilled tool call columns")


# ── FTS5 Setup ────────────────────────────────────────────────────────────────


def _setup_fts5(conn: sqlite3.Connection) -> None:
    """Create or recreate FTS5 virtual tables for full-text search."""

    # Messages FTS
    existing = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages_fts'"
    ).fetchone()

    if existing:
        schema = existing["sql"] or ""
        if "content_rowid" in schema:
            # Stale external-content schema — drop and recreate
            conn.execute("DROP TABLE messages_fts")
            conn.execute("""
                CREATE VIRTUAL TABLE messages_fts USING fts5(
                    content,
                    tokenize='porter unicode61'
                )
            """)
            conn.execute(
                "INSERT INTO messages_fts(rowid, content) "
                "SELECT message_id, content FROM messages"
            )
    else:
        conn.execute("""
            CREATE VIRTUAL TABLE messages_fts USING fts5(
                content,
                tokenize='porter unicode61'
            )
        """)

    # Summaries FTS
    summaries_fts = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='summaries_fts'"
    ).fetchone()

    summaries_cols = conn.execute("PRAGMA table_info(summaries_fts)").fetchall()
    has_sid_col = any(c["name"] == "summary_id" for c in summaries_cols)

    should_recreate = (
        summaries_fts is None
        or not has_sid_col
        or "content_rowid" in (summaries_fts["sql"] or "")
    )

    if should_recreate:
        conn.execute("DROP TABLE IF EXISTS summaries_fts")
        conn.execute("""
            CREATE VIRTUAL TABLE summaries_fts USING fts5(
                summary_id UNINDEXED,
                content,
                tokenize='porter unicode61'
            )
        """)
        conn.execute(
            "INSERT INTO summaries_fts(summary_id, content) "
            "SELECT summary_id, content FROM summaries"
        )

    logger.debug("FTS5 tables configured")


# ── Public API ────────────────────────────────────────────────────────────────


def run_lcm_migrations(
    conn: sqlite3.Connection,
    *,
    fts5_available: bool | None = None,
) -> None:
    """
    Run all LCM database migrations.

    Creates the core schema, ensures forward-compatible columns,
    backfills computed metadata, and sets up FTS5 if available.

    Args:
        conn: SQLite connection (must have row_factory = sqlite3.Row)
        fts5_available: Override FTS5 detection. None = auto-detect.
    """
    logger.info("Running LCM migrations...")

    # Core schema
    conn.executescript(_CORE_SCHEMA)

    # Forward-compatible column additions
    _ensure_column(conn, "conversations", "bootstrapped_at", "TEXT")
    _ensure_column(conn, "conversations", "session_key", "TEXT")
    _ensure_column(conn, "summaries", "depth", "INTEGER NOT NULL", "0")
    _ensure_column(conn, "summaries", "earliest_at", "TEXT")
    _ensure_column(conn, "summaries", "latest_at", "TEXT")
    _ensure_column(conn, "summaries", "descendant_count", "INTEGER NOT NULL", "0")
    _ensure_column(conn, "summaries", "descendant_token_count", "INTEGER NOT NULL", "0")
    _ensure_column(conn, "summaries", "source_message_token_count", "INTEGER NOT NULL", "0")
    _ensure_column(conn, "summaries", "model", "TEXT NOT NULL", "'unknown'")

    # Backfill
    _backfill_summary_depths(conn)
    _backfill_summary_metadata(conn)
    _backfill_tool_call_columns(conn)

    conn.commit()

    # FTS5 (optional)
    if fts5_available is None:
        fts5_available = get_fts5_available(conn)

    if fts5_available:
        _setup_fts5(conn)
        conn.commit()
        logger.info("LCM migrations complete (FTS5 enabled)")
    else:
        logger.info("LCM migrations complete (FTS5 not available)")
