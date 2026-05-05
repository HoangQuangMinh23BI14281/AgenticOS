#!/usr/bin/env python3
"""LCMv3 CLI — vault management and diagnostics."""

import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db


def cmd_status(args):
    conn = db.get_db()
    sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    unsummarised = conn.execute("SELECT COUNT(*) FROM messages WHERE summarised = 0").fetchone()[0]
    summaries = conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]
    max_depth = db.get_max_summary_depth()
    consolidated = conn.execute("SELECT COUNT(*) FROM summaries WHERE consolidated = 1").fetchone()[0]
    db_size = os.path.getsize(str(db.VAULT_DB)) / (1024 * 1024)
    from summarise import get_provider_info
    provider = get_provider_info()
    print(f"=== LCMv3 Vault Status ===")
    print(f"  DB: {db.VAULT_DB}")
    print(f"  Size: {db_size:.2f} MB")
    print(f"  -------------------------")
    print(f"  Sessions: {sessions}")
    print(f"  Messages: {messages} ({unsummarised} unsummarised)")
    print(f"  Summaries: {summaries} (depth 0-{max_depth}, {consolidated} consolidated)")
    print(f"  -------------------------")
    print(f"  Provider: {provider.get('provider', 'none')} ({provider.get('model', 'N/A')})")
    if provider.get("last_error"):
        print(f"  Last error: {provider['last_error']}")
    cfg = db.load_config()
    if cfg.get("embeddingEnabled"):
        from embed import detect_provider as dp
        ep = dp(cfg)
        emb_count = db.count_embeddings()
        print(f"  Embeddings: {emb_count} ({ep or 'disabled'})")
    print(f"==========================")


def cmd_summarise(args):
    from summarise import run_full_summarisation
    result = run_full_summarisation(args.session)
    print(json.dumps(result, indent=2))


def cmd_dream(args):
    from dream import run_dream
    cfg = db.load_config()
    scope = "global" if args.glob else "project"
    project = args.project or os.getcwd()
    print(run_dream(scope, project, cfg))


def cmd_sessions(args):
    sessions = db.list_sessions(limit=args.limit)
    for s in sessions:
        last = datetime.fromtimestamp(s["last_active"]).strftime("%Y-%m-%d %H:%M") if s.get("last_active") else "?"
        flag = " [stateless]" if s.get("stateless") else ""
        handoff = " [handoff]" if s.get("handoff_text") else ""
        print(f"  {s['session_id'][:40]:40s}  {last}  {s.get('working_dir', '')}{flag}{handoff}")


def cmd_search(args):
    query = " ".join(args.query)
    if not query:
        print("Usage: lcc search <query>"); return
    cfg = db.load_config()
    if cfg.get("embeddingEnabled"):
        from embed import hybrid_search
        results = hybrid_search(query, cfg, limit=args.limit)
    else:
        results = db.search_all(query, limit=args.limit)
    for m in results.get("messages", []):
        ts = datetime.fromtimestamp(m["timestamp"]).strftime("%m-%d %H:%M") if m.get("timestamp") else ""
        print(f"  [msg:{m['id']}] {ts} [{m['role']}] {m['content'][:120]}")
    for s in results.get("summaries", []):
        print(f"  [sum:{s['id']}] d={s['depth']} {s['content'][:120]}")
    if results.get("hybrid"):
        print("  (hybrid FTS5 + vector search)")


def cmd_expand(args):
    if args.file:
        sums = db.get_summaries_for_file(args.file, limit=10)
        print(f"Summaries touching {args.file}:")
        for s in sums:
            print(f"  [{s['id']}] depth={s['depth']} kind={s.get('kind', '?')}")
            print(f"    {s['content'][:200]}")
        return
    s = db.get_summary(args.summary_id)
    if not s:
        print(f"Summary {args.summary_id} not found"); return
    print(f"[{s['id']}] depth={s['depth']} tokens={s.get('token_count', '?')}")
    print(f"  {s['content']}")
    sources = db.get_summary_sources(args.summary_id)
    print(f"\nSources ({len(sources)}):")
    for src in sources:
        print(f"  {src['source_type']}:{src['source_id']}")


def cmd_reindex(args):
    cfg = db.load_config()
    if not cfg.get("embeddingEnabled"):
        print("Embeddings not enabled. Set 'embeddingEnabled': true in config."); return
    from embed import embed_messages_batch
    stored = embed_messages_batch(db.get_db(), cfg)
    print(f"Indexed {stored} messages.")


def main():
    parser = argparse.ArgumentParser(prog="lcc", description="LCMv3 CLI")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", help="Show vault status")
    p_sum = sub.add_parser("summarise", help="Run DAG summarisation")
    p_sum.add_argument("--session")
    p_dream = sub.add_parser("dream", help="Run dream cycle")
    p_dream.add_argument("--project")
    p_dream.add_argument("--global", dest="glob", action="store_true")
    p_sess = sub.add_parser("sessions", help="List sessions")
    p_sess.add_argument("--limit", type=int, default=20)
    p_search = sub.add_parser("search", help="Search vault")
    p_search.add_argument("query", nargs="*")
    p_search.add_argument("--limit", type=int, default=20)
    p_expand = sub.add_parser("expand", help="Expand a summary or file")
    p_expand.add_argument("summary_id", nargs="?")
    p_expand.add_argument("--file")
    sub.add_parser("reindex", help="Reindex embeddings")

    args = parser.parse_args()
    cmds = {"status": cmd_status, "summarise": cmd_summarise, "dream": cmd_dream,
            "sessions": cmd_sessions, "search": cmd_search, "expand": cmd_expand, "reindex": cmd_reindex}
    if args.command in cmds:
        cmds[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
