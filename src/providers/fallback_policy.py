from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FallbackDecision:
    action: str
    reason: str


def decide_fallback(validation_status: str, primary_ok: bool, secondary_ok: bool) -> FallbackDecision:
    if validation_status == "FAIL":
        return FallbackDecision(action="NO_TRADE", reason="validation_failed")
    if primary_ok:
        return FallbackDecision(action="USE_PRIMARY", reason="primary_healthy")
    if secondary_ok:
        return FallbackDecision(action="USE_SECONDARY", reason="primary_unavailable")
    return FallbackDecision(action="NO_TRADE", reason="both_unavailable")
