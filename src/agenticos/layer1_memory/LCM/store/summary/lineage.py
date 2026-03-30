"""
Summary Lineage — DAG operations, parent-child linking, and recursive CTE traversal.
Part of the LCM SummaryStore decomposition.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from typing import Any

from ...types import SummaryRecord, SummarySubtreeNode
from .crud import SUMMARY_COLS, safe_int, to_summary


def link_summary_to_messages(
    conn: sqlite3.Connection, summary_id: str, message_ids: list[int]
) -> None:
    if not message_ids:
        return
    for idx, mid in enumerate(message_ids):
        conn.execute(
            """INSERT INTO summary_messages (summary_id, message_id, ordinal)
               VALUES (?, ?, ?)
               ON CONFLICT (summary_id, message_id) DO NOTHING""",
            (summary_id, mid, idx),
        )


def link_summary_to_parents(
    conn: sqlite3.Connection, child_id: str, parent_id: str, ordinal: int = 0
) -> None:
    """Link a child node to its parent in the DAG."""
    conn.execute(
        """INSERT INTO summary_parents (summary_id, parent_summary_id, ordinal)
           VALUES (?, ?, ?)
           ON CONFLICT (summary_id, parent_summary_id) DO NOTHING""",
        (child_id, parent_id, ordinal),
    )


def fetch_subtree(
    conn: sqlite3.Connection, summary_id: str, max_depth: int = 20
) -> list[SummarySubtreeNode]:
    """Recursive subtree traversal using CTE."""
    sql = f"""WITH RECURSIVE subtree(summary_id, parent_summary_id, depth_from_root, path) AS (
               SELECT ?, NULL, 0, ''
               UNION ALL
               SELECT sp.summary_id, sp.parent_summary_id,
                      subtree.depth_from_root + 1,
                      CASE
                          WHEN subtree.path = '' THEN printf('%04d', sp.ordinal)
                          ELSE subtree.path || '.' || printf('%04d', sp.ordinal)
                      END
               FROM summary_parents sp
               JOIN subtree ON sp.parent_summary_id = subtree.summary_id
               WHERE subtree.depth_from_root < ?
           )
           SELECT {SUMMARY_COLS},
                  subtree.depth_from_root, subtree.parent_summary_id,
                  subtree.path,
                  (SELECT COUNT(*) FROM summary_parents sp2
                   WHERE sp2.parent_summary_id = s.summary_id) AS child_count
           FROM subtree
           JOIN summaries s ON s.summary_id = subtree.summary_id
           ORDER BY subtree.depth_from_root ASC, subtree.path ASC, s.created_at ASC"""

    rows = conn.execute(sql, (summary_id, max_depth)).fetchall()

    node_map: dict[str, SummarySubtreeNode] = {}
    for r in rows:
        sid = r["summary_id"]
        pid = r["parent_summary_id"]

        if sid not in node_map:
            base = to_summary(r)
            node = SummarySubtreeNode(
                **dataclasses.asdict(base),
                depth_from_root=max(0, int(r["depth_from_root"] or 0)),
                parent_ids=[pid] if pid else [],
                parent_summary_id=pid,
                path=r["path"] if isinstance(r["path"], str) else "",
                child_count=safe_int(r["child_count"]),
            )
            node_map[sid] = node
        else:
            if pid and pid not in node_map[sid].parent_ids:
                node_map[sid].parent_ids.append(pid)
    return list(node_map.values())


def fetch_descendant_metadata(
    conn: sqlite3.Connection, summary_ids: list[str]
) -> tuple[int, int]:
    """Calculate combined descendant count and tokens for a set of child summaries."""
    if not summary_ids:
        return 0, 0
    
    # Use placeholders for summary_ids
    placeholders = ",".join(["?"] * len(summary_ids))
    sql = f"""SELECT 
                COUNT(*) + SUM(descendant_count) as total_count,
                TOTAL(token_count) + TOTAL(descendant_token_count) as total_tokens
              FROM summaries 
              WHERE summary_id IN ({placeholders})"""
    
    row = conn.execute(sql, summary_ids).fetchone()
    if not row or row["total_count"] is None:
        return 0, 0
    
    return int(row["total_count"]), int(row["total_tokens"])
