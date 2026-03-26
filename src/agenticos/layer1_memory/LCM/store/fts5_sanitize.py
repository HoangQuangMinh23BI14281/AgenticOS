"""
FTS5 Query Sanitization — Clean user input for safe FTS5 MATCH queries.

Ported from store/fts5-sanitize.ts.
"""

from __future__ import annotations

import re

# Characters that are FTS5 operators and need escaping
_FTS5_SPECIAL = re.compile(r'["\*\(\)\+\-\:]')
_CONSECUTIVE_SPACES = re.compile(r"\s+")


def sanitize_fts5_query(query: str) -> str:
    """
    Sanitize a user query string for use in SQLite FTS5 MATCH.

    Escapes special FTS5 operator characters and wraps terms
    so they are treated as literal text searches.
    """
    if not query or not query.strip():
        return '""'

    # Remove FTS5 operators
    cleaned = _FTS5_SPECIAL.sub(" ", query)
    # Collapse whitespace
    cleaned = _CONSECUTIVE_SPACES.sub(" ", cleaned).strip()

    if not cleaned:
        return '""'

    # Quote each word for exact matching
    terms = cleaned.split()
    quoted = " ".join(f'"{term}"' for term in terms if term)
    return quoted if quoted else '""'
