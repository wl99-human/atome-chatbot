from __future__ import annotations

import hashlib
import re
from collections import Counter
from math import log


WORD_RE = re.compile(r"[a-zA-Z0-9']+")
WHITESPACE_RE = re.compile(r"\s+")


def normalize_whitespace(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text or "").strip()


def tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in WORD_RE.finditer(text or "")]


def chunk_text(text: str, max_chars: int = 900, overlap_chars: int = 160) -> list[str]:
    normalized = normalize_whitespace(text)
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + max_chars, len(normalized))
        if end < len(normalized):
            split_at = normalized.rfind(". ", start, end)
            if split_at == -1 or split_at <= start + max_chars // 2:
                split_at = normalized.rfind(" ", start, end)
            if split_at != -1 and split_at > start:
                end = split_at + 1
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def build_idf(documents: list[str]) -> dict[str, float]:
    token_sets = [set(tokenize(doc)) for doc in documents]
    total_docs = len(token_sets) or 1
    counts = Counter(token for tokens in token_sets for token in tokens)
    return {
        token: log((1 + total_docs) / (1 + doc_count)) + 1
        for token, doc_count in counts.items()
    }
