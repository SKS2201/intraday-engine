from src.analytics.opening_range import OpeningRangeMetrics
from src.analytics.scoring import CandidateInput, split_ranked_candidates
from src.providers.base import MarketRegime


def _cand(symbol: str, long_ok: bool, short_ok: bool, rvol: float) -> CandidateInput:
    return CandidateInput(
        symbol=symbol,
        sector="IT",
        metrics=OpeningRangeMetrics(
            orh=110,
            orl=100,
            or_close=111 if long_ok else 99,
            vwap=107,
            rvol=rvol,
            gap_pct=0.5 if long_ok else -0.5,
            retained_gap=True if long_ok else False,
            vwap_accepted=True,
            long_trigger_ok=long_ok,
            short_trigger_ok=short_ok,
            opening_move_pct=1.0 if long_ok else -1.0,
        ),
        market_regime=MarketRegime.TRENDING_BULLISH,
        sector_rel_strength=0.3,
        price_above_prev=True,
        price_below_prev=False,
    )


def test_split_ranked_candidates_returns_top_per_side():
    cands = [
        _cand("A", True, False, 1.4),
        _cand("B", True, False, 1.2),
        _cand("C", False, True, 1.5),
        _cand("D", False, True, 1.1),
    ]
    longs, shorts = split_ranked_candidates(cands, long_limit=2, short_limit=2)
    assert [x.direction for x in longs] == ["BUY", "BUY"]
    assert [x.direction for x in shorts] == ["SELL", "SELL"]
    assert len(longs) == 2
    assert len(shorts) == 2


def test_split_ranked_candidates_deterministic_order():
    cands = [_cand("AAA", True, False, 1.2), _cand("BBB", True, False, 1.2)]
    r1 = split_ranked_candidates(cands, long_limit=5, short_limit=5)
    r2 = split_ranked_candidates(cands, long_limit=5, short_limit=5)
    assert [x.symbol for x in r1[0]] == [x.symbol for x in r2[0]]
