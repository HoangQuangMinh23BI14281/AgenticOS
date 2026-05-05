#!/usr/bin/env python3
"""
DAG summarisation engine for LCMv3.

Collects unsummarised messages, chunks them, calls the summary model,
writes summary nodes to the DAG, and cascades to higher depths when
the node count at any depth exceeds the configured threshold.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db

_log = logging.getLogger("lcmv3")
if not _log.handlers:
    _log.addHandler(logging.StreamHandler(sys.stderr))
    _log.setLevel(logging.WARNING)

# Provider state (in-memory, per-process)
_provider_state = {"provider": None, "model": None, "auto_detected": False,
                   "last_error": None, "last_error_time": None, "consecutive_failures": 0}


def get_provider_info() -> dict:
    return dict(_provider_state)


# ---------------------------------------------------------------------------
# Circuit breaker (file-backed)
# ---------------------------------------------------------------------------

def _load_circuit_breaker_state() -> dict:
    try:
        p = db.LOSSLESS_HOME / "circuit_breaker.json"
        if p.exists():
            with open(p) as f:
                d = json.load(f)
            return {"failures": int(d.get("failures", 0)), "last_error_time": float(d.get("last_error_time", 0))}
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {"failures": 0, "last_error_time": 0}


def _write_circuit_breaker_state(failures: int, last_error_time: float) -> None:
    try:
        p = db.LOSSLESS_HOME / "circuit_breaker.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump({"failures": failures, "last_error_time": last_error_time}, f)
        tmp.replace(p)
    except OSError as e:
        _log.warning("[lcmv3] circuit_breaker write failed: %s", e)


def _check_circuit_breaker(cfg: dict) -> tuple[bool, str]:
    if not cfg.get("circuitBreakerEnabled", True):
        return (True, "")
    state = _load_circuit_breaker_state()
    threshold = cfg.get("circuitBreakerThreshold", 5)
    cooldown_secs = cfg.get("circuitBreakerCooldownMs", 1800000) / 1000
    if state["failures"] >= threshold:
        elapsed = time.time() - state["last_error_time"]
        if elapsed < cooldown_secs:
            return (False, f"Circuit breaker open ({state['failures']} failures, resets in {int(cooldown_secs - elapsed)}s)")
        _write_circuit_breaker_state(0, 0)
    return (True, "")


# ---------------------------------------------------------------------------
# Provider auto-detection
# ---------------------------------------------------------------------------

_claude_cli_path: str | None = None
_claude_cli_checked: bool = False


def _detect_provider(cfg: dict) -> tuple[str, str] | tuple[None, None]:
    global _claude_cli_path, _claude_cli_checked
    if not _claude_cli_checked:
        _claude_cli_path = shutil.which("claude")
        _claude_cli_checked = True
    if _claude_cli_path:
        return ("claude-cli", cfg.get("summaryModel") or "claude-haiku-4-5-20251001")
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ("anthropic", cfg.get("summaryModel") or "claude-haiku-4-5-20251001")
    if os.environ.get("OPENAI_API_KEY"):
        return ("openai", cfg.get("summaryModel") or "gpt-4.1-mini")
    base_url = cfg.get("openaiBaseUrl") or os.environ.get("OPENAI_BASE_URL")
    if base_url:
        return ("openai", cfg.get("summaryModel") or "llama3")
    return (None, None)


def _get_anthropic_auth() -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return {"api_key": key}
    creds_path = os.path.join(os.path.expanduser("~"), ".claude", ".credentials.json")
    try:
        with open(creds_path) as f:
            creds = json.load(f)
        token = creds.get("claudeAiOauth", {}).get("accessToken")
        if token:
            return {"auth_token": token}
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    oauth_env = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if oauth_env:
        return {"auth_token": oauth_env}
    return {}


MODEL_CONTEXT_WINDOWS = {
    "claude-haiku": 200_000, "claude-sonnet": 200_000, "claude-opus": 200_000,
    "gpt-4o-mini": 128_000, "gpt-4o": 128_000,
    "gpt-4.1-mini": 1_000_000, "gpt-4.1-nano": 1_000_000, "gpt-4.1": 1_000_000,
    "llama3": 8_192, "mistral": 32_000, "MiniMax": 1_000_000,
}


def _get_context_window(model: str) -> int:
    if not model:
        return 8192
    for prefix, size in MODEL_CONTEXT_WINDOWS.items():
        if model.startswith(prefix):
            return size
    return 8192


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def cap_summary_text(text: str, target_tokens: int, overage_factor: int = 3) -> str:
    max_tokens = target_tokens * overage_factor
    current_tokens = estimate_tokens(text)
    if current_tokens <= max_tokens:
        return text
    max_chars = max_tokens * 4
    truncated = text[:max_chars]
    capped = truncated.rsplit("\n", 1)[0]
    if len(capped) < max_chars // 2:
        capped = truncated
    return f"{capped}\n\n[Capped from ~{current_tokens} to ~{max_tokens} tokens]"


def _log_provider_error(category: str, provider: str, model: str, error: Exception) -> None:
    _provider_state["last_error"] = category
    _provider_state["last_error_time"] = time.time()
    _provider_state["consecutive_failures"] += 1
    cb = _load_circuit_breaker_state()
    _write_circuit_breaker_state(cb["failures"] + 1, _provider_state["last_error_time"])
    safe_msg = type(error).__name__
    if hasattr(error, "status_code"):
        safe_msg += f" (HTTP {error.status_code})"
    _log.warning("[lcmv3] %s (%s:%s): %s", category, provider, model, safe_msg)
    try:
        log_path = db.LOSSLESS_HOME / "provider.log"
        if log_path.exists() and log_path.stat().st_size > 100_000:
            log_path.write_text("")
        with open(log_path, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{category}] {provider}:{model} {safe_msg}\n")
    except OSError:
        pass


def call_llm(prompt: str, cfg: dict, json_mode: bool = False) -> str:
    provider = cfg.get("summaryProvider")
    model = cfg.get("summaryModel", "claude-haiku-4-5-20251001")

    if not provider:
        provider, detected_model = _detect_provider(cfg)
        if not model or model == "claude-haiku-4-5-20251001":
            model = detected_model or model
        _provider_state["auto_detected"] = True
    else:
        _provider_state["auto_detected"] = False

    _provider_state["provider"] = provider
    _provider_state["model"] = model

    if not provider:
        return ""

    should_proceed, breaker_msg = _check_circuit_breaker(cfg)
    if not should_proceed:
        _log.warning("[lcmv3] circuit_breaker: %s", breaker_msg)
        return ""

    if provider == "claude-cli":
        try:
            cli_path = _claude_cli_path or shutil.which("claude") or "claude"
            cli_prompt = prompt + ("\n\nRespond with valid JSON only. No markdown, no commentary." if json_mode else "")
            env = os.environ.copy()
            env.pop("ANTHROPIC_API_KEY", None)
            cli_cwd = os.path.expanduser("~/.lossless-code/.cli-cwd")
            os.makedirs(cli_cwd, exist_ok=True)
            result = subprocess.run([cli_path, "--print", "--model", model],
                input=cli_prompt, capture_output=True, text=True, timeout=120, env=env, cwd=cli_cwd)
            if result.returncode == 0 and result.stdout.strip():
                _provider_state["consecutive_failures"] = 0
                _provider_state["last_error"] = None
                _write_circuit_breaker_state(0, 0)
                return result.stdout.strip()
            _log_provider_error("llm_error", provider, model,
                RuntimeError(result.stderr.strip()[:200] or f"exit code {result.returncode}"))
        except subprocess.TimeoutExpired as e:
            _log_provider_error("timeout", provider, model, e)
        except Exception as e:
            _log_provider_error("llm_error", provider, model, e)

    elif provider == "openai":
        try:
            from openai import OpenAI
            base_url = cfg.get("openaiBaseUrl") or os.environ.get("OPENAI_BASE_URL")
            api_key = os.environ.get("OPENAI_API_KEY") or ("not-needed" if base_url else None)
            if not api_key:
                return ""
            client = OpenAI(base_url=base_url, api_key=api_key)
            kwargs = {"model": model, "max_tokens": 2048, "messages": [{"role": "user", "content": prompt}]}
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = client.chat.completions.create(**kwargs)
            _provider_state["consecutive_failures"] = 0
            _provider_state["last_error"] = None
            _write_circuit_breaker_state(0, 0)
            return resp.choices[0].message.content
        except Exception as e:
            _log_provider_error("llm_error", provider, model, e)

    elif provider == "anthropic":
        try:
            import anthropic
            auth = _get_anthropic_auth()
            base_url = cfg.get("anthropicBaseUrl") or os.environ.get("ANTHROPIC_BASE_URL")
            if base_url:
                auth["base_url"] = base_url
            client = anthropic.Anthropic(**auth)
            resp = client.messages.create(model=model, max_tokens=2048,
                messages=[{"role": "user", "content": prompt}])
            for block in resp.content:
                if hasattr(block, "text"):
                    _provider_state["consecutive_failures"] = 0
                    _provider_state["last_error"] = None
                    _write_circuit_breaker_state(0, 0)
                    return block.text
            return ""
        except Exception as e:
            _log_provider_error("llm_error", provider, model, e)

    elif provider == "local":
        return ""

    return ""


def _extractive_summary(text: str) -> str:
    import math
    import re
    from collections import Counter
    sentences = re.split(r'(?<=[.!?])\s+|\n\n+|\n(?=\[)', text.strip())
    sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 10]
    if len(sentences) <= 5:
        return "\n".join(sentences)
    def tokenize(s):
        return re.findall(r'[a-z0-9_./]+', s.lower())
    sentence_tfs = [Counter(tokenize(s)) for s in sentences]
    df = Counter()
    for tf in sentence_tfs:
        for term in tf:
            df[term] += 1
    n = len(sentences)
    scores = []
    for i, tf in enumerate(sentence_tfs):
        score = sum(count * (math.log((n + 1) / (df[term] + 1)) + 1) for term, count in tf.items())
        if any(kw in sentences[i].lower() for kw in ("decided", "decision", "created", "fixed", "error", "changed")):
            score *= 1.3
        scores.append((score, i))
    k = min(max(5, len(sentences) // 3), 15)
    top_indices = sorted([idx for _, idx in sorted(scores, reverse=True)[:k]])
    return "\n".join(sentences[i] for i in top_indices)


def call_summary_model(text: str, cfg: dict) -> str:
    prompt = ("Summarise the following conversation turns concisely, preserving all "
              "key decisions, facts, file paths, commands, and outputs. Do not omit "
              "anything actionable. Output ONLY the summary, no preamble.\n\n" + text)
    result = call_llm(prompt, cfg)
    if result:
        return result
    return _extractive_summary(text)


def format_messages_for_summary(messages: list[dict], model: str = None) -> str:
    ctx_window = _get_context_window(model) if model else 8192
    max_msg_chars = 4000 if ctx_window < 32_000 else 20_000
    parts = []
    for m in messages:
        content = m["content"]
        if len(content) > max_msg_chars:
            content = content[:max_msg_chars - 200] + "\n... [truncated]"
        prefix = f"[{m['role']}]"
        if m.get("tool_name"):
            prefix = f"[{m['role']}:{m['tool_name']}]"
        parts.append(f"{prefix} {content}")
    return "\n\n".join(parts)


def _compute_dynamic_chunk_size(cfg: dict, pending_count: int) -> int:
    base = cfg.get("chunkSize", 20)
    dyn = cfg.get("dynamicChunkSize", {})
    if not bool(dyn.get("enabled", True)):
        return base
    ceiling = min(int(dyn.get("max", 50)), 500)
    working = max(base, min(ceiling, pending_count // 2))
    return working


def classify_chunk_polarity(messages: list[dict]) -> Optional[str]:
    created = edited = read_only = False
    for m in messages:
        if m.get("role") != "tool" or not m.get("file_path"):
            continue
        tool = m.get("tool_name") or ""
        if tool == "Write":
            created = True
        elif tool in ("Edit", "MultiEdit", "NotebookEdit"):
            edited = True
        elif tool == "Read":
            read_only = True
    if created and edited:
        return "mixed"
    if created:
        return "created"
    if edited:
        return "edited"
    if read_only:
        return "discussed"
    return None


def summarise_messages(session_id: str = None) -> int:
    cfg = db.load_config()
    messages = db.get_unsummarised(session_id)
    if not messages:
        return 0
    chunk_size = _compute_dynamic_chunk_size(cfg, len(messages))
    leaf_target = cfg.get("leafTargetTokens", 2400)
    overage = cfg.get("summaryMaxOverageFactor", 3)
    created = 0
    for i in range(0, len(messages), chunk_size):
        chunk = messages[i:i + chunk_size]
        text = format_messages_for_summary(chunk)
        summary_text = cap_summary_text(call_summary_model(text, cfg), leaf_target, overage)
        summary_id = db.gen_summary_id()
        source_ids = [("message", str(m["id"])) for m in chunk]
        token_count = estimate_tokens(summary_text)
        chunk_sessions = set(m["session_id"] for m in chunk)
        sid = chunk_sessions.pop() if len(chunk_sessions) == 1 else None
        db.store_summary(summary_id=summary_id, content=summary_text, depth=0,
            source_ids=source_ids, session_id=sid, token_count=token_count,
            kind=classify_chunk_polarity(chunk))
        db.mark_summarised([m["id"] for m in chunk])
        created += 1
    return created


def cascade_summaries(session_id: str = None) -> int:
    cfg = db.load_config()
    threshold = cfg.get("depthThreshold", 10)
    max_depth = cfg.get("incrementalMaxDepth", 5)
    condensed_target = cfg.get("condensedTargetTokens", 2000)
    overage = cfg.get("summaryMaxOverageFactor", 3)
    total_created = 0
    depth = 0
    while True:
        if max_depth >= 0 and depth >= max_depth:
            break
        summaries = db.get_summaries_at_depth(depth, session_id)
        if len(summaries) <= threshold:
            break
        chunk_size = cfg.get("chunkSize", 20)
        created = 0
        for i in range(0, len(summaries), chunk_size):
            chunk = summaries[i:i + chunk_size]
            text = "\n\n---\n\n".join(s["content"] for s in chunk)
            summary_text = cap_summary_text(call_summary_model(text, cfg), condensed_target, overage)
            summary_id = db.gen_summary_id()
            source_ids = [("summary", s["id"]) for s in chunk]
            token_count = estimate_tokens(summary_text)
            chunk_sessions = set(s["session_id"] for s in chunk if s["session_id"])
            sid = chunk_sessions.pop() if len(chunk_sessions) == 1 else None
            child_kinds = {s.get("kind") for s in chunk if s.get("kind")}
            cascade_kind = None if not child_kinds else (child_kinds.pop() if len(child_kinds) == 1 else "mixed")
            db.store_summary(summary_id=summary_id, content=summary_text, depth=depth + 1,
                source_ids=source_ids, session_id=sid, token_count=token_count, kind=cascade_kind)
            created += 1
        total_created += created
        depth += 1
    return total_created


def run_full_summarisation(session_id: str = None) -> dict:
    return {"depth_0_created": summarise_messages(session_id),
            "cascaded_created": cascade_summaries(session_id)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run DAG summarisation")
    parser.add_argument("--session", help="Session ID to summarise")
    args = parser.parse_args()
    print(json.dumps(run_full_summarisation(args.session)))
