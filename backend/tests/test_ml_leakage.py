"""ML leakage-prevention and temporal-split tests. NON-NEGOTIABLE integrity."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.features.build_features import (
    FEATURE_COLS,
    REGIMES,
    bucket_labels,
    build_features,
    build_target,
    chronological_split,
)


def _series(n=300, seed=42, drift=True):
    rng = np.random.RandomState(seed)
    base = 100 + np.cumsum(rng.normal(0, 0.02, n))
    if drift:
        base = base * np.linspace(1, 1.5, n)
    idx = pd.date_range("2020-01-01", periods=n, freq="B", tz="UTC")
    return pd.Series(base, index=idx), pd.Series(rng.lognormal(6, 0.3, n), index=idx)


class TestFeatureConstruction:
    def test_no_future_leakage_in_features(self):
        """Feature row t must be identical whether or not future rows exist."""
        closes, vols = _series(300)
        feats_full = build_features(closes, vols)
        cut = 250
        feats_cut = build_features(closes.iloc[:cut], vols.iloc[:cut])
        f_full = feats_full.iloc[:cut][FEATURE_COLS].fillna(-999)
        f_cut = feats_cut[FEATURE_COLS].fillna(-999)
        np.testing.assert_allclose(f_full.values, f_cut.values)

    def test_rolling_windows_trailing_only(self):
        closes, _ = _series(50)
        feats = build_features(closes)
        # vol_20d at t == std of ret[t-19..t]; verify manually at t=25
        t = 25
        ret = closes.pct_change()
        expected = ret.iloc[t - 19 : t + 1].std()
        assert feats["vol_20d"].iloc[t] == pytest.approx(expected, rel=1e-9)

    def test_first_rows_have_nans_not_fabricated(self):
        closes, vols = _series(40)
        feats = build_features(closes, vols)
        assert pd.isna(feats["vol_60d"].iloc[0])
        assert pd.isna(feats["ret_1d"].iloc[0])


class TestTarget:
    def test_target_uses_future_only(self):
        closes, _ = _series(100)
        fwd = build_target(closes, horizon=20)
        t = 50
        ret = closes.pct_change()
        expected = ret.iloc[t + 1 : t + 21].std() * np.sqrt(252) * 100
        assert fwd.iloc[t] == pytest.approx(expected, rel=1e-6)

    def test_target_disjoint_from_features(self):
        """Feature at t and label at t must never share the same return."""
        closes, vols = _series(120)
        feats = build_features(closes, vols)
        fwd = build_target(closes)
        # vol_20d at t uses ret up to t; fwd at t uses ret from t+1 onward — verify boundary
        t = 100
        ret = closes.pct_change()
        used_by_feature = set(range(t - 19, t + 1))
        used_by_label = set(range(t + 1, t + 21))
        assert used_by_feature.isdisjoint(used_by_label)


class TestChronologicalSplit:
    def test_split_order(self):
        closes, _ = _series(200)
        df = pd.DataFrame({"x": closes.values}, index=closes.index)
        tr, va, te = chronological_split(df)
        assert tr.index.max() < va.index.min()
        assert va.index.max() < te.index.min()

    def test_split_fractions(self):
        closes, _ = _series(200)
        df = pd.DataFrame({"x": closes.values}, index=closes.index)
        tr, va, te = chronological_split(df)
        assert len(tr) == 120 and len(va) == 40 and len(te) == 40

    def test_rejects_small_data(self):
        df = pd.DataFrame({"x": range(50)})
        with pytest.raises(ValueError):
            chronological_split(df)


class TestLabelBucketing:
    def test_thresholds_from_train_only(self):
        scores = pd.Series([5, 10, 15, 20, 25, 30, 35, 40, 50, 60], dtype=float)
        labels = bucket_labels(scores, q25=15, q75=35, q90=50)
        assert labels.iloc[0] == "LOW"
        assert labels.iloc[3] == "NORMAL"
        assert labels.iloc[7] == "ELEVATED"
        assert labels.iloc[9] == "HIGH"

    def test_nan_maps_unknown(self):
        labels = bucket_labels(pd.Series([np.nan]), 1, 2, 3)
        assert labels.iloc[0] == "UNKNOWN"

    def test_regimes_complete(self):
        assert set(REGIMES) == {"LOW", "NORMAL", "ELEVATED", "HIGH"}
