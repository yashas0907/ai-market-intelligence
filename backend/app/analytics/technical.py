"""Analytics layer — all deterministic financial calculations (no LLM math)."""
from __future__ import annotations

import math
import statistics as stats
from typing import Any

from app.schemas.common import PricePoint


def pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / old * 100.0


def daily_returns(closes: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] and closes[i - 1] != 0:
            out.append(closes[i] / closes[i - 1] - 1.0)
    return out


def sma(values: list[float], window: int) -> list[float | None]:
    if window <= 0:
        raise ValueError("window must be positive")
    out: list[float | None] = [None] * len(values)
    if len(values) < window:
        return out
    running = sum(values[:window])
    out[window - 1] = running / window
    for i in range(window, len(values)):
        running += values[i] - values[i - window]
        out[i] = running / window
    return out


def ema(values: list[float], window: int) -> list[float | None]:
    if window <= 0 or len(values) < window:
        return [None] * len(values)
    out: list[float | None] = [None] * len(values)
    alpha = 2.0 / (window + 1.0)
    out[window - 1] = sum(values[:window]) / window
    for i in range(window, len(values)):
        out[i] = values[i] * alpha + out[i - 1] * (1 - alpha)
    return out


def std(values: list[float], ddof: int = 0) -> float | None:
    if len(values) < ddof + 1:
        return None
    return stats.stdev(values) if ddof and len(values) > 1 else (stats.pstdev(values) if values else None)


def rsi(closes: list[float], window: int = 14) -> list[float | None]:
    if len(closes) < window + 1:
        return [None] * len(closes)
    out: list[float | None] = [None] * len(closes)
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, window + 1):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window
    out[window] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss)) if avg_loss != 0 else 100.0
    for i in range(window + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gain = max(ch, 0.0)
        loss = max(-ch, 0.0)
        avg_gain = (avg_gain * (window - 1) + gain) / window
        avg_loss = (avg_loss * (window - 1) + loss) / window
        out[i] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss)) if avg_loss != 0 else 100.0
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[list[float | None], list[float | None], list[float | None]]:
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line: list[float | None] = [None] * len(closes)
    for i, (f, s) in enumerate(zip(ema_fast, ema_slow)):
        if f is not None and s is not None:
            macd_line[i] = f - s
    valid = [(i, v) for i, v in enumerate(macd_line) if v is not None]
    signal_line: list[float | None] = [None] * len(closes)
    hist: list[float | None] = [None] * len(closes)
    if len(valid) >= signal:
        idxs = [i for i, _ in valid]
        vals = [v for _, v in valid]
        sig = ema(vals, signal)
        for k, i in enumerate(idxs):
            if k >= signal - 1 and sig[k] is not None:
                signal_line[i] = sig[k]
                hist[i] = macd_line[i] - sig[k]
    return macd_line, signal_line, hist


def bollinger(closes: list[float], window: int = 20, num_std: float = 2.0) -> tuple[list[float | None], list[float | None], list[float | None]]:
    n = len(closes)
    mid = sma(closes, window)
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    for i in range(window - 1, n):
        window_vals = closes[i - window + 1 : i + 1]
        m = mid[i]
        sd = std(window_vals)
        if m is not None and sd is not None:
            upper[i] = m + num_std * sd
            lower[i] = m - num_std * sd
    return upper, mid, lower


