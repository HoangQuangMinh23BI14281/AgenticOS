#!/usr/bin/env python3
"""LCMv3 MCP Server — read-only tools for Claude Code integration."""

import json
import os
import sys
from datetime import datetime

# Add LCMv3 root to path for db imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
from inject_context import build_context, get_handoff, format_file_fingerprint
from summarise import get_provider_info, estimate_tokens, call_llm

from mcp.server import Server
from mcp.types import Tool, TextContent


def _ts(epoch):
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M") if epoch else "?"


def _tool_lcc_grep(query: str, limit: int = 20) -> str:
    cfg = db.load_config()
    embed_enabled = cfg.get("embeddingEnabled", False)
    if embed_enabled:
        from embed import hybrid_search
        results = hybrid_search(query, cfg, limit=limit)
    else:
        results = db.search_all(query, limit=limit)
    parts = []
    for m in results.get("messages", [])[:limit]:
        ts = _ts(m.get("timestamp"))
        parts.append(f"[msg:{m['id']}] {ts} [{m['role']}] {m['content'][:300]}")
    for s in results.get("summaries", [])[:limit]:
        parts.append(f"[sum:{s['id']}] depth={s['depth']} {s['content'][:300]}")
    if not parts:
        return f"No results for: {query}"
    header = f"Found {len(parts)} results"
    if results.get("hybrid"):
        header += " (hybrid FTS5 + vector)"
    return header + "\n\n" + "\n\n".join(parts)


def _tool_lcc_expand(summary_id: str = None, file: str = None) -> str:
    if file:
        sums = db.get_summaries_for_file(file, limit=10)
        if not sums:
            return f"No summaries found for file: {file}"
        parts = [f"File: {file} — {len(sums)} summaries"]
        for s in sums:
            parts.append(f"[{s['id']}] depth={s['depth']} kind={s.get('kind', '?')}\n{s['content'][:500]}")
        return "\n\n".join(parts)
    if not summary_id:
        return "Provide summary_id or file parameter"
    s = db.get_summary(summary_id)
    if not s:
        return f"Summary {summary_id} not found"
    sources = db.get_summary_sources(summary_id)
    parts = [f"[{s['id']}] depth={s['depth']} tokens={s.get('token_count', '?')} kind={s.get('kind', '?')}",
             s['content'], f"\nSources ({len(sources)}):"]
    for src in sources:
        if src["source_type"] == "message":
            msgs = db.get_messages_by_ids([int(src["source_id"])])
            if msgs:
                m = msgs[0]
                parts.append(f"  [msg:{m['id']}] [{m['role']}] {m['content'][:200]}")
        else:
            child = db.get_summary(src["source_id"])
            if child:
                parts.append(f"  [{child['id']}] depth={child['depth']} {child['content'][:200]}")
    return "\n".join(parts)


def _tool_lcc_context(session_id: str = None, working_dir: str = None, query: str = "") -> str:
    return build_context(session_id=session_id, working_dir=working_dir, query=query)


def _tool_lcc_sessions(limit: int = 20) -> str:
    sessions = db.list_sessions(limit=limit)
    if not sessions:
        return "No sessions in vault."
    parts = []
    for s in sessions:
        last = _ts(s.get("last_active"))
        flag = " [stateless]" if s.get("stateless") else ""
        handoff = " ✓handoff" if s.get("handoff_text") else ""
        parts.append(f"{s['session_id'][:50]}  last={last}  dir={s.get('working_dir', '')}{flag}{handoff}")
    return f"{len(sessions)} sessions:\n" + "\n".join(parts)


