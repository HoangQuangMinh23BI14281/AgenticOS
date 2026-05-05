#!/usr/bin/env python3
"""Lossless Dream — pattern extraction, DAG consolidation, and reporting."""

import fcntl
import json
import logging
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db
import summarise as summarise_mod

DREAM_DIR = db.VAULT_DIR / "dream"
LOG_FILE = DREAM_DIR / "dream.log"


def _get_logger():
    logger = logging.getLogger("lcmv3-dream")
    if not logger.handlers:
        DREAM_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(str(LOG_FILE))
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def _dream_llm_cfg(config):
    return {"summaryProvider": config.get("summaryProvider"),
            "summaryModel": config.get("dreamModel", config.get("summaryModel", "claude-haiku-4-5-20251001")),
            "anthropicBaseUrl": config.get("anthropicBaseUrl"), "openaiBaseUrl": config.get("openaiBaseUrl")}


PATTERN_CATEGORIES = ["CORRECTION", "PREFERENCE", "ANTI_PATTERN", "CONVENTION", "DECISION"]

PATTERN_PROMPT = """\
Analyze the following conversation history and extract recurring patterns.
Categorize each pattern as one of: CORRECTION, PREFERENCE, ANTI_PATTERN, CONVENTION, DECISION.

Return your response as a JSON object with this schema:
{"patterns": [{"category": "CORRECTION", "description": "...", "sources": ["msg:123", "sum_abc"]}]}

Rules:
- Only extract patterns that appear in 2+ instances or have strong evidence
- Keep descriptions to 1-2 sentences
- Preserve source IDs exactly as provided
- Output ONLY valid JSON, no preamble or commentary

Conversation history:
"""

PATTERN_PROMPT_TEXT = """\
Analyze the following conversation history and extract recurring patterns.
Categorize each pattern as one of: CORRECTION, PREFERENCE, ANTI_PATTERN, CONVENTION, DECISION.

For each pattern, output a line in this exact format:
[CATEGORY] Pattern description. (Source: {source_ids})

Rules:
- Only extract patterns that appear in 2+ instances or have strong evidence
- Keep descriptions to 1-2 sentences
- Output ONLY the pattern lines, no preamble or headers

Conversation history:
"""


def extract_patterns(messages, summaries, config):
    items = []
    for m in messages:
        c = m["content"][:3800] + "\n... [truncated]" if len(m["content"]) > 4000 else m["content"]
        items.append((f"[{m['role']}] {c}", f"msg:{m['id']}"))
    for s in summaries:
        c = s["content"][:3800] + "\n... [truncated]" if len(s["content"]) > 4000 else s["content"]
        items.append((c, s["id"]))
    if not items:
        return []

    chunk_size = config.get("chunkSize", 20)
    all_patterns = []
    cfg = _dream_llm_cfg(config)
    for i in range(0, len(items), chunk_size):
        chunk = items[i:i + chunk_size]
        chunk_text = "\n\n".join(f"[{sid}] {text}" for text, sid in chunk)
        response = summarise_mod.call_llm(PATTERN_PROMPT + chunk_text, cfg, json_mode=True)
        if response:
            patterns = _parse_patterns_json(response) or _parse_pattern_response(
                summarise_mod.call_llm(PATTERN_PROMPT_TEXT + chunk_text, cfg) or "")
            all_patterns.extend(patterns)
    if not all_patterns:
        all_patterns = _extractive_pattern_fallback(messages)
    return all_patterns


def _parse_patterns_json(response):
    try:
        data = json.loads(response)
        return [{"category": p.get("category", "").upper(), "description": p.get("description", ""),
                 "source_ids": ", ".join(p.get("sources", [])) if isinstance(p.get("sources"), list) else str(p.get("sources", ""))}
                for p in data.get("patterns", []) if p.get("category", "").upper() in PATTERN_CATEGORIES]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def _parse_pattern_response(response):
    patterns = []
    for line in response.strip().split("\n"):
        line = line.strip()
        for cat in PATTERN_CATEGORIES:
            if line.startswith(f"[{cat}]"):
                desc = line[len(f"[{cat}]"):].strip()
                source_ids = ""
                if "(Source:" in desc:
                    idx = desc.rindex("(Source:")
                    source_ids = desc[idx + 8:].rstrip(")")
                    desc = desc[:idx].strip()
                patterns.append({"category": cat, "description": desc, "source_ids": source_ids.strip()})
                break
    return patterns


