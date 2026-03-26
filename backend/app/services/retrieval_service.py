from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, KnowledgeDocument
from app.utils.text import build_idf, tokenize


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "can",
    "do",
    "for",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "my",
    "of",
    "or",
    "the",
    "to",
    "what",
    "when",
    "where",
    "why",
    "you",
}


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    title: str
    source_url: str | None
    content: str
    score: float
    source_type: str


class RetrievalService:
    def search(self, db: Session, revision_id: str, query: str, limit: int = 5) -> list[RetrievedChunk]:
        chunk_rows = db.execute(
            select(
                KnowledgeChunk.id.label("chunk_id"),
                KnowledgeChunk.document_id.label("document_id"),
                KnowledgeChunk.content.label("content"),
                KnowledgeChunk.payload_json.label("chunk_payload"),
                KnowledgeDocument.title.label("title"),
                KnowledgeDocument.source_url.label("source_url"),
                KnowledgeDocument.source_type.label("source_type"),
                KnowledgeDocument.payload_json.label("document_payload"),
            )
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(KnowledgeChunk.revision_id == revision_id)
        ).all()
        if not chunk_rows:
            return []

        query_tokens = [token for token in tokenize(query) if token not in STOPWORDS]
        if not query_tokens:
            return []
        idf = build_idf([row.content for row in chunk_rows])
        query_counts = Counter(query_tokens)

        scored: list[RetrievedChunk] = []
        for row in chunk_rows:
            chunk_tokens = [token for token in tokenize(row.content) if token not in STOPWORDS]
            metadata_text = " ".join(
                [
                    row.title or "",
                    str((row.chunk_payload or {}).get("section_name", "")),
                    str((row.chunk_payload or {}).get("metadata_text", "")),
                    str((row.document_payload or {}).get("section_name", "")),
                ]
            )
            metadata_tokens = [token for token in tokenize(metadata_text) if token not in STOPWORDS]
            if not chunk_tokens:
                chunk_tokens = []
            chunk_counts = Counter(chunk_tokens)
            metadata_counts = Counter(metadata_tokens)
            score = 0.0
            for token, count in query_counts.items():
                if token in chunk_counts:
                    score += (1 + min(chunk_counts[token], 4)) * count * idf.get(token, 1.0)
                if token in metadata_counts:
                    score += 2.4 * (1 + min(metadata_counts[token], 3)) * count * idf.get(token, 1.0)
            if row.source_type == "correction":
                score *= 1.1
            if score > 0:
                scored.append(
                    RetrievedChunk(
                        chunk_id=row.chunk_id,
                        document_id=row.document_id,
                        title=row.title,
                        source_url=row.source_url,
                        content=row.content,
                        score=score,
                        source_type=row.source_type,
                    )
                )
        return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]


retrieval_service = RetrievalService()
