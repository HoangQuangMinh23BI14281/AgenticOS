"""
LCM Grep Tool — Search memory efficiently.

Ported from lcm-grep-tool.ts
"""

from __future__ import annotations

import json
from typing import Any

from ..store import ConversationStore, SummaryStore

def run_lcm_grep(
    query: str,
    conversations: ConversationStore,
    summaries: SummaryStore,
    session_id: str | None = None,
    limit: int = 15,
) -> str:
    """Execute FTS5/LIKE search across messages and summaries."""
    
    conv_id = None
    if session_id:
        conv = conversations.get_conversation_by_session_id(session_id)
        if conv:
            conv_id = conv.conversation_id

    # 1. Search Messages
    msg_hits = conversations.search_messages(
        query=query, 
        mode="full_text", 
        conversation_id=conv_id, 
        limit=limit
    )

    # 2. Search Summaries
    sum_hits = summaries.search_summaries(
        query=query, 
        mode="full_text", 
        conversation_id=conv_id, 
        limit=limit
    )

    if not msg_hits and not sum_hits:
        return f"No results found for query: '{query}'"

    res = [f"Search results for '{query}':\n"]

    if sum_hits:
        res.append(f"--- Summaries ({len(sum_hits)}) ---")
        for hit in sum_hits:
            res.append(
                f"[Summary {hit['summary_id']} | Depth {hit.get('depth', '?')}]\n"
                f"{hit['snippet']}\n"
            )

    if msg_hits:
        res.append(f"--- Messages ({len(msg_hits)}) ---")
        for hit in msg_hits:
            res.append(
                f"[Message {hit['message_id']} | {hit['role']}]\n"
                f"{hit['snippet']}\n"
            )

    res.append("Use lcm_describe on an ID for full metadata, or lcm_expand to read its contents.")
    return "\n".join(res)
