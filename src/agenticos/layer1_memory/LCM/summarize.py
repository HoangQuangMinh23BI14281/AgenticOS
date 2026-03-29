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
import re
from dataclasses import dataclass
from typing import Any

from .types import CompleteFn, CompletionResult

logger = logging.getLogger("lcm.summarize")


# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_CONDENSED_TARGET_TOKENS = 2_000
SUMMARIZER_TIMEOUT_S = 180.0
SYSTEM_PROMPT = (
    "You are a context-compaction summarization engine. "
    "Follow user instructions exactly and return plain text summary content only."
)

AUTH_ERROR_PATTERN = re.compile(
    r"\b401\b|unauthorized|unauthorised|invalid[_ -]?token"
    r"|invalid[_ -]?api[_ -]?key|authentication failed"
    r"|authorization failed|missing scope|insufficient scope"
    r"|model\.request\b",
    re.IGNORECASE,
)


# ── Exceptions ────────────────────────────────────────────────────────────────


class LcmProviderAuthError(Exception):
    """Raised when the summarizer hits a provider auth failure."""

    def __init__(self, provider: str, model: str, message: str = ""):
        super().__init__(
            f"[lcm] compaction failed: provider auth error. "
            f"Check configured summaryProvider credentials. "
            f"Current: {provider}/{model}. {message}"
        )
        self.provider = provider
        self.model = model


class SummarizerTimeoutError(Exception):
    """Raised when a summarization call exceeds the timeout."""

    def __init__(self, timeout_s: float, label: str):
        super().__init__(f"[lcm] summarizer timeout after {timeout_s}s ({label})")


# ── Token estimation ──────────────────────────────────────────────────────────


def estimate_tokens_fallback(text: str) -> int:
    """Rough fallback estimate (~4 chars/token). Use real tokenizer when available."""
    return math.ceil(len(text) / 4)


def resolve_target_tokens(
    input_tokens: int,
    aggressive: bool = False,
    is_condensed: bool = False,
    condensed_target: int = DEFAULT_CONDENSED_TARGET_TOKENS,
) -> int:
    """Calculate target summary token count based on input and mode."""
    if is_condensed:
        # Nén mạnh D1/D2: Ép xuống tối đa 30% so với tổng đầu vào, nhưng không quá 500 token.
        return max(150, min(500, int(input_tokens * 0.3)))
    if aggressive:
        # Nén Leaf khi sắp tràn: Cực kỳ ngắn gọn
        return max(50, min(150, int(input_tokens * 0.15)))
    # Nén Leaf bình thường
    return max(100, min(300, int(input_tokens * 0.25)))


# ── Prompt Builders ───────────────────────────────────────────────────────────


def build_leaf_prompt(
    text: str,
    target_tokens: int,
    aggressive: bool = False,
    previous_summary: str | None = None,
    custom_instructions: str | None = None,
) -> str:
    """Build leaf-level segment summarization prompt."""
    prev_ctx = (previous_summary or "").strip() or "(none)"
    instr = f"Operator instructions:\n{custom_instructions.strip()}" if custom_instructions and custom_instructions.strip() else "Operator instructions: (none)"

    policy = (
        "Aggressive summary policy:\n"
        "- Keep only durable facts and current task state.\n"
        "- Remove examples, repetition, and low-value narrative details.\n"
        "- Preserve explicit TODOs, blockers, decisions, and constraints."
    ) if aggressive else (
        "Normal summary policy:\n"
        "- Preserve key decisions, rationale, constraints, and active tasks.\n"
        "- Keep essential technical details needed to continue work safely.\n"
        "- Remove obvious repetition and conversational filler."
    )

    return "\n\n".join([
        "You summarize a list of messages. Output ONLY the factual summary content.",
        "CRITICAL: DO NOT include prefixes like 'User:', 'Assistant:', or 'AI:' in your response.",
        "Your output must be a pure third-party narrative of the events or topics discussed.",
        policy,
        instr,
        (
            "Output requirements:\n"
            "- Plain text only. No roles, no markers, no 'User says'.\n"
            "- No preamble, headings, or markdown formatting.\n"
            "- Track file operations (created, modified, deleted, renamed) with paths.\n"
            '- If no file operations appear, include exactly: "Files: none".\n'
            '- End with exactly: "Expand for details about: <comma-separated list>".\n'
            f"- Target length: about {target_tokens} tokens or less."
        ),
        f"<conversation_segment>\n{text}\n</conversation_segment>",
    ])


