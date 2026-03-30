"""
LCM Processor — Logic for message part parsing and token estimation.
"""

from __future__ import annotations

import logging
from typing import Any

from .types import MessagePartType, TokenizerProtocol
from .store.summary.crud import sanitize_summary_content

logger = logging.getLogger("lcm.processor")


class LcmProcessor:
    """
    Handles internal message processing:
    - Splitting content into typed parts.
    - Token counting per part.
    - Raw content merging for indexing.
    """

    def __init__(self, tokenizer: TokenizerProtocol, max_tokens: int = 64000) -> None:
        self.tokenizer = tokenizer
        self.max_tokens = max_tokens

    def process_message_parts(
        self, 
        session_id: str, 
        parts: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], int, str]:
        """
        Transform raw part dicts into structured records and count tokens.
        Includes a Hard Boundary check to prevent OOM on massive messages.
        """
        expanded_parts = []
        total_tokens = 0
        content_pieces = []

        for ordinal, part in enumerate(parts):
            ptype_str = part.get("type") or part.get("part_type") or "text"
            
            # [Safety Fallback] Handle cases where role string is accidentally passed as part_type
            try:
                ptype = MessagePartType(ptype_str)
            except ValueError:
                logger.warning("[lcm.processor] invalid part_type '%s', falling back to TEXT", ptype_str)
                ptype = MessagePartType.TEXT

            text = part.get("content") or part.get("text_content") or ""
            
            # --- CHAOS GUARD: Hard Token Ceiling ---
            # Even with char truncation, some tokenizers are O(N^2). 
            # We enforce a strict char limit based on max_tokens to prevent OOM.
            char_limit = self.max_tokens * 10
            if len(text) > char_limit:
                orig_len = len(text)
                text = text[:char_limit] + "\n[... Content truncated due to extreme length ...]"
                logger.error(
                    "[lcm] CRITICAL: hard boundary truncation triggered for session %s "
                    "(original_chars=%d, limited_chars=%d). This input was too large for safety.",
                    session_id, orig_len, len(text)
                )
            # ---------------------------------------
            
            # Normalization logic for tools/outputs
            if ptype == MessagePartType.TOOL:
                text = f"ToolCall({part.get('toolName') or part.get('tool_name')}): {part.get('toolInput') or part.get('tool_input')}"
            elif ptype == MessagePartType.TOOL_OUTPUT:
                ptype = MessagePartType.TEXT
                text = f"ToolOutput: {part.get('text_content') or part.get('tool_output') or ''}"

            # [Clean CoT/Leaks] - Apply before token counting
            text = sanitize_summary_content(text)

            # Tokenize ONLY after truncation and cleanup
            tokens = self.tokenizer.encode(text) if text else []
            
            # Final Safety: If tokens still exceed max_tokens (e.g. 1 char = 1 token), 
            # we do a second pass via tokenizer.
            if len(tokens) > self.max_tokens:
                logger.error(
                    "[lcm] CRITICAL: token count (%d) still exceeds limit (%d) after char truncation. "
                    "Performing second-pass hard truncation.",
                    len(tokens), self.max_tokens
                )
                tokens = tokens[:self.max_tokens]
                text = self.tokenizer.decode(tokens) + "\n[... Token limit reached ...]"

            total_tokens += len(tokens)
            content_pieces.append(text)

            expanded_parts.append({
                "session_id": session_id,
                "part_type": ptype.value,
                "ordinal": ordinal,
                "text_content": text,
                "tool_call_id": part.get("tool_call_id"),
                "tool_name": part.get("tool_name"),
                "tool_input": part.get("tool_input"),
                "tool_output": part.get("tool_output"),
                "metadata": part.get("metadata"),
            })

        merged_content = "\n\n".join(piece for piece in content_pieces if piece)
        
        return expanded_parts, total_tokens, merged_content
