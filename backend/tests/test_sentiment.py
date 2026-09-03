"""Sentiment engine tests: polarity, negation, modifiers, aggregation."""
import pytest

from app.analytics.sentiment import aggregate_sentiment, analyze_article, analyze_text


class TestPolarity:
    def test_positive(self):
        r = analyze_text("Company beats earnings expectations with record revenue growth")
        assert r["label"] == "positive"
        assert r["score"] > 0

    def test_negative(self):
        r = analyze_text("Shares plunge after company warns of massive losses")
        assert r["label"] == "negative"
        assert r["score"] < 0

    def test_neutral(self):
        r = analyze_text("The company announced its quarterly schedule")
        assert r["label"] == "neutral"

    def test_empty(self):
        r = analyze_text("")
        assert r["label"] == "neutral" and r["score"] == 0.0


class TestNegation:
    def test_negation_flips(self):
        pos = analyze_text("company beats estimates")
        neg = analyze_text("company does not beat estimates")
        assert pos["label"] == "positive"
        assert neg["score"] < pos["score"]

    def test_booster_amplifies(self):
        plain = analyze_text("stock rises")
        boosted = analyze_text("stock sharply rises")
        assert abs(boosted["score"]) > abs(plain["score"])


class TestAggregate:
    def _arts(self, labels_scores):
        from datetime import datetime, timezone

        return [
            {"title": f"article {i}", "source": "s", "url": f"http://x/{i}", "published_at": datetime.now(timezone.utc), "sentiment_label": lab, "sentiment_score": sc}
            for i, (lab, sc) in enumerate(labels_scores)
        ]

    def test_aggregate_positive(self):
        arts = self._arts([("positive", 0.6), ("positive", 0.5), ("neutral", 0.0)])
        agg = aggregate_sentiment(arts)
        assert agg["aggregate_label"] == "positive"
        assert agg["distribution"]["positive"] == 2

    def test_aggregate_empty(self):
        agg = aggregate_sentiment([])
        assert agg["aggregate_label"] == "neutral"
        assert agg["article_count"] == 0

    def test_trend_series(self):
        agg = aggregate_sentiment(self._arts([("positive", 0.5), ("negative", -0.5), ("positive", 0.4)]))
        assert isinstance(agg["trend"], list)


class TestArticle:
    def test_article_combines_title_summary(self):
        r = analyze_article("Company reports strong results", "however lawsuit filed against firm")
        assert r["label"] in ("positive", "neutral", "negative")
        assert r["title_label"] == "positive"
        terms = [m["term"] for m in r["matched_terms"]]
        assert "strong" in terms and "lawsuit" in terms  # both title and summary contributed

    def test_negative_title_negative_label(self):
        r = analyze_article("Shares plunge on weak outlook", "")
        assert r["label"] == "negative"
