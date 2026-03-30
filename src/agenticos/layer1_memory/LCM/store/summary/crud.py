"""
Summary CRUD — Lower-level database operations and record mapping.
Part of the LCM SummaryStore decomposition.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from ...types import (
    ContextItemRecord,
    ContextItemType,
    LargeFileRecord,
    SummaryKind,
    SummaryRecord,
)

logger = logging.getLogger("lcm.store.summary.crud")

# Summary columns shared across queries
SUMMARY_COLS = "s.summary_id, s.conversation_id, s.kind, s.depth, s.content, s.token_count, s.is_active, s.file_ids, s.earliest_at, s.latest_at, s.descendant_count, s.descendant_token_count, s.source_message_token_count, s.model, s.created_at"


def parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value == value and value >= 0:
        return int(value)
    return default


def to_summary(row: sqlite3.Row) -> SummaryRecord | None:
    if not row:
        return None
    return SummaryRecord(
        summary_id=row["summary_id"],
        conversation_id=row["conversation_id"],
        kind=SummaryKind(row["kind"]),
        depth=row["depth"],
        content=row["content"],
        token_count=row["token_count"],
        is_active=bool(row["is_active"]),
        file_ids=json.loads(row["file_ids"] or "[]"),
        earliest_at=parse_dt(row["earliest_at"]),
        latest_at=parse_dt(row["latest_at"]),
        descendant_count=row["descendant_count"],
        descendant_token_count=row["descendant_token_count"],
        source_message_token_count=row["source_message_token_count"],
        model=row["model"],
        created_at=parse_dt(row["created_at"]),
    )


def insert_summary_record(
    conn: sqlite3.Connection,
    summary_id: str,
    conversation_id: int,
    kind: SummaryKind,
    content: str,
    token_count: int,
    depth: int = 0,
    file_ids: list[str] | None = None,
    earliest_at: datetime | None = None,
    latest_at: datetime | None = None,
    descendant_count: int = 0,
    descendant_token_count: int = 0,
    source_message_token_count: int = 0,
    model: str = "unknown",
) -> SummaryRecord:
    conn.execute(
        f"""INSERT INTO summaries (
                summary_id, conversation_id, kind, depth, content, token_count,
                is_active, file_ids, earliest_at, latest_at, descendant_count,
                descendant_token_count, source_message_token_count, model
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)""",
        (
            summary_id, conversation_id, kind.value, depth,
            content, token_count, json.dumps(file_ids or []),
            earliest_at.isoformat() if earliest_at else None,
            latest_at.isoformat() if latest_at else None,
            max(0, descendant_count), max(0, descendant_token_count),
            max(0, source_message_token_count), model,
        ),
    )
    row = conn.execute(
        f"SELECT {SUMMARY_COLS} FROM summaries s WHERE s.summary_id = ?",
        (summary_id,),
    ).fetchone()
    return to_summary(row)


def to_context_item(row: sqlite3.Row) -> ContextItemRecord:
    return ContextItemRecord(
        conversation_id=row["conversation_id"],
        ordinal=row["ordinal"],
        item_type=ContextItemType(row["item_type"]),
        message_id=row["message_id"],
        summary_id=row["summary_id"],
        created_at=parse_dt(row["created_at"]),
    )


def to_large_file(row: sqlite3.Row) -> LargeFileRecord:
    return LargeFileRecord(
        file_id=row["file_id"],
        conversation_id=row["conversation_id"],
        storage_uri=row["storage_uri"],
        file_name=row["file_name"],
        mime_type=row["mime_type"],
        byte_size=row["byte_size"],
        exploration_summary=row["exploration_summary"],
        created_at=parse_dt(row["created_at"]),
    )


def sanitize_summary_content(text: str) -> str:
    """Removes prompt leaks, CoT, and system headers before database insertion."""
    if not text:
        return ""
    
    original_text = text

    # 1. Handle DeepSeek style <think>...</think>
    if "</think>" in text:
        # Keep everything after the last think tag
        parts = text.split("</think>")
        if len(parts) > 1 and parts[-1].strip():
            text = parts[-1]
        else:
            # If think is the only content, keep it but remove the tags
            text = text.replace("<think>", "").replace("</think>", "")

    # 2. Handle "Thinking Process:" markers
    if "Thinking Process:" in text:
        parts = text.split("Thinking Process:")
        if len(parts) > 1 and parts[-1].strip():
            text = parts[-1]

    # 3. Handle specific XML leaks and boilerplate (Avoid broad <\w+>)
    patterns = [
        r"<previous_context>.*?</previous_context>",
        r"<conversation_segment>.*?</conversation_segment>",
        r"<conversation_to_condense>.*?</conversation_to_condense>",
        r"<historical_logs>.*?</historical_logs>",
        r"<thought>.*?</thought>",
        r"\*\*output requirements:\*\*",
        r"\*\*previous_context\*\*",
    ]
    for p in patterns:
        text = re.sub(p, "", text, flags=re.DOTALL | re.IGNORECASE)
    
    final_text = text.strip()
    return final_text if final_text else original_text.strip()

def insert_summary_and_replace_context_atomic(
    conn: sqlite3.Connection,
    summary_id: str,
    conversation_id: int,
    content: str,
    kind: SummaryKind,
    depth: int,
    token_count: int,
    source_message_token_count: int = 0,
    model: str = "unknown",
    source_message_ids: list[int] = None,
    source_summary_ids: list[str] = None,
    start_ordinal: int = 0,
    end_ordinal: int = 0,
) -> None:
    """Atomic ingestion of a summary and context replacement."""
    from .lineage import fetch_descendant_metadata, link_summary_to_messages, link_summary_to_parents
    from .context import replace_context_range_atomic

    # 1. Calculate live descendant metadata
    desc_count, desc_tokens = fetch_descendant_metadata(conn, source_summary_ids or [])

    # 2. Insert Summary
    insert_summary_record(
        conn,
        summary_id=summary_id,
        conversation_id=conversation_id,
        kind=kind,
        content=sanitize_summary_content(content),
        depth=depth,
        token_count=token_count,
        descendant_count=desc_count,
        descendant_token_count=desc_tokens,
        source_message_token_count=source_message_token_count,
        model=model,
    )

    # 3. Link Lineage
    # summary_id is the Parent (the New node), source_summary_ids are Children (the Old nodes).
    for idx, child_id in enumerate(source_summary_ids or []):
        link_summary_to_parents(conn, child_id, summary_id, idx)
    link_summary_to_messages(conn, summary_id, source_message_ids or [])

    # 4. Update is_active for source items
    if source_message_ids:
        m_placeholders = ",".join(["?"] * len(source_message_ids))
        conn.execute(
            f"UPDATE messages SET is_active = 0 WHERE message_id IN ({m_placeholders})",
            source_message_ids,
        )
    if source_summary_ids:
        s_placeholders = ",".join(["?"] * len(source_summary_ids))
        conn.execute(
            f"UPDATE summaries SET is_active = 0 WHERE summary_id IN ({s_placeholders})",
            source_summary_ids,
        )

    # 5. Replace in Context
    replace_context_range_atomic(conn, conversation_id, start_ordinal, end_ordinal, summary_id)
