#!/usr/bin/env python3
"""Hook helper: embed messages after session stop."""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db
from embed import embed_messages_batch

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default=None)
    parser.add_argument("--dir", default="")
    args = parser.parse_args()
    cfg = db.load_config()
    if not cfg.get("embeddingEnabled", False): return
    stored = embed_messages_batch(db.get_db(), cfg, session_id=args.session)
    if stored:
        print(f"[lcmv3] Embedded {stored} messages", file=sys.stderr)

if __name__ == "__main__":
    main()
