"""Train + select volatility-regime classifier with strict temporal integrity."""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, classification_report, confusion_matrix
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.features.build_features import (  # noqa: E402
    FEATURE_COLS,
    REGIMES,
    bucket_labels,
    build_features,
    build_target,
    chronological_split,
)

MODEL_VERSION = "1.0.0"


def train_regime_classifier(closes: pd.Series, volumes: pd.Series | None, symbol: str, save: bool = True) -> dict:
    """Full temporal pipeline: features → labels(train thresholds) → chronological split →
    fit candidates → select on validation → evaluate once on test."""
    feats = build_features(closes, volumes)
    fwd_vol = build_target(closes)
    df = feats.copy()
    df["fwd_vol"] = fwd_vol

    usable = df[["close", "fwd_vol"] + FEATURE_COLS].dropna(subset=["fwd_vol"])
    usable = usable[FEATURE_COLS + ["fwd_vol"]]
    if len(usable) < 120:
        raise ValueError(f"insufficient labeled data after feature/target windows: {len(usable)} rows (need >=120)")

    train, val, test = chronological_split(usable)

    # Label thresholds from TRAIN only
    q25 = float(train["fwd_vol"].quantile(0.25))
    q75 = float(train["fwd_vol"].quantile(0.75))
    q90 = float(train["fwd_vol"].quantile(0.90))

    for split in (train, val, test):
        split["label"] = bucket_labels(split["fwd_vol"], q25, q75, q90)

    candidates = {
        "logistic_regression": Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("clf", LogisticRegression(max_iter=2000, class_weight="balanced", C=1.0)),
        ]),
        "gradient_boosting": Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("clf", GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42)),
        ]),
    }

    # Fit imputer-related stats on train only (inside pipeline, fit on train)
    results: dict[str, dict] = {}
    for name, model in candidates.items():
        model.fit(train[FEATURE_COLS], train["label"])
        val_pred = model.predict(val[FEATURE_COLS])
        results[name] = {
            "val_accuracy": round(float(accuracy_score(val["label"], val_pred)), 4),
            "val_f1_macro": round(float(f1_score(val["label"], val_pred, average="macro", labels=REGIMES, zero_division=0)), 4),
        }

    best_name = max(results, key=lambda k: results[k]["val_f1_macro"])
    best = candidates[best_name]

    test_pred = best.predict(test[FEATURE_COLS])
    test_metrics = {
        "accuracy": round(float(accuracy_score(test["label"], test_pred)), 4),
        "f1_macro": round(float(f1_score(test["label"], test_pred, average="macro", labels=REGIMES, zero_division=0)), 4),
    }

    majority = test["label"].value_counts().idxmax()
    baseline = round(float(accuracy_score(test["label"], [majority] * len(test))), 4)

    labels_present = [l for l in REGIMES if l in set(test["label"]) or l in set(val["label"])]

    summary = {
        "symbol": symbol,
        "model_name": best_name,
        "model_version": MODEL_VERSION,
        "task": "volatility_regime_classification",
        "target": "forward 20-day annualized realized volatility bucketed into LOW/NORMAL/ELEVATED/HIGH (train-set quartiles)",
        "n_train": len(train), "n_val": len(val), "n_test": len(test),
        "train_period": f"{train.index.min()} .. {train.index.max()}",
        "val_period": f"{val.index.min()} .. {val.index.max()}",
        "test_period": f"{test.index.min()} .. {test.index.max()}",
        "thresholds": {"q25": round(q25, 2), "q75": round(q75, 2), "q90": round(q90, 2)},
        "candidates": results,
        "test_metrics": test_metrics,
        "majority_baseline_test": baseline,
        "confusion_matrix": confusion_matrix(test["label"], test_pred, labels=labels_present).tolist(),
        "classification_report": classification_report(test["label"], test_pred, labels=labels_present, zero_division=0, output_dict=True),
        "methodology": "Chronological split 60/20/20 (train<val<test in time, no shuffle). Labels use future 20d returns only. Thresholds and imputation fitted on train only. Model selected on validation F1.",
        "limitations": "Regimes describe forward realized volatility structure, not direction. Past regime patterns may not persist; not a trading signal.",
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }

    if save:
        import joblib

        artifacts = Path(__file__).resolve().parents[1] / "artifacts"
        artifacts.mkdir(exist_ok=True)
        path = artifacts / f"regime_{symbol}_{MODEL_VERSION}.joblib"
        joblib.dump({"model": best, "feature_cols": FEATURE_COLS, "thresholds": {"q25": q25, "q75": q75, "q90": q90}, "summary": {k: v for k, v in summary.items() if k != "confusion_matrix"}}, path)
        summary["artifact_path"] = str(path)
    return summary


if __name__ == "__main__":
    import httpx

    sym = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    r = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params={"range": "5y", "interval": "1d"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    res = r.json()["chart"]["result"][0]
    ts = res["timestamp"]
    closes = pd.Series([q for q in res["indicators"]["quote"][0]["close"] if q is not None])
    closes.index = pd.to_datetime([t for t, q in zip(ts, res["indicators"]["quote"][0]["close"]) if q is not None], unit="s", utc=True)
    vols = pd.Series(res["indicators"]["quote"][0]["volume"], index=closes.index).ffill()
    summary = train_regime_classifier(closes, vols, sym)
    print(json.dumps(summary, indent=2, default=str))
