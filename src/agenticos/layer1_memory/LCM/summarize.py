"""
LCM Summarization — LLM-based summarization with depth-aware prompts.

Supports 4 prompt templates: leaf (normal/aggressive), D1, D2, D3+.
Includes provider auth error detection and deterministic fallback.

Ported from summarize.ts.
"""

from __future__ import annotations

import asyncio
import logging
import math

logger = logging.getLogger("lcm.summarizer")
import re
from dataclasses import dataclass
from typing import Any
from .store.fulltext_fallback import contains_cjk
from .types import (
    SummaryKind,
    CompleteFn,
    CompletionResult,
    TokenizerProtocol,
    LcmProviderAuthError,
    SummarizerTimeoutError,
)
from .tokenizer_util import LcmSimpleTokenizer
from .prompts import (
    SYSTEM_PROMPT,
    build_leaf_prompt,
    build_condensed_prompt,
    build_expansion_prompt,
    resolve_target_tokens,
)

# ── Configuration Constants ───────────────────────────────────────────────────

SUMMARIZER_TIMEOUT_S = 180.0
AUTH_ERROR_PATTERN = re.compile(
    r"(invalid api key|authentication|unauthorized|permission denied|api_key_invalid)",
    re.I,
)



# ── Deterministic Fallback ────────────────────────────────────────────────────


def deterministic_fallback_summary(text: str, tokenizer: TokenizerProtocol, max_tokens: int = 512) -> str:
    """
    Deterministic truncation fallback using real Tokenizer estimation.
    """
    tokens = tokenizer.encode(text)
    if len(tokens) <= max_tokens:
        return text.strip()
    
    # Heuristic: Backwards estimation based on average token length if we can't slice tokens directly.
    # But since we use SimpleTokenizer, we'll take last max_tokens * 4 chars as a safe buffer.
    max_chars = max_tokens * 4
    return f"[Truncated — oldest context removed]\n\n{text[-max_chars:].strip()}"


# ── Response Normalization ────────────────────────────────────────────────────


def normalize_completion_summary(content: Any) -> str:
    """Extract text safely from provider completion response (supports LiteLLM/OpenAI/Anthropic)."""
    res = ""
    if isinstance(content, str):
        res = content.strip()
    
    # Check for .text attribute (CompletionResult or similar)
    elif hasattr(content, "text"):
        res = str(content.text).strip()
    
    # Handle list of blocks (CompletionContentBlock)
    elif isinstance(content, list) and len(content) > 0:
        parts = []
        for item in content:
            if hasattr(item, "text"): parts.append(str(item.text))
            elif isinstance(item, str): parts.append(item)
        res = "\n".join(parts).strip()
            
    elif isinstance(content, dict):
        for key in ["text", "content", "summary", "response"]:
            if key in content and isinstance(content[key], str):
                res = content[key].strip()
                break
    
    if not res:
        res = str(content)

    # Strip common prompt leaks and role prefixes (Aggressive Cleaning)
    res = re.sub(r"<(/?)(previous_context|conversation_segment|conversation_to_condense|raw_logs|lcm_metadata)>", "", res, flags=re.I)
    res = re.sub(r"\*\*?(User|Assistant|AI|System|Thinking|Instruction)\*\*?[:\s-]*", "", res, flags=re.I)
    res = re.sub(r"^(User|Assistant|AI|System|Thinking|Instruction)[:\s-]*", "", res, flags=re.I)
    res = re.sub(r"\n(User|Assistant|AI|System|Thinking|Instruction)[:\s-]*", "\n", res, flags=re.I)
    res = re.sub(r"\[/?(previous_context|summary|context_history|lcm_metadata)\]", "", res, flags=re.I)
    
    # Remove any thinking block that might have leaked into content (DeepSeek/R1 support)
    if "<think>" in res.lower():
        res = re.sub(r"<think>.*?</think>", "", res, flags=re.I | re.DOTALL)
    if "--- thinking ---" in res.lower():
        res = re.sub(r"--- thinking ---.*?--- end thinking ---", "", res, flags=re.I | re.DOTALL)
    
    # --- Anti-Chatty Preamble Stripper ---
    # Removes "Here is the summary:", "Sure! I can help...", etc.
    preambles = [
        r"^(here is|this is|certainly|sure|ok|okay)(.*?)(summary|rewrite|text)(.*?)(format|below)[\s:]*",
        r"^summary[\s:]*",
        r"^rewrite[\s:]*",
        r"^here's a brief summary[\s:]*",
        r"^factual summary[\s:]*",
    ]
    for p in preambles:
        res = re.sub(p, "", res, flags=re.I | re.MULTILINE).strip()

    return res.strip()


def detect_provider_auth_failure(error: Any) -> bool:
    """Check if an error looks like a provider auth failure."""
    err_str = str(error).lower()
    return bool(AUTH_ERROR_PATTERN.search(err_str))


