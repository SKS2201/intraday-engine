from src.engine.stage0_replay import SyntheticPreopenInput, build_synthetic_preopen_rows
from src.providers.base import PrevDayOHLC


def test_synthetic_preopen_deterministic():
    rows = [
        SyntheticPreopenInput(
            symbol="INFY",
            prev=PrevDayOHLC(pdh=100, pdl=95, prev_close=98),
            first_open=99,
            first_close=100,
            first_volume=1200,
        )
    ]
    out = build_synthetic_preopen_rows(rows)
    assert out[0].symbol == "INFY"
    assert round(out[0].change_pct, 2) == round(((100 - 98) / 98) * 100, 2)
    assert out[0].status == "SIMULATED"
