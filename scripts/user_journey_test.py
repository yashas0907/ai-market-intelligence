"""User-POV acceptance test: walks the real user journey against a live server,
measuring response time and payload sanity for every feature.

Usage:  python user_journey_test.py [base_url]   (default http://127.0.0.1:8000)
"""
from __future__ import annotations

import json
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

PASS, FAIL = [], []


def check(name: str, fn) -> None:
    t0 = time.perf_counter()
    try:
        result = fn()
        ms = (time.perf_counter() - t0) * 1000
        if isinstance(result, tuple):
            ok, detail = result
        else:
            ok, detail = result, ""
        (PASS if ok else FAIL).append((name, round(ms), detail))
        print(f"{'PASS' if ok else 'FAIL'}  {ms:7.0f}ms  {name}  {detail}")
    except Exception as exc:
        FAIL.append((name, 0, str(exc)[:150]))
        print(f"FAIL      0ms  {name}  EXC: {str(exc)[:150]}")


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=60)

    # --- 1. Discovery: landing + search ---
    check("health", lambda: c.get("/api/health").json()["status"] == "ok")
    check("search 'apple' finds AAPL", lambda: any(h["symbol"] == "AAPL" for h in c.get("/api/companies/search", params={"q": "apple"}).json()))
    check("search 'microsoft' finds MSFT", lambda: any(h["symbol"] == "MSFT" for h in c.get("/api/companies/search", params={"q": "microsoft"}).json()))
    check("search garbage returns empty/err gracefully", lambda: (r := c.get("/api/companies/search", params={"q": "zzzzqqq!!!"})) and r.status_code in (200, 502))

    # --- 2. Company view ---
    def profile():
        r = c.get("/api/company/AAPL").json()
        return r["name"] == "Apple Inc." or "APPLE" in r["name"].upper(), f"{r['name']}, sector={r.get('sector')}"
    check("profile AAPL", profile)

    def market():
        r = c.get("/api/company/AAPL/market", params={"range": "1y"}).json()
        assert r["count"] > 200, f"only {r['count']} points"
        closes = [p["close"] for p in r["points"] if p["close"]]
        assert all(isinstance(x, float) for x in closes)
        assert r["retrieved_at"], "no freshness stamp"
        return True, f"{r['count']} pts, last={closes[-1]:.2f}"
    check("market 1y OHLCV", market)

    def technical():
        r = c.get("/api/company/AAPL/technical", params={"range": "1y"}).json()
        assert r["statistics"]["annualized_volatility_pct"], "no vol"
        assert r["series"]["close"], "no series for chart"
        return True, f"vol={r['statistics']['annualized_volatility_pct']}%, signals={len(r['signals'])}"
    check("technical indicators", technical)

    def fundamentals():
        r = c.get("/api/company/AAPL/fundamentals").json()
        rev = r["derived"]["latest"].get("revenue", {})
        assert rev.get("value"), "no revenue"
        assert rev.get("period"), "no period — violates reporting-period rule"
        return True, f"rev={rev['value']/1e9:.1f}B period={rev['period']}"
    check("fundamentals w/ period", fundamentals)

    def news():
        r = c.get("/api/company/AAPL/news", params={"limit": 10}).json()
        assert r["articles"], "no news"
        a = r["articles"][0]
        assert a["url"] and a["published_at"] and a["sentiment"] in ("positive", "neutral", "negative")
        return True, f"{len(r['articles'])} articles, top='{a['title'][:40]}…'"
    check("news + sentiment", news)

    # --- 3. Research pipeline (async job) ---
    t0 = time.perf_counter()
    r = c.post("/api/research", params={"symbol": "AAPL", "depth": "standard"}).json()
    job_id = r["job_id"]
    assert not r.get("deduplicated"), "fresh run expected"
    final = None
    for _ in range(100):
        time.sleep(2)
        st = c.get(f"/api/research/{job_id}").json()
        if st["status"] in ("completed", "failed"):
            final = st
            break
    research_ms = (time.perf_counter() - t0) * 1000
    ok = final and final["status"] == "completed"
    det = ""
    if ok:
        rep = final["report"]
        sup = sum(1 for cl in rep["claims"] if cl["verification"] == "SUPPORTED")
        det = f"claims={len(rep['claims'])} supported={sup} sources={len(rep['sources'])} risks={len(rep['risks'])}"
    (PASS if ok else FAIL).append(("research pipeline (standard AAPL)", round(research_ms), det))
    print(f"{'PASS' if ok else 'FAIL'}  {research_ms:7.0f}ms  research pipeline (standard AAPL)  {det}")

    # --- 4. Research dedup (cost control) ---
    t0 = time.perf_counter()
    r2 = c.post("/api/research", params={"symbol": "AAPL", "depth": "standard"}).json()
    dedup_ms = (time.perf_counter() - t0) * 1000
    ok2 = r2.get("deduplicated") is True
    (PASS if ok2 else FAIL).append(("research dedup (2nd identical request)", round(dedup_ms), f"deduplicated={r2.get('deduplicated')}"))
    print(f"{'PASS' if ok2 else 'FAIL'}  {dedup_ms:7.0f}ms  research dedup  deduplicated={r2.get('deduplicated')}")

    # --- 5. Watchlist ---
    check("watchlist add NVDA", lambda: c.post("/api/watchlist", json={"symbol": "NVDA"}).status_code == 201)
    check("watchlist duplicate 409", lambda: c.post("/api/watchlist", json={"symbol": "NVDA"}).status_code == 409)
    check("watchlist list", lambda: any(i["symbol"] == "NVDA" for i in c.get("/api/watchlist").json()["items"]))
    check("watchlist remove", lambda: c.delete("/api/watchlist/NVDA").status_code == 200)

    # --- 6. Compare ---
    def compare():
        r = c.post("/api/compare", json={"symbols": ["AAPL", "MSFT"]}).json()
        g = r["metrics"].get("revenue_growth_yoy", {}).get("values", {})
        assert "AAPL" in g and "MSFT" in g
        assert r["methodology"]
        return True, f"keys={len(r['metrics'])}, aapl_growth={g.get('AAPL')}"
    check("compare AAPL vs MSFT", compare)
    check("compare rejects <2 symbols", lambda: c.post("/api/compare", json={"symbols": ["AAPL"]}).status_code == 422)

    # --- 7. Documents + RAG ---
    def upload():
        files = {"file": ("research.txt", b"Apple services revenue reached record levels in fiscal 2026 with strong margins. The company also faces regulatory scrutiny in the EU over app store practices.", "text/plain")}
        r = c.post("/api/documents/upload", params={"company_symbol": "AAPL"}, files=files)
        assert r.status_code == 200, r.text
        s = c.get("/api/documents/search", params={"q": "services revenue record", "company_symbol": "AAPL"}).json()
        assert s["results"], "retrieval returned nothing"
        return True, f"top_sim={s['results'][0]['similarity']}"
    check("upload + RAG retrieval", upload)
    check("upload rejects .exe", lambda: c.post("/api/documents/upload", params={"company_symbol": "AAPL"}, files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 415)

    # --- 8. Observability + sources ---
    def metrics():
        r = c.get("/api/metrics").json()
        assert r["counters"], "no counters"
        return True, f"{len(r['counters'])} counters, {len(r['timers'])} timers"
    check("metrics endpoint", metrics)
    check("sources health", lambda: len(c.get("/api/sources/health").json()["sources"]) == 4)

    # --- 9. Edge cases / security ---
    check("bad symbol 4xx not 500", lambda: c.get("/api/company/INVALID%20SYM%60").status_code in (404, 422, 502))
    check("research bad symbol rejected", lambda: c.post("/api/research", params={"symbol": "BAD SYMB"}).status_code in (400, 422, 502))
    check("missing research job 404", lambda: c.get("/api/research/nonexistent-id").status_code == 404)
    check("docs page reachable", lambda: c.get("/api/docs").status_code == 200)

    print("\n" + "=" * 70)
    print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
    if PASS:
        avg = sum(p[1] for p in PASS) / len(PASS)
        slow = sorted(PASS, key=lambda p: -p[1])[:5]
        print(f"avg latency (sync features): {avg:.0f}ms")
        print("slowest:", ", ".join(f"{n} {m}ms" for n, m, _ in slow))
    for name, ms, det in FAIL:
        print(f"  FAILED: {name} — {det}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
