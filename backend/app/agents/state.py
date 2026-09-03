"""Explicit workflow state — agents exchange typed, bounded structures, never giant blobs."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.schemas.common import Claim, Evidence


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class WorkflowState:
    symbol: str
    company_name: str
    depth: str = "standard"
    created_at: datetime = field(default_factory=utcnow)

    # collected data (structured)
    profile: dict[str, Any] = field(default_factory=dict)
    market: dict[str, Any] = field(default_factory=dict)
    fundamentals: dict[str, Any] = field(default_factory=dict)
    technical: dict[str, Any] = field(default_factory=dict)
    news: list[dict[str, Any]] = field(default_factory=list)
    sentiment: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    # evidence layer
    evidence: dict[str, Evidence] = field(default_factory=dict)
    claims: dict[str, Claim] = field(default_factory=dict)
    contradictions: list[dict[str, Any]] = field(default_factory=list)

    # agent outputs (bounded)
    agent_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    risks: list[dict[str, Any]] = field(default_factory=list)
    bull_case: list[dict[str, Any]] = field(default_factory=list)
    bear_case: list[dict[str, Any]] = field(default_factory=list)
    key_unknowns: list[str] = field(default_factory=list)

    # final
    report: dict[str, Any] = field(default_factory=dict)

    # freshness
    data_freshness: list[dict[str, Any]] = field(default_factory=list)

    # progress tracking (operational events only — no chain-of-thought)
    progress: list[dict[str, Any]] = field(default_factory=list)
    current_stage: str = "initialized"

    errors: list[str] = field(default_factory=list)

    def record_stage(self, stage: str, detail: str = "") -> None:
        self.progress.append({"stage": stage, "detail": detail, "at": utcnow().isoformat()})
        self.current_stage = stage

    def add_evidence(self, evidence: Evidence) -> str:
        self.evidence[evidence.evidence_id] = evidence
        return evidence.evidence_id

    def add_claim(self, claim: Claim) -> str:
        self.claims[claim.claim_id] = claim
        return claim.claim_id

    def evidence_snapshot(self) -> list[Evidence]:
        return list(self.evidence.values())

    def evidence_for_claim(self, claim_id: str) -> list[Evidence]:
        claim = self.claims.get(claim_id)
        if not claim:
            return []
        return [self.evidence[eid] for eid in claim.evidence_ids if eid in self.evidence]
