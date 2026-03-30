"""
LCM Migration — Backfill functions for depths and metadata.
"""

from __future__ import annotations

import logging
import sqlite3

from .utils import parse_dt, iso_or_none

logger = logging.getLogger("lcm.db.migration.backfill")


def backfill_summary_depths(conn: sqlite3.Connection) -> None:
    """Compute and set correct depth values for all summaries."""
    # Leaves are always depth 0.
    conn.execute("UPDATE summaries SET depth = 0 WHERE kind = 'leaf'")

    rows = conn.execute(
        "SELECT DISTINCT conversation_id FROM summaries WHERE kind = 'condensed'"
    ).fetchall()

    if not rows:
        return

    for row in rows:
        conv_id = row["conversation_id"]

        summaries = conn.execute(
            "SELECT summary_id, kind, depth FROM summaries WHERE conversation_id = ?",
            (conv_id,),
        ).fetchall()

        depth_by_id: dict[str, int] = {}
        unresolved = set()

        for s in summaries:
            if s["kind"] == "leaf":
                depth_by_id[s["summary_id"]] = 0
            else:
                unresolved.add(s["summary_id"])

        edges = conn.execute(
            """SELECT summary_id, parent_summary_id
               FROM summary_parents
               WHERE summary_id IN (
                   SELECT summary_id FROM summaries
                   WHERE conversation_id = ? AND kind = 'condensed'
               )""",
            (conv_id,),
        ).fetchall()

        parents_map: dict[str, list[str]] = {}
        for e in edges:
            parents_map.setdefault(e["summary_id"], []).append(
                e["parent_summary_id"]
            )

        while unresolved:
            progressed = False
            for sid in list(unresolved):
                parent_ids = parents_map.get(sid, [])
                if not parent_ids:
                    depth_by_id[sid] = 1
                    unresolved.discard(sid)
                    progressed = True
                    continue

                parent_depths = [depth_by_id.get(pid) for pid in parent_ids]
                if any(d is None for d in parent_depths):
                    continue

                depth_by_id[sid] = max(d for d in parent_depths if d is not None) + 1
                unresolved.discard(sid)
                progressed = True

            if not progressed:
                for sid in unresolved:
                    depth_by_id[sid] = 1
                unresolved.clear()

        for s in summaries:
            depth = depth_by_id.get(s["summary_id"])
            if depth is not None:
                conn.execute(
                    "UPDATE summaries SET depth = ? WHERE summary_id = ?",
                    (depth, s["summary_id"]),
                )

    logger.debug("Backfilled summary depths")


