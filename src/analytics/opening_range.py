from __future__ import annotations

from dataclasses import dataclass

from src.providers.base import Candle, PrevDayOHLC


@dataclass(frozen=True)
class OpeningRangeMetrics:
    orh: float
    orl: float
    or_close: float
    vwap: float
    rvol: float
    gap_pct: float
    retained_gap: bool
    vwap_accepted: bool
    long_trigger_ok: bool
    short_trigger_ok: bool
    opening_move_pct: float


def compute_vwap(candles: list[Candle]) -> float:
    pv = 0.0
    vol = 0.0
    for c in candles:
        typical = (c.high + c.low + c.close) / 3.0
        pv += typical * c.volume
        vol += c.volume
    if vol <= 0:
        return 0.0
    return pv / vol


def compute_opening_range_metrics(
    candles: list[Candle],
    prev: PrevDayOHLC,
    allow_vwap_rejection_filter: bool = True,
) -> OpeningRangeMetrics | None:
    if not candles:
        return None
    orh = max(c.high for c in candles)
    orl = min(c.low for c in candles)
    or_close = candles[-1].close
    vwap = compute_vwap(candles)
    total_vol = sum(c.volume for c in candles)
    baseline_vol = 1.0 if len(candles) == 0 else max(1.0, (total_vol / len(candles)) * len(candles))
    rvol = total_vol / baseline_vol

    day_open = candles[0].open
    gap_pct = ((day_open - prev.prev_close) / prev.prev_close) * 100 if prev.prev_close else 0.0
    retained_gap = (gap_pct >= 0 and or_close >= day_open) or (gap_pct < 0 and or_close <= day_open)
    vwap_accepted = (or_close >= vwap and day_open >= vwap) or (or_close <= vwap and day_open <= vwap)
    opening_move_pct = ((or_close - day_open) / day_open) * 100 if day_open else 0.0

    long_trigger_ok = or_close > prev.pdh
    short_trigger_ok = or_close < prev.pdl
    if allow_vwap_rejection_filter:
        long_trigger_ok = long_trigger_ok and or_close >= vwap
        short_trigger_ok = short_trigger_ok and or_close <= vwap

    return OpeningRangeMetrics(
        orh=orh,
        orl=orl,
        or_close=or_close,
        vwap=vwap,
        rvol=rvol,
        gap_pct=gap_pct,
        retained_gap=retained_gap,
        vwap_accepted=vwap_accepted,
        long_trigger_ok=long_trigger_ok,
        short_trigger_ok=short_trigger_ok,
        opening_move_pct=opening_move_pct,
    )
