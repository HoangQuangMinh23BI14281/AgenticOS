"""
LCM Database Migration — FTS5 virtual tables and triggers.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("lcm.db.migration.fts")


def setup_fts5(conn: sqlite3.Connection) -> None:
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
        conn.execute(
            "INSERT INTO messages_fts (rowid, content) SELECT message_id, content FROM messages"
        )

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

    # Trigger cho messages_fts (Standard FTS5 Table)
    triggers = [
        """CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages
           BEGIN
               INSERT INTO messages_fts (rowid, content) VALUES (new.message_id, new.content);
           END;""",
        """CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages
           BEGIN
               DELETE FROM messages_fts WHERE rowid = old.message_id;
           END;""",
        """CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages
           BEGIN
               UPDATE messages_fts 
               SET content = new.content 
               WHERE rowid = new.message_id;
           END;""",
        """CREATE TRIGGER IF NOT EXISTS summaries_ai AFTER INSERT ON summaries
           BEGIN
               INSERT INTO summaries_fts (summary_id, content) VALUES (new.summary_id, new.content);
           END;""",
        """CREATE TRIGGER IF NOT EXISTS summaries_ad AFTER DELETE ON summaries
           BEGIN
               DELETE FROM summaries_fts WHERE summary_id = old.summary_id;
           END;""",
        """CREATE TRIGGER IF NOT EXISTS summaries_au AFTER UPDATE ON summaries
           BEGIN
               UPDATE summaries_fts 
               SET content = new.content 
               WHERE summary_id = new.summary_id;
           END;"""
    ]
    for sql in triggers:
        conn.execute(sql)

    logger.debug("FTS5 tables and triggers configured")
