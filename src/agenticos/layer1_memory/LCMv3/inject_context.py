#!/usr/bin/env python3
"""Context injection for LCMv3 — budget-aware context assembly."""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db
from summarise import estimate_tokens


def _load_dream_patterns(working_dir: str = "", config: dict = None) -> str:
    if config is None:
        config = db.load_config()
    token_budget = config.get("dreamTokenBudget", 2000)
    dream_dir = db.VAULT_DIR / "dream"
    parts = []
    if working_dir:
        phash = db.project_hash(working_dir)
        project_path = dream_dir / "projects" / phash / "patterns.md"
        if project_path.exists():
            try:
                content = project_path.read_text().strip()
                if content:
                    parts.append(content)
            except OSError:
                pass
    global_path = dream_dir / "global" / "patterns.md"
    if global_path.exists():
        try:
            content = global_path.read_text().strip()
            if content:
                parts.append(content)
        except OSError:
            pass
    if not parts:
        return ""
    combined = "\n\n".join(parts)
    max_chars = token_budget * 4
    if len(combined) > max_chars:
        combined = combined[:max_chars] + "\n... [truncated to token budget]"
    return combined


_CONTROL_CHARS = "".join(chr(c) for c in range(0x20) if c not in (0x09,))


def _sanitize_for_context(value: str, max_len: int = 256) -> str:
    if not value:
        return ""
    cleaned = value.replace("\r", " ").replace("\n", " ")
    for ch in _CONTROL_CHARS:
        cleaned = cleaned.replace(ch, " ")
    cleaned = cleaned.strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len - 1] + "…"
    return cleaned


def format_file_fingerprint(file_path: str, summaries: list[dict], token_budget: int = 200) -> str:
    if not summaries:
        return ""
    from collections import Counter
    from datetime import datetime
    file_path = _sanitize_for_context(file_path, max_len=256)
    n = len(summaries)
    latest = max((s.get("created_at") or 0) for s in summaries)
    last_touched = datetime.fromtimestamp(latest).strftime("%Y-%m-%d") if latest else ""
    kinds = [s.get("kind") for s in summaries if s.get("kind")]
    polarity = ", ".join(f"{k}×{v}" for k, v in Counter(kinds).most_common()) if kinds else "unknown"

    def _topic(content: str) -> str:
        first_line = (content or "").strip().split("\n", 1)[0]
        return _sanitize_for_context(" ".join(first_line.split()[:6]), max_len=120)

    seen = set()
    raw_topics = []
    for s in summaries:
        t = _topic(s.get("content", ""))
        if t and t not in seen:
            seen.add(t)
            raw_topics.append(t)

    max_chars = token_budget * 4
    expand_line = f'   Expand: call MCP tool `lcc_expand` with {{"file": "{file_path}"}}'

    def _render(topics_limit, include_last_touched):
        topics = raw_topics[:topics_limit]
        topics_str = "; ".join(topics) if topics else "none"
        parts = [f"[lcc] {file_path} — {n} prior summaries"]
        if include_last_touched and last_touched:
            parts.append(f"last touched {last_touched}")
        parts.append(f"polarity: {polarity}")
        parts.append(f"topics: {topics_str}")
        return ", ".join(parts) + f".\n{expand_line}"

    for tl, ilt in ((5, True), (3, True), (3, False)):
        out = _render(tl, ilt)
        if len(out) <= max_chars:
            return out
    return f"[lcc] {file_path} — {n} prior summaries, polarity: {polarity}.\n{expand_line}"


def get_handoff(session_id: str = None) -> str:
    if session_id:
        session = db.get_session(session_id)
        if session and session.get("handoff_text"):
            return session["handoff_text"]
    sessions = db.list_sessions(limit=10)
    for s in sessions:
        if s.get("handoff_text"):
            return s["handoff_text"]
    return ""


def get_relevant_summaries(query: str = "", limit: int = 5) -> list[dict]:
    candidates = limit * 3
    if query and query.strip():
        results = db.search_summaries(query, limit=candidates)
        if results:
            return results
    return db.get_top_summaries(limit=candidates)


def build_context(session_id=None, working_dir="", query="", limit=5, config_override=None) -> str:
    config = {**db.load_config(), **(config_override or {})}
    ctx_budget = config.get("contextTokenBudget", 8000)

    reserved_parts = []
    reserved_tokens = 0
    handoff = get_handoff(session_id)
    if handoff:
        hb = f"## Previous Session Handoff\n{handoff}"
        reserved_parts.append(hb)
        reserved_tokens += estimate_tokens(hb)
    dream_ctx = _load_dream_patterns(working_dir, config)
    if dream_ctx:
        db_block = f"## Dream Patterns (extracted from history)\n{dream_ctx}"
        reserved_parts.append(db_block)
        reserved_tokens += estimate_tokens(db_block)

    header = "# Lossless Context (auto-injected)\n"
    section_header = "## Relevant Context (from conversation history)"
    separator = "\n\n"
    header_tokens = estimate_tokens(header)
    separator_tokens = estimate_tokens(separator) * (len(reserved_parts) + 1)
    summary_budget = max(0, ctx_budget - reserved_tokens - header_tokens
                         - estimate_tokens(section_header) - separator_tokens)
    candidates = get_relevant_summaries(query, limit=limit)
    selected = []
    used = 0
    for s in candidates:
        depth_label = f"depth-{s['depth']}" if "depth" in s else ""
        item_text = f"### [{len(selected) + 1}] {depth_label}\n{s.get('content', '')}"
        item_tokens = estimate_tokens(item_text) + estimate_tokens(separator)
        if used + item_tokens <= summary_budget:
            selected.append(item_text)
            used += item_tokens

    parts = []
    if reserved_parts:
        parts.extend(reserved_parts)
    if selected:
        parts.append(section_header)
        parts.extend(selected)
    if not parts:
        return ""
    return header + separator.join(parts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session")
    parser.add_argument("--dir")
    parser.add_argument("--query", default="")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    context = build_context(session_id=args.session, working_dir=args.dir, query=args.query, limit=args.limit)
    print(json.dumps({"context": context}) if args.json else context)