def atr(highs: list[float], lows: list[float], closes: list[float], window: int = 14) -> list[float | None]:
    n = len(closes)
    if n < window + 1:
        return [None] * n
    trs: list[float] = []
    for i in range(1, n):
        h = highs[i] if highs[i] is not None else closes[i]
        l = lows[i] if lows[i] is not None else closes[i]
        prev_c = closes[i - 1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    out: list[float | None] = [None] * n
    if len(trs) >= window:
        atr_val = sum(trs[:window]) / window
        out[window] = atr_val
        for k in range(window, len(trs)):
            atr_val = (atr_val * (window - 1) + trs[k]) / window
            out[k + 1] = atr_val
    return out


def annualized_volatility(returns: list[float], periods_per_year: int = 252) -> float | None:
    if len(returns) < 2:
        return None
    sd = stats.stdev(returns)
    return sd * math.sqrt(periods_per_year) * 100.0


def max_drawdown(closes: list[float]) -> float | None:
    if len(closes) < 2:
        return None
    peak = closes[0]
    mdd = 0.0
    for c in closes:
        if c > peak:
            peak = c
        if peak > 0:
            mdd = min(mdd, c / peak - 1.0)
    return mdd * 100.0


def volume_sma(volumes: list[float], window: int = 20) -> list[float | None]:
    return sma(volumes, window)


def compute_technicals(points: list[PricePoint]) -> dict[str, Any]:
    closes = [p.close for p in points if p.close is not None]
    highs = [p.high if p.high is not None else (p.close or 0.0) for p in points]
    lows = [p.low if p.low is not None else (p.close or 0.0) for p in points]
    vols = [p.volume or 0.0 for p in points]

    n = len(points)
    ts = [p.ts.isoformat() for p in points]

    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)
    ema12 = ema(closes, 12)
    macd_line, macd_signal, macd_hist = macd(closes)
    rsi14 = rsi(closes, 14)
    bb_upper, bb_mid, bb_lower = bollinger(closes, 20, 2.0)
    atr14 = atr(highs, lows, closes, 14)
    vsma20 = volume_sma(vols, 20)

    rets = daily_returns(closes)
    vol_annual = annualized_volatility(rets)
    mdd = max_drawdown(closes)

    def last(series: list[float | None]) -> float | None:
        for v in reversed(series):
            if v is not None:
                return round(v, 4)
        return None

    signals: list[dict[str, Any]] = []
    if n >= 50:
        s20, s50 = last(sma20), last(sma50)
        if s20 is not None and s50 is not None:
            if s20 > s50:
                signals.append({"indicator": "sma_cross", "reading": "golden-cross-zone", "note": f"SMA20 ({s20}) above SMA50 ({s50}) — medium-term upward momentum (historical observation)"})
            else:
                signals.append({"indicator": "sma_cross", "reading": "death-cross-zone", "note": f"SMA20 ({s20}) below SMA50 ({s50}) — medium-term downward momentum (historical observation)"})
    rsi_l = last(rsi14)
    if rsi_l is not None:
        band = "overbought-zone" if rsi_l >= 70 else ("oversold-zone" if rsi_l <= 30 else "neutral-zone")
        signals.append({"indicator": "rsi14", "reading": band, "note": f"RSI(14) = {rsi_l:.1f} ({band.replace('-zone','')})"})
    macd_l, macd_s = last(macd_line), last(macd_signal)
    if macd_l is not None and macd_s is not None:
        direction = "bullish" if macd_l > macd_s else "bearish"
        signals.append({"indicator": "macd", "reading": direction, "note": f"MACD line {'above' if macd_l > macd_s else 'below'} signal line ({macd_l:.3f} vs {macd_s:.3f})"})
    close_last = closes[-1] if closes else None
    if close_last is not None:
        for i in range(len(closes) - 1, -1, -1):
            if bb_upper[i] is not None:
                if close_last > bb_upper[i]:
                    signals.append({"indicator": "bollinger", "reading": "above-upper-band", "note": f"Close ({close_last:.2f}) above upper Bollinger band — strong recent extension"})
                elif close_last < bb_lower[i]:
                    signals.append({"indicator": "bollinger", "reading": "below-lower-band", "note": f"Close ({close_last:.2f}) below lower Bollinger band — sharp recent weakness"})
                break

    return {
        "series": {
            "ts": ts,
            "close": closes,
            "sma20": _pad(sma20, n),
            "sma50": _pad(sma50, n),
            "sma200": _pad(sma200, n),
            "ema12": _pad(ema12, n),
            "rsi14": _pad(rsi14, n),
            "macd": _pad(macd_line, n),
            "macd_signal": _pad(macd_signal, n),
            "macd_hist": _pad(macd_hist, n),
            "bb_upper": _pad(bb_upper, n),
            "bb_lower": _pad(bb_lower, n),
            "atr14": _pad(atr14, n),
            "volume_sma20": _pad(vsma20, n),
        },
        "latest": {
            "sma20": last(sma20),
            "sma50": last(sma50),
            "sma200": last(sma200),
            "rsi14": rsi_l,
            "macd": macd_l,
            "macd_signal": macd_s,
            "atr14": last(atr14),
            "bb_upper": last(bb_upper),
            "bb_lower": last(bb_lower),
        },
        "statistics": {
            "annualized_volatility_pct": round(vol_annual, 2) if vol_annual is not None else None,
            "max_drawdown_pct": round(mdd, 2) if mdd is not None else None,
            "period_days": n,
        },
        "signals": signals,
    }


def _pad(series: list[float | None], n: int) -> list[float | None]:
    return series + [None] * (n - len(series)) if len(series) < n else series[:n]


# ---------- Fundamentals ----------

