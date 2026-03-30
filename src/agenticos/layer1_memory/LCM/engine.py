"""
LCM Engine — Core message ingestion and context assembly (Platinum Orchestrator).

Main entry point for Lossless Context Management. Orchestrates Processor, 
Assembler, Compaction, and Storage layers.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from typing import Any

from .config import LcmConfig, LcmDependencies
from .types import (
    ContextItemType,
    MessagePartType,
    MessageRecord,
    MessageRole,
)
from .db import ConnectionPool, get_fts5_available, run_lcm_migrations
from .store import ConversationStore, SummaryStore
from .summarize import LcmSummarizer
from .tokenizer_util import LcmSimpleTokenizer
from .compaction.coordinator import CompactionEngine
from .file_dispatcher import FileDispatcher
from .processor import LcmProcessor
from .assembler import LcmAssembler
from .tools.lcm_grep import run_lcm_grep
from .tools.lcm_describe import run_lcm_describe
from .tools.lcm_expand import run_lcm_expand

logger = logging.getLogger("lcm.engine")


class LcmEngine:
    """
    Platinum Orchestrator for Lossless Context Management.
    """

    def __init__(self, config: LcmConfig, deps: LcmDependencies) -> None:
        self.config = config
        self.tokenizer = deps.tokenizer or LcmSimpleTokenizer()
        self.complete_fn = deps.complete

        # 1. Initialize DB & Stores
        self.pool = ConnectionPool(config.database_path)
        fts5 = get_fts5_available(self.pool.writer)
        run_lcm_migrations(self.pool.writer, fts5_available=fts5)

        self.conversations = ConversationStore(self.pool, fts5_available=fts5)
        self.summaries = SummaryStore(self.pool, fts5_available=fts5)

        # 2. Initialize Functional Modules
        self.processor = LcmProcessor(self.tokenizer, max_tokens=config.max_message_tokens)
        self.assembler = LcmAssembler()
        self.summarizer = LcmSummarizer(
            complete=self.complete_fn,
            provider=config.summary_provider,
            model=config.summary_model,
            tokenizer=self.tokenizer,
        )

        # 3. Initialize Strategy Engines
        self.compaction = CompactionEngine(
            self.conversations, self.summaries, self.summarizer, config, self.tokenizer
        )
        self.file_dispatcher = FileDispatcher(
            self.conversations, self.summaries, self.summarizer, config
        )

        # 4. Concurrency Protection (Bulletproof Locking)
        # Use WeakValueDictionary to prevent memory leaks from inactive sessions.
        self._locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()

    # ── Ingestion ─────────────────────────────────────────────────────────

    async def add_message(
        self,
        session_id: str,
        role: MessageRole,
        content: str,
        session_key: str | None = None,
        context_budget: int = 32000,
    ) -> MessageRecord:
        """
        [Standardized API] Ingest message, process tokens, and save to DB.
        """
        conv = self.conversations.get_or_create_conversation(session_id, session_key)

        async with self._get_lock(conv.conversation_id):
            # 1. Atomic Ingestion (ACID Transaction)
            def _atomic_ingest(conn: sqlite3.Connection) -> MessageRecord:
                # A. Process Message Parts (Now INSIDE transaction for maximum integrity)
                parts, total_tokens, merged_content = self.processor.process_message_parts(session_id, [
                    {"part_type": MessagePartType.TEXT.value, "text_content": content}
                ])

                # B. Fetch Sequence & Create Message
                last_seq = self.conversations.get_max_seq(conv.conversation_id)
                msg = self.conversations.create_message(
                    conv.conversation_id, last_seq + 1, role, merged_content, total_tokens, conn=conn
                )
                
                # C. Create Parts
                self.conversations.create_message_parts(msg.message_id, parts, conn=conn)
                
                # D. Link to Context Window
                self.summaries.append_context_message(conv.conversation_id, msg.message_id, conn=conn)
                return msg

            msg = self.pool.execute_in_transaction(_atomic_ingest)

            # 3. Trigger Compaction check (Threshold based)
            dec = self.compaction.evaluate(conv.conversation_id, context_budget)
            if dec.should_compact:
                 logger.info("[lcm] inline compaction triggered for %s", session_id)
                 await self.compaction.compact_until_under(conv.conversation_id, context_budget)
            
            return msg

    # ── Assembly ──────────────────────────────────────────────────────────

    def get_assembled_context(self, session_id: str, context_budget: int = 32000) -> str:
        """
        [Standardized API] Assemble hydrated prompt with Bindle cues.
        """
        conv = self.conversations.get_conversation_by_session_id(session_id)
        if not conv: return ""

        items = self.summaries.get_context_items(conv.conversation_id)
        if not items: return ""

        # Hydrate Records
        bindles = []
        messages = []
        for x in items:
            if x.item_type == ContextItemType.SUMMARY and x.summary_id:
                s = self.summaries.get_summary(x.summary_id)
                if s: bindles.append(s)
            elif x.item_type == ContextItemType.MESSAGE and x.message_id:
                m = self.conversations.get_message_by_id(x.message_id)
                if m: messages.append(m)

        # Build final prompt via Assembler
        full_ctx = self.assembler.assemble_context_history(bindles, messages)
        logger.debug("[lcm] assembled context for %s", session_id)
        return full_ctx

    # ── Maintenance ───────────────────────────────────────────────────────

    def _get_lock(self, conversation_id: int) -> asyncio.Lock:
        """
        Get or create an async lock for a specific conversation ID.
        Atomic operation via setdefault.
        """
        return self._locks.setdefault(conversation_id, asyncio.Lock())

    async def process_maintenance(self, session_id: str, context_budget: int = 32000) -> None:
        """
        [Standardized API] Run compaction and file externalization passes.
        """
        conv = self.conversations.get_conversation_by_session_id(session_id)
        if not conv: return

        async with self._get_lock(conv.conversation_id):
            await self.file_dispatcher.scan_and_externalize(conv.conversation_id)
            await self.compaction.compact_until_under(conv.conversation_id, context_budget)

    # ── Tools Router ──────────────────────────────────────────────────────

    def grep(self, query: str, session_id: str | None = None, limit: int = 15) -> str:
        return run_lcm_grep(query, self.conversations, self.summaries, session_id, limit)

    def describe(self, node_id: str) -> str:
        return run_lcm_describe(node_id, self.conversations, self.summaries)

    async def expand(self, item_id: str, query: str | None = None) -> str:
        return await run_lcm_expand(
            item_id=item_id,
            query=query or "",
            conversations=self.conversations, 
            summaries=self.summaries,
            summarizer_or_dispatcher=self.summarizer
        )

    def close(self) -> None:
        self.pool.close()
