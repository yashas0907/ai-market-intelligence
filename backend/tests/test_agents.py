"""Agent/workflow tests with FAKE source clients (no network). Verifies:
state transitions, claim/evidence wiring, fact-checker behavior, failure recovery."""
import asyncio
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from app.agents.evidence_utils import make_claim, make_evidence
from app.agents.state import WorkflowState
from app.schemas.common import PricePoint


class FakeCtx:
    def __init__(self, state, session=None, llm=None):
        self.state = state
        self.db_session = session
        from app.services.llm import HeuristicLLM

        self.llm = llm or HeuristicLLM()


def _points(n=250, start=100.0):
    pts = []
    price = start
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(n):
        price *= 1.0 + (0.002 if i % 3 else -0.001)
        pts.append(PricePoint(ts=datetime.fromtimestamp(now.timestamp() + i * 86400, tz=timezone.utc), open=price, high=price * 1.01, low=price * 0.99, close=price, adjclose=price, volume=1_000_000 + i * 1000))
    return pts


class TestEvidenceState:
    def test_add_evidence_claim(self):
        state = WorkflowState(symbol="TEST", company_name="Test")
        ev = make_evidence("price", "price rose 5%", "fake-src", "market", value=5.0)
        eid = state.add_evidence(ev)
        cl = make_claim("Price rose 5%.", [ev])
        state.add_claim(cl)
        assert eid in state.evidence
        assert state.evidence_for_claim(cl.claim_id) == [ev]

    def test_evidence_for_missing_claim(self):
        state = WorkflowState(symbol="TEST", company_name="Test")
        assert state.evidence_for_claim("nope") == []

    def test_record_stage_progress(self):
        state = WorkflowState(symbol="TEST", company_name="Test")
        state.record_stage("collect:market", "done")
        assert state.current_stage == "collect:market"
        assert len(state.progress) == 1


class TestFactChecker:
    @pytest.mark.asyncio
    async def test_supported_numeric_claim(self):
        from app.agents.specialized import FactCheckerAgent

        state = WorkflowState(symbol="TEST", company_name="Test")
        ev = make_evidence("v", "volatility 42.5%", "src", "market", value=42.5, unit="%")
        state.add_evidence(ev)
        cl = make_claim("Annualized volatility is 42.5%.", [ev])
        state.add_claim(cl)
        ctx = FakeCtx(state)
        result = await FactCheckerAgent().run(ctx)
        assert state.claims[cl.claim_id].verification == "SUPPORTED"
        assert "1 SUPPORTED" in result["summary"]

    @pytest.mark.asyncio
    async def test_contradicted_numeric_claim(self):
        from app.agents.specialized import FactCheckerAgent

        state = WorkflowState(symbol="TEST", company_name="Test")
        ev = make_evidence("v", "volatility 42.5%", "src", "market", value=42.5, unit="%")
        state.add_evidence(ev)
        cl = make_claim("Annualized volatility is 90%.", [ev])  # number disagrees with evidence
        state.add_claim(cl)
        ctx = FakeCtx(state)
        await FactCheckerAgent().run(ctx)
        assert state.claims[cl.claim_id].verification == "PARTIALLY_SUPPORTED"

    @pytest.mark.asyncio
    async def test_insufficient_evidence_claim(self):
        from app.agents.specialized import FactCheckerAgent

        state = WorkflowState(symbol="TEST", company_name="Test")
        cl = make_claim("Unverifiable statement.", [])
        state.add_claim(cl)
        ctx = FakeCtx(state)
        await FactCheckerAgent().run(ctx)
        assert state.claims[cl.claim_id].verification == "INSUFFICIENT_EVIDENCE"


class TestRiskAgent:
    @pytest.mark.asyncio
    async def test_high_volatility_flagged(self):
        from app.agents.specialized import RiskAgent

        state = WorkflowState(symbol="TEST", company_name="Test")
        pts = []
        price = 100.0
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i in range(200):
            price *= 1.0 + (0.06 if i % 2 else -0.055)  # wild swings → >60% vol
            pts.append({"ts": now.timestamp() + i * 86400, "close": price, "volume": 1e6})
        state.market = {"points": pts}
        ctx = FakeCtx(state)
        await RiskAgent().run(ctx)
        assert any(r["category"] == "Market Risk" for r in state.risks)
        assert all("evidence" in r for r in state.risks)

    @pytest.mark.asyncio
    async def test_risks_never_empty_of_methodology(self):
        from app.agents.specialized import RiskAgent

        state = WorkflowState(symbol="TEST", company_name="Test")
        ctx = FakeCtx(state)
        await RiskAgent().run(ctx)
        assert any(r["category"] == "Model Risk" for r in state.risks)


class TestSynthesizer:
    @pytest.mark.asyncio
    async def test_bull_bear_split_by_evidence(self):
        from app.agents.specialized import ResearchSynthesizerAgent

        state = WorkflowState(symbol="TEST", company_name="Test")
        state.fundamentals = {"derived": {"latest": {"revenue_growth_yoy": {"value": 12.0, "period": "2025-12-31"}, "net_income": {"value": 250.0}, "ratios": {"net_profit_margin": {"value": 0.18, "period": "2025-12-31"}, "debt_to_equity": {"value": 2.5, "period": "2025-12-31"}}}}}
        state.sentiment = {"aggregate_label": "positive", "aggregate_score": 0.4, "article_count": 10}
        ctx = FakeCtx(state)
        await ResearchSynthesizerAgent().run(ctx)
        assert any("Revenue grew" in b["argument"] for b in state.bull_case)
        assert any("Leverage" in b["argument"] for b in state.bear_case)
        assert state.key_unknowns  # unknowns always present (epistemic honesty)
        assert "no forward-looking guidance" in " ".join(state.key_unknowns).lower()


class TestEventDetection:
    def test_event_patterns(self):
        from app.agents.specialized import _detect_events

        arts = [
            {"title": "Acme reports Q3 earnings beats estimates", "source": "Reuters", "url": "http://x/1", "published_at": "2026-01-01", "sentiment_label": "positive"},
            {"title": "Acme faces antitrust investigation", "source": "Bloomberg", "url": "http://x/2", "published_at": "2026-01-02", "sentiment_label": "negative"},
            {"title": "Acme launches new product line", "source": "TechCrunch", "url": "http://x/3", "published_at": "2026-01-03", "sentiment_label": "positive"},
        ]
        events = _detect_events(arts, "ACME")
        types = {e["event_type"] for e in events}
        assert "earnings" in types and "regulatory" in types and "product" in types
        ev = next(e for e in events if e["event_type"] == "regulatory")
        assert ev["verification"] == "verified"

    def test_no_false_events(self):
        from app.agents.specialized import _detect_events

        arts = [{"title": "Acme announces office potluck", "source": "X", "url": "http://x", "published_at": "2026-01-01"}]
        assert _detect_events(arts, "ACME") == []


class TestFailureRecovery:
    @pytest.mark.asyncio
    async def test_agent_failure_recorded_not_fatal(self):
        from app.agents.specialized import FundamentalAnalysisAgent

        state = WorkflowState(symbol="TEST", company_name="Test")

        class FailingToolCtx(FakeCtx):
            pass

        ctx = FakeCtx(state)
        # make tool_fundamentals fail by passing a session that raises
        ctx.db_session = None
        result = await FundamentalAnalysisAgent().run(ctx)
        assert result["status"] == "failed"
        assert state.errors  # error recorded in state
        # workflow continues: other agents can still run
        assert state.current_stage is not None
