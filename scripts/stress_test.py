"""Multi-company stress test: runs the research pipeline across diverse tickers
and reports per-company behavior (US tech, semis, retail, pharma, auto, non-SEC)."""
from __future__ import annotations

import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

# diverse market-cap / sector / data-availability cases
TICKERS = [
    ("AAPL", "mega-cap tech, SEC filer"),
    ("NVDA", "semiconductor"),
    ("WMT", "retail"),
    ("JPM", "bank (complex balance sheet)"),
    ("PFE", "pharma"),
    ("TSLA", "high volatility"),
    ("KO", "staples"),
    ("SAP", "foreign private issuer (IFRS!)"),
]


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=90)
    print(f"{'ticker':7} {'case':42} {'status':10} {'secs':>5}  claims sup risks  notes")
    for sym, case in TICKERS:
        t0 = time.perf_counter()
        try:
            r = c.post("/api/research", params={"symbol": sym, "depth": "quick"}).json()
            if r.get("deduplicated"):
                rep = r["report"]
                secs = 0.0
            else:
                jid = r["job_id"]
                for _ in range(120):
                    time.sleep(2)
                    st = c.get(f"/api/research/{jid}").json()
                    if st["status"] in ("completed", "failed"):
                        break
                secs = time.perf_counter() - t0
                if st["status"] != "completed":
                    print(f"{sym:7} {case:42} {'FAILED':10} {secs:5.1f}  {st.get('error', '')[:60]}")
                    continue
                rep = st["report"]
            claims = rep.get("claims", [])
            sup = sum(1 for cl in claims if cl["verification"] == "SUPPORTED")
            risks = len(rep.get("risks", []))
            f = rep.get("fundamental_analysis", {}).get("latest", {})
            rev = f.get("revenue", {}).get("value")
            notes = f"rev={'OK' if rev else 'MISSING'}"
            if not rev:
                notes += " (no SEC XBRL — expected for IFRS/foreign filers)"
            print(f"{sym:7} {case:42} {'ok':10} {secs:5.1f}  {len(claims):>5} {sup:>3} {risks:>5}  {notes}")
        except Exception as exc:
            print(f"{sym:7} {case:42} {'EXC':10}       {str(exc)[:70]}")


if __name__ == "__main__":
    main()
