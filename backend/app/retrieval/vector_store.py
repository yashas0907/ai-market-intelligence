"""Document ingestion → chunking → metadata → embeddings → vector store → retriever."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select

from app.data.validation import sanitize_untrusted
from app.models import DocumentChunk
from app.retrieval.embeddings import cosine, embed_text

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_CHUNK_WORDS = 120
_CHUNK_OVERLAP = 20


def chunk_text(text: str, words_per_chunk: int = _CHUNK_WORDS, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Sentence-aware chunking with word-count target and overlap."""
    text = (text or "").strip()
    if not text:
        return []
    sentences = _SENT_SPLIT.split(text)
    chunks: list[str] = []
    current: list[str] = []
    count = 0
    for sent in sentences:
        words = len(sent.split())
        if count + words > words_per_chunk and current:
            chunks.append(" ".join(current))
            current = current[-overlap:] if overlap else []
            count = sum(len(s.split()) for s in current)
        current.append(sent)
        count += words
    if current:
        chunks.append(" ".join(current))
    return chunks


async def ingest_document(session, doc: dict[str, Any]) -> dict[str, Any]:
    """Ingest a document into the vector store.

    doc keys: doc_id, company_symbol, doc_type, doc_source, title, published_at,
    url, text. Content is treated as untrusted data and sanitized.
    """
    text = sanitize_untrusted(doc.get("text", ""), max_len=50000)
    title = sanitize_untrusted(doc.get("title", ""), max_len=300)
    chunks = chunk_text(text)
    if not chunks:
        return {"doc_id": doc.get("doc_id"), "chunks": 0}

    await session.execute(delete(DocumentChunk).where(DocumentChunk.doc_id == doc["doc_id"]))
    for idx, chunk in enumerate(chunks):
        embedding = await embed_text(f"{title}. {chunk}")
        session.add(
            DocumentChunk(
                doc_id=doc["doc_id"],
                company_symbol=doc["company_symbol"],
                doc_type=doc.get("doc_type", "news"),
                doc_source=doc.get("doc_source", "unknown"),
                title=title,
                published_at=doc.get("published_at"),
                url=doc.get("url"),
                chunk_index=idx,
                text=chunk,
                embedding=embedding,
                embedding_model="local-tfidf-hash-256",
                created_at=datetime.now(timezone.utc),
            )
        )
    return {"doc_id": doc["doc_id"], "chunks": len(chunks)}


async def retrieve(session, query: str, company_symbol: str | None = None, doc_type: str | None = None, top_k: int = 5) -> list[dict[str, Any]]:
    """Metadata-filtered vector search. Query is sanitized before embedding."""
    query = sanitize_untrusted(query, max_len=500)
    q_vec = await embed_text(query)
    stmt = select(DocumentChunk)
    if company_symbol:
        stmt = stmt.where(DocumentChunk.company_symbol == company_symbol)
    if doc_type:
        stmt = stmt.where(DocumentChunk.doc_type == doc_type)
    result = await session.execute(stmt)
    rows = result.scalars().all()
    scored: list[tuple[float, DocumentChunk]] = []
    for row in rows:
        if not row.embedding:
            continue
        sim = cosine(q_vec, row.embedding)
        scored.append((sim, row))
    scored.sort(key=lambda t: -t[0])
    out: list[dict[str, Any]] = []
    for sim, row in scored[:top_k]:
        out.append(
            {
                "similarity": round(sim, 4),
                "doc_id": row.doc_id,
                "doc_type": row.doc_type,
                "doc_source": row.doc_source,
                "title": row.title,
                "text": row.text,
                "url": row.url,
                "published_at": row.published_at.isoformat() if row.published_at else None,
                "company_symbol": row.company_symbol,
            }
        )
    return out
