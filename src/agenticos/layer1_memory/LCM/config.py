"""
LCM Configuration — Pydantic model with 3-tier precedence.

Precedence (highest → lowest):
  1. Environment variables (LCM_*)
  2. Plugin config dict
  3. Hardcoded defaults
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def _env_int(key: str) -> int | None:
    """Safely extract an integer from an environment variable."""
    val = os.environ.get(key)
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _env_float(key: str) -> float | None:
    """Safely extract a float from an environment variable."""
    val = os.environ.get(key)
    if val is None:
        return None
    try:
        f = float(val)
        return f if f == f else None  # NaN check
    except (ValueError, TypeError):
        return None


def _env_bool(key: str) -> bool | None:
    """Safely extract a boolean from an environment variable."""
    val = os.environ.get(key)
    if val is None:
        return None
    return val.lower() not in ("false", "0", "no")


def _env_str(key: str) -> str | None:
    """Safely extract a trimmed non-empty string from env."""
    val = os.environ.get(key)
    if val is None:
        return None
    val = val.strip()
    return val if val else None


def _env_str_list(key: str) -> list[str] | None:
    """Safely extract a comma-separated list of strings from env."""
    val = os.environ.get(key)
    if val is None:
        return None
    return [s.strip() for s in val.split(",") if s.strip()]


def _pc_int(pc: dict[str, Any], *keys: str) -> int | None:
    """Extract an integer from plugin config, trying multiple keys."""
    for key in keys:
        val = pc.get(key)
        if isinstance(val, int):
            return val
        if isinstance(val, str):
            try:
                return int(val)
            except (ValueError, TypeError):
                continue
    return None


def _pc_float(pc: dict[str, Any], *keys: str) -> float | None:
    """Extract a float from plugin config, trying multiple keys."""
    for key in keys:
        val = pc.get(key)
        if isinstance(val, (int, float)) and val == val:
            return float(val)
        if isinstance(val, str):
            try:
                f = float(val)
                return f if f == f else None
            except (ValueError, TypeError):
                continue
    return None


def _pc_bool(pc: dict[str, Any], key: str) -> bool | None:
    """Extract a boolean from plugin config."""
    val = pc.get(key)
    if isinstance(val, bool):
        return val
    if val == "true":
        return True
    if val == "false":
        return False
    return None


def _pc_str(pc: dict[str, Any], *keys: str) -> str | None:
    """Extract a trimmed non-empty string from plugin config."""
    for key in keys:
        val = pc.get(key)
        if isinstance(val, str):
            val = val.strip()
            if val:
                return val
    return None


def _first(*values: Any) -> Any:
    """Return the first non-None value, like JS ?? chaining."""
    for v in values:
        if v is not None:
            return v
    return None


class LcmConfig(BaseModel):
    """LCM configuration with all tuning parameters."""

    enabled: bool = True
    database_path: str = Field(
        default_factory=lambda: str(Path.home() / ".agenticos" / "lcm.db")
    )

    # Session patterns
    ignore_session_patterns: list[str] = Field(default_factory=list)
    stateless_session_patterns: list[str] = Field(default_factory=list)
    skip_stateless_sessions: bool = True

    # Thresholds
    context_threshold: float = 0.55
    fresh_tail_count: int = 32

    # Compaction fanout
    leaf_min_fanout: int = 8
    condensed_min_fanout: int = 4
    condensed_min_fanout_hard: int = 2
    incremental_max_depth: int = 0

    # Token budgets
    leaf_chunk_tokens: int = 20_000
    leaf_target_tokens: int = 1_200
    condensed_target_tokens: int = 2_000
    max_expand_tokens: int = 4_000
    large_file_token_threshold: int = 25_000

    # LLM provider overrides
    summary_provider: str = ""
    summary_model: str = ""
    large_file_summary_provider: str = ""
    large_file_summary_model: str = ""
    expansion_provider: str = ""
    expansion_model: str = ""

    # Behavior
    autocompact_disabled: bool = False
    timezone: str = "UTC"
    prune_heartbeat_ok: bool = False

    # Compaction engine
    max_rounds: int = 10


def resolve_lcm_config(
    plugin_config: dict[str, Any] | None = None,
) -> LcmConfig:
    """
    Resolve LCM configuration with three-tier precedence:
      1. Environment variables (highest)
      2. Plugin config dict
      3. Hardcoded defaults (lowest)
    """
    pc = plugin_config or {}

    def _resolve(env_key: str, pc_keys: list[str], default: Any, type_fn: Any) -> Any:
        # 1. Env
        env_val = os.environ.get(env_key)
        if env_val is not None:
            if type_fn == _env_bool: return _env_bool(env_key)
            if type_fn == _env_int: return _env_int(env_key)
            if type_fn == _env_float: return _env_float(env_key)
            if type_fn == _env_str_list: return _env_str_list(env_key)
            return _env_str(env_key)
        # 2. Plugin Config
        for k in pc_keys:
            val = pc.get(k)
            if val is not None:
                if type_fn == _env_bool: return _pc_bool(pc, k)
                if type_fn == _env_int: return _pc_int(pc, k)
                if type_fn == _env_float: return _pc_float(pc, k)
                return _pc_str(pc, k)
        return default

    return LcmConfig(
        enabled=_resolve("LCM_ENABLED", ["enabled"], True, _env_bool),
        database_path=(
            _env_str("LCM_DATABASE_PATH")
            or _pc_str(pc, "dbPath", "databasePath")
            or str(Path.home() / ".agenticos" / "lcm.db")
        ),
        ignore_session_patterns=_resolve("LCM_IGNORE_SESSION_PATTERNS", ["ignoreSessionPatterns"], [], _env_str_list),
        stateless_session_patterns=_resolve("LCM_STATELESS_SESSION_PATTERNS", ["statelessSessionPatterns"], [], _env_str_list),
        skip_stateless_sessions=_resolve("LCM_SKIP_STATELESS_SESSIONS", ["skipStatelessSessions"], True, _env_bool),
        context_threshold=_resolve("LCM_CONTEXT_THRESHOLD", ["contextThreshold"], 0.55, _env_float),
        fresh_tail_count=_resolve("LCM_FRESH_TAIL_COUNT", ["freshTailCount"], 32, _env_int),
        leaf_min_fanout=_resolve("LCM_LEAF_MIN_FANOUT", ["leafMinFanout"], 8, _env_int),
        condensed_min_fanout=_resolve("LCM_CONDENSED_MIN_FANOUT", ["condensedMinFanout"], 4, _env_int),
        condensed_min_fanout_hard=_resolve("LCM_CONDENSED_MIN_FANOUT_HARD", ["condensedMinFanoutHard"], 2, _env_int),
        incremental_max_depth=_resolve("LCM_INCREMENTAL_MAX_DEPTH", ["incrementalMaxDepth"], 0, _env_int),
        leaf_chunk_tokens=_resolve("LCM_LEAF_CHUNK_TOKENS", ["leafChunkTokens"], 20_000, _env_int),
        leaf_target_tokens=_resolve("LCM_LEAF_TARGET_TOKENS", ["leafTargetTokens"], 1_200, _env_int),
        condensed_target_tokens=_resolve("LCM_CONDENSED_TARGET_TOKENS", ["condensedTargetTokens"], 2_000, _env_int),
        max_expand_tokens=_resolve("LCM_MAX_EXPAND_TOKENS", ["maxExpandTokens"], 4_000, _env_int),
        large_file_token_threshold=_resolve("LCM_LARGE_FILE_TOKEN_THRESHOLD", ["largeFileThresholdTokens", "largeFileTokenThreshold"], 25_000, _env_int),
        summary_provider=_resolve("LCM_SUMMARY_PROVIDER", ["summaryProvider"], "", _env_str),
        summary_model=_resolve("LCM_SUMMARY_MODEL", ["summaryModel"], "", _env_str),
        large_file_summary_provider=_resolve("LCM_LARGE_FILE_SUMMARY_PROVIDER", ["largeFileSummaryProvider"], "", _env_str),
        large_file_summary_model=_resolve("LCM_LARGE_FILE_SUMMARY_MODEL", ["largeFileSummaryModel"], "", _env_str),
        expansion_provider=_resolve("LCM_EXPANSION_PROVIDER", ["expansionProvider"], "", _env_str),
        expansion_model=_resolve("LCM_EXPANSION_MODEL", ["expansionModel"], "", _env_str),
        autocompact_disabled=_resolve("LCM_AUTOCOMPACT_DISABLED", ["autocompactDisabled"], False, _env_bool),
        timezone=(os.environ.get("TZ", "").strip() or _pc_str(pc, "timezone") or "UTC"),
        prune_heartbeat_ok=_resolve("LCM_PRUNE_HEARTBEAT_OK", ["pruneHeartbeatOk"], False, _env_bool),
    )
