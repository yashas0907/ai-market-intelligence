"""Deterministic lexicon-based news sentiment with negation handling.

Methodology (documented in docs/evaluation.md):
- Finance-tuned lexicon (positive/negative term weights) applied to title + snippet.
- Negation window flips polarity of following scored term within 3 tokens.
- Intensity modifiers (boosters/downtoners) scale the following term.
- Output: label (positive/negative/neutral) + score in [-1, 1].
Limitations: no irony/sarcasm detection; snippet-only (short input); English only.
"""
from __future__ import annotations

import math
import re
from typing import Any

_POSITIVE = {
    "beat": 2.0, "beats": 2.0, "surge": 2.0, "surges": 2.0, "soar": 2.0, "soars": 2.0, "record": 1.6, "rally": 1.8,
    "rallies": 1.8, "gain": 1.2, "gains": 1.2, "rose": 1.2, "rises": 1.2, "growth": 1.4, "strong": 1.4, "upgrade": 1.8,
    "upgraded": 1.8, "outperform": 1.6, "profit": 1.2, "bullish": 1.8, "optimism": 1.2, "expands": 1.0, "expansion": 1.0,
    "partnership": 1.2, "launch": 1.0, "innovation": 1.0, "dividend": 0.8, "buyback": 1.2, "repurchase": 1.0, "boost": 1.2,
    "boosts": 1.2, "high": 0.6, "up": 0.6, "jump": 1.6, "jumps": 1.6, "climb": 1.2, "rebound": 1.4, "raise": 1.0,
    "raises": 1.0, "raised": 1.0, "exceeds": 1.8, "tops": 1.4, "positive": 1.0, "improve": 1.0, "improved": 1.0,
    "improvement": 1.0, "success": 1.2, "wins": 1.2, "win": 1.0, "approved": 1.0, "approves": 1.0, "approval": 1.2,
    "breakthrough": 1.8, "optimistic": 1.2, "upside": 1.0, "accumulate": 0.8, "outlook": 0.3, "advance": 1.0, "advances": 1.0,
}
_NEGATIVE = {
    "miss": -2.0, "misses": -2.0, "missed": -2.0, "plunge": -2.0, "plunges": -2.0, "slump": -1.8, "slumps": -1.8,
    "drop": -1.2, "drops": -1.2, "fell": -1.2, "falls": -1.2, "decline": -1.2, "declines": -1.2, "loss": -1.4, "losses": -1.4,
    "weak": -1.4, "downgrade": -1.8, "downgraded": -1.8, "underperform": -1.6, "bearish": -1.8, "pessimism": -1.2,
    "cut": -1.4, "cuts": -1.4, "warning": -1.6, "warns": -1.6, "warned": -1.6, "lawsuit": -1.8, "sued": -1.6, "probe": -1.4,
    "investigation": -1.6, "fraud": -2.4, "recall": -1.6, "layoff": -1.4, "layoffs": -1.6, "bankruptcy": -2.8,
    "insolvency": -2.4, "debt": -0.6, "default": -2.0, "risk": -0.6, "risks": -0.8, "concern": -1.0, "concerns": -1.2,
    "slowdown": -1.4, "recession": -2.0, "inflation": -0.8, "low": -0.6, "down": -0.6, "fall": -1.0, "tumble": -1.8,
    "tumbles": -1.8, "crash": -2.4, "sell-off": -1.8, "selloff": -1.8, "sink": -1.6, "sinks": -1.6, "slide": -1.2,
    "slides": -1.2, "revised": -0.4, "delay": -1.0, "delayed": -1.0, "halt": -1.4, "halted": -1.6, "suspend": -1.4,
    "suspended": -1.6, "negative": -1.0, "worse": -1.2, "declined": -1.2, "disappointing": -1.6, "shortfall": -1.8,
    "regulatory": -0.8, "scrutiny": -1.0, "fine": -1.4, "fined": -1.6, "penalty": -1.6, "volatility": -0.8, "uncertainty": -1.0,
}
_NEGATIONS = {"not", "no", "never", "without", "n't", "isn't", "wasn't", "didn't", "doesn't", "won't", "can't", "fails to", "lack of", "lacks"}
_BOOSTERS = {"sharply": 1.5, "significantly": 1.5, "massive": 1.8, "record-breaking": 2.0, "strongly": 1.5, "substantial": 1.4, "suddenly": 1.2, "surging": 1.6}
_DOWNTONERS = {"slightly": 0.5, "mildly": 0.6, "somewhat": 0.7, "marginally": 0.5, "modest": 0.6, "modestly": 0.6, "partially": 0.6}

