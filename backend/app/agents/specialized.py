"""Specialized agents. Each agent: runs tools, produces structured findings,
claims (with evidence IDs), and bounded summaries. No giant text blobs pass between agents.

Agents NEVER invent data — factual claims must reference evidence captured from tool output.
"""
from __future__ import annotations

import time
from typing import Any, Protocol

from app.agents.evidence_utils import make_claim, make_evidence
from app.agents.state import WorkflowState
from app.analytics.sentiment import aggregate_sentiment, analyze_article
from app.core.observability import logger, obs
from app.data.validation import sanitize_untrusted
from app.services.llm import LLMProvider, generate_section
from app.tools.registry import (
    NewsToolParams,
    TechnicalToolParams,
    tool_fundamentals,
    tool_market_data,
    tool_news,
    tool_technical,
)


class AgentContext(Protocol):
    state: WorkflowState
    db_session: Any
    llm: LLMProvider


class BaseAgent:
    name: str = "base"

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        raise NotImplementedError


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


async def _run_agent(ctx: AgentContext, agent: "BaseAgent", agent_fn) -> dict[str, Any]:
    start = time.perf_counter()
    name = agent.name
    ctx.state.record_stage(f"agent:{name}")
    logger.info("agent.start", agent=name, symbol=ctx.state.symbol)
    try:
        result = await agent_fn()
        duration = _elapsed_ms(start)
        ctx.state.agent_results[name] = {**result, "status": "ok", "duration_ms": duration}
        obs.incr("agent_run", agent=name, status="ok")
        logger.info("agent.done", agent=name, symbol=ctx.state.symbol, ms=duration)
        return result
    except Exception as exc:
        duration = _elapsed_ms(start)
        ctx.state.agent_results[name] = {"status": "failed", "error": str(exc)[:300], "duration_ms": duration}
        ctx.state.errors.append(f"{name}: {str(exc)[:200]}")
        obs.incr("agent_run", agent=name, status="error")
        logger.error("agent.error", agent=name, symbol=ctx.state.symbol, error=str(exc)[:200])
        return {"status": "failed", "error": str(exc)[:300], "findings": [], "claims": []}


