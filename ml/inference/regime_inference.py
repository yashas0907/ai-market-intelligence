"""Inference: apply the trained regime classifier to a live symbol's features."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))

from ml.features.build_features import FEATURE_COLS, REGIMES  # noqa: E402


def load_model(artifact_path: str | Path):
    import joblib

    return joblib.load(artifact_path)


def predict_regime(bundle: dict, closes: pd.Series, volumes: pd.Series | None) -> dict:
    """Predict regime for the LATEST day. Features use trailing windows only."""
    from ml.features.build_features import build_features

    feats = build_features(closes, volumes)
    X = feats[FEATURE_COLS]
    latest = X.tail(1)
    model = bundle["model"]
    proba = model.predict_proba(latest)[0]
    classes = list(model.classes_)
    idx = int(model.predict(latest)[0])
    return {
        "regime": classes[idx],
        "probabilities": {c: round(float(p), 4) for c, p in zip(classes, proba)},
        "as_of": str(closes.index[-1]),
        "note": "Classifies current conditions into volatility regimes learned from past data. Historical/analytical output — not a forecast of returns.",
    }


def regime_history(bundle: dict, closes: pd.Series, volumes: pd.Series | None, last_n: int = 30) -> list[dict]:
    from ml.features.build_features import build_features

    feats = build_features(closes, volumes)
    X = feats[FEATURE_COLS].dropna()
    preds = bundle["model"].predict(X.tail(last_n))
    return [{"date": str(d.date()), "regime": p} for d, p in zip(X.tail(last_n).index, preds)]