def derive_fundamentals(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive ratios from raw SEC XBRL metrics. Never invents missing values."""
    by_key: dict[str, dict[int, dict[str, Any]]] = {}
    for m in metrics:
        by_key.setdefault(m["metric_key"], {}).setdefault(m["fiscal_year"], m)

    years = sorted({m["fiscal_year"] for m in metrics}) if metrics else []
    # latest year = latest fiscal year that actually has a usable headline metric
    usable_years = [y for y in years if any(by_key.get(k, {}).get(y) for k in ("revenue", "net_income", "total_assets"))] if metrics else []
    if usable_years:
        years = usable_years + [y for y in years if y not in usable_years and y < min(usable_years)]
    derived: dict[str, Any] = {}
    ratios_latest: dict[str, Any] = {}
    if usable_years:
        latest_year = usable_years[-1]
        prev_candidates = [y for y in usable_years if y < latest_year]
        prev_year = max(prev_candidates) if prev_candidates else None

        def val(key: str, year: int) -> tuple[float | None, str | None]:
            rec = by_key.get(key, {}).get(year)
            if rec is None:
                return None, None
            return rec["value"], rec.get("period")

        rev, rev_p = val("revenue", latest_year)
        ni, ni_p = val("net_income", latest_year)
        ta, ta_p = val("total_assets", latest_year)
        tl, tl_p = val("total_liabilities", latest_year)
        eq, eq_p = val("stockholders_equity", latest_year)
        ocf, ocf_p = val("free_cash_flow_proxy", latest_year)
        rd, rd_p = val("research_development", latest_year)
        eps_d, eps_p = val("eps_diluted", latest_year)

        def safe_div(a: float | None, b: float | None) -> float | None:
            if a is None or b is None or b == 0:
                return None
            return round(a / b, 4)

        net_margin = safe_div(ni, rev)
        roe = safe_div(ni, eq)
        debt_to_equity = safe_div(tl, eq)
        rd_intensity = safe_div(rd, rev)

        rev_prev, _ = val("revenue", prev_year) if prev_year else (None, None)
        ni_prev, _ = val("net_income", prev_year) if prev_year else (None, None)
        rev_growth = pct_change(rev, rev_prev)
        ni_growth = pct_change(ni, ni_prev)

        ratios_latest = {
            "net_profit_margin": {"value": net_margin, "unit": "ratio", "period": ni_p or rev_p, "note": "net income / revenue" if net_margin is not None else "insufficient data"},
            "roe": {"value": roe, "unit": "ratio", "period": ni_p or eq_p, "note": "net income / stockholders' equity" if roe is not None else "insufficient data"},
            "debt_to_equity": {"value": debt_to_equity, "unit": "ratio", "period": tl_p or eq_p, "note": "total liabilities / equity" if debt_to_equity is not None else "insufficient data"},
            "rd_intensity": {"value": rd_intensity, "unit": "ratio", "period": rd_p or rev_p, "note": "R&D expense / revenue" if rd_intensity is not None else "insufficient data"},
        }
        derived = {
            "latest_fiscal_year": latest_year,
            "revenue": {"value": rev, "unit": _scale_unit("revenue", by_key), "period": rev_p},
            "net_income": {"value": ni, "unit": _scale_unit("net_income", by_key), "period": ni_p},
            "eps_diluted": {"value": eps_d, "unit": "USD/share", "period": eps_p},
            "total_assets": {"value": ta, "unit": _scale_unit("total_assets", by_key), "period": ta_p},
            "stockholders_equity": {"value": eq, "unit": _scale_unit("stockholders_equity", by_key), "period": eq_p},
            "operating_cash_flow": {"value": ocf, "unit": _scale_unit("free_cash_flow_proxy", by_key), "period": ocf_p},
            "revenue_growth_yoy": {"value": round(rev_growth, 2) if rev_growth is not None else None, "unit": "%", "period": rev_p},
            "net_income_growth_yoy": {"value": round(ni_growth, 2) if ni_growth is not None else None, "unit": "%", "period": ni_p},
            "ratios": ratios_latest,
        }

    trend: dict[str, list[dict[str, Any]]] = {}
    if metrics:
        for key in ("revenue", "net_income", "stockholders_equity", "total_assets"):
            series = []
            for y in years:
                rec = by_key.get(key, {}).get(y)
                if rec:
                    series.append({"year": y, "value": rec["value"], "period": rec.get("period")})
            trend[key] = series

    return {"latest": derived, "trend": trend, "available_years": years}


def _scale_unit(metric_key: str, by_key: dict[str, dict[int, dict[str, Any]]]) -> str:
    """Infer display unit from the SEC XBRL unit of the stored value."""
    for year_rec in by_key.get(metric_key, {}).values():
        unit = year_rec.get("unit", "")
        if unit in ("shares",):
            return unit
        if "share" in unit:
            return "USD/share"
        if unit and unit != "USD":
            return unit
        return "USD"
    return "USD"
