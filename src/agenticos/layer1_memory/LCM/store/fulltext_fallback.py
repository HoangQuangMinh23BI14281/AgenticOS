"""
LCM Full-text Search Fallback Utilities.

Provides CJK detection, FTS5 sanitization, and LIKE-based search planning
for SQLite environments without FTS5 or for specific character sets.
"""

from __future__ import annotations

import re
from typing import Any

# ── Helpers ───────────────────────────────────────────────────────────────────

def contains_cjk(text: str) -> bool:
    """Detect if string contains Chinese/Japanese/Korean characters."""
    # Common CJK Unified Ideographs block
    return any('\u4e00' <= char <= '\u9fff' for char in text)

def sanitize_fts5_query(query: str) -> str:
    """Sanitize query for FTS5 MATCH to prevent injection or syntax errors."""
    clean = re.sub(r'[^a-zA-Z0-9\sÀ-ÿ\u4e00-\u9fff]', ' ', query)
    return " ".join(clean.split())

def build_like_search_plan(column: str, query: str) -> dict[str, Any]:
    """
    Build WHERE and ARGS for a multi-term LIKE search.
    Example: term1 AND term2 AND term3.
    """
    clean = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', query)
    terms = [t for t in clean.split() if len(t) > 0]
    
    if not terms:
        return {"where": ["1=0"], "args": [], "terms": []}
    
    where = []
    args = []
    for t in terms:
        where.append(f"{column} LIKE ?")
        args.append(f"%{t}%")
        
    return {
        "where": where,
        "args": args,
        "terms": terms
    }

def create_fallback_snippet(content: str, query: str, window: int = 40) -> str:
    """
    Generate a simple snippet around the first match of the query terms.
    """
    if not content or not query:
        return ""
        
    clean_query = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', query)
    terms = [t.lower() for t in clean_query.split() if len(t) > 0]
    if not terms:
        return content[:window*2] + "..."
    
    content_lower = content.lower()
    first_idx = -1
    for t in terms:
        idx = content_lower.find(t)
        if idx != -1:
            if first_idx == -1 or idx < first_idx:
                first_idx = idx
                
    if first_idx == -1:
        return content[:window*2] + "..."
        
    start = max(0, first_idx - window)
    end = min(len(content), first_idx + window)
    
    snippet = content[start:end]
    if start > 0: snippet = "..." + snippet
    if end < len(content): snippet = snippet + "..."
    
    return snippet