_TOKEN_RE = re.compile(r"[a-z'-]+")


def analyze_text(text: str) -> dict[str, Any]:
    tokens = _TOKEN_RE.findall((text or "").lower())
    if not tokens:
        return {"label": "neutral", "score": 0.0, "matched_terms": []}

    matched: list[dict[str, Any]] = []
    total = 0.0
    i = 0
    while i < len(tokens):
        t = tokens[i]
        polarity = _POSITIVE.get(t) or _NEGATIVE.get(t)
        if polarity is not None:
            weight = polarity
            negated = False
            window = tokens[max(0, i - 3) : i]
            for w in window:
                if w in _NEGATIONS:
                    negated = True
                    break
            if negated:
                weight = -weight
            if i > 0 and tokens[i - 1] in _BOOSTERS:
                weight *= _BOOSTERS[tokens[i - 1]]
            elif i > 0 and tokens[i - 1] in _DOWNTONERS:
                weight *= _DOWNTONERS[tokens[i - 1]]
            total += weight
            matched.append({"term": t, "weight": round(weight, 2), "negated": negated})
        i += 1

    if total == 0 or not matched:
        return {"label": "neutral", "score": 0.0, "matched_terms": matched}

    norm = 1.0 - 1.0 / (1.0 + abs(total) / 3.0)
    score = math.copysign(norm, total)
    label = "positive" if score > 0.15 else ("negative" if score < -0.15 else "neutral")
    return {"label": label, "score": round(score, 3), "matched_terms": matched}


def analyze_article(title: str, summary: str = "") -> dict[str, Any]:
    combined = f"{title}. {summary}"
    result = analyze_text(combined)
    title_only = analyze_text(title)
    return {**result, "title_label": title_only["label"]}


def aggregate_sentiment(articles: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [a["sentiment_score"] for a in articles if a.get("sentiment_score") is not None]
    labels = [a["sentiment_label"] or "neutral" for a in articles]
    if not scores:
        return {"aggregate_label": "neutral", "aggregate_score": 0.0, "article_count": len(articles), "distribution": {"positive": 0, "neutral": 0, "negative": 0}, "trend": [], "note": "no scored articles"}
    from collections import Counter

    dist = Counter(labels)
    avg = sum(scores) / len(scores)
    agg_label = "positive" if avg > 0.15 else ("negative" if avg < -0.15 else "neutral")
    by_day: dict[str, list[float]] = {}
    for a in articles:
        if a.get("sentiment_score") is None:
            continue
        pub = a.get("published_at")
        day = pub.strftime("%Y-%m-%d") if hasattr(pub, "strftime") else str(pub)[:10]
        by_day.setdefault(day, []).append(a["sentiment_score"])
    trend = [{"day": d, "avg_score": round(sum(v) / len(v), 3), "n": len(v)} for d, v in sorted(by_day.items())][-30:]
    return {
        "aggregate_label": agg_label,
        "aggregate_score": round(avg, 3),
        "article_count": len(articles),
        "distribution": {"positive": dist.get("positive", 0), "neutral": dist.get("neutral", 0), "negative": dist.get("negative", 0)},
        "trend": trend,
        "note": "Lexicon-based sentiment; analytical signal with limitations — does not predict returns.",
    }
