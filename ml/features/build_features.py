"""Volatility regime classifier — the platform's ML component.

Task: classify each trading day into a volatility regime (LOW / NORMAL / ELEVATED / HIGH)
based on features computable from PAST data only.

Target construction (labels):
  forward_vol_20d = annualized std of next 20 daily returns (realized forward volatility)
  Regime = quartile bucket of forward_vol_20d within the training set:
    LOW < Q25, NORMAL [Q25, Q75), ELEVATED [Q75, Q90), HIGH >= Q90

Leakage prevention (NON-NEGOTIABLE, enforced in tests):
  1. Features at time t use ONLY data up to and including t (rolling windows ending at t).
  2. Labels use returns from t+1 ... t+20 (strictly future) — this is the prediction
     target, so label leakage is by definition excluded from features.
  3. Chronological split: oldest 60% train, next 20% validation, newest 20% test.
     NO shuffling. Test period is strictly AFTER training period.
  4. Quartile thresholds for bucketing are computed on TRAIN only, then applied to val/test.
  5. Feature medians for imputation computed on TRAIN only.

Models: logistic regression (linear baseline) and gradient boosting (non-linear),
both from scikit-learn. Selection on validation, reported on test.
Limitations: regimes describe PAST realized volatility structure; not a trading signal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def build_features(closes: pd.Series, volumes: pd.Series | None = None) -> pd.DataFrame:
    """Features at row t use only info up to t. All rolling windows are trailing."""
    df = pd.DataFrame({"close": closes.astype(float)})
    ret = df["close"].pct_change()

    df["ret_1d"] = ret
    df["ret_5d"] = df["close"].pct_change(5)
    df["vol_10d"] = ret.rolling(10, min_periods=6).std()
    df["vol_20d"] = ret.rolling(20, min_periods=12).std()
    df["vol_60d"] = ret.rolling(60, min_periods=30).std()
    df["dollar_vol_20d_chg"] = np.log(df["close"]).rolling(20).std().pct_change()
    df["range_10d"] = (df["close"].rolling(10).max() - df["close"].rolling(10).min()) / df["close"]
    df["drawdown_60d"] = df["close"] / df["close"].rolling(60, min_periods=20).max() - 1.0
    df["atr_ratio_20d"] = (df["close"].rolling(20).max() - df["close"].rolling(20).min()) / (df["close"].rolling(20).std() + 1e-9)
    if volumes is not None:
        v = volumes.astype(float)
        df["volume_z_20d"] = (v - v.rolling(20, min_periods=10).mean()) / (v.rolling(20, min_periods=10).std() + 1e-9)
        df["volume_trend_10d"] = v.rolling(10, min_periods=5).mean() / (v.rolling(60, min_periods=20).mean() + 1e-9)
    return df


FEATURE_COLS = [
    "ret_1d", "ret_5d", "vol_10d", "vol_20d", "vol_60d", "dollar_vol_20d_chg",
    "range_10d", "drawdown_60d", "atr_ratio_20d", "volume_z_20d", "volume_trend_10d",
]


def build_target(closes: pd.Series, horizon: int = 20) -> pd.Series:
    """Forward realized volatility (annualized) — the label. Uses ONLY future returns by construction."""
    ret = closes.astype(float).pct_change()
    fwd = ret.shift(-horizon).rolling(horizon, min_periods=10).std().shift(-horizon + 1)
    # fwd at time t = std of returns over (t+1 ... t+horizon)
    fwd = ret.rolling(horizon).std().shift(-horizon)
    fwd = fwd * np.sqrt(252) * 100.0
    return fwd


REGIMES = ["LOW", "NORMAL", "ELEVATED", "HIGH"]


def bucket_labels(fwd_vol: pd.Series, q25: float, q75: float, q90: float) -> pd.Series:
    """Apply TRAIN-derived thresholds to any split. No test statistics used."""
    def _bucket(v: float) -> str:
        if np.isnan(v):
            return "UNKNOWN"
        if v < q25:
            return "LOW"
        if v < q75:
            return "NORMAL"
        if v < q90:
            return "ELEVATED"
        return "HIGH"

    return fwd_vol.map(_bucket)


def chronological_split(df: pd.DataFrame, train_frac: float = 0.6, val_frac: float = 0.2) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Strict chronological split: train < validation < test in time. Never shuffled."""
    n = len(df)
    if n < 100:
        raise ValueError(f"need >=100 rows for a meaningful temporal split, got {n}")
    train_end = int(n * train_frac)
    val_end = int(n * (train_frac + val_frac))
    train = df.iloc[:train_end]
    val = df.iloc[train_end:val_end]
    test = df.iloc[val_end:]
    assert train.index.max() < val.index.min() <= val.index.max() < test.index.min(), "chronology violated"
    return train, val, test