def build_d1_prompt(
    text: str,
    target_tokens: int,
    previous_summary: str | None = None,
    custom_instructions: str | None = None,
) -> str:
    """Build D1 condensation prompt (session → condensed)."""
    instr = f"Operator instructions:\n{custom_instructions.strip()}" if custom_instructions and custom_instructions.strip() else "Operator instructions: (none)"
    prev_ctx = (previous_summary or "").strip()
    prev_block = (
        "It already has this preceding summary as context. Do not repeat information\n"
        "that appears there unchanged. Focus on what is new, changed, or resolved:\n\n"
        f"<previous_context>\n{prev_ctx}\n</previous_context>"
    ) if prev_ctx else "Focus on what matters for continuation:"

    return "\n\n".join([
        "You are compacting leaf-level conversation summaries into a single condensed memory node.",
        "You are preparing context for a fresh model instance that will continue this conversation.",
        instr,
        prev_block,
        (
            "Preserve:\n"
            "- Decisions made and their rationale when rationale matters going forward.\n"
            "- Earlier decisions that were superseded, and what replaced them.\n"
            "- Completed tasks/topics with outcomes.\n"
            "- In-progress items with current state and what remains.\n"
            "- Blockers, open questions, and unresolved tensions.\n"
            "- Specific references (names, paths, URLs, identifiers) needed for continuation.\n\n"
            "Drop low-value detail:\n"
            "- Context that has not changed from previous_context.\n"
            "- Intermediate dead ends where the conclusion is already known.\n"
            "- Transient states that are already resolved.\n"
            "- Tool-internal mechanics and process scaffolding.\n\n"
            "Use plain text. No mandatory structure.\n"
            "Include a timeline with timestamps (hour or half-hour) for significant events.\n"
            "Present information chronologically and mark superseded decisions.\n"
            '- End with exactly: "Expand for details about: <comma-separated list>".\n'
            f"Target length: about {target_tokens} tokens."
        ),
        f"<conversation_to_condense>\n{text}\n</conversation_to_condense>",
    ])


def build_d2_prompt(
    text: str,
    target_tokens: int,
    custom_instructions: str | None = None,
) -> str:
    """Build D2 condensation prompt (session-level → phase-level)."""
    instr = f"Operator instructions:\n{custom_instructions.strip()}" if custom_instructions and custom_instructions.strip() else "Operator instructions: (none)"

    return "\n\n".join([
        "You are condensing multiple session-level summaries into a higher-level memory node.",
        "A future model should understand trajectory, not per-session minutiae.",
        instr,
        (
            "Preserve:\n"
            "- Decisions still in effect and their rationale.\n"
            "- Decisions that evolved: what changed and why.\n"
            "- Completed work with outcomes.\n"
            "- Active constraints, limitations, and known issues.\n"
            "- Current state of in-progress work.\n\n"
            "Drop:\n"
            "- Session-local operational detail and process mechanics.\n"
            "- Identifiers that are no longer relevant.\n"
            "- Intermediate states superseded by later outcomes.\n\n"
            "Use plain text. Brief headers are fine if useful.\n"
            "Include a timeline with dates and approximate time of day for key milestones.\n"
            '- End with exactly: "Expand for details about: <comma-separated list>".\n'
            f"Target length: about {target_tokens} tokens."
        ),
        f"<conversation_to_condense>\n{text}\n</conversation_to_condense>",
    ])


