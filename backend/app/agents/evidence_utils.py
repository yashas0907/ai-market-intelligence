"""Evidence utilities — provenance chains: claim → evidence → source → metadata."""
from __future__ import annotations

import uuid
from typing import Any

from app.schemas.common import Claim, Evidence, EvidencePayload, SourceRef


def make_evidence(kind: str, label: str, source_name: str, source_kind: str, source_url: str | None = None, value: float | None = None, as_of: Any = None, unit: str | None = None, extra: dict[str, Any] | None = None, confidence: float = 1.0) -> Evidence:
    return Evidence(
        evidence_id=f"ev-{uuid.uuid4().hex[:10]}",
        claim_keys=[],
        source=SourceRef(
            source_id=f"src-{uuid.uuid4().hex[:8]}",
            name=source_name,
            kind=source_kind,
            url=source_url,
            retrieved_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            metadata={"as_of": str(as_of) if as_of else None},
        ),
        payload=EvidencePayload(kind=kind, label=label, value=value, as_of=as_of, unit=unit, extra=extra or {}),
        confidence=confidence,
    )


def make_claim(statement: str, evidence: list[Evidence], origin: str = "agent") -> Claim:
    return Claim(claim_id=f"cl-{uuid.uuid4().hex[:10]}", statement=statement, evidence_ids=[e.evidence_id for e in evidence], verification=None, origin=origin)
