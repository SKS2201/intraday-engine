from src.providers.fallback_policy import decide_fallback


def test_fallback_decision_matrix():
    assert decide_fallback("FAIL", True, True).action == "NO_TRADE"
    assert decide_fallback("PASS", True, True).action == "USE_PRIMARY"
    assert decide_fallback("PASS", False, True).action == "USE_SECONDARY"
    assert decide_fallback("PASS", False, False).action == "NO_TRADE"