class MarketDataAgent(BaseAgent):
    """Analyzes price, volume, volatility, market behavior."""

    name = "market_data"

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        async def _inner() -> dict[str, Any]:
            state = ctx.state
            result = await tool_market_data(ctx.db_session, type("P", (), {"symbol": state.symbol, "range": "1y"})())
            if not result.ok:
                raise RuntimeError(result.error or "market data fetch failed")
            points = result.data["points"]
            state.market = {"points": points, "currency": result.data["currency"], "retrieved_at": result.retrieved_at}
            state.data_freshness.append({"source": "Yahoo Finance chart API", "retrieved_at": result.retrieved_at.isoformat(), "note": "daily OHLCV, split-adjusted close"})

            closes = [p["close"] for p in points if p.get("close")]
            findings: list[dict[str, Any]] = []
            claims: list[Any] = []

            if len(closes) >= 2:
                first, last = closes[0], closes[-1]
                change_pct = (last - first) / first * 100 if first else None
                ev = make_evidence("price_change", f"Price change over period: {change_pct:+.2f}% ({first:.2f} → {last:.2f})", "Yahoo Finance chart API", "market_data", value=change_pct, unit="%", extra={"first": first, "last": last, "n_points": len(points)})
                state.add_evidence(ev)
                claim = make_claim(f"Closing price changed {change_pct:+.2f}% over the analysis window.", [ev], origin="market_agent")
                state.add_claim(claim)
                findings.append({"finding": "price_change", "value": round(change_pct, 2), "evidence_id": ev.evidence_id})
                claims.append(claim.claim_id)

            from app.analytics.technical import annualized_volatility, daily_returns, max_drawdown

            rets = daily_returns(closes)
            vol = annualized_volatility(rets)
            if vol is not None:
                ev = make_evidence("volatility", f"Annualized volatility: {vol:.1f}%", "Yahoo Finance chart API (calculated)", "market_data", value=round(vol, 2), unit="%")
                state.add_evidence(ev)
                claim = make_claim(f"Annualized daily-return volatility is approximately {vol:.1f}%.", [ev], origin="market_agent")
                state.add_claim(claim)
                findings.append({"finding": "volatility_annual_pct", "value": round(vol, 2), "evidence_id": ev.evidence_id})
                claims.append(claim.claim_id)

            mdd = max_drawdown(closes)
            if mdd is not None:
                ev = make_evidence("max_drawdown", f"Max drawdown in window: {mdd:.1f}%", "Yahoo Finance chart API (calculated)", "market_data", value=round(mdd, 2), unit="%")
                state.add_evidence(ev)
                claim = make_claim(f"Maximum peak-to-trough decline within the window was {mdd:.1f}%.", [ev], origin="market_agent")
                state.add_claim(claim)
                findings.append({"finding": "max_drawdown_pct", "value": round(mdd, 2), "evidence_id": ev.evidence_id})
                claims.append(claim.claim_id)

            vols_list = [p["volume"] for p in points if p.get("volume")]
            if vols_list:
                avg_vol = sum(vols_list) / len(vols_list)
                findings.append({"finding": "avg_daily_volume", "value": round(avg_vol), "evidence_id": None})
                state.market["avg_daily_volume"] = round(avg_vol)

            summary = await generate_section(ctx.llm, "Summarize the market behavior for this stock over the analysis window (trend direction, volatility, drawdown). Neutral tone, historical observations only.", {"price_change_pct": findings[0]["value"] if findings else None, "annualized_volatility_pct": vol, "max_drawdown_pct": mdd, "period_days": len(closes), "avg_daily_volume": state.market.get("avg_daily_volume")})
            return {"summary": summary, "findings": findings, "claims": claims}

        return await _run_agent(ctx, self, _inner)


class TechnicalAnalysisAgent(BaseAgent):
    name = "technical_analysis"

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        async def _inner() -> dict[str, Any]:
            state = ctx.state
            result = await tool_technical(ctx.db_session, TechnicalToolParams(symbol=state.symbol, range="1y"))
            if not result.ok:
                raise RuntimeError(result.error or "technical tool failed")
            tech = result.data
            state.technical = tech
            state.data_freshness.append({"source": "Yahoo Finance chart API (indicator calculations)", "retrieved_at": result.retrieved_at.isoformat(), "note": "SMA/EMA/RSI/MACD/Bollinger/ATR computed from daily closes"})

            findings: list[dict[str, Any]] = []
            claims: list[Any] = []
            for signal in tech.get("signals", []):
                ev = make_evidence("indicator_signal", f"{signal['indicator']}: {signal['reading']} — {signal['note']}", "computed from Yahoo Finance daily OHLCV", "technical", extra=signal)
                state.add_evidence(ev)
                claim = make_claim(f"Technical indicator {signal['indicator']} reads {signal['reading']} as of the latest close.", [ev], origin="technical_agent")
                state.add_claim(claim)
                findings.append({"finding": signal["indicator"], "reading": signal["reading"], "evidence_id": ev.evidence_id})
                claims.append(claim.claim_id)

            stats = tech.get("statistics", {})
            summary = await generate_section(ctx.llm, "Interpret the technical indicators. Explain each signal briefly in neutral language. State that these are historical observations, not predictions.", {"signals": tech.get("signals"), "statistics": stats, "latest": tech.get("latest")})
            return {"summary": summary, "findings": findings, "claims": claims}

        return await _run_agent(ctx, self, _inner)


