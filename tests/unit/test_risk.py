from src.analytics.risk import position_size, validate_rr


def test_rr_validation_rejects_below_threshold():
    assert validate_rr(entry=100, stop=99, target=101.7, min_rr=1.8) is False
    assert validate_rr(entry=100, stop=99, target=101.8, min_rr=1.8) is True


def test_position_sizing():
    qty = position_size(capital=51000, risk_pct=1.0, entry=100, stop=99)
    assert qty == 510
