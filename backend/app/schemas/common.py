from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class DataFreshness(BaseModel):
    retrieved_at: datetime
    source: str
    note: str = ""


class SourceRef(BaseModel):
    source_id: str
    name: str
    kind: str
    url: str | None = None
    retrieved_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidencePayload(BaseModel):
    kind: str
    label: str
    value: float | None = None
    as_of: datetime | None = None
    unit: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    evidence_id: str
    claim_keys: list[str] = Field(default_factory=list)
    source: SourceRef
    payload: EvidencePayload
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class Claim(BaseModel):
    claim_id: str
    statement: str
    evidence_ids: list[str] = Field(default_factory=list)
    verification: str | None = None
    origin: str = "agent"
    contradiction_group: str | None = None


class PricePoint(BaseModel):
    ts: datetime
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    adjclose: float | None
    volume: float | None


class Quote(BaseModel):
    symbol: str
    company_name: str
    price: float | None
    change_pct: float | None
    currency: str | None
    market_state: str | None
    as_of: datetime
    retrieved_at: datetime
    source: str


class TechnicalIndicators(BaseModel):
    symbol: str
    as_of: datetime
    retrieved_at: datetime
    series: dict[str, list[float]] = Field(default_factory=dict)
    latest: dict[str, float | None] = Field(default_factory=dict)
    signals: list[dict[str, Any]] = Field(default_factory=list)


class FundamentalMetric(BaseModel):
    key: str
    label: str
    value: float | None
    unit: str
    period: str | None = None
    fiscal_year: int | None = None
    source: str
    retrieved_at: datetime


class NewsArticle(BaseModel):
    article_id: str
    title: str
    source: str
    url: str
    published_at: datetime
    retrieved_at: datetime
    company_symbol: str
    summary: str = ""
    sentiment_label: str | None = None
    sentiment_score: float | None = None


class AgentResult(BaseModel):
    agent: str
    status: str
    summary: str = ""
    findings: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0


class ResearchReport(BaseModel):
    report_id: str
    symbol: str
    company_name: str
    generated_at: datetime
    data_freshness: list[DataFreshness] = Field(default_factory=list)
    executive_summary: str = ""
    company_overview: dict[str, Any] = Field(default_factory=dict)
    market_performance: dict[str, Any] = Field(default_factory=dict)
    technical_analysis: dict[str, Any] = Field(default_factory=dict)
    fundamental_analysis: dict[str, Any] = Field(default_factory=dict)
    news_intelligence: dict[str, Any] = Field(default_factory=dict)
    sentiment_section: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    bull_case: list[dict[str, Any]] = Field(default_factory=list)
    bear_case: list[dict[str, Any]] = Field(default_factory=list)
    key_unknowns: list[str] = Field(default_factory=list)
    contradictions: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    disclaimer: str = ""


class CompanySummary(BaseModel):
    symbol: str
    name: str
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    cik: int | None = None
    retrieved_at: datetime
    source: str


class SearchHit(BaseModel):
    symbol: str
    name: str
    exchange: str | None = None
    score: float = 0.0
    source: str = "sec"
