"""
LCM Describe Tool — Inspect DAG architecture and item status (Bindle vs Archive Stub).

Ported from lcm-describe-tool.ts and heavily enhanced to convey Active vs Archive state.
"""

from __future__ import annotations

import json
from ..store import ConversationStore, SummaryStore
from ..types import ContextItemType

def run_lcm_describe(
    item_id: str,
    conversations: ConversationStore,
    summaries: SummaryStore,
) -> str:
    """
    Returns exact token metrics (descTok, srcTok) and a child manifest
    so the agent can plan its retrieval strategy without spending tokens on expansion.
    """
    s = summaries.get_summary(item_id)
    if s:
        # Build Child Manifest
        children = summaries.get_summary_children(item_id)
        child_manifest = [
            {
                "id": c.summary_id,
                "depth": c.depth,
                "tokens": c.token_count,
                "type": c.kind.value
            }
            for c in children
        ]

        # Trả về JSON chuẩn chỉ để LLM dễ parse thành Object
        response = {
            "id": s.summary_id,
            "depth": s.depth,
            "descTok": s.descendant_token_count,
            "srcTok": s.source_message_token_count,
            "content_preview": s.content[:150] + "...",
            "child_manifest": child_manifest
        }
        return json.dumps(response, indent=2)

    # Fallback cho Raw Message
    if item_id.isdigit():
        m = conversations.get_message_by_id(int(item_id))
        if m:
            return json.dumps({
                "id": m.message_id,
                "depth": "RAW",
                "tokens": m.token_count,
                "role": m.role.value,
                "child_manifest": []
            }, indent=2)

    return json.dumps({"error": f"Node {item_id} not found."})
