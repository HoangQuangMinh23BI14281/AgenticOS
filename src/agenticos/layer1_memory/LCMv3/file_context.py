#!/usr/bin/env python3
"""File context fingerprint lookup for the PreToolUse hook."""

import argparse
import errno
import fcntl
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db
from inject_context import format_file_fingerprint

CACHE_TTL_SECONDS = 60
DEFAULT_LIMIT = 3


def _cache_dir() -> Path: return db.VAULT_DIR / "cache"
def _cache_file() -> Path: return _cache_dir() / "file_fingerprints.json"
def _inflight_dir() -> Path: return _cache_dir() / "inflight"
def _inflight_sentinel(fp): return _inflight_dir() / hashlib.sha1(fp.encode("utf-8")).hexdigest()
def _ensure_cache_dirs():
    _cache_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    _inflight_dir().mkdir(parents=True, exist_ok=True, mode=0o700)


def _load_cache() -> dict:
    path = _cache_file()
    if not path.exists(): return {}
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError: return {}
    try:
        try: fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except (OSError, IOError): return {}
        try:
            raw = os.read(fd, 1024 * 1024)
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError, OSError): return {}
        finally: fcntl.flock(fd, fcntl.LOCK_UN)
    finally: os.close(fd)


def _store_cache(cache: dict):
    _ensure_cache_dirs()
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".fp.", suffix=".json", dir=str(_cache_dir()))
    try: os.write(tmp_fd, json.dumps(cache).encode("utf-8"))
    finally: os.close(tmp_fd)
    os.chmod(tmp_path, 0o600)
    path = _cache_file()
    if path.exists():
        try:
            real_fd = os.open(str(path), os.O_WRONLY)
            try:
                fcntl.flock(real_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.replace(tmp_path, str(path))
            except (OSError, IOError): os.unlink(tmp_path); return
            finally: fcntl.flock(real_fd, fcntl.LOCK_UN); os.close(real_fd)
            return
        except OSError: pass
    os.replace(tmp_path, str(path))


def _claim_inflight(file_path):
    _ensure_cache_dirs()
    sentinel = _inflight_sentinel(file_path)
    try:
        return os.open(str(sentinel), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as e:
        if e.errno == errno.EEXIST:
            try:
                if time.time() - sentinel.stat().st_mtime > 30:
                    sentinel.unlink(missing_ok=True)
                    return _claim_inflight(file_path)
            except OSError: pass
        return None


def _release_inflight(fd, file_path):
    try: os.close(fd)
    except OSError: pass
    try: _inflight_sentinel(file_path).unlink(missing_ok=True)
    except OSError: pass


def _cold_lookup(file_path, limit):
    if not db.VAULT_DB.exists(): return []
    try:
        conn = sqlite3.connect(f"file:{db.VAULT_DB}?mode=ro", uri=True, timeout=1)
    except sqlite3.OperationalError: return []
    conn.row_factory = sqlite3.Row
    try: conn.execute("PRAGMA busy_timeout = 100")
    except sqlite3.OperationalError: pass
    try:
        rows = conn.execute("""
            WITH RECURSIVE ancestors(summary_id, hop) AS (
                SELECT ss.summary_id, 0 FROM summary_sources ss
                JOIN messages m ON ss.source_type = 'message' AND CAST(ss.source_id AS INTEGER) = m.id
                WHERE m.file_path = ?
                UNION
                SELECT ss.summary_id, a.hop + 1 FROM ancestors a
                JOIN summary_sources ss ON ss.source_type = 'summary' AND ss.source_id = a.summary_id
                WHERE a.hop < 16
            )
            SELECT s.* FROM summaries s
            WHERE s.id IN (SELECT DISTINCT summary_id FROM ancestors)
              AND COALESCE(s.consolidated, 0) = 0
            ORDER BY s.created_at DESC LIMIT ?
        """, (file_path, limit)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.DatabaseError: return []
    finally: conn.close()


def get_file_fingerprint(file_path: str, limit: int = DEFAULT_LIMIT) -> str:
    if not file_path: return ""
    cfg = db.load_config()
    if not cfg.get("fileContextEnabled", False): return ""
    now = time.time()
    cache = _load_cache()
    entry = cache.get(file_path)
    if entry and (now - entry.get("ts", 0)) < CACHE_TTL_SECONDS:
        return entry.get("output", "")
    claim = _claim_inflight(file_path)
    if claim is None:
        return entry.get("output", "") if entry else ""
    try:
        summaries = _cold_lookup(file_path, limit)
        output = format_file_fingerprint(file_path, summaries)
        cache[file_path] = {"ts": now, "output": output}
        _store_cache(cache)
        return output
    finally:
        _release_inflight(claim, file_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()
    out = get_file_fingerprint(args.file, limit=args.limit)
    if out: sys.stdout.write(out)
