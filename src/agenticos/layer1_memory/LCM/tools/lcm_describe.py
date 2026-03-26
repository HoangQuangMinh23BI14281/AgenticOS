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
    session_id: str | None = None,
) -> str:
    """
    Describe a specific Memory Item (Summary or Message).
    Crucially identifies if it's currently active in the Context Window (a Bindle)
    or if it's deep storage (an Archive Stub that must be expanded).
    """

    # Check if Large File:
    if item_id.startswith("file_"):
        f = summaries.get_large_file(item_id)
        if f:
            return (
                f"Large File Node: {item_id}\n"
                f"Storage URI: {f.storage_uri}\n"
                f"Size: {f.byte_size} bytes\n"
                f"Exploration Summary: {f.exploration_summary}\n\n"
                f"Call lcm_expand on {item_id} to retrieve full raw text."
            )

    conv_id = None
    if session_id:
        conv = conversations.get_conversation_by_session_id(session_id)
        if conv:
            conv_id = conv.conversation_id

    # Gather Active Bindle IDs
    active_summary_ids = set()
    if conv_id:
        items = summaries.get_context_items(conv_id)
        for i in items:
            if i.item_type == ContextItemType.SUMMARY and i.summary_id:
                active_summary_ids.add(i.summary_id)

    # 1. Try resolving as a Summary
    s = summaries.get_summary(item_id)
    if s:
        is_active = s.summary_id in active_summary_ids
        state_str = (
            "ACTIVE in current Context Window (Bindle)" 
            if is_active else 
            "ARCHIVED deep storage node (Archive Stub)"
        )

        parents = summaries.get_summary_parents(item_id)
        children = summaries.get_summary_children(item_id)

        res = [
            f"Summary Node: {item_id}",
            f"State: {state_str}",
            f"Depth: {s.depth} ({s.kind.value})",
            f"Tokens: {s.token_count}",
            f"Contains: {s.source_message_token_count} source tokens across {s.descendant_count} descendants",
        ]
        
        if s.file_ids:
            res.append(f"Associated Files: {', '.join(s.file_ids)}")

        if not is_active:
            res.append("\nNOTE: This node is NOT currently in your LLM context window.")
            res.append(f"To read its actual contents, you MUST run: lcm_expand(item_id=\"{item_id}\")")

        if parents:
            res.append(f"\nAncestors (Merged into this node): {', '.join(p.summary_id for p in parents)}")
        if children:
            res.append(f"Descendants (This node was merged into): {', '.join(c.summary_id for c in children)}")

        return "\n".join(res)

    # 2. Try resolving as a Message (must be pure int)
    if item_id.isdigit():
        msg_id = int(item_id)
        m = conversations.get_message_by_id(msg_id)
        if m:
            parts = conversations.get_message_parts(msg_id)
            tool_calls = [p for p in parts if p.tool_name]
            
            res = [
                f"Raw Message Node: {msg_id}",
                f"Role: {m.role.value}",
                f"Tokens: {m.token_count}",
                f"Date: {m.created_at.isoformat()}"
            ]
            if tool_calls:
                res.append(f"Tool Invocations: {', '.join(t.tool_name for t in tool_calls if t.tool_name)}")
                
            res.append(f"\nTo read actual message contents, run: lcm_expand(item_id=\"{item_id}\")")
            return "\n".join(res)

    return f"Error: No Summary, Message, or Large File found matching '{item_id}'"
