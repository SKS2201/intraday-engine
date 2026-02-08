from src.analytics.opening_range import OpeningRangeMetrics
from src.analytics.scoring import CandidateInput, rank_candidates
from src.providers.base import MarketRegime


def test_scoring_deterministic_on_fixed_input():
    c1 = CandidateInput(
        symbol="ABC",
        sector="IT",
        metrics=OpeningRangeMetrics(
            orh=110,
            orl=100,
            or_close=111,
            vwap=107,
            rvol=1.4,
            gap_pct=0.8,
            retained_gap=True,
            vwap_accepted=True,
            long_trigger_ok=True,
            short_trigger_ok=False,
            opening_move_pct=1.0,
        ),
        market_regime=MarketRegime.TRENDING_BULLISH,
        sector_rel_strength=0.6,
        price_above_prev=True,
        price_below_prev=False,
    )
    c2 = CandidateInput(
        symbol="XYZ",
        sector="OTHER",
        metrics=OpeningRangeMetrics(
            orh=210,
            orl=205,
            or_close=204,
            vwap=207,
            rvol=0.9,
            gap_pct=-0.2,
            retained_gap=True,
            vwap_accepted=True,
            long_trigger_ok=False,
            short_trigger_ok=True,
            opening_move_pct=-1.0,
        ),
        market_regime=MarketRegime.TRENDING_BULLISH,
        sector_rel_strength=-0.2,
        price_above_prev=False,
        price_below_prev=True,
    )
    r1 = rank_candidates([c1, c2], limit=2)
    r2 = rank_candidates([c1, c2], limit=2)
    assert [x.symbol for x in r1] == [x.symbol for x in r2]
    assert [x.score for x in r1] == [x.score for x in r2]
