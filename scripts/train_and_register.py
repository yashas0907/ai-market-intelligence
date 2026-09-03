"""Train the volatility-regime model for one or more symbols and save summaries."""
import json
import sys
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from ml.training.train_regime import train_regime_classifier  # noqa: E402


def fetch_closes(symbol: str, rng: str = "5y") -> tuple[pd.Series, pd.Series]:
    r = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}", params={"range": rng, "interval": "1d"}, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    ts = res["timestamp"]
    quote = res["indicators"]["quote"][0]
    rows = [(t, c, v) for t, c, v in zip(ts, quote["close"], quote["volume"]) if c is not None]
    idx = pd.to_datetime([r[0] for r in rows], unit="s", utc=True)
    closes = pd.Series([r[1] for r in rows], index=idx)
    vols = pd.Series([r[2] if r[2] is not None else 0.0 for r in rows], index=idx)
    return closes, vols


def main() -> None:
    symbols = sys.argv[1:] or ["AAPL", "MSFT"]
    out_dir = ROOT / "ml" / "artifacts"
    out_dir.mkdir(exist_ok=True)
    for symbol in symbols:
        print(f"training {symbol} …")
        closes, vols = fetch_closes(symbol)
        summary = train_regime_classifier(closes, vols, symbol)
        summary["feature_cols"] = summary.pop("feature_cols", None) or None
        with open(out_dir / f"summary_{symbol.lower()}.json", "w") as f:
            json.dump(summary, f, indent=2, default=str)
        print(f"  {summary['model_name']}  test_acc={summary['test_metrics']['accuracy']}  baseline={summary['majority_baseline_test']}")
    print(f"summaries saved to {out_dir}")


if __name__ == "__main__":
    main()
