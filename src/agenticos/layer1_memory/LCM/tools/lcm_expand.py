"""
LCM Expand Tool — Drill down into Archive Stubs or raw messages.

Ported from lcm-expand-query-tool.ts
"""

from __future__ import annotations

import logging
from ..store import ConversationStore, SummaryStore
from ..summarize import LcmSummarizer

logger = logging.getLogger("lcm.expand")


async def run_lcm_expand(
    item_id: str,
    query: str | None,
    conversations: ConversationStore,
    summaries: SummaryStore,
    summarizer: LcmSummarizer | None = None,
) -> str:
    """
    Expand a Summary (Archive Stub), Message, or Large File to read its contents.
    If 'query' is provided and the node is a Summary, it will recursively expand
    its children and extract an answer to the query using the summarizer.
    """
    # 1. Expand Large File
    if item_id.startswith("file_"):
        # Real implementation would read from actual storage (e.g. S3/local disk)
        f = summaries.get_large_file(item_id)
        if not f:
            return f"Error: Large File {item_id} not found."
            
        return (
            f"FILE CONTENTS ({f.file_name}):\n"
            f"[MOCK: Actual file storage not wired in Python port yet.]\n"
            f"Expected URI: {f.storage_uri}"
        )

    # 2. Expand Summary (Archive Stub)
    s = summaries.get_summary(item_id)
    if s:
        # If no query is provided, we just dump what this summary inherently says.
        if not query:
            children = summaries.get_summary_children(item_id)
            refs = "\n".join(f"- {c.summary_id}" for c in children) if children else "None"
            return (
                f"--- SUMMARY CONTENT ({item_id}) ---\n{s.content}\n\n"
                f"--- DESCENDANTS you can drill into next ---\n{refs}"
            )
            
        # Recursive queried expansion
        if not summarizer:
            return "Error: Cannot perform recursive query expansion without a summarizer instance."
            
        # Fetch the entire subtree
        nodes = summaries.get_subtree(item_id)
        if not nodes:
            return f"No sub-nodes found under {item_id}"
            
        # Build text to query against
        block = [f"Subtree expansion for {item_id}:"]
        for node in nodes:
            # depth_from_root comes from the CTE
            prefix = "  " * getattr(node, "depth_from_root", 0)
            block.append(f"{prefix}[{node.summary_id}]: {node.content}")
            
        full_text = "\n".join(block)
        
        # In a real prompt, we might want to ensure we don't blow token budget
        # but for Phase 1 port we just pass to LLM directly.
        extraction_prompt = (
            f"A user specifically asked about this within a historical log:\n\n"
            f"Query: {query}\n\n"
            f"Please extract the specific details answering the user's query from the following historical logs. "
            f"If the answer is not present, just explicitly state that.\n\n"
            f"<historical_logs>\n{full_text}\n</historical_logs>"
        )
        
        logger.info("[lcm_expand] querying LLM for details inside %s", item_id)
        answer = await summarizer.summarize(
            extraction_prompt, 
            aggressive=True,
            custom_instructions="Extract facts only. Do not invent. Stay strictly within the bounds of the provided logs."
        )
        
        return f"--- Q: {query} ---\n{answer}"

    # 3. Expand raw message
    if item_id.isdigit():
        msg_id = int(item_id)
        m = conversations.get_message_by_id(msg_id)
        if m:
            parts = conversations.get_message_parts(msg_id)
            
            res = [f"--- RAW MESSAGE ({msg_id}) | {m.role.value.capitalize()} ---"]
            for part in parts:
                res.append(f"[{part.part_type.value}]:")
                if part.text_content:
                    res.append(part.text_content)
                if part.tool_name:
                    res.append(f"Tool {part.tool_name}( {part.tool_input} ) -> {part.tool_output}")
                    
            return "\n".join(res)

    return f"Error: Cannot expand. Item '{item_id}' not found."