class FundamentalAnalysisAgent(BaseAgent):
    name = "fundamental_analysis"

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        async def _inner() -> dict[str, Any]:
            from app.analytics.technical import derive_fundamentals

            state = ctx.state
            result = await tool_fundamentals(ctx.db_session, type("P", (), {"symbol": state.symbol})())
            if not result.ok:
                raise RuntimeError(result.error or "fundamentals tool failed")
            derived = derive_fundamentals(result.data["metrics"])
            state.fundamentals = {"metrics": result.data["metrics"], "derived": derived, "retrieved_at": result.retrieved_at.isoformat()}
            state.data_freshness.append({"source": "SEC EDGAR XBRL companyfacts", "retrieved_at": result.retrieved_at.isoformat(), "note": "annual (FY) XBRL facts from 10-K filings"})

            findings: list[dict[str, Any]] = []
            claims: list[Any] = []
            latest = derived.get("latest", {})
            claims_specs = [
                ("revenue", "Revenue (latest FY)"),
                ("net_income", "Net income (latest FY)"),
                ("eps_diluted", "Diluted EPS (latest FY)"),
                ("revenue_growth_yoy", "Revenue growth YoY"),
            ]
            for key, label in claims_specs:
                rec = latest.get(key)
                if rec and rec.get("value") is not None:
                    unit = rec.get("unit", "")
                    ev = make_evidence("fundamental", f"{label}: {rec['value']} {unit} (period {rec.get('period')})", "SEC EDGAR XBRL (10-K)", "fundamental", value=rec["value"], unit=unit, extra={"period": rec.get("period")})
                    state.add_evidence(ev)
                    claim = make_claim(f"{label} was {rec['value']} {unit} for fiscal period ending {rec.get('period')}.", [ev], origin="fundamental_agent")
                    state.add_claim(claim)
                    findings.append({"finding": key, "value": rec["value"], "period": rec.get("period"), "evidence_id": ev.evidence_id})
                    claims.append(claim.claim_id)

            ratios = latest.get("ratios", {})
            for key, rec in ratios.items():
                if rec.get("value") is not None:
                    ev = make_evidence("fundamental_ratio", f"{key}: {rec['value']} ({rec.get('note')})", "SEC EDGAR XBRL (10-K), derived ratio", "fundamental", value=rec["value"], extra={"formula": rec.get("note")})
                    state.add_evidence(ev)
                    claim = make_claim(f"Derived ratio {key} is {rec['value']} based on fiscal period {rec.get('period')}.", [ev], origin="fundamental_agent")
                    state.add_claim(claim)
                    claims.append(claim.claim_id)

            summary = await generate_section(ctx.llm, "Summarize the company fundamentals from SEC filings data: revenue, earnings, margins, leverage, growth. Report each metric with its fiscal period. If metrics are missing, say so.", {"latest": latest, "trend_years": derived.get("available_years")})
            return {"summary": summary, "findings": findings, "claims": claims}

        return await _run_agent(ctx, self, _inner)


