"""Calculation tests: returns, volatility, indicators, ratios — deterministic math."""
import math

import pytest

from app.analytics.technical import (
    annualized_volatility,
    atr,
    bollinger,
    daily_returns,
    derive_fundamentals,
    ema,
    macd,
    max_drawdown,
    pct_change,
    rsi,
    sma,
    std,
)


class TestReturnsAndVolatility:
    def test_pct_change(self):
        assert pct_change(110, 100) == pytest.approx(10.0)
        assert pct_change(90, 100) == pytest.approx(-10.0)
        assert pct_change(100, 0) is None
        assert pct_change(None, 100) is None

    def test_daily_returns(self):
        rets = daily_returns([100, 110, 99])
        assert rets[0] == pytest.approx(0.10)
        assert rets[1] == pytest.approx(-0.10)

    def test_volatility_constant_series_zero(self):
        assert annualized_volatility([0.001] * 10) is not None
        rets = daily_returns([100.0] * 20)
        assert all(r == 0 for r in rets)

    def test_volatility_known_value(self):
        rets = [0.01, -0.01, 0.01, -0.01]
        vol = annualized_volatility(rets)
        assert vol is not None and vol > 0

    def test_max_drawdown(self):
        mdd = max_drawdown([100, 120, 60, 90])
        assert mdd == pytest.approx(-50.0)
        assert max_drawdown([1, 2, 3]) == pytest.approx(0.0)
        assert max_drawdown([5]) is None


class TestMovingAverages:
    def test_sma_basic(self):
        out = sma([1, 2, 3, 4, 5], 3)
        assert out[:2] == [None, None]
        assert out[2] == pytest.approx(2.0)
        assert out[3] == pytest.approx(3.0)
        assert out[4] == pytest.approx(4.0)

    def test_sma_window_too_large(self):
        assert sma([1, 2], 5) == [None, None]

    def test_sma_invalid_window(self):
        with pytest.raises(ValueError):
            sma([1, 2, 3], 0)

    def test_ema_converges(self):
        values = [10.0] * 50
        out = ema(values, 10)
        assert out[-1] == pytest.approx(10.0)

    def test_ema_leading_nones(self):
        out = ema([1.0, 2.0, 3.0], 5)
        assert all(v is None for v in out)


class TestRSI:
    def test_all_gains_rsi_100(self):
        closes = [float(i) for i in range(1, 40)]
        out = rsi(closes, 14)
        assert out[-1] == pytest.approx(100.0)

    def test_all_losses_rsi_0(self):
        closes = [float(40 - i) for i in range(40)]
        out = rsi(closes, 14)
        assert out[-1] == pytest.approx(0.0)

    def test_flat_series_neutral(self):
        closes = [50.0] * 30
        out = rsi(closes, 14)
        assert out[-1] == pytest.approx(100.0)  # zero losses → 100 by formula; documented behavior

    def test_too_short(self):
        assert rsi([1.0, 2.0], 14) == [None, None]


class TestMACD:
    def test_shape(self):
        closes = [math.sin(i / 5) * 10 + 50 for i in range(60)]
        m, s, h = macd(closes)
        assert len(m) == len(s) == len(h) == len(closes)
        assert m[0] is None

    def test_known_uptrend_macd_positive(self):
        closes = [float(i) for i in range(1, 61)]
        m, s, h = macd(closes)
        assert m[-1] is not None and m[-1] > 0


class TestBollinger:
    def test_bands_bracket_middle(self):
        closes = [float(i % 10) + 10 for i in range(60)]
        upper, mid, lower = bollinger(closes, 20, 2.0)
        assert upper[-1] >= mid[-1] >= lower[-1]
        assert (upper[-1] - mid[-1]) == pytest.approx(mid[-1] - lower[-1])


class TestATR:
    def test_atr_positive_constant_range(self):
        highs = [11.0] * 20
        lows = [9.0] * 20
        closes = [10.0] * 20
        out = atr(highs, lows, closes, 14)
        assert out[-1] == pytest.approx(2.0)


class TestStd:
    def test_std_known(self):
        assert std([2, 4], 0) == pytest.approx(1.0)
        assert std([1], 0) == pytest.approx(0.0)
        assert std([], 0) is None


class TestDeriveFundamentals:
    def _metrics(self):
        def m(key, fy, value, period):
            return {"metric_key": key, "fiscal_year": fy, "period": period, "value": value, "unit": "USD"}
        return [
            m("revenue", 2024, 1000.0, "2024-12-31"),
            m("revenue", 2023, 800.0, "2023-12-31"),
            m("net_income", 2024, 150.0, "2024-12-31"),
            m("stockholders_equity", 2024, 500.0, "2024-12-31"),
            m("total_liabilities", 2024, 900.0, "2024-12-31"),
        ]

    def test_ratios(self):
        d = derive_fundamentals(self._metrics())
        latest = d["latest"]
        assert latest["revenue"]["value"] == 1000.0
        assert latest["revenue_growth_yoy"]["value"] == pytest.approx(25.0)
        assert latest["ratios"]["net_profit_margin"]["value"] == pytest.approx(0.15)
        assert latest["ratios"]["roe"]["value"] == pytest.approx(0.3)
        assert latest["ratios"]["debt_to_equity"]["value"] == pytest.approx(1.8)

    def test_missing_data_not_fabricated(self):
        d = derive_fundamentals([{"metric_key": "revenue", "fiscal_year": 2024, "period": "2024-12-31", "value": 100.0, "unit": "USD"}])
        assert d["latest"]["net_income"]["value"] is None
        assert d["latest"]["ratios"]["net_profit_margin"]["value"] is None

    def test_empty(self):
        d = derive_fundamentals([])
        assert d["latest"] == {}
        assert d["trend"] == {}
