"""
Large File Store — CRUD for large file records in LCM.
Part of the LCM SummaryStore decomposition.
"""

from __future__ import annotations

import sqlite3

from ...types import LargeFileRecord
from .crud import to_large_file


def insert_file_record(
    conn: sqlite3.Connection,
    file_id: str,
    conversation_id: int,
    storage_uri: str,
    file_name: str | None = None,
    mime_type: str | None = None,
    byte_size: int | None = None,
    exploration_summary: str | None = None,
) -> LargeFileRecord:
    conn.execute(
        """INSERT INTO large_files (file_id, conversation_id, file_name, mime_type,
                                    byte_size, storage_uri, exploration_summary)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (file_id, conversation_id, file_name, mime_type, byte_size, storage_uri, exploration_summary),
    )
    row = conn.execute(
        """SELECT file_id, conversation_id, file_name, mime_type, byte_size,
                  storage_uri, exploration_summary, created_at
           FROM large_files WHERE file_id = ?""",
        (file_id,),
    ).fetchone()
    return to_large_file(row)


def fetch_file_record(conn: sqlite3.Connection, file_id: str) -> LargeFileRecord | None:
    row = conn.execute(
        """SELECT file_id, conversation_id, file_name, mime_type, byte_size,
                  storage_uri, exploration_summary, created_at
           FROM large_files WHERE file_id = ?""",
        (file_id,),
    ).fetchone()
    return to_large_file(row) if row else None


def fetch_files_by_conversation(
    conn: sqlite3.Connection, conversation_id: int
) -> list[LargeFileRecord]:
    rows = conn.execute(
        """SELECT file_id, conversation_id, file_name, mime_type, byte_size,
                  storage_uri, exploration_summary, created_at
           FROM large_files WHERE conversation_id = ? ORDER BY created_at""",
        (conversation_id,),
    ).fetchall()
    return [to_large_file(row) for row in rows]
