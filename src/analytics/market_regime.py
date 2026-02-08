from __future__ import annotations

from src.analytics.opening_range import compute_vwap
from src.providers.base import Candle, MarketRegime


def classify_market_regime(index_candles: dict[str, list[Candle]]) -> MarketRegime:
    nifty = index_candles.get("NIFTY", [])
    banknifty = index_candles.get("BANKNIFTY", [])
    if not nifty or not banknifty:
        return MarketRegime.RANGE_BOUND

    n_open, n_close = nifty[0].open, nifty[-1].close
    b_open, b_close = banknifty[0].open, banknifty[-1].close
    n_range = max(c.high for c in nifty) - min(c.low for c in nifty)
    b_range = max(c.high for c in banknifty) - min(c.low for c in banknifty)
    n_vwap = compute_vwap(nifty)
    b_vwap = compute_vwap(banknifty)

    n_gap = abs((n_open - nifty[0].close) / n_open) if n_open else 0.0
    b_gap = abs((b_open - banknifty[0].close) / b_open) if b_open else 0.0
    high_vol = (n_range / n_open > 0.01) or (b_range / b_open > 0.012) or (n_gap > 0.01) or (b_gap > 0.01)
    if high_vol:
        return MarketRegime.HIGH_VOL_EVENT

    bull_align = n_close > n_open and b_close > b_open and n_close >= n_vwap and b_close >= b_vwap
    bear_align = n_close < n_open and b_close < b_open and n_close <= n_vwap and b_close <= b_vwap
    if bull_align:
        return MarketRegime.TRENDING_BULLISH
    if bear_align:
        return MarketRegime.TRENDING_BEARISH
    return MarketRegime.RANGE_BOUND
