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
    """
    Searches every node in the DAG across all depths.
    Returns results with precise depth labels.
    """
    conv_id = None
    if session_id:
        conv = conversations.get_conversation_by_session_id(session_id)
        if conv:
            conv_id = conv.conversation_id

    sum_hits = summaries.search_summaries(query, "full_text", conv_id, limit)
    msg_hits = conversations.search_messages(query, "full_text", conv_id, limit)

    results = []
    
    # Dán nhãn chuẩn hóa cho Summaries
    for hit in sum_hits:
        depth_val = hit.get('depth', '?')
        results.append(f"[Depth {depth_val} | {hit['summary_id']}] {hit['snippet']}")

    # Dán nhãn chuẩn hóa cho Raw Messages
    for hit in msg_hits:
        results.append(f"[Depth RAW | msg_{hit['message_id']}] {hit['role'].upper()}: {hit['snippet']}")

    if not results:
        return f"No matches found for '{query}' in DAG."

    return "GREP RESULTS:\n" + "\n".join(results)
