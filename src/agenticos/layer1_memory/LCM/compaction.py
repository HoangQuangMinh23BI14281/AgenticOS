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

from .types import CompactionDecision, CompactionResult, SummaryKind
from .config import LcmConfig
from .store import ConversationStore, SummaryStore
from .summarize import LcmSummarizer

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
    ) -> None:
        self._conv_store = conversation_store
        self._summary_store = summary_store
        self._summarizer = summarizer
        self._config = config

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
                if r.action_taken:
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
                if r.action_taken:
                    results.append(r)
                    state.current_tokens = r.tokens_after
                else:
                    made_progress = False

        # Level 3: Deterministic tail truncation (not LLM-summary based)
        # In a real node impl, this would blindly delete context items from the top
        # until budget is satisfied. For now, log warning if we couldn't converge.
        if state.current_tokens > state.target_budget:
            logger.error(
                "[lcm] convergence failure after %d rounds! "
                "Context (%d) exceeds soft limit (%d). "
                "Deterministic truncation required.",
                state.rounds, state.current_tokens, state.target_budget
            )
        else:
            logger.info(
                "[lcm] compaction clear after %d rounds. Context %d <= %d threshold.",
                state.rounds, state.current_tokens, state.target_budget
            )

        # Clear LRU cache post-compaction
        if results:
            self._summary_store.invalidate_cache()

        return results

    # ── Stubbed internal passes ────────────────────────────────────────────────

    async def _leaf_pass(self, conv_id: int, aggressive: bool) -> str | None:
        """Find raw messages to condense into a leaf node. Impl stubbed."""
        # TODO: Full LLM loop, replace context items, insert new summary.
        return None

    async def _condensed_pass(self, conv_id: int, aggressive: bool) -> str | None:
        """Find multiple active summaries to merge. Impl stubbed."""
        # TODO: Full LLM loop, replace context items, insert new summary.
        return None
