"""
Compaction Coordinator — Orchestrates incremental context compression.
Part of the LCM compaction package.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from ..config import LcmConfig
from ..store import ConversationStore, SummaryStore
from ..summarize import LcmSummarizer
from ..types import (
    CompactionDecision,
    CompactionResult,
    TokenizerProtocol,
)
from .discovery import (
    find_message_chunk_for_leaf,
    find_summary_chunk_for_condensation,
)
from .passes import (
    execute_condensed_pass,
    execute_emergency_truncation,
    execute_leaf_pass,
)

logger = logging.getLogger("lcm.compaction")


@dataclass
class _CompactionState:
    """Internal state for an ongoing compaction session."""
    conversation_id: int
    current_tokens: int
    target_budget: int
    rounds: int = 0
    max_rounds: int = 10


class CompactionEngine:
    """
    Engine driving incremental context compression.
    Coordination-only; business logic is in discovery.py and passes.py.
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
        """Evaluate if compaction is needed."""
        threshold = int(context_limit * self._config.context_threshold)
        current = self._summary_store.get_context_token_count(conversation_id)
        should_compact = current > threshold

        return CompactionDecision(
            should_compact=should_compact,
            reason="threshold" if should_compact else "none",
            current_tokens=current,
            threshold=threshold,
        )

    async def compact(self, conversation_id: int, aggressive: bool = False) -> CompactionResult:
        """Run a single compaction sweep (Leaf or Condensed)."""
        initial_tokens = self._summary_store.get_context_token_count(conversation_id)
        
        # 1. Leaf Pass
        leaf_id = await execute_leaf_pass(
            conversation_id, self._conv_store, self._summary_store,
            self._summarizer, self._tokenizer, aggressive
        )
        if leaf_id:
            return self._build_result(initial_tokens, conversation_id, leaf_id, False, aggressive)

        # 2. Condensed Pass
        condensed_id = await execute_condensed_pass(
            conversation_id, self._summary_store, self._summarizer,
            self._tokenizer, aggressive
        )
        if condensed_id:
            return self._build_result(initial_tokens, conversation_id, condensed_id, True, aggressive)

        return CompactionResult(False, initial_tokens, initial_tokens)

    def _build_result(self, initial: int, conv_id: int, summary_id: str, condensed: bool, aggressive: bool) -> CompactionResult:
        final = self._summary_store.get_context_token_count(conv_id)
        return CompactionResult(
            action_taken=True,
            tokens_before=initial,
            tokens_after=final,
            created_summary_id=summary_id,
            condensed=condensed,
            level="aggressive" if aggressive else "normal"
        )

    async def compact_until_under(self, conversation_id: int, context_limit: int) -> list[CompactionResult]:
        """Iterative convergence loop with 3-level escalation."""
        if self._config.autocompact_disabled:
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

        # Escalation Levels
        for aggressive in [False, True]:
            last_token_count = state.current_tokens
            no_progress_rounds = 0
            
            while state.current_tokens > state.target_budget and state.rounds < state.max_rounds:
                state.rounds += 1
                r = await self.compact(conversation_id, aggressive=aggressive)
                
                # [CHAOS GUARD] Yield control to other tasks to prevent SQLITE_BUSY-starvation
                await asyncio.sleep(0.01)

                if r.action_taken:
                    # [CHAOS GUARD] Bloat Loop Prevention: Stop if no real progress
                    if r.tokens_after >= last_token_count:
                        no_progress_rounds += 1
                        if no_progress_rounds >= 2:
                            logger.warning("[lcm] no compaction progress in 2 rounds, stopping early.")
                            break
                    else:
                        no_progress_rounds = 0
                        
                    results.append(r)
                    state.current_tokens = r.tokens_after
                    last_token_count = r.tokens_after
                else:
                    break

        if results:
            self._summary_store.invalidate_cache()

        return results
