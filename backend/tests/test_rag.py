"""RAG retrieval tests: chunking, metadata filtering, relevance."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestChunking:
    def test_sentence_aware_split(self):
        from app.retrieval.vector_store import chunk_text

        text = "Sentence one here. Sentence two follows. Third sentence arrives now. Fourth one."
        chunks = chunk_text(text, words_per_chunk=5, overlap=1)
        assert len(chunks) >= 2
        assert all(len(c.split()) <= 12 for c in chunks)

    def test_overlap_present(self):
        from app.retrieval.vector_store import chunk_text

        text = ("Alpha beta gamma. " * 20).strip()
        chunks = chunk_text(text, words_per_chunk=6, overlap=2)
        if len(chunks) > 1:
            last_words_of_first = chunks[0].split()[-2:]
            assert any(w in chunks[1] for w in last_words_of_first)

    def test_empty(self):
        from app.retrieval.vector_store import chunk_text

        assert chunk_text("") == []

    def test_long_single_sentence(self):
        from app.retrieval.vector_store import chunk_text

        # a single long sentence cannot be split mid-sentence; chunk is whole sentence
        text = "word " * 500
        chunks = chunk_text(text, words_per_chunk=120, overlap=20)
        assert len(chunks) == 1
        assert len(chunks[0].split()) == 500


class TestEmbeddings:
    def test_local_deterministic(self):
        from app.retrieval.embeddings import _local_embed

        a = _local_embed("apple revenue growth")
        b = _local_embed("apple revenue growth")
        assert a == b
        assert len(a) == 256

    def test_cosine_similarity(self):
        from app.retrieval.embeddings import _local_embed, cosine

        a = _local_embed("revenue grew strongly")
        b = _local_embed("revenue growth strong")
        c = _local_embed("lawsuit regulatory probe")
        assert cosine(a, b) > cosine(a, c)

    def test_empty_text_zero_vector(self):
        from app.retrieval.embeddings import _local_embed

        assert all(v == 0.0 for v in _local_embed(""))


@pytest.mark.asyncio
async def test_retrieve_with_metadata_filter(tmp_path):
    """Integration: ingest two docs for different symbols; filter excludes other company."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models import Base
    from app.retrieval.vector_store import ingest_document, retrieve

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/rag.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with maker() as session:
        await ingest_document(session, {"doc_id": "d1", "company_symbol": "AAA", "doc_type": "news", "doc_source": "test", "title": "AAA earnings", "text": "AAA reported strong revenue growth of 20 percent in the latest quarter according to filings."})
        await ingest_document(session, {"doc_id": "d2", "company_symbol": "BBB", "doc_type": "news", "doc_source": "test", "title": "BBB lawsuit", "text": "BBB faces a regulatory lawsuit and antitrust investigation by authorities."})
        await session.commit()

        hits = await retrieve(session, "revenue growth", company_symbol="AAA", top_k=5)
        assert len(hits) >= 1
        assert all(h["company_symbol"] == "AAA" for h in hits)
        assert "revenue" in hits[0]["text"].lower()

        hits_b = await retrieve(session, "lawsuit investigation", company_symbol="BBB", top_k=5)
        assert len(hits_b) >= 1 and "lawsuit" in hits_b[0]["text"].lower()
    await engine.dispose()
