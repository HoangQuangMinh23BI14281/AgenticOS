"""Dream cycle log entries for the LCMv3 vault."""

import hashlib
import os
import time
from typing import Optional


def project_hash(working_dir: str) -> str:
    return hashlib.sha256(os.path.abspath(working_dir).encode()).hexdigest()[:16]


def get_last_dream(project_hash_val: str) -> Optional[dict]:
    from . import get_db
    row = get_db().execute(
        "SELECT * FROM dream_log WHERE project_hash = ? ORDER BY dreamed_at DESC LIMIT 1",
        (project_hash_val,)).fetchone()
    return dict(row) if row else None


def store_dream_log(
    project_hash_val: str, scope: str, patterns_found: int,
    consolidations: int, sessions_analyzed: int,
    report_path: str = "", dreamed_at: Optional[int] = None,
) -> int:
    from . import get_db
    db = get_db()
    now = dreamed_at if dreamed_at is not None else int(time.time())
    cur = db.execute(
        """INSERT INTO dream_log (project_hash, scope, dreamed_at, patterns_found,
           consolidations, sessions_analyzed, report_path) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (project_hash_val, scope, now, patterns_found, consolidations, sessions_analyzed, report_path))
    db.commit()
    return cur.lastrowid
