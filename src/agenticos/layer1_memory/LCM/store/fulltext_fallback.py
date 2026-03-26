"""
Full-Text Search Fallback — LIKE-based search for non-FTS5 or CJK queries.

Ported from store/full-text-fallback.ts.
"""

from __future__ import annotations

import re
import unicodedata

# CJK Unicode ranges
_CJK_RANGES = re.compile(
    "["
    "\u3000-\u303f"   # CJK Symbols and Punctuation
    "\u3040-\u309f"   # Hiragana
    "\u30a0-\u30ff"   # Katakana
    "\u3400-\u4dbf"   # CJK Unified Ideographs Extension A
    "\u4e00-\u9fff"   # CJK Unified Ideographs
    "\uf900-\ufaff"   # CJK Compatibility Ideographs
    "\ufe30-\ufe4f"   # CJK Compatibility Forms
    "\U00020000-\U0002a6df"  # CJK Extension B
    "\U0002a700-\U0002b73f"  # CJK Extension C
    "\U0002b740-\U0002b81f"  # CJK Extension D
    "\U0002b820-\U0002ceaf"  # CJK Extension E
    "\U0002ceb0-\U0002ebef"  # CJK Extension F
    "\U00030000-\U0003134f"  # CJK Extension G
    "]"
)


def contains_cjk(text: str) -> bool:
    """Check if text contains CJK characters."""
    return bool(_CJK_RANGES.search(text))


def build_like_search_plan(
    column: str,
    query: str,
) -> dict:
    """
    Build a LIKE-based search plan for SQL queries.

    Returns a dict with:
      - terms: list of search terms
      - where: list of WHERE clause fragments
      - args: list of bind parameters
    """
    terms = [t.strip() for t in query.split() if t.strip()]
    if not terms:
        return {"terms": [], "where": [], "args": []}

    where_parts = []
    args = []
    for term in terms:
        # Escape SQL LIKE special characters
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where_parts.append(f"{column} LIKE ? ESCAPE '\\'")
        args.append(f"%{escaped}%")

    return {"terms": terms, "where": where_parts, "args": args}


def create_fallback_snippet(
    content: str,
    query: str,
    context_chars: int = 80,
) -> str:
    """
    Create a snippet from content around the first match of the query.

    Returns a substring centered on the first occurrence, with
    ellipsis markers for truncation.
    """
    if not content or not query:
        return content[:200] if content else ""

    lower_content = content.lower()
    lower_query = query.lower()

    # Find first occurrence of any query term
    terms = lower_query.split()
    best_pos = -1
    for term in terms:
        pos = lower_content.find(term)
        if pos >= 0 and (best_pos < 0 or pos < best_pos):
            best_pos = pos

    if best_pos < 0:
        return content[:200]

    start = max(0, best_pos - context_chars)
    end = min(len(content), best_pos + len(query) + context_chars)

    snippet = content[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."

    return snippet