class NewsIntelligenceAgent(BaseAgent):
    name = "news_intelligence"

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        async def _inner() -> dict[str, Any]:
            state = ctx.state

            limit = {"quick": 10, "standard": 20, "deep": 30}.get(state.depth, 20)
            result = await tool_news(ctx.db_session, NewsToolParams(symbol=state.symbol, limit=limit))
            if not result.ok:
                raise RuntimeError(result.error or "news tool failed")
            articles = result.data["articles"]
            result.data.get("company_name") or state.company_name

            analyzed = []
            for a in articles:
                s = analyze_article(a["title"], a.get("summary", ""))
                enriched = {**a, "sentiment_label": s["label"], "sentiment_score": s["score"]}
                analyzed.append(enriched)
            agg = aggregate_sentiment(analyzed)
            state.news = analyzed
            state.sentiment = agg
            state.data_freshness.append({"source": "Google News RSS", "retrieved_at": result.retrieved_at.isoformat(), "note": f"news metadata + snippets, max {limit} articles"})

            findings: list[dict[str, Any]] = []
            claims: list[Any] = []
            for a in analyzed[:8]:
                ev = make_evidence("news_article", f"'{a['title']}' — {a['source']}, {a['published_at']}", a["source"], "news", source_url=a["url"], as_of=a["published_at"], extra={"sentiment": a["sentiment_label"], "score": a["sentiment_score"]}, confidence=0.8)
                state.add_evidence(ev)
                claim = make_claim(f"News article reported: '{a['title']}' ({a['source']}, {a['published_at']}).", [ev], origin="news_agent")
                state.add_claim(claim)
                claims.append(claim.claim_id)
            if analyzed:
                ev = make_evidence("sentiment_aggregate", f"Aggregate news sentiment: {agg['aggregate_label']} (score {agg['aggregate_score']}, {agg['article_count']} articles)", "platform sentiment engine (lexicon-based)", "derived", value=agg["aggregate_score"], extra={"distribution": agg["distribution"]}, confidence=0.7)
                state.add_evidence(ev)
                claim = make_claim(f"Aggregate news sentiment across {agg['article_count']} recent articles is {agg['aggregate_label']} (mean score {agg['aggregate_score']}).", [ev], origin="news_agent")
                state.add_claim(claim)
                claims.append(claim.claim_id)

            events = _detect_events(analyzed, state.symbol)
            state.events = events
            for event in events:
                if event["verification"] == "verified":
                    ev = make_evidence("event", f"Verified event ({event['event_type']}): {event['description']}", event["source"], "news", source_url=event.get("url"), as_of=event.get("date"), confidence=0.85)
                    state.add_evidence(ev)
                    claim = make_claim(f"Verified event: {event['description']}", [ev], origin="event_detection")
                    state.add_claim(claim)

            summary = await generate_section(ctx.llm, "Summarize recent news for this company: key themes, notable events, and aggregate sentiment. Cite article titles and sources. Note that sentiment is an analytical signal, not a return predictor.", {"articles": [{"title": a["title"], "source": a["source"], "published": a["published_at"], "sentiment": a["sentiment_label"]} for a in analyzed[:10]], "aggregate_sentiment": {"label": agg["aggregate_label"], "score": agg["aggregate_score"]}, "events": events})
            return {"summary": summary, "findings": findings, "claims": claims, "events": events}

        return await _run_agent(ctx, self, _inner)


_EVENT_PATTERNS = [
    ("earnings", ["earnings", "quarterly results", "q1 results", "q2 results", "q3 results", "q4 results", "reports q", "revenue beats", "revenue misses"]),
    ("acquisition", ["acquisition", "acquires", "acquire", "merger", "to buy", "takeover", "deal to acquire"]),
    ("leadership", ["ceo", "cfo", "executive", "leadership", "appoints", "resign", "steps down", "names new"]),
    ("product", ["launch", "launches", "unveils", "unveil", "introduces", "introduce", "debut", "rolls out", "roll out", "new product", "new service", "release date"]),
    ("regulatory", ["regulator", "regulatory", "lawsuit", "sues", "sued", "investigation", "probe", "fine", "settlement", "antitrust", "sec files", "doj"]),
    ("guidance", ["guidance", "outlook", "forecast", "raises guidance", "cuts guidance", "lowers guidance", "full-year view"]),
]


