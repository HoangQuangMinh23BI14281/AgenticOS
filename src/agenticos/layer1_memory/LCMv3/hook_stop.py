#!/usr/bin/env python3
"""Hook helper: persist conversation from transcript on Stop event."""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db

def extract_text_content(message_obj: dict) -> str:
    content = message_obj.get("content", "")
    if isinstance(content, str): return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)

def parse_transcript(transcript_path: str) -> list[dict]:
    messages = []
    if not transcript_path or not os.path.isfile(transcript_path): return messages
    with open(transcript_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: entry = json.loads(line)
            except json.JSONDecodeError: continue
            if entry.get("type", "") not in ("user", "assistant"): continue
            message_obj = entry.get("message", {})
            content = extract_text_content(message_obj) if message_obj else extract_text_content(entry)
            role = message_obj.get("role", entry.get("type", "")) if message_obj else entry.get("type", "")
            if content and content.strip():
                messages.append({"role": role, "content": content.strip()})
    return messages

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument("--dir", default="")
    parser.add_argument("--transcript", default="")
    args = parser.parse_args()
    cfg = db.load_config()
    if db.matches_any_pattern(args.session, cfg.get("ignoreSessionPatterns", [])): return
    stateless = db.matches_any_pattern(args.session, cfg.get("statelessSessionPatterns", []))
    db.ensure_session(args.session, args.dir, stateless=stateless)
    all_messages = parse_transcript(args.transcript)
    if not all_messages: return
    existing_count = db.count_session_messages(args.session)
    for msg in all_messages[existing_count:]:
        db.store_message(session_id=args.session, role=msg["role"], content=msg["content"], working_dir=args.dir)

if __name__ == "__main__":
    main()