# ── Summarizer ────────────────────────────────────────────────────────────────


class LcmSummarizer:
    """
    LLM-based summarizer with depth-aware prompts and fallback.

    Usage::

        summarizer = LcmSummarizer(complete_fn, provider="anthropic", model="claude-3-haiku")
        summary = await summarizer.summarize(text, aggressive=False)
    """

    def __init__(
        self,
        complete: CompleteFn,
        provider: str = "",
        model: str = "",
        timeout_s: float = SUMMARIZER_TIMEOUT_S,
        custom_instructions: str | None = None,
        tokenizer: TokenizerProtocol | None = None,
    ) -> None:
        self._complete = complete
        self._provider = provider
        self._model = model
        self._timeout_s = timeout_s or 180.0
        self._custom_instructions = custom_instructions
        self._tokenizer = tokenizer or LcmSimpleTokenizer()

    @property
    def model(self) -> str:
        """Returns the LLM model name used for summarization."""
        return self._model or "unknown"

    async def summarize(
        self,
        text: str,
        aggressive: bool = False,
        previous_summary: str | None = None,
        is_condensed: bool = False,
        depth: int = 0,
        custom_instructions: str | None = None,
        mode: str = "summary",
        query: str = ""
    ) -> str:
        """
        Summarize or Expand text using LLM with appropriate prompt.
        """
        instr = custom_instructions if custom_instructions else self._custom_instructions

        input_tokens = len(self._tokenizer.encode(text))
        
        if mode == "expansion":
            prompt = build_expansion_prompt(text, query, instr)
            system = "You are a high-fidelity retrieval agent. Answer questions accurately based on logs."
            target_tokens = 2048 
        else:
            target_tokens = resolve_target_tokens(
                input_tokens, aggressive, is_condensed
            )
            system = "You are a factual summarization tool. NO conversation. NO preamble. NO markdown headers."
            prompt = "\n\n".join([
                "TEXT TO COMPRESS:",
                "---",
                text,
                "---",
                f"TASK: Provide a concise factual summary in MAXIMUM {target_tokens} tokens.",
                "STRICT RULES:",
                "1. NO preambles (e.g. 'Here is...')",
                "2. NO markdown titles (e.g. # Chapter X)",
                "3. NO character invention.",
                "4. OUTPUT ONLY THE SUMMARY TEXT.",
                "SUMMARY:",
            ])

        label = f"{mode} {'condensed' if is_condensed else 'leaf'} d={depth}"
        logger.debug(f"[lcm] Calling LLM for {label} - Target: {target_tokens} tok")

        try:
            result = await asyncio.wait_for(
                self._complete(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=max(target_tokens * 2, 2048),
                    temperature=0.3,
                ),
                timeout=self._timeout_s,
            )
            
            content = result.content if hasattr(result, "content") else result
            summary = normalize_completion_summary(content)
            
            if "</think>" in summary:
                summary = summary.split("</think>")[-1].strip()

            if not summary:
                logger.warning("[lcm] empty summary from LLM (%s), using fallback", label)
                return deterministic_fallback_summary(text)

            summary_tokens = len(self._tokenizer.encode(summary))

            # --- Safeguard: Reject Hallucination Boilerplate ---
            if "Files: none" in summary or len(summary) < 50:
                 logger.warning("[lcm] REJECTING junk summary (%s): content too suspicious.", label)
                 return ""

            # --- Hard Physical Bloat Guard (Audit Fix - Relaxed to 1.15) ---
            summary_tokens = len(self._tokenizer.encode(summary))
            if summary_tokens > input_tokens * 1.15:
                logger.error(
                    "[lcm] CRITICAL BLOAT: output tokens (%d) > 1.15 * input tokens (%d). REJECTING summary (%s).",
                    summary_tokens, input_tokens, label
                )
                return ""
            
            logger.info("[lcm] summary metrics (%s): in=%d, out=%d, ratio=%.2f", label, input_tokens, summary_tokens, summary_tokens / input_tokens)

            # --- Token Overflow Guard (Audit Fix - Tightened to 90%) ---
            if summary_tokens >= input_tokens * 0.90 and not aggressive:
                logger.warning(
                    "[lcm] poor summary compression ratio (in=%d, out=%d, ratio=%.2f). "
                    "Rejecting summary (%s) to maintain context density.",
                    input_tokens, summary_tokens, summary_tokens / input_tokens, label
                )
                return "" # Return empty to signal failure to compactor
            # -----------------------------------------------------------

            return summary

        except asyncio.TimeoutError:
            logger.warning("[lcm] summarizer timeout after %.0fs (%s)", self._timeout_s, label)
            return summary if (summary := deterministic_fallback_summary(text, self._tokenizer)) else ""
        except Exception as exc:
            logger.error(f"[lcm] summarizer error ({label}): {exc}")
            return deterministic_fallback_summary(text, self._tokenizer)
