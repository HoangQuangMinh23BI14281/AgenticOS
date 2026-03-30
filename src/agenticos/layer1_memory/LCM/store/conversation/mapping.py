"""
Conversation Mapping — Row mappers and date parsing.
Part of the LCM ConversationStore decomposition.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from ...types import (
    ConversationRecord,
    MessagePartRecord,
    MessagePartType,
    MessageRecord,
    MessageRole,
)


def _parse_dt(value: str | None) -> datetime:
    """Parse an ISO timestamp string, defaulting to UTC now."""
    if not value:
        return datetime.now(timezone.utc)
    try:
        # Đảm bảo luôn có tzinfo kể cả khi chuỗi naive
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
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
