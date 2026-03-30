"""
LCM Prompt Engineering — Templates and builders for context compaction.

Provides depth-aware prompts for leaf, session-level (D1), phase-level (D2),
and durable memory (D3+) summarization.
"""

from __future__ import annotations

# ── Policy Constants ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a context-compaction summarization engine. "
    "Follow user instructions exactly and return plain text summary content only."
)

DEFAULT_CONDENSED_TARGET_TOKENS = 2_000

# ── Target Calculations ───────────────────────────────────────────────────────

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
            "- Focus 100% on the core narrative and factual sequence.\n"
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
            "STRICT RULES:\n"
            "- ONLY use provided conversation segments. DO NOT use external knowledge.\n"
            "- DO NOT add new details, names, or events not present in the source.\n"
            "- If input is about a specific story (e.g. Harry Potter), stay in that world.\n\n"
            "Preserve:\n"
            "- Decisions made and their rationale.\n"
            "- Completed tasks/topics with outcomes.\n"
            "- In-progress items with current state.\n"
            "- Specific references (names, paths, identifiers) needed for continuation.\n\n"
            "Drop:\n"
            "- Context that has not changed from previous_context.\n"
            "- Transient details already resolved.\n\n"
            "Use plain text. Chronological narrative is preferred.\n"
            '- End with exactly: "Expand for details about: <comma-separated list>".\n'
            f"Target length: MAXIMUM {target_tokens} tokens. (Shorter is better if less info is present)."
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