def build_d3plus_prompt(
    text: str,
    target_tokens: int,
    custom_instructions: str | None = None,
) -> str:
    """Build D3+ condensation prompt (phase-level → durable memory)."""
    instr = f"Operator instructions:\n{custom_instructions.strip()}" if custom_instructions and custom_instructions.strip() else "Operator instructions: (none)"

    return "\n\n".join([
        "You are creating a high-level memory node from multiple phase-level summaries.",
        "This may persist for the rest of the conversation. Keep only durable context.",
        instr,
        (
            "Preserve:\n"
            "- Key decisions and rationale.\n"
            "- What was accomplished and current state.\n"
            "- Active constraints and hard limitations.\n"
            "- Important relationships between people, systems, or concepts.\n"
            "- Durable lessons learned.\n\n"
            "Drop:\n"
            "- Operational and process detail.\n"
            "- Method details unless the method itself was the decision.\n"
            "- Specific references unless essential for continuation.\n\n"
            "Use plain text. Be concise.\n"
            "Include a brief timeline with dates (or date ranges) for major milestones.\n"
            '- End with exactly: "Expand for details about: <comma-separated list>".\n'
            f"Target length: about {target_tokens} tokens."
        ),
        f"<conversation_to_condense>\n{text}\n</conversation_to_condense>",
    ])


def build_expansion_prompt(
    text: str,
    query: str,
    custom_instructions: str | None = None,
) -> str:
    """Build expansion prompt for high-fidelity retrieval."""
    instr = f"Operator instructions:\n{custom_instructions.strip()}" if custom_instructions and custom_instructions.strip() else ""
    
    return "\n\n".join([
        "You are an information retrieval agent.",
        f"The user is asking: '{query}'",
        "Based on the RAW conversation logs provided below, provide a DIRECT and DETAILED answer.",
        "Do not summarize if the detail is relevant to the query. Keep facts verbatim.",
        instr,
        (
            "Output requirements:\n"
            "- Plain text only.\n"
            "- No 'Expand for details about' footers.\n"
            "- Focus 100% on the user's query."
        ),
        f"<raw_logs>\n{text}\n</raw_logs>",
    ])


def build_condensed_prompt(
    text: str,
    target_tokens: int,
    depth: int,
    previous_summary: str | None = None,
    custom_instructions: str | None = None,
) -> str:
    """Select prompt template based on depth."""
    if depth <= 1:
        return build_d1_prompt(text, target_tokens, previous_summary, custom_instructions)
    if depth == 2:
        return build_d2_prompt(text, target_tokens, custom_instructions)
    return build_d3plus_prompt(text, target_tokens, custom_instructions)


# ── Deterministic Fallback ────────────────────────────────────────────────────


def deterministic_fallback_summary(text: str, max_tokens: int = 512) -> str:
    """
    Deterministic truncation fallback when LLM output is empty.

    Takes ~max_tokens * 4 characters from the end of the input,
    prefixed with a marker indicating truncation.
    """
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text.strip()
    return f"[Truncated — oldest context removed]\n\n{text[-max_chars:].strip()}"


# ── Response Normalization ────────────────────────────────────────────────────


