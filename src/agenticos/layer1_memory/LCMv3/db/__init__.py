"""
LCMv3 database layer.

Manages the SQLite vault (vault.db) — sessions, messages, summaries, and the
DAG link table (summary_sources). Path constants and connection state live here
so test fixtures and the MCP server can reassign them at runtime.
"""

import os
import sqlite3
from pathlib import Path
from typing import Optional

from .schema import SCHEMA_SQL, FTS_SQL, FTS_TRIGGERS_SQL

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

LOSSLESS_HOME = Path(os.environ.get("LOSSLESS_HOME", Path.home() / ".lossless-code"))
VAULT_DIR = Path(os.environ.get("LOSSLESS_VAULT_DIR", Path.home() / ".lossless-code"))
VAULT_DB = VAULT_DIR / "vault.db"
CONFIG_PATH = VAULT_DIR / "config.json"

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

_conn: Optional[sqlite3.Connection] = None


def get_db() -> sqlite3.Connection:
    """Return a module-level connection, creating vault.db if needed."""
    global _conn
    if _conn is not None:
        return _conn

    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(VAULT_DB), timeout=10)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.executescript(SCHEMA_SQL)
    _conn.executescript(FTS_SQL)
    _conn.executescript(FTS_TRIGGERS_SQL)
    # Idempotent migrations
    for col_sql in [
        "ALTER TABLE summaries ADD COLUMN consolidated INTEGER DEFAULT 0",
        "ALTER TABLE sessions ADD COLUMN stateless INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE messages ADD COLUMN file_path TEXT",
        "ALTER TABLE summaries ADD COLUMN kind TEXT",
    ]:
        try:
            _conn.execute(col_sql)
        except sqlite3.OperationalError:
            pass
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_file_path ON messages(file_path) WHERE file_path IS NOT NULL")
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_summary_sources_source ON summary_sources(source_type, source_id)")
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_summaries_depth_consolidated ON summaries(depth, consolidated)")
    _conn.execute("""CREATE TABLE IF NOT EXISTS message_embeddings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER NOT NULL REFERENCES messages(id),
        model_name TEXT NOT NULL, vector BLOB NOT NULL, created_at INTEGER NOT NULL,
        UNIQUE(message_id, model_name))""")
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_emb_message ON message_embeddings(message_id)")
    _conn.execute("CREATE INDEX IF NOT EXISTS idx_emb_model ON message_embeddings(model_name)")
    _conn.commit()
    return _conn


def close_db() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


# ---------------------------------------------------------------------------
# Submodule re-exports
# ---------------------------------------------------------------------------

from .config import DEFAULT_CONFIG, load_config, save_config
from .sessions import (matches_any_pattern, get_session_stateless, ensure_session,
                        get_session, list_sessions, set_handoff, count_sessions_since)
from .messages import (store_message, get_unsummarised, mark_summarised,
                        get_messages_by_ids, count_session_messages, get_messages_since)
from .summaries import (gen_summary_id, store_summary, get_summary, get_summary_sources,
                         get_summaries_at_depth, get_top_summaries, get_summaries_since,
                         get_summary_ids_since, get_summaries_by_ids, get_overlapping_summaries,
                         mark_consolidated, get_summaries_for_file, get_max_summary_depth)
from .search import escape_fts5_query, search_messages, search_summaries, search_all
from .dream_log import project_hash, get_last_dream, store_dream_log
from .embeddings import (upsert_embedding, get_unembed_messages, get_all_messages_for_reindex,
                          delete_embeddings_for_model, count_embeddings,
                          get_embedding_model_coverage, get_all_embeddings)
