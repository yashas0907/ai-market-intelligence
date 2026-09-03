
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.observability import obs
from app.data.validation import normalize_symbol, sanitize_untrusted
from app.retrieval.vector_store import ingest_document, retrieve

router = APIRouter()


@router.get("/metrics")
async def metrics() -> dict:
    snap = obs.snapshot()
    return snap


@router.get("/sources/health")
async def sources_health() -> dict:
    from app.data.sources.clients import GoogleNewsRssClient, SecClient, YahooMarketClient, YahooSearchClient

    clients = [YahooMarketClient(), YahooSearchClient(), SecClient(), GoogleNewsRssClient()]
    out = []
    for c in clients:
        out.append(c.health_check())
    return {"sources": out}


@router.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    company_symbol: str = Query(...),
    doc_type: str = Query(default="user_research", pattern="^(user_research|filing|report|news)$"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    s = get_settings()
    try:
        symbol = normalize_symbol(company_symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    ext = (file.filename or "").lower()
    if not any(ext.endswith(e) for e in s.allowed_upload_extensions):
        raise HTTPException(status_code=415, detail=f"unsupported file type; allowed: {s.allowed_upload_extensions}")

    content = await file.read()
    if len(content) > s.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"file exceeds {s.max_upload_size_mb}MB limit")
    if not content:
        raise HTTPException(status_code=422, detail="empty file")

    if ext.endswith(".pdf"):
        text = _extract_pdf_text(content)
    else:
        try:
            text = content.decode("utf-8", errors="replace")
        except Exception:
            raise HTTPException(status_code=422, detail="file could not be decoded as text")

    import uuid

    doc_id = f"doc-{uuid.uuid4().hex[:10]}"
    await ingest_document(
        db,
        {
            "doc_id": doc_id,
            "company_symbol": symbol,
            "doc_type": doc_type,
            "doc_source": f"upload:{sanitize_untrusted(file.filename or 'unnamed', max_len=60)}",
            "title": sanitize_untrusted(file.filename or "Uploaded document", max_len=200),
            "published_at": None,
            "url": None,
            "text": text,
        },
    )
    await db.commit()
    return {"doc_id": doc_id, "symbol": symbol, "chars": len(text), "status": "ingested"}


def _extract_pdf_text(content: bytes) -> str:
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        pages = []
        for page in reader.pages[:50]:
            pages.append(page.extract_text() or "")
        return "\n".join(pages)
    except ImportError:
        raise HTTPException(status_code=501, detail="PDF parsing requires pypdf: pip install pypdf")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"PDF parsing failed: {str(exc)[:150]}")


@router.get("/documents/search")
async def search_documents(q: str = Query(min_length=2, max_length=200), company_symbol: str | None = None, doc_type: str | None = None, top_k: int = Query(default=5, ge=1, le=20), db: AsyncSession = Depends(get_db)) -> dict:
    try:
        symbol = normalize_symbol(company_symbol) if company_symbol else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    results = await retrieve(db, q, company_symbol=symbol, doc_type=doc_type, top_k=top_k)
    return {"query": q, "results": results, "note": "TF-IDF-style hashed embeddings (local, deterministic) or configured provider"}
