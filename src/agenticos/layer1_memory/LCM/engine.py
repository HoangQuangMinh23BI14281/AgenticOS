"""
LCM Engine — Core message ingestion and context assembly.

Handles:
  - Token tracking per message part
  - Assembling context history with "Metadata Hooks" (Bindles vs Archive)
  - Delegating to CompactionEngine when budget exceeded
"""

from __future__ import annotations

import logging
from typing import Any

from .config import LcmConfig
from .types import (
    ContextItemRecord,
    ContextItemType,
    LcmDependencies,
    MessagePartRecord,
    MessagePartType,
    MessageRecord,
    MessageRole,
)
from .db import ConnectionPool, get_fts5_available, run_lcm_migrations
from .store import ConversationStore, SummaryStore
from .summarize import LcmSummarizer
from .compaction import CompactionEngine
from .file_dispatcher import FileDispatcher
from .tools.lcm_grep import run_lcm_grep
from .tools.lcm_describe import run_lcm_describe
from .tools.lcm_expand import run_lcm_expand

logger = logging.getLogger("lcm.engine")


class LcmEngine:
    """
    Main interface for Lossless Context Management.
    """

    def __init__(self, config: LcmConfig, deps: LcmDependencies) -> None:
        self.config = config
        self.tokenizer = deps.tokenizer
        self.complete_fn = deps.complete

        # Initialize DB pool
        self.pool = ConnectionPool(config.database_path)
        fts5 = get_fts5_available(self.pool.writer)

        # Run migrations safely on writer
        run_lcm_migrations(self.pool.writer, fts5_available=fts5)

        # Initialize Stores
        self.conversations = ConversationStore(self.pool, fts5_available=fts5)
        self.summaries = SummaryStore(self.pool, fts5_available=fts5)

        # Initialize Sub-engines
        self.summarizer = LcmSummarizer(
            complete=self.complete_fn,
            provider=config.summary_provider,
            model=config.summary_model,
        )
        self.compaction = CompactionEngine(
            self.conversations, self.summaries, self.summarizer, config, tokenizer=self.tokenizer
        )
        self.file_dispatcher = FileDispatcher(
            self.conversations, self.summaries, self.summarizer, config
        )

    # ── Ingestion ─────────────────────────────────────────────────────────

    def ingest_message(
        self,
        session_id: str,
        role: MessageRole,
        parts: list[dict[str, Any]],
        session_key: str | None = None,
    ) -> MessageRecord:
        """
        Ingest a new message, count tokens, save to DB, and append to context.
        """
        conv = self.conversations.get_or_create_conversation(
            session_id, session_key
        )

        # 1. Expand parts and count tokens
        expanded_parts = []
        total_tokens = 0
        content_pieces = []

        for idx, part in enumerate(parts):
            ptype = MessagePartType(part.get("type", "text"))
            text = part.get("text") or part.get("text_content") or ""
            
            # Tools might have formatting specific logic in a real impl;
            # we just count the JSON string representation
            if ptype == MessagePartType.TOOL:
                text = f"ToolCall({part.get('toolName')}): {part.get('toolInput')}"
            elif ptype == MessagePartType.TOOL_OUTPUT:
                ptype = MessagePartType.TEXT
                text = f"ToolOutput: {part.get('text_content', '')}"

            tokens = self.tokenizer.encode(text) if text else []
            total_tokens += len(tokens)
            content_pieces.append(text)

            expanded_parts.append({
                "session_id": session_id,
                "part_type": ptype.value,
                "ordinal": idx,
                "text_content": text,
                "tool_call_id": part.get("tool_call_id"),
                "tool_name": part.get("tool_name"),
                "tool_input": part.get("tool_input"),
                "tool_output": part.get("tool_output"),
                "metadata": part.get("metadata"),
            })

        # 2. Save Message
        seq = self.conversations.get_max_seq(conv.conversation_id) + 1
        merged_content = "\n\n".join(piece for piece in content_pieces if piece)

        msg = self.conversations.create_message(
            conversation_id=conv.conversation_id,
            seq=seq,
            role=role,
            content=merged_content,
            token_count=total_tokens,
        )

        # 3. Save Parts
        self.conversations.create_message_parts(msg.message_id, expanded_parts)

        # 4. Append to Context Window
        self.summaries.append_context_message(conv.conversation_id, msg.message_id)

        logger.debug(
            "[lcm] ingested message %d (role=%s, tokens=%d)",
            msg.message_id, role.value, total_tokens
        )
        return msg

    # ── Assembly (Context Hydration) ──────────────────────────────────────

    def assemble(self, session_id: str, context_budget: int = 32000) -> str:
        """
        Assemble the current context window for the model.
        Informs the model of available BINDLES (active summaries).
        """
        conv = self.conversations.get_conversation_by_session_id(session_id)
        if not conv:
            return ""

        items = self.summaries.get_context_items(conv.conversation_id)
        if not items:
            return ""

        # Separate items into Bindles (summaries) and Messages
        bindles = []
        messages = []

        for item in items:
            if item.item_type == ContextItemType.SUMMARY and item.summary_id:
                summary = self.summaries.get_summary(item.summary_id)
                if summary:
                    bindles.append(summary)
            elif item.item_type == ContextItemType.MESSAGE and item.message_id:
                msg = self.conversations.get_message_by_id(item.message_id)
                if msg:
                    messages.append(msg)

        # Construct Metadata Hooks block
        header = self._build_metadata_hooks(bindles)

        # Format actual history
        history_pieces = []
        
        if bindles:
            history_pieces.append("--- ACTIVE MEMORY SUMMARIES ---")
            history_pieces.append("The following are condensed summaries of older conversations. Use 'lcm_expand' if you need details.")
            for b in bindles:
                history_pieces.append(f"[SUMMARY D{b.depth} | ID: {b.summary_id}]: {b.content}")
            history_pieces.append("--- END SUMMARIES ---")

        if messages:
            for msg in messages:
                history_pieces.append(f"{msg.role.value.capitalize()}: {msg.content}")

        history_text = "\n\n".join(history_pieces)
        full_ctx = f"{header}\n\n<context_history>\n{history_text}\n</context_history>"
        
        logger.debug(f"[lcm] Assembled context for {session_id}:\n{full_ctx}")
        return full_ctx

    def _build_metadata_hooks(self, bindles: list) -> str:
        """
        Build the <lcm_metadata> XML block to inject at the top of the prompt.
        This fulfills the "Dolt Retrieval Traversal" requirement for active cue injection.
        """
        if not bindles:
             return (
                 "<lcm_metadata>\n"
                 "  <state>Empty Context</state>\n"
                 "</lcm_metadata>"
             )

        xml = ["<lcm_metadata>"]
        xml.append("  <active_summaries> <!-- 'Bindles' currently in RAM -->")
        for b in bindles:
            snippet = b.content[:60].replace("\n", " ") + "..."
            xml.append(
                f'    <summary id="{b.summary_id}" depth="{b.depth}" tokens="{b.token_count}" '
                f'topic="{snippet}">Depth {b.depth} condensed memory</summary>'
            )
        xml.append("  </active_summaries>")
        xml.append(
            "  <instruction>\n"
            "    You are an agent with LCM (Lossless Context Management) memory.\n"
            "    Active memories (summaries) are provided below in the <context_history> block.\n"
            "    CRITICAL: If you encounter a [SUMMARY] node and need deeper historical details,\n"
            "    you MUST call `lcm_expand(item_id=...)` to retrieve the original lossless data.\n"
            "    Do not hallucinate details that are not explicitly in the summary; use your tools.\n"
            "  </instruction>"
        )
        xml.append("</lcm_metadata>")

        return "\n".join(xml)

    # ── Background Thread Delegation ──────────────────────────────────────

    async def run_maintenance(self, session_id: str, context_budget: int = 32000) -> None:
        """
        Entry point for background thread (or async task) to run compaction.
        Call this AFTER ingest_message in a non-blocking way.
        """
        conv = self.conversations.get_conversation_by_session_id(session_id)
        if not conv:
            return

        # Large File externalization pass
        await self.file_dispatcher.scan_and_externalize(conv.conversation_id)

        # Compaction pass (3-level escalation)
        await self.compaction.compact_until_under(conv.conversation_id, context_budget)

    # ── Tools ─────────────────────────────────────────────────────────────

    def grep(self, query: str, session_id: str | None = None, limit: int = 15) -> str:
        return run_lcm_grep(query, self.conversations, self.summaries, session_id, limit)

    def describe(self, node_id: str) -> str:
        return run_lcm_describe(node_id, self.conversations, self.summaries)

    async def expand(self, item_id: str, query: str | None = None) -> str:
        summarizer = self.compaction._summarizer if hasattr(self, 'compaction') else None
        return await run_lcm_expand(
            item_id=item_id,
            query=query or "",
            conversations=self.conversations,
            summaries=self.summaries,
            summarizer_or_dispatcher=summarizer
        )

    def close(self) -> None:
        """Cleanup database connections."""
        self.pool.close()
