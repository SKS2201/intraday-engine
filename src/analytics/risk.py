from __future__ import annotations

from dataclasses import dataclass
from math import floor


@dataclass(frozen=True)
class RiskPlan:
    quantity: int
    risk_per_share: float
    rr: float
    t1: float
    t2: float | None


def reward_risk(entry: float, stop: float, target: float) -> float:
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    return abs(target - entry) / risk


def validate_rr(entry: float, stop: float, target: float, min_rr: float) -> bool:
    return reward_risk(entry, stop, target) + 1e-9 >= min_rr


def position_size(capital: float, risk_pct: float, entry: float, stop: float) -> int:
    risk_amount = capital * (risk_pct / 100.0)
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0:
        return 0
    return max(0, floor(risk_amount / risk_per_share))


def build_risk_plan(
    capital: float,
    risk_pct: float,
    entry: float,
    stop: float,
    min_rr: float,
    include_t2: bool = True,
) -> RiskPlan | None:
    qty = position_size(capital, risk_pct, entry, stop)
    if qty <= 0:
        return None
    risk = abs(entry - stop)
    t1 = entry + (1.8 * risk) if entry > stop else entry - (1.8 * risk)
    rr = reward_risk(entry, stop, t1)
    if rr + 1e-9 < min_rr:
        return None
    t2 = (entry + (2.5 * risk)) if include_t2 and entry > stop else ((entry - (2.5 * risk)) if include_t2 else None)
    return RiskPlan(quantity=qty, risk_per_share=risk, rr=rr, t1=t1, t2=t2)
