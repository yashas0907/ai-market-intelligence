"""Research workflow orchestrator — explicit state machine, no uncontrolled loops.

Pipeline: collect → agents (parallel where independent) → fact-check → synthesize → persist.
Cost control: fixed agent set, bounded article counts, token-capped LLM calls,
duplicate-query prevention (TTL cache on completed reports).
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.agents.specialized import (
    ALL_AGENTS,
    FactCheckerAgent,
    ResearchSynthesizerAgent,
)
from app.agents.state import WorkflowState
from app.core.config import get_settings
from app.core.observability import logger, obs
from app.data.collectors import resolve_company_profile
from app.data.validation import normalize_symbol
from app.models import AgentRun, EvidenceRecord, ResearchReportDB, ResearchSession
from app.services.llm import get_llm


class Orchestrator:
    def __init__(self, db_session):
        self.session = db_session
        self.settings = get_settings()

    async def run_research(self, public_id: str, symbol: str, depth: str) -> dict[str, Any]:
        start = time.perf_counter()
        symbol = normalize_symbol(symbol)

        db_session_row = (await self.session.execute(select(ResearchSession).where(ResearchSession.public_id == public_id))).scalar_one_or_none()
        if db_session_row is None:
            raise RuntimeError(f"research session {public_id} not found")

        state = WorkflowState(symbol=symbol, company_name=symbol, depth=depth)
        state.record_stage("collect:profile")

        class _Ctx:
            def __init__(self, state_, session_, llm_):
                self.state = state_
                self.db_session = session_
                self.llm = llm_

        llm = get_llm()
        ctx = _Ctx(state, self.session, llm)

        # Collect phase: profile first (news query needs the company name),
        # then market + news PREFETCH in parallel — each with its own DB session
        # (SQLite: no shared-session concurrency). The prefetch warms the caches
        # so the analysis agents hit them instantly instead of re-fetching.
        from app.core.db import SessionLocal

        profile = await resolve_company_profile(self.session, symbol)
        state.profile = profile
        state.company_name = profile["name"]

        async def collect_market_branch():
            from app.tools.registry import tool_market_data

            async with SessionLocal() as s:
                return await tool_market_data(s, type("P", (), {"symbol": symbol, "range": "1y"})())

        async def collect_news_branch():
            from app.core.config import get_settings as _gs

            async with SessionLocal() as s:
                return await collect_news(s, symbol, profile["name"], _gs().research_max_news_articles)

        state.record_stage("collect:market")
        from app.data.collectors import collect_news

        market_res, news_res = await asyncio.gather(collect_market_branch(), collect_news_branch(), return_exceptions=True)

        if isinstance(market_res, Exception):
            state.errors.append(f"market collection: {str(market_res)[:150]}")
        elif market_res.ok:
            state.market = {"points": market_res.data["points"], "currency": market_res.data["currency"], "retrieved_at": market_res.data["retrieved_at"]}
            state.data_freshness.append({"source": "Yahoo Finance chart API", "retrieved_at": market_res.data["retrieved_at"], "note": "daily OHLCV"})

        if isinstance(news_res, Exception):
            state.errors.append(f"news prefetch: {str(news_res)[:150]}")

        progress_pct = 10
        await self._update_session(db_session_row, progress=progress_pct, stage="Data collection", stages=state.progress)

        # Phase 1a: independent DATA agents (market, technical, fundamental, news) —
        # concurrent, each with its own DB session (SQLite: no shared-session concurrency).
        # RiskAgent runs AFTER (Phase 1b) because it reads the outputs of the others.
        data_agents = [a for a in ALL_AGENTS if a.name != "risk"]
        risk_agent = next(a for a in ALL_AGENTS if a.name == "risk")

        semaphore = asyncio.Semaphore(self.settings.research_concurrency)

        async def run_agent_with_sem(agent):
            async with semaphore:
                from app.core.db import SessionLocal

                async with SessionLocal() as agent_session:
                    agent_ctx = _Ctx(state, agent_session, llm)
                    return await agent.run(agent_ctx)

        state.record_stage("agents:analysis")
        results = await asyncio.gather(*(run_agent_with_sem(a) for a in data_agents), return_exceptions=True)
        for agent, res in zip(data_agents, results):
            if isinstance(res, Exception):
                logger.error("workflow.agent_exception", agent=agent.name, error=str(res)[:200])
                state.agent_results[agent.name] = {"status": "failed", "error": str(res)[:300]}

        # Phase 1b: risk agent — sequential, sees completed data-agent state
        await run_agent_with_sem(risk_agent)
        progress_pct = 55
        await self._update_session(db_session_row, progress=progress_pct, stage="Specialized agents", stages=state.progress)

        # Phase 2: fact checker over the full claim set
        state.record_stage("agents:fact_check")
        fact_checker = FactCheckerAgent()
        await fact_checker.run(ctx)
        await self._update_session(db_session_row, progress=70, stage="Evidence verification", stages=state.progress)

        # Phase 3: synthesis
        state.record_stage("agents:synthesis")
        synthesizer = ResearchSynthesizerAgent()
        await synthesizer.run(ctx)
        await self._update_session(db_session_row, progress=85, stage="Report synthesis", stages=state.progress)

        # Phase 4: build final report dict + persist
        state.record_stage("persist")
        report = self._compose_report(state)
        state.report = report

        duration_ms = int((time.perf_counter() - start) * 1000)
        completed_at = datetime.now(timezone.utc)

        await self._persist(db_session_row, state, report, duration_ms, completed_at)
        await self._update_session(db_session_row, progress=100, stage="completed", status="completed", stages=state.progress, duration_ms=duration_ms, completed_at=completed_at)

        obs.incr("research_completed")
        obs.record_event("research_completed", {"symbol": symbol, "ms": duration_ms})
        logger.info("workflow.completed", symbol=symbol, ms=duration_ms, claims=len(state.claims), evidence=len(state.evidence))
        return report

    def _compose_report(self, state: WorkflowState) -> dict[str, Any]:
        disclaimer = "EDUCATIONAL/RESEARCH TOOL — NOT FINANCIAL ADVICE. This report presents evidence and analytical signals with uncertainty. No buy/sell recommendations, no guaranteed outcomes. All figures come from the cited sources as of the retrieval times shown."
        report = {
            "report_id": f"rpt-{uuid.uuid4().hex[:12]}",
            "symbol": state.symbol,
            "company_name": state.company_name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_freshness": state.data_freshness,
            "executive_summary": state.report.get("executive_summary", ""),
            "company_overview": {
                "symbol": state.symbol,
                "name": state.company_name,
                "sector": state.profile.get("sector"),
                "industry": state.profile.get("industry"),
                "exchange": state.profile.get("exchange"),
                "cik": state.profile.get("cik"),
                "profile_sources": state.profile.get("sources", []),
            },
            "market_performance": {
                "summary": state.agent_results.get("market_data", {}).get("summary", ""),
                "avg_daily_volume": state.market.get("avg_daily_volume"),
                "currency": state.market.get("currency"),
                "period_days": len(state.market.get("points", [])),
            },
            "technical_analysis": {
                "summary": state.agent_results.get("technical_analysis", {}).get("summary", ""),
                "signals": state.technical.get("signals", []),
                "statistics": state.technical.get("statistics", {}),
                "latest": state.technical.get("latest", {}),
                "series": state.technical.get("series", {}),
            },
            "fundamental_analysis": {
                "summary": state.agent_results.get("fundamental_analysis", {}).get("summary", ""),
                "latest": state.fundamentals.get("derived", {}).get("latest", {}),
                "trend": state.fundamentals.get("derived", {}).get("trend", {}),
                "available_years": state.fundamentals.get("derived", {}).get("available_years", []),
            },
            "news_intelligence": {
                "summary": state.agent_results.get("news_intelligence", {}).get("summary", ""),
                "articles": [{"title": a["title"], "source": a["source"], "url": a["url"], "published_at": a["published_at"], "sentiment": a["sentiment_label"]} for a in state.news[:20]],
                "events": state.events,
            },
            "sentiment_section": state.sentiment,
            "risks": state.risks,
            "bull_case": state.bull_case,
            "bear_case": state.bear_case,
            "key_unknowns": state.key_unknowns,
            "contradictions": state.contradictions,
            "claims": [
                {
                    "claim_id": c.claim_id,
                    "statement": c.statement,
                    "verification": c.verification,
                    "origin": c.origin,
                    "evidence": [
                        {
                            "evidence_id": e.evidence_id,
                            "label": e.payload.label,
                            "source": {"name": e.source.name, "kind": e.source.kind, "url": e.source.url, "retrieved_at": e.source.retrieved_at.isoformat()},
                            "value": e.payload.value,
                            "confidence": e.confidence,
                        }
                        for e in state.evidence_for_claim(c.claim_id)
                    ],
                }
                for c in state.claims.values()
            ],
            "sources": [
                {"source_id": e.evidence_id, "name": e.source.name, "kind": e.source.kind, "url": e.source.url, "retrieved_at": e.source.retrieved_at.isoformat(), "detail": e.payload.label}
                for e in state.evidence.values()
            ],
            "agent_runs": {name: {"status": r.get("status"), "duration_ms": r.get("duration_ms"), "summary": (r.get("summary") or "")[:400]} for name, r in state.agent_results.items()},
            "errors": state.errors,
            "disclaimer": disclaimer,
        }
        return report

    async def _update_session(self, db_row: ResearchSession, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(db_row, k, v)
        await self.session.commit()

    async def _persist(self, db_row: ResearchSession, state: WorkflowState, report: dict[str, Any], duration_ms: int, completed_at: datetime) -> None:
        self.session.add(ResearchReportDB(session_id=db_row.id, report_json=report))
        for name, result in state.agent_results.items():
            self.session.add(
                AgentRun(
                    session_id=db_row.id,
                    agent_name=name,
                    status=result.get("status", "unknown"),
                    summary=(result.get("summary") or "")[:2000],
                    claims_json=result.get("claims", []),
                    evidence_ids=[eid for eid in state.evidence.keys()][:50],
                    error=result.get("error"),
                    duration_ms=result.get("duration_ms", 0),
                )
            )
        for claim in state.claims.values():
            for ev in state.evidence_for_claim(claim.claim_id):
                self.session.add(
                    EvidenceRecord(
                        evidence_id=ev.evidence_id,
                        session_id=db_row.id,
                        claim_id=claim.claim_id,
                        source_id=ev.source.source_id,
                        source_name=ev.source.name,
                        source_kind=ev.source.kind,
                        source_url=ev.source.url,
                        retrieved_at=ev.source.retrieved_at,
                        payload={"label": ev.payload.label, "value": ev.payload.value, "unit": ev.payload.unit, "extra": ev.payload.extra},
                        claim_statement=claim.statement,
                        verification=claim.verification,
                        confidence=ev.confidence,
                    )
                )
        await self.session.commit()