def normalize_completion_summary(content: Any) -> str:
    """Extract text safely from provider completion response (supports LiteLLM/OpenAI/Anthropic)."""
    if isinstance(content, str):
        return content.strip()
    
    # Check for .text attribute (CompletionResult or similar)
    if hasattr(content, "text"):
        return str(content.text).strip()
    
    # Handle list of blocks (CompletionContentBlock)
    if isinstance(content, list) and len(content) > 0:
        parts = []
        for item in content:
            if hasattr(item, "text"): parts.append(str(item.text))
            elif isinstance(item, str): parts.append(item)
        return "\n".join(parts).strip()
            
    if isinstance(content, dict):
        for key in ["text", "content", "summary", "response"]:
            if key in content and isinstance(content[key], str):
                return content[key].strip()

    # Strip common prompt leaks and role prefixes (Aggressive Cleaning)
    res = re.sub(r"<(/?)(previous_context|conversation_segment|conversation_to_condense|raw_logs)>", "", res, flags=re.I)
    res = re.sub(r"\*\*?(User|Assistant|AI|System|Thinking)\*\*?[:\s-]*", "", res, flags=re.I)
    res = re.sub(r"^(User|Assistant|AI|System|Thinking)[:\s-]*", "", res, flags=re.I, count=0)
    res = re.sub(r"\n(User|Assistant|AI|System|Thinking)[:\s-]*", "\n", res, flags=re.I)
    res = re.sub(r"\[/?(previous_context|summary|context_history)\]", "", res, flags=re.I)
    
    # Remove any thinking block that might have leaked into content
    if "<think>" in res.lower():
        res = re.sub(r"<think>.*?</think>", "", res, flags=re.I | re.DOTALL)
    
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
    ) -> None:
        self._complete = complete
        self._provider = provider
        self._model = model
        self._timeout_s = timeout_s
        self._custom_instructions = custom_instructions

    async def summarize(
        self,
        text: str,
        aggressive: bool = False,
        previous_summary: str | None = None,
        is_condensed: bool = False,
        depth: int = 0,
        custom_instructions: str | None = None, # Thêm tham số này để Expand Tool dùng được
    ) -> str:
        """
        Summarize text using LLM with appropriate prompt.
        """
        # Ưu tiên custom_instructions được truyền vào (dùng cho Expand Query)
        instr = custom_instructions if custom_instructions else self._custom_instructions

    async def summarize(
        self,
        text: str,
        aggressive: bool = False,
        previous_summary: str | None = None,
        is_condensed: bool = False,
        depth: int = 0,
        custom_instructions: str | None = None, # Thêm tham số này để Expand Tool dùng được
        mode: str = "summary", # "summary" | "expansion"
        query: str = ""
    ) -> str:
        """
        Summarize or Expand text using LLM with appropriate prompt.
        """
        # Ưu tiên custom_instructions được truyền vào (dùng cho Expand Query)
        instr = custom_instructions if custom_instructions else self._custom_instructions

        input_tokens = estimate_tokens_fallback(text)
        
        if mode == "expansion":
            prompt = build_expansion_prompt(text, query, instr)
            system = "You are a high-fidelity retrieval agent. Answer questions accurately based on logs."
            target_tokens = 2048 # High budget for expansion
        else:
            target_tokens = resolve_target_tokens(
                input_tokens, aggressive, is_condensed
            )
            system = SYSTEM_PROMPT
            if is_condensed:
                prompt = build_condensed_prompt(
                    text, target_tokens, depth,
                    previous_summary, instr,
                )
            else:
                prompt = build_leaf_prompt(
                    text, target_tokens, aggressive,
                    previous_summary, instr,
                )

        label = f"{mode} {'condensed' if is_condensed else 'leaf'} d={depth}"
        
        # In log ra để bạn biết LLM đang được gọi bằng Prompt gì!
        logger.debug(f"[lcm] Calling LLM for {label} - Target: {target_tokens} tok")

        try:
            result = await asyncio.wait_for(
                self._complete(
                    model=self._model,
                    # Chú ý: Cấu trúc messages chuẩn của API
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=max(target_tokens * 2, 2048),
                    temperature=0.3,
                ),
                timeout=self._timeout_s,
            )
            
            # Xử lý nội dung trả về
            summary = normalize_completion_summary(
                result.content if hasattr(result, "content") else result
            )
            
            # Cắt bỏ thẻ <think> của Deepseek (nếu có)
            if "</think>" in summary:
                summary = summary.split("</think>")[-1].strip()

            if not summary:
                logger.warning("[lcm] empty summary from LLM (%s), using fallback", label)
                return deterministic_fallback_summary(text)

            return summary

        except asyncio.TimeoutError:
            logger.warning("[lcm] summarizer timeout after %.0fs (%s)", self._timeout_s, label)
            return deterministic_fallback_summary(text)
        except Exception as exc:
            logger.error(f"[lcm] summarizer error ({label}): {exc}")
            return deterministic_fallback_summary(text)