def _extractive_pattern_fallback(messages):
    patterns = []
    for m in messages:
        cl = m["content"].lower()
        sid = f"msg:{m['id']}"
        for kw in ["don't", "no,", "wrong", "should be", "instead of"]:
            if kw in cl:
                for s in m["content"].split("."):
                    if kw in s.lower():
                        patterns.append({"category": "CORRECTION", "description": s.strip()[:200], "source_ids": sid})
                        break
                break
        for kw in ["always", "never", "prefer", "avoid"]:
            if kw in cl and m["role"] == "user":
                for s in m["content"].split("."):
                    if kw in s.lower():
                        patterns.append({"category": "PREFERENCE", "description": s.strip()[:200], "source_ids": sid})
                        break
                break
    seen = set()
    return [p for p in patterns if p["description"][:80] not in seen and not seen.add(p["description"][:80])][:20]


def consolidate_dag(config):
    max_depth = db.get_max_summary_depth()
    stats = {}
    for depth in range(max_depth + 1):
        pairs = db.get_overlapping_summaries(depth)
        if not pairs:
            continue
        clusters = _cluster_overlapping(pairs)
        consolidated_in_depth = 0
        for cluster in clusters:
            sums = [s for sid in cluster if (s := db.get_summary(sid))]
            if len(sums) < 2:
                continue
            merged = _merge_summaries(sums, config)
            if not merged:
                continue
            merged = summarise_mod.cap_summary_text(merged, config.get("condensedTargetTokens", 2000),
                                                     config.get("summaryMaxOverageFactor", 3))
            all_sources = []
            seen_src = set()
            for s in sums:
                for src in db.get_summary_sources(s["id"]):
                    key = (src["source_type"], src["source_id"])
                    if key not in seen_src:
                        seen_src.add(key)
                        all_sources.append(key)
            new_id = db.gen_summary_id()
            session_ids = set(s["session_id"] for s in sums if s["session_id"])
            sid = session_ids.pop() if len(session_ids) == 1 else None
            db.store_summary(summary_id=new_id, content=merged, depth=depth,
                source_ids=list(all_sources), session_id=sid,
                token_count=summarise_mod.estimate_tokens(merged))
            db.mark_consolidated([s["id"] for s in sums])
            consolidated_in_depth += len(sums)
        if consolidated_in_depth > 0:
            stats[depth] = {"consolidated": consolidated_in_depth}
    return stats


def _cluster_overlapping(pairs):
    parent = {}
    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x
    for a, b in pairs:
        parent.setdefault(a, a)
        parent.setdefault(b, b)
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    clusters_map = {}
    for node in parent:
        clusters_map.setdefault(find(node), set()).add(node)
    return list(clusters_map.values())


def _merge_summaries(summaries, config):
    combined = "\n\n---\n\n".join(s["content"] for s in summaries)
    prompt = ("Merge the following summaries into one concise summary that preserves "
              "all unique information. Remove redundant content but keep all distinct "
              "facts, decisions, file paths, and commands. Output ONLY the merged summary.\n\n" + combined)
    result = summarise_mod.call_llm(prompt, _dream_llm_cfg(config))
    if not result or result == combined:
        seen = set()
        lines = []
        for s in summaries:
            for line in s["content"].split("\n"):
                if line.strip() and line.strip() not in seen:
                    seen.add(line.strip())
                    lines.append(line)
        return "\n".join(lines)
    return result


