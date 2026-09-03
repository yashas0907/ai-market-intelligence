"""Embeddings: deterministic local TF-IDF-style hashed embeddings, or OpenAI if configured.

Local mode requires no API key and is fully reproducible — suitable for an
educational project and for tests. OpenAI mode used only when explicitly configured.
"""
from __future__ import annotations

import hashlib
import math
import re

from app.core.config import get_settings

_TOKEN_RE = re.compile(r"[a-z0-9']+")
_DIM = 256
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "from", "is",
    "are", "was", "were", "be", "been", "it", "its", "this", "that", "these", "those", "as", "has", "have",
    "had", "will", "would", "could", "should", "may", "might", "shall", "can", "their", "they", "them",
}


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS and len(t) > 1]


def _hash_dim(token: str) -> int:
    return int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % _DIM


def _local_embed(text: str) -> list[float]:
    tokens = _tokenize(text)
    if not tokens:
        return [0.0] * _DIM
    vec = [0.0] * _DIM
    for token in tokens:
        idx = _hash_dim(token)
        sign = 1.0 if (int(hashlib.md5(("s" + token).encode("utf-8")).hexdigest(), 16) % 2) == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


async def embed_text(text: str) -> list[float]:
    s = get_settings()
    if s.embedding_provider == "openai" and s.llm_api_key:
        try:
            return await _openai_embed(text, s)
        except Exception:
            return _local_embed(text)
    return _local_embed(text)


async def _openai_embed(text: str, s) -> list[float]:
    import httpx

    url = (s.llm_base_url or "https://api.openai.com/v1").rstrip("/") + "/embeddings"
    headers = {"Authorization": f"Bearer {s.llm_api_key}", "Content-Type": "application/json"}
    payload = {"model": s.embedding_model, "input": text[:8000]}
    async with httpx.AsyncClient(timeout=s.http_timeout_seconds) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return data["data"][0]["embedding"]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)