def _tool_lcc_handoff(session_id: str = None, generate: bool = False) -> str:
    if generate and session_id:
        msgs = db.get_messages_since(0)
        session_msgs = [m for m in msgs if m["session_id"] == session_id]
        if not session_msgs:
            return "No messages in session to generate handoff from."
        text = "\n".join(f"[{m['role']}] {m['content'][:500]}" for m in session_msgs[-10:])
        cfg = db.load_config()
        model_cfg = {"summaryProvider": cfg.get("summaryProvider"),
                     "summaryModel": cfg.get("handoffModel") or cfg.get("summaryModel"),
                     "openaiBaseUrl": cfg.get("openaiBaseUrl"), "anthropicBaseUrl": cfg.get("anthropicBaseUrl")}
        prompt = ("Generate a concise handoff note for the next session. Include: what was accomplished, "
                  "what's in progress, any blockers, and next steps. Output ONLY the handoff note.\n\n" + text)
        handoff = call_llm(prompt, model_cfg)
        if handoff:
            db.set_handoff(session_id, handoff)
            return f"Handoff saved:\n{handoff}"
        return "Failed to generate handoff (no LLM provider available)"
    existing = get_handoff(session_id)
    return existing if existing else "No handoff text available."


def _tool_lcc_status() -> str:
    conn = db.get_db()
    sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    unsummarised = conn.execute("SELECT COUNT(*) FROM messages WHERE summarised = 0").fetchone()[0]
    summaries = conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]
    max_depth = db.get_max_summary_depth()
    consolidated = conn.execute("SELECT COUNT(*) FROM summaries WHERE consolidated = 1").fetchone()[0]
    db_size = os.path.getsize(str(db.VAULT_DB)) / (1024 * 1024) if db.VAULT_DB.exists() else 0
    provider = get_provider_info()
    cfg = db.load_config()
    lines = [f"LCMv3 Vault: {db.VAULT_DB}", f"Size: {db_size:.2f}MB",
             f"Sessions: {sessions}", f"Messages: {messages} ({unsummarised} pending)",
             f"Summaries: {summaries} (depth 0-{max_depth}, {consolidated} consolidated)",
             f"Provider: {provider.get('provider', 'none')} ({provider.get('model', 'N/A')})"]
    if cfg.get("embeddingEnabled"):
        lines.append(f"Embeddings: {db.count_embeddings()}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP Server registration
# ---------------------------------------------------------------------------

TOOLS = [
    {"name": "lcc_grep", "description": "Search the LCMv3 vault (messages + summaries). Use for recalling past decisions, code patterns, errors, and conversations.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}, "limit": {"type": "integer", "default": 20}}, "required": ["query"]}},
    {"name": "lcc_expand", "description": "Drill into a summary node or get file history. Shows source messages/child summaries.",
     "inputSchema": {"type": "object", "properties": {"summary_id": {"type": "string"}, "file": {"type": "string"}}}},
    {"name": "lcc_context", "description": "Get budget-aware assembled context from vault history.",
     "inputSchema": {"type": "object", "properties": {"session_id": {"type": "string"}, "working_dir": {"type": "string"}, "query": {"type": "string"}}}},
    {"name": "lcc_sessions", "description": "List recent sessions in the vault.",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}}},
    {"name": "lcc_handoff", "description": "Get or generate a session handoff note.",
     "inputSchema": {"type": "object", "properties": {"session_id": {"type": "string"}, "generate": {"type": "boolean", "default": False}}}},
    {"name": "lcc_status", "description": "Get vault statistics and provider info.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def run_server():
    server = Server("lcmv3")

    @server.list_tools()
    async def list_tools():
        return [Tool(**t) for t in TOOLS]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        handlers = {
            "lcc_grep": lambda a: _tool_lcc_grep(a.get("query", ""), a.get("limit", 20)),
            "lcc_expand": lambda a: _tool_lcc_expand(a.get("summary_id"), a.get("file")),
            "lcc_context": lambda a: _tool_lcc_context(a.get("session_id"), a.get("working_dir"), a.get("query", "")),
            "lcc_sessions": lambda a: _tool_lcc_sessions(a.get("limit", 20)),
            "lcc_handoff": lambda a: _tool_lcc_handoff(a.get("session_id"), a.get("generate", False)),
            "lcc_status": lambda a: _tool_lcc_status(),
        }
        handler = handlers.get(name)
        if not handler:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            result = handler(arguments or {})
            return [TextContent(type="text", text=result)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    import asyncio
    from mcp.server.stdio import stdio_server
    async def _run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    asyncio.run(_run())


if __name__ == "__main__":
    run_server()
