"""
Compaction Engine — Incremental memory compaction logic.

Performs progressive leaf and condensed sweeps:
  1. Evaluate: Checks if token count exceeds threshold
  2. Compact: Leaf pass (un-compacted tails) + Condensed pass (deep historical DAG)
  3. CompactUntilUnder: Iterative process mapping to 3-level escalation

Ported from compaction.ts with Context Rot prevention logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import uuid4

from .types import CompactionDecision, CompactionResult, SummaryKind, ContextItemType, MessageRole
from .config import LcmConfig
from .store import ConversationStore, SummaryStore
from .summarize import LcmSummarizer, estimate_tokens_fallback

logger = logging.getLogger("lcm.compaction")


@dataclass
class _CompactionState:
    """Working state for an ongoing compaction pass."""
    conversation_id: int
    current_tokens: int
    target_budget: int
    rounds: int = 0
    max_rounds: int = 10


class CompactionEngine:
    """
    Engine driving incremental context compression.

    Implements:
      - 3-level escalation (normal leaf -> aggressive leaf -> deterministic fallback)
      - Depth-aware condensed sweeps (merging D0->D1, or D1->D2)
    """

    def __init__(
        self,
        conversation_store: ConversationStore,
        summary_store: SummaryStore,
        summarizer: LcmSummarizer,
        config: LcmConfig,
        tokenizer: TokenizerProtocol | None = None,
    ) -> None:
        self._conv_store = conversation_store
        self._summary_store = summary_store
        self._summarizer = summarizer
        self._config = config
        self._tokenizer = tokenizer

    def evaluate(self, conversation_id: int, context_limit: int) -> CompactionDecision:
        """
        Evaluate if compaction is needed based on token thresholds.

        Addresses 'Context Rot' by triggering when above the soft limit
        (config.context_threshold, e.g. 55-60%), BEFORE hitting the hard limit.
        """
        # Threshold calculation
        threshold = int(context_limit * self._config.context_threshold)

        # Count tokens in context (messages + active summaries)
        current = self._summary_store.get_context_token_count(conversation_id)

        should_compact = current > threshold

        if should_compact:
            reason = "threshold"
            logger.debug(
                "[lcm] evaluate: %d > %d threshold (limit %d). Need compaction.",
                current, threshold, context_limit
            )
        else:
            reason = "none"
            logger.debug(
                "[lcm] evaluate: %d <= %d threshold. OK.",
                current, threshold
            )

        return CompactionDecision(
            should_compact=should_compact,
            reason=reason,
            current_tokens=current,
            threshold=threshold,
        )

    async def compact(
        self,
        conversation_id: int,
        aggressive: bool = False,
    ) -> CompactionResult:
        """
        Run a single compaction sweep (Leaf and/or Condensed).

        1. Looks for uncompacted fresh messages to turn into Leaf summaries.
        2. If none, looks for active summaries to merge into Condensed summaries.
        """
        initial_tokens = self._summary_store.get_context_token_count(conversation_id)
        logger.debug("[lcm] compact: start | tokens=%d | aggressive=%s", initial_tokens, aggressive)

        # 1. Leaf Pass (grouping raw messages)
        leaf_result = await self._leaf_pass(conversation_id, aggressive)
        if leaf_result:
            final_tokens = self._summary_store.get_context_token_count(conversation_id)
            return CompactionResult(
                action_taken=True,
                tokens_before=initial_tokens,
                tokens_after=final_tokens,
                created_summary_id=leaf_result,
                condensed=False,
                level="aggressive" if aggressive else "normal"
            )

        # 2. Condensed Pass (merging summaries)
        condensed_result = await self._condensed_pass(conversation_id, aggressive)
        if condensed_result:
            final_tokens = self._summary_store.get_context_token_count(conversation_id)
            return CompactionResult(
                action_taken=True,
                tokens_before=initial_tokens,
                tokens_after=final_tokens,
                created_summary_id=condensed_result,
                condensed=True,
                level="aggressive" if aggressive else "normal"
            )

        # No action taken
        return CompactionResult(
            action_taken=False,
            tokens_before=initial_tokens,
            tokens_after=initial_tokens,
            created_summary_id=None,
            condensed=False,
            level=None
        )

    async def compact_until_under(
        self,
        conversation_id: int,
        context_limit: int,
    ) -> list[CompactionResult]:
        """
        Iterative convergence loop with 3-level escalation.
        Runs until tokens are below threshold or exhausted max rounds.
        """
        if self._config.autocompact_disabled:
            logger.info("[lcm] compaction disabled by configuration")
            return []

        dec = self.evaluate(conversation_id, context_limit)
        if not dec.should_compact:
            return []

        results: list[CompactionResult] = []
        state = _CompactionState(
            conversation_id=conversation_id,
            current_tokens=dec.current_tokens,
            target_budget=dec.threshold,
            max_rounds=self._config.max_rounds,
        )

        # Level 1: Normal sweeps
        if state.current_tokens > state.target_budget and state.rounds < state.max_rounds:
            made_progress = True
            while made_progress and state.current_tokens > state.target_budget and state.rounds < state.max_rounds:
                state.rounds += 1
                r = await self.compact(conversation_id, aggressive=False)
                if r.action_taken and r.tokens_after < state.current_tokens:
                    results.append(r)
                    state.current_tokens = r.tokens_after
                else:
                    made_progress = False

        # Level 2: Aggressive sweeps
        if state.current_tokens > state.target_budget and state.rounds < state.max_rounds:
            logger.warning("[lcm] escalation: level 2 (aggressive)")
            made_progress = True
            while made_progress and state.current_tokens > state.target_budget and state.rounds < state.max_rounds:
                state.rounds += 1
                r = await self.compact(conversation_id, aggressive=True)
                if r.action_taken and r.tokens_after < state.current_tokens:
                    results.append(r)
                    state.current_tokens = r.tokens_after
                else:
                    made_progress = False

        # Level 3: Deterministic truncation (Level 3 - Guaranteed Convergence)
        if state.current_tokens > state.target_budget:
            logger.warning("[lcm] escalation: level 3 (deterministic truncate)")
            await self._deterministic_truncate(conversation_id, state.target_budget)
            state.current_tokens = self._summary_store.get_context_token_count(conversation_id)

        if state.current_tokens <= state.target_budget:
            logger.info(
                "[lcm] compaction clear after %d rounds. Context %d <= %d threshold.",
                state.rounds, state.current_tokens, state.target_budget
            )
        else:
             logger.error("[lcm] compaction failed to reach target after all escalations")

        # Clear LRU cache post-compaction
        if results:
            self._summary_store.invalidate_cache()

        return results

    async def _leaf_pass(self, conv_id: int, aggressive: bool) -> str | None:
        """Find raw messages to condense into a leaf node (Depth 0)."""
        items = self._summary_store.get_context_items(conv_id)
        
        chunk = []
        start_idx = -1
        for i, item in enumerate(items):
            if item.item_type == ContextItemType.MESSAGE:
                if start_idx == -1: start_idx = i
                chunk.append(item)
            else:
                if chunk: break
                
        # =========================================================
        # FIX 1-2: MASSIVE COMPACTION & QA PROTECTION (Paper Logic)
        # =========================================================
        is_at_tail = (start_idx + len(chunk) == len(items))
        
        if is_at_tail:
            # Giữ lại đúng 6 tin nhắn (tương đương 3 cặp Hỏi-Đáp) làm Fresh Tail
            tail_keep = 6
            
            # Đảm bảo không nén dở dang cặp (Hỏi - Đáp)
            if len(chunk) > tail_keep:
                last_msg_in_chunk = self._conv_store.get_message_by_id(chunk[-1].message_id)
                if last_msg_in_chunk and last_msg_in_chunk.role == MessageRole.USER:
                    tail_keep += 1 # Đẩy USER sang vùng Fresh
            
            if len(chunk) > tail_keep:
                chunk = chunk[:-tail_keep]
            else:
                return None

        # Bỏ giới hạn GROUP_SIZE để gom toàn bộ "đống rác" phía trước
        # =========================================================
        
        if len(chunk) < 2:
            return None
            
        text_blocks = []
        message_ids = []
        for item in chunk:
            msg = self._conv_store.get_message_by_id(item.message_id)
            if msg:
                text_blocks.append(f"{msg.role.value.capitalize()}: {msg.content}")
                message_ids.append(msg.message_id)
        
        if not text_blocks:
            return None

        full_text = "\n\n".join(text_blocks)
        summary_id = f"sum_{uuid4().hex[:8]}"
        summary_text = await self._summarizer.summarize(full_text, aggressive=aggressive)
        
        if not summary_text:
            logger.info("[lcm] leaf pass: summary rejected by overflow guard. skipping replacement.")
            return None
        
        # Đếm token chuẩn
        if self._tokenizer:
            token_count = len(self._tokenizer.encode(summary_text))
        else:
            token_count = estimate_tokens_fallback(summary_text)
            
        total_source_tokens = sum(m.token_count for m in [self._conv_store.get_message_by_id(mid) for mid in message_ids] if m)
        
        self._summary_store.insert_summary(
            summary_id=summary_id,
            conversation_id=conv_id,
            kind=SummaryKind.LEAF,
            content=summary_text,
            token_count=token_count,
            depth=0,
            source_message_token_count=total_source_tokens
        )
        
        # Stricter Compression Guard
        if token_count >= total_source_tokens * 0.9 and not aggressive:
            logger.warning(
                "[lcm] poor compression in leaf pass (%d -> %d). "
                "Retrying with aggressive mode in next round.",
                total_source_tokens, token_count
            )
        
        self._summary_store.link_to_messages(summary_id, message_ids)
        
        self._summary_store.replace_context_range_with_summary(
            conversation_id=conv_id,
            start_ordinal=items[start_idx].ordinal,
            end_ordinal=items[start_idx + len(chunk) - 1].ordinal,
            summary_id=summary_id
        )
        
        return summary_id

    async def _condensed_pass(self, conv_id: int, aggressive: bool) -> str | None:
        """Find multiple active summaries of the SAME DEPTH to merge."""
        items = self._summary_store.get_context_items(conv_id)
        
        chunk = []
        start_idx = -1
        target_depth = -1
        
        # Thuật toán tìm chuỗi các Summary liền kề CÙNG ĐỘ SÂU
        for i, item in enumerate(items):
            if item.item_type == ContextItemType.SUMMARY:
                s = self._summary_store.get_summary(item.summary_id)
                if not s: continue
                
                if start_idx == -1:
                    start_idx = i
                    target_depth = s.depth
                    chunk.append((item, s))
                elif s.depth == target_depth:
                    # Nếu cùng độ sâu với chunk hiện tại thì gộp tiếp
                    chunk.append((item, s))
                else:
                    # Đụng độ sâu khác. Nếu chunk trước đó đã đủ lớn thì dừng lại để nén
                    if len(chunk) >= (3 if aggressive else 4):
                        break
                    # Nếu chưa đủ lớn, đập đi xây lại chunk mới từ item này
                    chunk = [(item, s)]
                    start_idx = i
                    target_depth = s.depth
            else:
                # Đụng phải tin nhắn thô (Message), ngắt chuỗi
                if len(chunk) >= (3 if aggressive else 4):
                    break
                chunk = []
                start_idx = -1
                target_depth = -1
        
        GROUP_SIZE = 4
        if len(chunk) > GROUP_SIZE:
            chunk = chunk[:GROUP_SIZE]

        if len(chunk) < (3 if aggressive else GROUP_SIZE):
            return None
            
        summary_ids = []
        text_blocks = []
        total_source_tokens = 0
        
        # Lúc này chắc chắn mọi s trong chunk đều có cùng target_depth
        for item, s in chunk:
            text_blocks.append(f"[Depth {s.depth} Summary]:\n{s.content}")
            summary_ids.append(s.summary_id)
            total_source_tokens += s.source_message_token_count
        
        full_text = "\n\n".join(text_blocks)
        summary_id = f"sum_{uuid4().hex[:8]}"
        
        # Depth mới sẽ nâng lên 1 cấp so với các node con
        new_depth = target_depth + 1
        
        summary_text = await self._summarizer.summarize(
            full_text, aggressive=aggressive, is_condensed=True, depth=new_depth
        )
        
        if not summary_text:
            logger.info("[lcm] condensed pass: summary rejected by overflow guard. skipping replacement.")
            return None
        
        if self._tokenizer:
            token_count = len(self._tokenizer.encode(summary_text))
        else:
            token_count = estimate_tokens_fallback(summary_text)
            
        self._summary_store.insert_summary(
            summary_id=summary_id,
            conversation_id=conv_id,
            kind=SummaryKind.CONDENSED,
            content=summary_text,
            token_count=token_count,
            depth=new_depth,
            source_message_token_count=total_source_tokens
        )
        
        # Stricter Compression Guard (Condensed)
        if token_count >= total_source_tokens * 0.9 and not aggressive:
             logger.warning(
                "[lcm] poor compression in condensed pass (%d -> %d).",
                total_source_tokens, token_count
            )
        
        self._summary_store.link_to_parents(summary_id, summary_ids)
        
        self._summary_store.replace_context_range_with_summary(
            conversation_id=conv_id,
            start_ordinal=items[start_idx].ordinal,
            end_ordinal=items[start_idx + len(chunk) - 1].ordinal,
            summary_id=summary_id
        )
        
        return summary_id

    async def _deterministic_truncate(self, conv_id: int, target_budget: int) -> None:
        """
        Level 3 Fallback: Blindly remove the oldest context items until under budget.
        Does not delete messages from DB, only from active Context Window.
        """
        items = self._summary_store.get_context_items(conv_id)
        current = self._summary_store.get_context_token_count(conv_id)
        
        removed_count = 0
        for item in items:
            if current <= target_budget:
                break
            
            # Xóa item cũ nhất (truy cập pool nội bộ của summary_store)
            self._summary_store._pool.execute_write(lambda conn: conn.execute(
                "DELETE FROM context_items WHERE conversation_id = ? AND ordinal = ?",
                (conv_id, item.ordinal)
            ))
            
            # Cập nhật dự đoán token
            current = self._summary_store.get_context_token_count(conv_id)
            removed_count += 1
            
        logger.info(f"[lcm] level 3: removed {removed_count} items to meet hard budget")
