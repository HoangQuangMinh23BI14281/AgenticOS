"""
Compaction Passes — Core logic for Leaf, Condensed, and Emergency truncation passes.
Part of the LCM CompactionEngine decomposition.
"""

from __future__ import annotations

import logging
import sqlite3
from uuid import uuid4

from ..store import ConversationStore, SummaryStore
from ..summarize import LcmSummarizer, deterministic_fallback_summary
from ..types import ContextItemType, SummaryKind, TokenizerProtocol
from .discovery import (
    find_message_chunk_for_leaf,
    find_summary_chunk_for_condensation,
)

logger = logging.getLogger("lcm.compaction.passes")


async def execute_leaf_pass(
    conv_id: int,
    conversation_store: ConversationStore,
    summary_store: SummaryStore,
    summarizer: LcmSummarizer,
    tokenizer: TokenizerProtocol,
    aggressive: bool = False,
) -> str | None:
    """Find and condense raw messages into a leaf summary (Depth 0)."""
    items = summary_store.get_context_items(conv_id)
    chunk, start_idx = find_message_chunk_for_leaf(items, conversation_store)
    if not chunk:
        return None

    text_blocks = []
    message_ids = []
    total_source_tokens = 0

    for item in chunk:
        msg = conversation_store.get_message_by_id(item.message_id)
        if not msg:
            continue

        # PRE-SUMMARIZATION THRESHOLD (Chaos Guard)
        content = msg.content
        if msg.token_count > 6000:
            content = deterministic_fallback_summary(content, tokenizer, max_tokens=2000)
            logger.warning("[lcm] pre-summarization truncate message %d", msg.message_id)

        text_blocks.append(f"{msg.role.value.capitalize()}: {content}")
        message_ids.append(msg.message_id)
        total_source_tokens += msg.token_count

    if not text_blocks:
        return None

    full_text = "\n\n".join(text_blocks)
    summary_id = f"sum_{uuid4().hex[:8]}"
    summary_text = await summarizer.summarize(full_text, aggressive=aggressive)

    if not summary_text:
        return None

    # BLOAT GUARD
    summary_tokens = len(tokenizer.encode(summary_text))
    if summary_tokens >= total_source_tokens * 0.95:
        logger.warning("[lcm] token bloat in leaf. Discarding summary.")
        return None

    # ATOMIC Persist
    summary_store.insert_summary_and_replace_context_atomic(
        summary_id=summary_id,
        conversation_id=conv_id,
        content=summary_text,
        kind=SummaryKind.LEAF,
        depth=0,
        token_count=summary_tokens,
        source_message_token_count=total_source_tokens,
        model=summarizer.model,
        source_message_ids=message_ids,
        start_ordinal=items[start_idx].ordinal,
        end_ordinal=items[start_idx + len(chunk) - 1].ordinal,
    )
    return summary_id


async def execute_condensed_pass(
    conv_id: int,
    summary_store: SummaryStore,
    summarizer: LcmSummarizer,
    tokenizer: TokenizerProtocol,
    aggressive: bool = False,
) -> str | None:
    """Find and merge existing summaries into higher-depth summaries."""
    items = summary_store.get_context_items(conv_id)
    chunk, target_depth, start_idx = find_summary_chunk_for_condensation(
        items, summary_store, aggressive
    )
    if not chunk:
        return None

    summary_ids = []
    text_blocks = []
    total_source_tokens = 0
    new_depth = target_depth + 1

    # DEPTH GUARD
    if new_depth > 18:
        logger.error("[lcm] recursive depth reached limit. Escalating.")
        return None

    for _, s in chunk:
        text_blocks.append(f"[Depth {s.depth} Summary]:\n{s.content}")
        summary_ids.append(s.summary_id)
        total_source_tokens += s.source_message_token_count

    full_text = "\n\n".join(text_blocks)
    summary_id = f"sum_{uuid4().hex[:8]}"
    summary_text = await summarizer.summarize(
        full_text, aggressive=aggressive, is_condensed=True, depth=new_depth
    )

    if not summary_text:
        return None

    # BLOAT GUARD
    summary_tokens = len(tokenizer.encode(summary_text))
    if summary_tokens >= total_source_tokens * 0.9:
        logger.warning("[lcm] token bloat in condensation. Discarding.")
        return None

    # ATOMIC Persist
    summary_store.insert_summary_and_replace_context_atomic(
        summary_id=summary_id,
        conversation_id=conv_id,
        content=summary_text,
        kind=SummaryKind.CONDENSED,
        depth=new_depth,
        token_count=summary_tokens,
        source_message_token_count=total_source_tokens,
        model=summarizer.model,
        source_summary_ids=summary_ids,
        start_ordinal=items[start_idx].ordinal,
        end_ordinal=items[start_idx + len(chunk) - 1].ordinal,
    )
    return summary_id


async def execute_emergency_truncation(
    conv_id: int, target_budget: int, summary_store: SummaryStore
) -> None:
    """
    Level 3: Deterministic range delete to stay within budget.
    Ensures a safety buffer (15% below budget) to prevent oscillation.
    """
    items = summary_store.get_context_items(conv_id)
    current = summary_store.get_context_token_count(conv_id)
    
    # Target 15% below the limit to avoid immediate re-compaction
    safety_target = int(target_budget * 0.85)

    # [RESILIENCE FIX] Protect the last 2 messages (Fresh Tail) from emergency deletion.
    # This prevents the "vanishing chat" effect when a single new message exceeds the threshold.
    tail_reservation = 2
    candidates = items[:-tail_reservation] if len(items) > tail_reservation else []

    removed_count = 0
    to_remove_ordinals = []

    for item in candidates:
        if current <= safety_target:
            break
        to_remove_ordinals.append(item.ordinal)
        # Heuristic node size for iteration; actual delete is by ordinal
        # Use getattr safely as ContextItemRecord might not be a dataclass with token_count in some contexts
        t_count = getattr(item, "token_count", 300) 
        current -= t_count
        removed_count += 1

    if to_remove_ordinals:
        max_ord = max(to_remove_ordinals)

        def _truncate(conn: sqlite3.Connection) -> None:
            conn.execute(
                "DELETE FROM context_items WHERE conversation_id = ? AND ordinal <= ?",
                (conv_id, max_ord),
            )
            # Re-index remaining ordinals
            conn.execute(
                "UPDATE context_items SET ordinal = ordinal - ? WHERE conversation_id = ? AND ordinal > ?",
                (removed_count, conv_id, max_ord),
            )

        summary_store._pool.execute_in_transaction(_truncate)

    logger.info(f"[lcm] emergency truncation: removed {removed_count} nodes (target: {safety_target})")
