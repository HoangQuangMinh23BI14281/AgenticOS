"""
LCM Expand Tool — Drill down into Archive Stubs or raw messages.

Ported from lcm-expand-query-tool.ts
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from ..store import ConversationStore, SummaryStore
from ..summarize import LcmSummarizer

logger = logging.getLogger("lcm.expand")


@dataclass
class DelegationGrant:
    target_node_id: str
    query: str
    token_budget: int
    session_id: str | None

async def run_lcm_expand(
    item_id: str,
    query: str,
    conversations: ConversationStore,
    summaries: SummaryStore,
    summarizer_or_dispatcher, # Component Lớp 3/4 hoặc Summarizer fallback
    session_id: str | None = None,
    budget: int = 4000
) -> str:
    """
    Issues a delegation grant and spawns a sub-agent to navigate the DAG.
    """
    if not query:
        return "Error: lcm_expand_query requires a specific 'query' to delegate to the sub-agent."

    logger.info(f"Issuing Delegation Grant for node {item_id} with budget {budget}")
    
    grant = DelegationGrant(
        target_node_id=item_id,
        query=query,
        token_budget=budget,
        session_id=session_id
    )

    # -------------------------------------------------------------------------
    # SUB-AGENT SPAWNING LOGIC (Pseudo-code tùy thuộc vào engine Agent của bạn)
    # Thay vì tự nhồi text vào hàm summarize(), ta ủy quyền cho Sub-Agent.
    # Sub-agent sẽ nhận cái grant này, tự gọi lcm_describe và lcm_expand bên trong sandbox của nó.
    # -------------------------------------------------------------------------
    
    # FALLBACK: Ta tạm dùng RAG đệ quy
    logger.warning("Sub-agent dispatcher not fully implemented. Falling back to recursive RAG.")
    nodes = summaries.get_subtree(item_id)
    if not nodes:
        return f"No data found under {item_id}"
        
    block = [f"Subtree logs for {item_id}:"]
    for node in nodes:
        block.append(f"[{node.summary_id}]: {node.content}")
        
    full_text = "\n".join(block)
    
    # Fallback query
    # Try if it has summarize method (like LcmSummarizer)
    if hasattr(summarizer_or_dispatcher, "summarize"):
        answer = await summarizer_or_dispatcher.summarize(
            f"Query: {query}\nLogs:\n{full_text}", 
            aggressive=True
        )
    else:
        answer = "[Error: No summarizer provided for fallback retrieval]"
    return f"[Fallback Retrieval]:\n{answer}"