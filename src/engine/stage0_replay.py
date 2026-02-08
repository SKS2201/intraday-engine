from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.providers.base import PreopenRow, PrevDayOHLC


@dataclass(frozen=True)
class SyntheticPreopenInput:
    symbol: str
    prev: PrevDayOHLC
    first_open: float
    first_close: float
    first_volume: float


def build_synthetic_preopen_rows(rows: list[SyntheticPreopenInput]) -> list[PreopenRow]:
    out: list[PreopenRow] = []
    for r in rows:
        ref = r.prev.prev_close if r.prev.prev_close else r.first_open
        change_pct = ((r.first_close - ref) / ref) * 100 if ref else 0.0
        out.append(
            PreopenRow(
                symbol=r.symbol,
                indicative_price=r.first_close,
                change_pct=change_pct,
                volume=r.first_volume,
                status="SIMULATED",
            )
        )
    return out


def build_stage0_replay_header(asof: datetime, replay_date: str) -> list[str]:
    return [
        "TEST MODE: ON",
        f"Replay Date: {replay_date}",
        "Run Context: Weekend/Off-hours replay",
        "SIMULATED PRE-OPEN (historical reconstruction)",
        f"As of: {asof.strftime('%Y-%m-%d %H:%M:%S %Z')}",
    ]
