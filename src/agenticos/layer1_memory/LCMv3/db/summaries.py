"""Summary nodes and the summary→source DAG."""

import time
import uuid
from typing import Optional


def gen_summary_id() -> str:
    return f"sum_{uuid.uuid4().hex[:12]}"


def store_summary(
    summary_id: str, content: str, depth: int,
    source_ids: list[tuple[str, str]],
    session_id: Optional[str] = None,
    token_count: Optional[int] = None,
    kind: Optional[str] = None,
) -> None:
    from . import get_db
    db = get_db()
    now = int(time.time())
    db.execute(
        """INSERT INTO summaries (id, session_id, content, depth, token_count, created_at, kind)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (summary_id, session_id, content, depth, token_count, now, kind),
    )
    db.executemany(
        "INSERT INTO summary_sources (summary_id, source_type, source_id) VALUES (?, ?, ?)",
        [(summary_id, stype, sid) for stype, sid in source_ids],
    )
    db.commit()


def get_summary(summary_id: str) -> Optional[dict]:
    from . import get_db
    row = get_db().execute("SELECT * FROM summaries WHERE id = ?", (summary_id,)).fetchone()
    return dict(row) if row else None


def get_summary_sources(summary_id: str) -> list[dict]:
    from . import get_db
    rows = get_db().execute(
        "SELECT * FROM summary_sources WHERE summary_id = ?", (summary_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_summaries_at_depth(depth: int, session_id: Optional[str] = None) -> list[dict]:
    from . import get_db
    db = get_db()
    if session_id:
        rows = db.execute(
            "SELECT * FROM summaries WHERE depth = ? AND session_id = ? ORDER BY created_at",
            (depth, session_id)).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM summaries WHERE depth = ? ORDER BY created_at", (depth,)).fetchall()
    return [dict(r) for r in rows]


def get_top_summaries(limit: int = 5, session_id: Optional[str] = None) -> list[dict]:
    from . import get_db
    db = get_db()
    if session_id:
        rows = db.execute(
            "SELECT * FROM summaries WHERE session_id = ? ORDER BY depth DESC, created_at DESC LIMIT ?",
            (session_id, limit)).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM summaries ORDER BY depth DESC, created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_summaries_since(timestamp: int, working_dir: str = None, limit: int = 2000) -> list[dict]:
    from . import get_db
    db = get_db()
    if working_dir:
        rows = db.execute(
            """SELECT s.* FROM summaries s JOIN sessions sess ON s.session_id = sess.session_id
               WHERE s.created_at > ? AND sess.working_dir = ? ORDER BY s.created_at LIMIT ?""",
            (timestamp, working_dir, limit)).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM summaries WHERE created_at > ? ORDER BY created_at LIMIT ?",
            (timestamp, limit)).fetchall()
    return [dict(r) for r in rows]


def get_summary_ids_since(timestamp: int, working_dir: str = None, limit: int = 2000) -> list[str]:
    from . import get_db
    db = get_db()
    if working_dir:
        rows = db.execute(
            """SELECT s.id FROM summaries s JOIN sessions sess ON s.session_id = sess.session_id
               WHERE s.created_at > ? AND sess.working_dir = ? ORDER BY s.created_at LIMIT ?""",
            (timestamp, working_dir, limit)).fetchall()
    else:
        rows = db.execute(
            "SELECT id FROM summaries WHERE created_at > ? ORDER BY created_at LIMIT ?",
            (timestamp, limit)).fetchall()
    return [r[0] for r in rows]


def get_summaries_by_ids(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    if len(ids) > 900:
        results = []
        for i in range(0, len(ids), 900):
            results.extend(get_summaries_by_ids(ids[i:i + 900]))
        return results
    from . import get_db
    ph = ",".join("?" for _ in ids)
    rows = get_db().execute(f"SELECT * FROM summaries WHERE id IN ({ph}) ORDER BY created_at", ids).fetchall()
    return [dict(r) for r in rows]


def get_overlapping_summaries(depth: int, threshold: float = 0.5) -> list[tuple[str, str]]:
    from . import get_db
    rows = get_db().execute(
        """SELECT ss.summary_id, ss.source_id FROM summary_sources ss
           JOIN summaries s ON s.id = ss.summary_id
           WHERE s.depth = ? AND s.consolidated = 0""", (depth,)).fetchall()
    if not rows:
        return []
    source_sets: dict[str, set[str]] = {}
    for r in rows:
        source_sets.setdefault(r["summary_id"], set()).add(r["source_id"])
    if len(source_sets) < 2:
        return []
    pairs = []
    ids = list(source_sets.keys())
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            sa, sb = source_sets[a], source_sets[b]
            if sa and sb:
                overlap = len(sa & sb)
                mn = min(len(sa), len(sb))
                if mn > 0 and overlap / mn > threshold:
                    pairs.append((a, b))
    return pairs


def mark_consolidated(summary_ids: list[str]) -> None:
    from . import get_db
    db = get_db()
    db.executemany("UPDATE summaries SET consolidated = 1 WHERE id = ?", [(sid,) for sid in summary_ids])
    db.commit()


def get_summaries_for_file(file_path: str, limit: int = 3) -> list[dict]:
    from . import get_db
    rows = get_db().execute("""
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


def get_max_summary_depth() -> int:
    from . import get_db
    row = get_db().execute("SELECT COALESCE(MAX(depth), 0) FROM summaries").fetchone()
    return row[0] if row else 0
