"""FTS5 full-text search over messages and summaries."""

import re

_FTS5_SPECIAL = re.compile(r'[*?()\\\"^+\-~:]')


def escape_fts5_query(query: str) -> str:
    cleaned = _FTS5_SPECIAL.sub(" ", query)
    words = cleaned.split()
    if not words:
        return ""
    return " ".join(f'"{w}"' for w in words)


def search_messages(query: str, limit: int = 20) -> list[dict]:
    from . import get_db
    escaped = escape_fts5_query(query)
    if not escaped:
        return []
    rows = get_db().execute(
        """SELECT m.*, rank FROM messages_fts f
           JOIN messages m ON m.id = f.rowid
           WHERE messages_fts MATCH ? ORDER BY rank LIMIT ?""",
        (escaped, limit)).fetchall()
    return [dict(r) for r in rows]


def search_summaries(query: str, limit: int = 20) -> list[dict]:
    from . import get_db
    escaped = escape_fts5_query(query)
    if not escaped:
        return []
    rows = get_db().execute(
        """SELECT s.*, rank FROM summaries_fts f
           JOIN summaries s ON s.rowid = f.rowid
           WHERE summaries_fts MATCH ? ORDER BY rank LIMIT ?""",
        (escaped, limit)).fetchall()
    return [dict(r) for r in rows]


def search_all(query: str, limit: int = 20) -> dict:
    return {"messages": search_messages(query, limit), "summaries": search_summaries(query, limit)}
