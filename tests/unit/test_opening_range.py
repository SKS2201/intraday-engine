from datetime import datetime

from src.analytics.opening_range import compute_opening_range_metrics, compute_vwap
from src.providers.base import Candle, PrevDayOHLC


def test_vwap_calculation():
    candles = [
        Candle(datetime.fromisoformat("2025-01-01T09:15:00+05:30"), 100, 102, 99, 101, 10),
        Candle(datetime.fromisoformat("2025-01-01T09:16:00+05:30"), 101, 103, 100, 102, 20),
    ]
    got = compute_vwap(candles)
    expected = (((102 + 99 + 101) / 3) * 10 + ((103 + 100 + 102) / 3) * 20) / 30
    assert round(got, 6) == round(expected, 6)


def test_orh_orl_computation():
    candles = [
        Candle(datetime.fromisoformat("2025-01-01T09:15:00+05:30"), 100, 101, 99, 100.5, 100),
        Candle(datetime.fromisoformat("2025-01-01T09:16:00+05:30"), 100.5, 103, 100, 102.5, 120),
        Candle(datetime.fromisoformat("2025-01-01T09:17:00+05:30"), 102.5, 102.8, 98, 101, 80),
    ]
    prev = PrevDayOHLC(pdh=99.5, pdl=95.0, prev_close=98.0)
    m = compute_opening_range_metrics(candles, prev)
    assert m is not None
    assert m.orh == 103
    assert m.orl == 98
