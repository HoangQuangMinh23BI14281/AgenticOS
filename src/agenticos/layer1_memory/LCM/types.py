"""
LCM Type Definitions — Protocols, Enums, and Record Dataclasses.

Defines the contracts between LCM and the rest of AgenticOS,
abstracting away direct imports from core internals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable


# ── Enums ─────────────────────────────────────────────────────────────────────


class MessageRole(str, Enum):
    """Database-level message role enum."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MessagePartType(str, Enum):
    """Typed content block within a message."""
    TEXT = "text"
    REASONING = "reasoning"
    TOOL = "tool"
    TOOL_OUTPUT = "tool_output"
    PATCH = "patch"
    FILE = "file"
    SUBTASK = "subtask"
    COMPACTION = "compaction"
    STEP_START = "step_start"
    STEP_FINISH = "step_finish"
    SNAPSHOT = "snapshot"
    AGENT = "agent"
    RETRY = "retry"


class SummaryKind(str, Enum):
    """Summary node type in the DAG."""
    LEAF = "leaf"
    CONDENSED = "condensed"


class ContextItemType(str, Enum):
    """Context window item type."""
    MESSAGE = "message"
    SUMMARY = "summary"


# ── Protocols ─────────────────────────────────────────────────────────────────


@runtime_checkable
class TokenizerProtocol(Protocol):
    """
    Tokenizer interface — inject a real tokenizer as a dependency.

    Compatible with:
      - transformers.AutoTokenizer
      - tiktoken.Encoding
      - Any object with encode(str) -> list[int]
    """

    def encode(self, text: str) -> list[int]:
        """Encode text into token IDs."""
        ...

    def decode(self, tokens: list[int]) -> str:
        """Decode token IDs back into text."""
        ...


@runtime_checkable
class CompleteFn(Protocol):
    """
    Minimal LLM completion interface for summarization.

    Mirrors the CompleteFn signature from the TS reference.
    """

    async def __call__(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
        provider: str | None = None,
    ) -> CompletionResult:
        ...


# ── Record Dataclasses ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CompletionContentBlock:
    """Single content block from an LLM completion."""
    type: str
    text: str | None = None


@dataclass(frozen=True, slots=True)
class CompletionResult:
    """LLM completion response envelope."""
    content: list[CompletionContentBlock] = field(default_factory=list)
    error: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    """Persisted conversation row."""
    conversation_id: int
    session_id: str
    session_key: str | None = None
    title: str | None = None
    bootstrapped_at: datetime | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True, slots=True)
class MessageRecord:
    """Persisted message row."""
    message_id: int
    conversation_id: int
    seq: int
    role: MessageRole
    content: str
    token_count: int
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True, slots=True)
class MessagePartRecord:
    """Structured content part within a message."""
    part_id: str
    message_id: int
    session_id: str
    part_type: MessagePartType
    ordinal: int
    text_content: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_input: str | None = None
    tool_output: str | None = None
    metadata: str | None = None


@dataclass(frozen=True, slots=True)
class SummaryRecord:
    """Persisted summary DAG node."""
    summary_id: str
    conversation_id: int
    kind: SummaryKind
    depth: int
    content: str
    token_count: int
    file_ids: list[str] = field(default_factory=list)
    earliest_at: datetime | None = None
    latest_at: datetime | None = None
    descendant_count: int = 0
    descendant_token_count: int = 0
    source_message_token_count: int = 0
    model: str = "unknown"
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True, slots=True)
class SummarySubtreeNode(SummaryRecord):
    """Summary record enriched with subtree traversal metadata."""
    depth_from_root: int = 0
    parent_summary_id: str | None = None
    path: str = ""
    child_count: int = 0


@dataclass(frozen=True, slots=True)
class ContextItemRecord:
    """Active context window item (message or summary pointer)."""
    conversation_id: int
    ordinal: int
    item_type: ContextItemType
    message_id: int | None = None
    summary_id: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True, slots=True)
class LargeFileRecord:
    """Externalized large file metadata."""
    file_id: str
    conversation_id: int
    storage_uri: str
    file_name: str | None = None
    mime_type: str | None = None
    byte_size: int | None = None
    exploration_summary: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True, slots=True)
class CompactionDecision:
    """Whether compaction is needed."""
    should_compact: bool
    reason: str  # "threshold" | "manual" | "none"
    current_tokens: int
    threshold: int


@dataclass(frozen=True, slots=True)
class CompactionResult:
    """Outcome of a compaction sweep."""
    action_taken: bool
    tokens_before: int
    tokens_after: int
    created_summary_id: str | None = None
    condensed: bool = False
    level: str | None = None  # "normal" | "aggressive" | "fallback"


# ── Dependency Container ──────────────────────────────────────────────────────


@dataclass
class LcmDependencies:
    """
    Dependencies injected into the LCM engine at init time.

    Replaces all direct imports from external systems.
    """

    tokenizer: TokenizerProtocol
    complete: CompleteFn
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("lcm"))