def write_patterns(patterns, project_hash_val, working_dir, scope):
    out_dir = (DREAM_DIR / "global") if scope == "global" else (DREAM_DIR / "projects" / project_hash_val)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "patterns.md"
    by_cat = {}
    for p in patterns:
        by_cat.setdefault(p["category"], []).append(p)
    titles = {"CORRECTION": "Corrections", "PREFERENCE": "Preferences", "ANTI_PATTERN": "Anti-Patterns",
              "CONVENTION": "Conventions", "DECISION": "Decisions"}
    lines = [f"# Dream Patterns — {os.path.basename(working_dir) if working_dir else 'global'}",
             f"# Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for cat in PATTERN_CATEGORIES:
        items = by_cat.get(cat, [])
        if not items:
            continue
        lines.append(f"## {titles.get(cat, cat)}")
        for p in items:
            src = f" (Source: {p['source_ids']})" if p["source_ids"] else ""
            lines.append(f"- {p['description']}{src}")
        lines.append("")
    out_path.write_text("\n".join(lines))
    return str(out_path)


def check_auto_trigger(config, working_dir):
    if not config.get("autoDream", True):
        return False
    lock_path = DREAM_DIR / ".lock"
    if lock_path.exists():
        try:
            fd = open(lock_path, "r")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
        except (OSError, IOError):
            return False
    phash = db.project_hash(working_dir)
    last = db.get_last_dream(phash)
    if last is None:
        return db.count_sessions_since(0, working_dir) >= config.get("dreamAfterSessions", 5)
    if (time.time() - last["dreamed_at"]) / 3600 >= config.get("dreamAfterHours", 24):
        return True
    return db.count_sessions_since(last["dreamed_at"], working_dir) >= config.get("dreamAfterSessions", 5)


def run_dream(scope, working_dir, config):
    log = _get_logger()
    start_time = time.time()
    lock_path = DREAM_DIR / ".lock"
    DREAM_DIR.mkdir(parents=True, exist_ok=True)
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        lock_fd.close()
        return "Another dream cycle is already running. Skipping."
    try:
        phash = db.project_hash(working_dir) if scope != "global" else "global"
        log.info(f"Dream starting: scope={scope} dir={working_dir}")
        summarise_mod.run_full_summarisation()
        last = db.get_last_dream(phash)
        since_ts = last["dreamed_at"] if last else 0
        wd = working_dir if scope != "global" else None
        messages = db.get_messages_since(since_ts, wd)
        summary_ids = db.get_summary_ids_since(since_ts, wd)
        sessions_analyzed = db.count_sessions_since(since_ts, wd)
        if not messages and not summary_ids:
            return "No new data since last dream."
        batch_size = config.get("dreamBatchSize", 100)
        patterns = []
        for i in range(0, max(len(summary_ids), 1), batch_size):
            batch_sums = db.get_summaries_by_ids(summary_ids[i:i + batch_size])
            patterns.extend(extract_patterns(messages if i == 0 else [], batch_sums, config))
        consolidation_stats = consolidate_dag(config)
        total_consolidated = sum(s.get("consolidated", 0) for s in consolidation_stats.values())
        if patterns:
            write_patterns(patterns, phash, working_dir, scope)
        duration = time.time() - start_time
        # Report
        reports_dir = DREAM_DIR / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_path = str(reports_dir / f"{ts}-dream.md")
        (reports_dir / f"{ts}-dream.md").write_text(
            f"# Dream Report — {ts}\n\n- Scope: {scope}\n- Dir: {working_dir}\n"
            f"- Sessions: {sessions_analyzed}\n- Duration: {duration:.1f}s\n"
            f"- Patterns: {len(patterns)}\n- Consolidated: {total_consolidated}\n")
        db.store_dream_log(project_hash_val=phash, scope=scope, patterns_found=len(patterns),
            consolidations=total_consolidated, sessions_analyzed=sessions_analyzed, report_path=report_path)
        summary = f"Dream complete ({duration:.1f}s) — {len(patterns)} patterns, {total_consolidated} consolidated"
        log.info(summary)
        return summary
    except Exception:
        log.exception("Dream cycle failed")
        return "Dream cycle failed — see dream.log"
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


if __name__ == "__main__":
    if "--check-trigger" in sys.argv:
        cwd = os.getcwd()
        if "--cwd" in sys.argv:
            idx = sys.argv.index("--cwd")
            if idx + 1 < len(sys.argv):
                cwd = sys.argv[idx + 1]
        print("true" if check_auto_trigger(db.load_config(), cwd) else "false")
    elif "--run" in sys.argv:
        project = os.getcwd()
        if "--project" in sys.argv:
            idx = sys.argv.index("--project")
            if idx + 1 < len(sys.argv):
                project = sys.argv[idx + 1]
        scope = "global" if "--global" in sys.argv else "project"
        print(run_dream(scope, project, db.load_config()))
    else:
        print("Usage: dream.py --run [--project DIR] [--global]")
        print("       dream.py --check-trigger [--cwd DIR]")
