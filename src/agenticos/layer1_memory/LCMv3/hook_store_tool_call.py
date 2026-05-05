#!/usr/bin/env python3
"""Hook helper: record tool call results (file path fingerprinting)."""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db

FILE_TOOLS = {"Read", "Edit", "MultiEdit", "Write", "NotebookEdit"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--dir", default="")
    parser.add_argument("--tool-name", default="")
    parser.add_argument("--input-json", default="")
    parser.add_argument("--output-json", default="")
    args = parser.parse_args()
    if args.tool_name not in FILE_TOOLS: return
    cfg = db.load_config()
    if db.matches_any_pattern(args.session, cfg.get("ignoreSessionPatterns", [])): return
    file_path = None
    if args.input_json:
        try:
            inp = json.loads(args.input_json)
            fp = inp.get("file_path") or inp.get("path") or ""
            if fp:
                if args.dir and os.path.isabs(fp) and fp.startswith(args.dir):
                    file_path = os.path.relpath(fp, args.dir)
                else:
                    file_path = fp
        except (json.JSONDecodeError, TypeError): pass
    if file_path:
        content = f"[{args.tool_name}] {file_path}"
        if args.output_json:
            try:
                out = json.loads(args.output_json)
                snippet = str(out)[:200]
                content += f"\n{snippet}"
            except (json.JSONDecodeError, TypeError): pass
        db.store_message(session_id=args.session, role="tool", content=content,
                         tool_name=args.tool_name, working_dir=args.dir, file_path=file_path)

if __name__ == "__main__":
    main()