def backfill_summary_metadata(conn: sqlite3.Connection) -> None:
    """Compute time ranges and descendant counts for summaries."""
    conv_rows = conn.execute(
        "SELECT DISTINCT conversation_id FROM summaries"
    ).fetchall()

    if not conv_rows:
        return

    for conv_row in conv_rows:
        conv_id = conv_row["conversation_id"]

        summaries = conn.execute(
            """SELECT summary_id, kind, depth, token_count, created_at
               FROM summaries
               WHERE conversation_id = ?
               ORDER BY depth ASC, created_at ASC""",
            (conv_id,),
        ).fetchall()

        if not summaries:
            continue

        # Leaf time ranges from linked messages
        leaf_ranges = conn.execute(
            """SELECT sm.summary_id,
                       MIN(m.created_at) AS earliest_at,
                       MAX(m.created_at) AS latest_at,
                       COALESCE(SUM(m.token_count), 0) AS source_message_token_count
                FROM summary_messages sm
                JOIN messages m ON m.message_id = sm.message_id
                JOIN summaries s ON s.summary_id = sm.summary_id
                WHERE s.conversation_id = ? AND s.kind = 'leaf'
                GROUP BY sm.summary_id""",
            (conv_id,),
        ).fetchall()

        leaf_range_map = {
            r["summary_id"]: {
                "earliest": r["earliest_at"],
                "latest": r["latest_at"],
                "src_tokens": r["source_message_token_count"] or 0,
            }
            for r in leaf_ranges
        }

        # Parent edges
        edges = conn.execute(
            """SELECT summary_id, parent_summary_id
               FROM summary_parents
               WHERE summary_id IN (
                   SELECT summary_id FROM summaries WHERE conversation_id = ?
               )""",
            (conv_id,),
        ).fetchall()

        parents_map: dict[str, list[str]] = {}
        for e in edges:
            parents_map.setdefault(e["summary_id"], []).append(
                e["parent_summary_id"]
            )

        token_by_id = {
            s["summary_id"]: max(0, int(s["token_count"] or 0))
            for s in summaries
        }

        meta: dict[str, dict] = {}

        for s in summaries:
            sid = s["summary_id"]
            fallback = parse_dt(s["created_at"])

            if s["kind"] == "leaf":
                lr = leaf_range_map.get(sid)
                meta[sid] = {
                    "earliest": parse_dt(lr["earliest"] if lr else s["created_at"]) or fallback,
                    "latest": parse_dt(lr["latest"] if lr else s["created_at"]) or fallback,
                    "desc_count": 0,
                    "desc_tokens": 0,
                    "src_tokens": max(0, lr["src_tokens"]) if lr else 0,
                }
                continue

            parent_ids = parents_map.get(sid, [])
            if not parent_ids:
                meta[sid] = {
                    "earliest": fallback,
                    "latest": fallback,
                    "desc_count": 0,
                    "desc_tokens": 0,
                    "src_tokens": 0,
                }
                continue

            earliest = None
            latest = None
            desc_count = 0
            desc_tokens = 0
            src_tokens = 0

            for pid in parent_ids:
                pm = meta.get(pid)
                if not pm:
                    continue
                pe = pm["earliest"]
                if pe and (earliest is None or pe < earliest):
                    earliest = pe
                pl = pm["latest"]
                if pl and (latest is None or pl > latest):
                    latest = pl
                desc_count += max(0, pm["desc_count"]) + 1
                desc_tokens += max(0, token_by_id.get(pid, 0)) + max(0, pm["desc_tokens"])
                src_tokens += max(0, pm["src_tokens"])

            meta[sid] = {
                "earliest": earliest or fallback,
                "latest": latest or fallback,
                "desc_count": max(0, desc_count),
                "desc_tokens": max(0, desc_tokens),
                "src_tokens": max(0, src_tokens),
            }

        for s in summaries:
            m = meta.get(s["summary_id"])
            if not m:
                continue
            conn.execute(
                """UPDATE summaries
                   SET earliest_at = ?, latest_at = ?,
                       descendant_count = ?, descendant_token_count = ?,
                       source_message_token_count = ?
                   WHERE summary_id = ?""",
                (
                    iso_or_none(m["earliest"]),
                    iso_or_none(m["latest"]),
                    max(0, m["desc_count"]),
                    max(0, m["desc_tokens"]),
                    max(0, m["src_tokens"]),
                    s["summary_id"],
                ),
            )

    logger.debug("Backfilled summary metadata")


def backfill_tool_call_columns(conn: sqlite3.Connection) -> None:
    """
    Backfill tool_call_id, tool_name, tool_input from metadata JSON.
    """
    # 1. Backfill tool_call_id
    conn.execute("""
        UPDATE message_parts
        SET tool_call_id = COALESCE(
            json_extract(metadata, '$.toolCallId'),
            json_extract(metadata, '$.raw.id'),
            json_extract(metadata, '$.raw.call_id'),
            json_extract(metadata, '$.raw.toolCallId'),
            json_extract(metadata, '$.raw.tool_call_id')
        )
        WHERE tool_call_id IS NULL
          AND metadata IS NOT NULL
          AND COALESCE(
              json_extract(metadata, '$.toolCallId'),
              json_extract(metadata, '$.raw.id'),
              json_extract(metadata, '$.raw.call_id'),
              json_extract(metadata, '$.raw.toolCallId'),
              json_extract(metadata, '$.raw.tool_call_id')
          ) IS NOT NULL
    """)

    # 2. Backfill tool_name
    conn.execute("""
        UPDATE message_parts
        SET tool_name = COALESCE(
            json_extract(metadata, '$.toolName'),
            json_extract(metadata, '$.raw.name'),
            json_extract(metadata, '$.raw.toolName'),
            json_extract(metadata, '$.raw.tool_name')
        )
        WHERE tool_name IS NULL
          AND metadata IS NOT NULL
          AND COALESCE(
              json_extract(metadata, '$.toolName'),
              json_extract(metadata, '$.raw.name'),
              json_extract(metadata, '$.raw.toolName'),
              json_extract(metadata, '$.raw.tool_name')
          ) IS NOT NULL
    """)

    # 3. Backfill tool_input
    conn.execute("""
        UPDATE message_parts
        SET tool_input = COALESCE(
            json_extract(metadata, '$.raw.input'),
            json_extract(metadata, '$.raw.arguments'),
            json_extract(metadata, '$.raw.toolInput')
        )
        WHERE tool_input IS NULL
          AND metadata IS NOT NULL
          AND COALESCE(
              json_extract(metadata, '$.raw.input'),
              json_extract(metadata, '$.raw.arguments'),
              json_extract(metadata, '$.raw.toolInput')
          ) IS NOT NULL
    """)

    logger.debug("Backfilled tool call columns")
