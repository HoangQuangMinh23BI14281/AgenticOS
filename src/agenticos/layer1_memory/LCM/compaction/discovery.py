"""
Compaction Discovery — Logic for finding candidates for Leaf and Condensed compaction.
Part of the LCM CompactionEngine decomposition.
"""

from __future__ import annotations

from ..types import ContextItemType, MessageRole
from ..store import ConversationStore, SummaryStore


def find_message_chunk_for_leaf(
    items: list, conversation_store: ConversationStore, tail_keep: int = 2
) -> tuple[list, int]:
    """Discovery logic for leaf candidates (Fresh messages)."""
    chunk = []
    start_idx = -1
    for i, item in enumerate(items):
        if item.item_type == ContextItemType.MESSAGE:
            if start_idx == -1:
                start_idx = i
            chunk.append(item)
        else:
            if chunk:
                break

    if not chunk:
        return [], -1

    # Fresh tail guard
    is_at_tail = start_idx + len(chunk) == len(items)
    if is_at_tail:
        if len(chunk) > tail_keep:
            last_msg = conversation_store.get_message_by_id(chunk[-1].message_id)
            # If last message is from user, keep one more to preserve immediate context
            effective_keep = tail_keep + 1 if (last_msg and last_msg.role == MessageRole.USER) else tail_keep
            chunk = chunk[:-effective_keep]
        else:
            return [], -1

    if len(chunk) < 2:
        return [], -1
    return chunk, start_idx


def find_summary_chunk_for_condensation(
    items: list, summary_store: SummaryStore, aggressive: bool = False
) -> tuple[list, int, int]:
    """Discovery logic for condensed candidates (History DAG)."""
    chunk = []
    start_idx = -1
    target_depth = -1

    min_size = 3 if aggressive else 4
    group_size = 4

    for i, item in enumerate(items):
        if item.item_type == ContextItemType.SUMMARY:
            s = summary_store.get_summary(item.summary_id)
            if not s:
                continue

            if start_idx == -1:
                start_idx = i
                target_depth = s.depth
                chunk.append((item, s))
            elif s.depth == target_depth:
                chunk.append((item, s))
                if len(chunk) >= group_size:
                    break
            else:
                if len(chunk) >= min_size:
                    break
                chunk = [(item, s)]
                start_idx = i
                target_depth = s.depth
        else:
            if len(chunk) >= min_size:
                break
            chunk = []
            start_idx = -1
            target_depth = -1

    if len(chunk) < min_size:
        return [], -1, -1
    return chunk, target_depth, start_idx