def _detect_events(articles: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    """Rule-based event detection over titles. 'verified' = explicitly stated in a
    source title (rule match); 'inferred' = classified by pattern only, pending fact-check."""
    events: list[dict[str, Any]] = []
    for a in articles:
        title_l = a["title"].lower()
        for event_type, keywords in _EVENT_PATTERNS:
            hit = next((kw for kw in keywords if kw in title_l), None)
            if hit:
                events.append(
                    {
                        "event_type": event_type,
                        "description": sanitize_untrusted(a["title"], max_len=300),
                        "date": a["published_at"],
                        "source": a["source"],
                        "url": a["url"],
                        "matched_keyword": hit,
                        "sentiment": a.get("sentiment_label"),
                        "verification": "verified" if hit in ["earnings", "acquisition", "lawsuit", "investigation", "probe"] else "inferred",
                    }
                )
                break
    seen = set()
    deduped: list[dict[str, Any]] = []
    for e in events:
        key = (e["event_type"], e["description"][:80])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    return deduped[:10]


class RiskAgent(BaseAgent):
    name = "risk"

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        async def _inner() -> dict[str, Any]:
            state = ctx.state
            risks: list[dict[str, Any]] = []

            market = state.market
            closes = [p["close"] for p in market.get("points", []) if p.get("close")]
            if closes:
                from app.analytics.technical import annualized_volatility, daily_returns, max_drawdown

                vol = annualized_volatility(daily_returns(closes))
                if vol is not None and vol > 40:
                    sev = "high" if vol > 60 else "medium"
                    risks.append({"category": "Market Risk", "risk": f"Elevated realized volatility: {vol:.0f}% annualized vs ~15-20% for broad indices (historical context)", "severity": sev, "evidence": "computed from daily closes (Yahoo Finance)", "basis": "computed"})
                mdd = max_drawdown(closes)
                if mdd is not None and mdd < -30:
                    risks.append({"category": "Market Risk", "risk": f"Large historical drawdown of {mdd:.0f}% occurred within the analysis window", "severity": "medium" if mdd > -50 else "high", "evidence": "computed from daily closes", "basis": "computed"})

            fund = state.fundamentals.get("derived", {}).get("latest", {})
            d2e = fund.get("ratios", {}).get("debt_to_equity", {})
            if d2e.get("value") is not None and d2e["value"] > 2.0:
                risks.append({"category": "Financial Risk", "risk": f"Total liabilities are {d2e['value']:.1f}x stockholders' equity", "severity": "high" if d2e["value"] > 4 else "medium", "evidence": "SEC 10-K balance sheet data", "basis": "computed"})
            if fund.get("net_income", {}).get("value") is not None and fund["net_income"]["value"] < 0:
                risks.append({"category": "Financial Risk", "risk": f"Latest fiscal year net income is negative ({fund['net_income']['value']})", "severity": "high", "evidence": "SEC 10-K income statement", "basis": "computed"})
            rev_growth = fund.get("revenue_growth_yoy", {}).get("value")
            if rev_growth is not None and rev_growth < 0:
                risks.append({"category": "Financial Risk", "risk": f"Revenue declined {abs(rev_growth):.1f}% YoY in latest fiscal year", "severity": "medium", "evidence": "SEC 10-K revenue facts", "basis": "computed"})
            if not state.fundamentals.get("metrics"):
                risks.append({"category": "Data Quality Risk", "risk": "No SEC XBRL fundamentals available for this ticker (foreign listing or no filings matched)", "severity": "low", "evidence": "SEC EDGAR lookup returned no annual facts", "basis": "observed"})

            neg = state.sentiment.get("distribution", {}).get("negative", 0)
            total = state.sentiment.get("article_count", 0) or 1
            if total and neg / total > 0.5 and total >= 4:
                risks.append({"category": "Company-Specific Risk", "risk": f"Majority of recent news articles ({neg}/{total}) carry negative sentiment", "severity": "medium", "evidence": "lexicon sentiment over Google News sample", "basis": "computed"})

            for e in state.events:
                if e.get("event_type") == "regulatory":
                    risks.append({"category": "Regulatory Risk", "risk": f"Regulatory/legal event reported: {e['description'][:140]}", "severity": "medium", "evidence": f"news source: {e.get('source')}", "basis": "reported"})
                    break

            if len(state.errors) > 0:
                risks.append({"category": "Data Quality Risk", "risk": f"{len(state.errors)} data-collection issue(s) occurred during this research run", "severity": "low", "evidence": "; ".join(state.errors[:2])[:200], "basis": "observed"})

            risks.append({"category": "Model Risk", "risk": "Sentiment lexicon and event detection are heuristic; technical indicators are descriptive, not predictive", "severity": "low", "evidence": "platform methodology (see docs)", "basis": "methodology"})

            state.risks = risks
            summary = await generate_section(ctx.llm, "Summarize the identified risks with their evidence. Use neutral language. Only reference the listed risks — do not invent new ones.", {"risks": risks})
            return {"summary": summary, "findings": risks, "claims": []}

        return await _run_agent(ctx, self, _inner)


class FactCheckerAgent(BaseAgent):
    """Verifies claims against the evidence layer. Classifies:
    SUPPORTED / PARTIALLY_SUPPORTED / CONTRADICTED / INSUFFICIENT_EVIDENCE."""

    name = "fact_checker"

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        async def _inner() -> dict[str, Any]:
            state = ctx.state
            verifications: list[dict[str, Any]] = []

            for claim_id, claim in state.claims.items():
                evs = state.evidence_for_claim(claim_id)
                if not evs:
                    claim.verification = "INSUFFICIENT_EVIDENCE"
                else:
                    numeric_ok = True
                    for ev in evs:
                        payload_val = ev.payload.value
                        if payload_val is not None and claim.statement and any(ch.isdigit() for ch in claim.statement):
                            num_in_claim = _extract_first_number(claim.statement)
                            num_in_payload = round(payload_val, 2)
                            if num_in_claim is not None and abs(num_in_claim - num_in_payload) > max(0.11 * abs(num_in_payload), 0.05):
                                numeric_ok = False
                    claim.verification = "SUPPORTED" if numeric_ok else "PARTIALLY_SUPPORTED"
                verifications.append({"claim_id": claim_id, "statement": claim.statement[:200], "verification": claim.verification, "n_evidence": len(evs)})

            contradictions = _find_contradictions(state)
            state.contradictions = contradictions

            obs.incr("claims_verified", n=len(verifications))
            supported = sum(1 for v in verifications if v["verification"] == "SUPPORTED")
            return {
                "summary": f"Verified {len(verifications)} claims: {supported} SUPPORTED, {len(verifications) - supported} flagged for review. {len(contradictions)} contradiction group(s) detected.",
                "findings": verifications,
                "claims": list(state.claims.keys()),
                "contradictions": contradictions,
            }

        return await _run_agent(ctx, self, _inner)


def _extract_first_number(text: str) -> float | None:
    import re

    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(m.group()) if m else None


def _find_contradictions(state: WorkflowState) -> list[dict[str, Any]]:
    """Detect contradictions: numeric disagreement between evidence items, or opposing signals."""
    groups: list[dict[str, Any]] = []

    tech = state.technical.get("signals", [])
    rsi_sig = next((s for s in tech if s["indicator"] == "rsi14"), None)
    macd_sig = next((s for s in tech if s["indicator"] == "macd"), None)
    sma_sig = next((s for s in tech if s["indicator"] == "sma_cross"), None)
    if rsi_sig and macd_sig and sma_sig:
        rsi_overbought = "overbought" in rsi_sig["reading"]
        macd_bearish = macd_sig["reading"] == "bearish"
        sma_up = "golden" in sma_sig["reading"]
        if (rsi_overbought or macd_bearish) and sma_up:
            groups.append({"group": "technical_signals", "description": "Mixed technical signals: moving-average trend is upward while momentum indicator reads stretched/weak", "evidence": [rsi_sig["note"], macd_sig["note"], sma_sig["note"]], "resolution": "Present both readings — indicators measure different lookbacks and can legitimately disagree."})

    if state.sentiment.get("aggregate_label") in ("negative",) and sma_sig and "golden" in sma_sig.get("reading", ""):
        groups.append({"group": "price_vs_sentiment", "description": "Recent news sentiment is negative while medium-term price trend is upward", "evidence": [f"aggregate sentiment: {state.sentiment.get('aggregate_label')} (score {state.sentiment.get('aggregate_score')})", sma_sig["note"]], "resolution": "Sentiment reflects recent news sampling; price trend reflects a longer window. Both are shown without overriding either."})

    fund = state.fundamentals.get("derived", {}).get("latest", {})
    rev_growth = fund.get("revenue_growth_yoy", {}).get("value")
    ni_growth = fund.get("net_income_growth_yoy", {}).get("value")
    if rev_growth is not None and ni_growth is not None and abs(rev_growth - ni_growth) > 50:
        groups.append({"group": "revenue_vs_earnings", "description": f"Revenue growth ({rev_growth:+.1f}%) and net-income growth ({ni_growth:+.1f}%) diverge significantly in the latest fiscal year", "evidence": ["SEC 10-K XBRL facts"], "resolution": "Report both figures with their periods; the divergence itself is a finding, not an error."})

    return groups


class ResearchSynthesizerAgent(BaseAgent):
    """Combines verified findings into the final structured report.
    Cannot override source evidence: every section is generated from verified state only."""

    name = "research_synthesizer"

    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        async def _inner() -> dict[str, Any]:
            state = ctx.state
            supported_claims = [c for c in state.claims.values() if c.verification == "SUPPORTED"]
            [c for c in state.claims.values() if c.verification == "PARTIALLY_SUPPORTED"]
            insufficient = [c for c in state.claims.values() if c.verification == "INSUFFICIENT_EVIDENCE"]

            bull = _build_bull_case(state)
            bear = _build_bear_case(state)
            unknowns = _build_unknowns(state, insufficient)
            state.bull_case = bull
            state.bear_case = bear
            state.key_unknowns = unknowns

            exec_data = {
                "symbol": state.symbol,
                "company_name": state.company_name,
                "supported_claims": [c.statement for c in supported_claims[:12]],
                "market_summary": state.agent_results.get("market_data", {}).get("summary", "")[:600],
                "fundamental_summary": state.agent_results.get("fundamental_analysis", {}).get("summary", "")[:600],
                "news_summary": state.agent_results.get("news_intelligence", {}).get("summary", "")[:600],
                "top_risks": [r["risk"][:160] for r in state.risks[:5]],
            }
            exec_summary = await generate_section(ctx.llm, "Write a 4-6 sentence executive summary of this company's situation based strictly on the supported claims and agent summaries. Neutral research language only. No advice, no predictions.", exec_data)

            state.report = {
                "executive_summary": exec_summary,
                "bull_case": bull,
                "bear_case": bear,
                "key_unknowns": unknowns,
            }
            obs.incr("report_generated")
            return {"summary": "Report synthesized from verified claims.", "findings": [], "claims": []}

        return await _run_agent(ctx, self, _inner)


def _build_bull_case(state: WorkflowState) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    fund = state.fundamentals.get("derived", {}).get("latest", {})
    rg = fund.get("revenue_growth_yoy", {}).get("value")
    if rg is not None and rg > 0:
        out.append({"argument": f"Revenue grew {rg:+.1f}% in the latest fiscal year (SEC 10-K)", "evidence": "SEC EDGAR XBRL revenue facts"})
    ni = fund.get("net_income", {})
    if ni.get("value") is not None and ni["value"] > 0:
        out.append({"argument": f"Profitable in latest fiscal year: net income {ni['value']}", "evidence": "SEC 10-K income statement"})
    margin = fund.get("ratios", {}).get("net_profit_margin", {})
    if margin.get("value") is not None and margin["value"] > 0.10:
        out.append({"argument": f"Net profit margin of {margin['value']*100:.1f}% (period {margin.get('period')})", "evidence": "derived from SEC 10-K facts"})
    sma_sig = next((s for s in state.technical.get("signals", []) if s["indicator"] == "sma_cross"), None)
    if sma_sig and "golden" in sma_sig["reading"]:
        out.append({"argument": f"Medium-term price trend positive: {sma_sig['note']}", "evidence": "computed from daily closes"})
    if state.sentiment.get("aggregate_label") == "positive":
        out.append({"argument": f"Aggregate recent news sentiment positive (score {state.sentiment.get('aggregate_score')})", "evidence": f"{state.sentiment.get('article_count')} articles, lexicon analysis"})
    for e in state.events:
        if e["event_type"] == "product" or (e["event_type"] == "guidance" and "raise" in e["description"].lower()):
            if e.get("sentiment") in ("positive", None):
                out.append({"argument": f"Recent development: {e['description'][:140]}", "evidence": f"news source: {e.get('source')}"})
                break
    return out


def _build_bear_case(state: WorkflowState) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    fund = state.fundamentals.get("derived", {}).get("latest", {})
    rg = fund.get("revenue_growth_yoy", {}).get("value")
    if rg is not None and rg < 0:
        out.append({"argument": f"Revenue declined {abs(rg):.1f}% in the latest fiscal year (SEC 10-K)", "evidence": "SEC EDGAR XBRL revenue facts"})
    ni = fund.get("net_income", {})
    if ni.get("value") is not None and ni["value"] < 0:
        out.append({"argument": f"Net loss in latest fiscal year: {ni['value']}", "evidence": "SEC 10-K income statement"})
    d2e = fund.get("ratios", {}).get("debt_to_equity", {})
    if d2e.get("value") is not None and d2e["value"] > 1.5:
        out.append({"argument": f"Leverage elevated: total liabilities are {d2e['value']:.1f}x equity", "evidence": "SEC 10-K balance sheet"})
    closes = [p["close"] for p in state.market.get("points", []) if p.get("close")]
    if closes:
        from app.analytics.technical import max_drawdown

        mdd = max_drawdown(closes)
        if mdd is not None and mdd < -20:
            out.append({"argument": f"Historical drawdown of {mdd:.0f}% occurred within the window — downside risk materialized before", "evidence": "computed from daily closes"})
    sma_sig = next((s for s in state.technical.get("signals", []) if s["indicator"] == "sma_cross"), None)
    if sma_sig and "death" in sma_sig["reading"]:
        out.append({"argument": f"Medium-term price trend negative: {sma_sig['note']}", "evidence": "computed from daily closes"})
    if state.sentiment.get("aggregate_label") == "negative":
        out.append({"argument": f"Aggregate recent news sentiment negative (score {state.sentiment.get('aggregate_score')})", "evidence": f"{state.sentiment.get('article_count')} articles, lexicon analysis"})
    for e in state.events:
        if e["event_type"] == "regulatory":
            out.append({"argument": f"Regulatory/legal development: {e['description'][:140]}", "evidence": f"news source: {e.get('source')}"})
            break
    return out


def _build_unknowns(state: WorkflowState, insufficient_claims: list[Any]) -> list[str]:
    unknowns: list[str] = []
    fund = state.fundamentals.get("derived", {}).get("latest", {})
    if not state.fundamentals.get("metrics"):
        unknowns.append("Fundamental analysis unavailable: no SEC XBRL annual facts matched this ticker")
    if fund and not fund.get("revenue", {}).get("value"):
        unknowns.append("Revenue could not be determined from available SEC facts")
    if not state.market.get("points"):
        unknowns.append("Market data unavailable for this symbol")
    if state.sentiment.get("article_count", 0) < 3:
        unknowns.append("News sample too small for a meaningful aggregate sentiment reading")
    unknowns.append("No forward-looking guidance, analyst estimates, or valuation multiples are computed by this platform")
    unknowns.append("News snippets are short excerpts; full-article text is not retrieved (licensing)")
    for c in insufficient_claims[:3]:
        unknowns.append(f"Unresolved claim (insufficient evidence): {c.statement[:120]}")
    return unknowns


ALL_AGENTS = [MarketDataAgent(), TechnicalAnalysisAgent(), FundamentalAnalysisAgent(), NewsIntelligenceAgent(), RiskAgent()]
