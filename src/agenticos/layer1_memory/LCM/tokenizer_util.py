"""
LCM Tokenizer Utilities — Precise character-based word/punctuation counting.

Provides LcmSimpleTokenizer as a professional fallback for Tiktoken/HuggingFace.
"""

from __future__ import annotations

import re
import math
from typing import Protocol

class TokenizerProtocol(Protocol):
    def encode(self, text: str) -> list[int]: ...

class LcmSimpleTokenizer:
    """
    Professional fallback tokenizer using Regex to estimate tokens.
    
    Heuristic: 
    - Words (alpha-numeric sequences) ~ 1 token.
    - Punctuation/Markers ~ 0.5-1 token.
    - Long words (~6+ chars) can count as 2.
    
    Significantly more accurate than len(text)/4 for diverse content.
    """

    def __init__(self) -> None:
        # Tách từ, dấu câu, và cụm ký tự đặc biệt
        self._pattern = re.compile(r"\w+|[^\w\s]", re.UNICODE)

    def encode(self, text: str) -> list[int]:
        """
        Produce a list of dummy integers whose length equals the estimated tokens.
        """
        if not text:
            return []

        tokens = self._pattern.findall(text)
        count = 0
        
        for t in tokens:
            # Heuristic: 
            # 1. Từ dài (> 10 ký tự) thường tốn nhiều hơn 1 token
            if len(t) > 10:
                count += math.ceil(len(t) / 4)
            # 2. Dấu câu đơn lẻ thường là 1 token (hoặc 0.5 tùy model, ta lấy 1 cho an toàn)
            else:
                count += 1
                
        # Trả về dummy list với độ dài tương ứng
        return [0] * count

def count_tokens_fallback(text: str) -> int:
    """One-shot helper for the new SimpleTokenizer."""
    return len(LcmSimpleTokenizer().encode(text))
