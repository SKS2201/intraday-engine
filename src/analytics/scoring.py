from __future__ import annotations

from dataclasses import dataclass

from src.analytics.opening_range import OpeningRangeMetrics
from src.providers.base import MarketRegime


@dataclass(frozen=True)
class CandidateInput:
    symbol: str
    sector: str
    metrics: OpeningRangeMetrics
    market_regime: MarketRegime
    sector_rel_strength: float
    price_above_prev: bool
    price_below_prev: bool
    preopen_change_pct: float = 0.0
    preopen_indicative_price: float = 0.0
    preopen_volume: float = 0.0


@dataclass(frozen=True)
class ScoredCandidate:
    symbol: str
    score: float
    direction: str
    reasons: list[str]
    confidence: int
    opening_move_pct: float
    preopen_change_pct: float


def _regime_bonus(regime: MarketRegime, direction: str) -> float:
    if regime == MarketRegime.HIGH_VOL_EVENT:
        return -12
    if regime == MarketRegime.RANGE_BOUND:
        return -6
    if regime == MarketRegime.TRENDING_BULLISH and direction == "BUY":
        return 8
    if regime == MarketRegime.TRENDING_BEARISH and direction == "SELL":
        return 8
    return -3


def score_candidate(c: CandidateInput) -> ScoredCandidate | None:
    m = c.metrics
    if not (m.long_trigger_ok or m.short_trigger_ok):
        return None

    direction = "BUY" if m.long_trigger_ok else "SELL"
    # Side-specific quality gates before ranking:
    # shorting requires downside acceptance + volume + gap/structure weakness.
    if direction == "SELL":
        short_quality_ok = (
            m.or_close < m.vwap
            and m.rvol >= 1.0
            and (m.gap_pct <= 0.0 or not m.retained_gap)
        )
        if not short_quality_ok:
            return None
    else:
        long_quality_ok = (
            m.or_close > m.vwap
            and m.rvol >= 1.0
            and (m.gap_pct >= 0.0 or m.retained_gap)
        )
        if not long_quality_ok:
            return None

    score = 50.0
    reasons: list[str] = []

    score += 10 if m.vwap_accepted else -8
    reasons.append(f"VWAP acceptance: {'yes' if m.vwap_accepted else 'no'}")

    score += min(12, max(-8, (m.rvol - 1.0) * 10))
    reasons.append(f"Relative volume: {m.rvol:.2f}x")

    score += 8 if m.retained_gap else -4
    reasons.append(f"Gap {'retained' if m.retained_gap else 'faded'} ({m.gap_pct:.2f}%)")

    score += max(-10, min(10, c.sector_rel_strength * 10))
    reasons.append(f"Sector rel strength: {c.sector_rel_strength:.2f}")

    score += _regime_bonus(c.market_regime, direction)
    reasons.append(f"Market regime: {c.market_regime.value}")

    # Pre-open alignment influences the final ranking adjustment.
    if direction == "BUY":
        preopen_adj = max(-8.0, min(8.0, c.preopen_change_pct * 2.0))
    else:
        preopen_adj = max(-8.0, min(8.0, -c.preopen_change_pct * 2.0))
    score += preopen_adj
    reasons.append(f"Pre-open change: {c.preopen_change_pct:+.2f}%")

    # Performance between 09:15 and 09:30 is a primary ranking factor.
    if direction == "BUY":
        opening_adj = max(-10.0, min(10.0, m.opening_move_pct * 8.0))
    else:
        opening_adj = max(-10.0, min(10.0, -m.opening_move_pct * 8.0))
    score += opening_adj

    if direction == "BUY" and not c.price_above_prev:
        score -= 10
    if direction == "SELL" and not c.price_below_prev:
        score -= 10

    score = max(0.0, min(100.0, score))
    confidence = int(round(score))
    reasons.append("Trigger: 15m close beyond PDH/PDL with no immediate VWAP rejection")
    reasons.append(f"Opening performance (09:15-09:30): {m.opening_move_pct:+.2f}%")
    return ScoredCandidate(
        symbol=c.symbol,
        score=score,
        direction=direction,
        reasons=reasons,
        confidence=confidence,
        opening_move_pct=m.opening_move_pct,
        preopen_change_pct=c.preopen_change_pct,
    )


def rank_candidates(candidates: list[CandidateInput], limit: int) -> list[ScoredCandidate]:
    scored = [x for x in (score_candidate(c) for c in candidates) if x is not None]
    scored.sort(
        key=lambda x: (
            0 if x.direction == "BUY" else 1,
            -x.opening_move_pct if x.direction == "BUY" else x.opening_move_pct,
            -x.preopen_change_pct if x.direction == "BUY" else x.preopen_change_pct,
            -x.score,
            x.symbol,
        )
    )
    return scored[:limit]


def split_ranked_candidates(
    candidates: list[CandidateInput],
    long_limit: int,
    short_limit: int,
) -> tuple[list[ScoredCandidate], list[ScoredCandidate]]:
    scored = [x for x in (score_candidate(c) for c in candidates) if x is not None]
    longs = [x for x in scored if x.direction == "BUY"]
    shorts = [x for x in scored if x.direction == "SELL"]
    # Ranking priority after rules pass: 09:15-09:30 directional performance.
    longs.sort(key=lambda x: (-x.opening_move_pct, -x.preopen_change_pct, -x.score, x.symbol))
    shorts.sort(key=lambda x: (x.opening_move_pct, x.preopen_change_pct, -x.score, x.symbol))
    return longs[:long_limit], shorts[:short_limit]
