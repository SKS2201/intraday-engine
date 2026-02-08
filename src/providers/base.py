from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol


class MarketRegime(str, Enum):
    TRENDING_BULLISH = "TRENDING_BULLISH"
    TRENDING_BEARISH = "TRENDING_BEARISH"
    RANGE_BOUND = "RANGE_BOUND"
    HIGH_VOL_EVENT = "HIGH_VOL_EVENT"


@dataclass(frozen=True)
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class PrevDayOHLC:
    pdh: float
    pdl: float
    prev_close: float
    prev_open: float = 0.0


@dataclass(frozen=True)
class HighLow52W:
    high_52w: float
    low_52w: float


@dataclass(frozen=True)
class PreopenRow:
    symbol: str
    indicative_price: float
    change_pct: float
    volume: float
    status: str


@dataclass(frozen=True)
class SectorSnapshot:
    name: str
    last: float
    change_pct: float
    rel_vs_nifty: float


@dataclass(frozen=True)
class TradePlan:
    stock_name: str
    trade_direction: str
    market_regime: str
    sector_trend: str
    selection_criteria_summary: list[str]
    entry_zone: str
    stop_loss: str
    targets: str
    reward_risk: str
    confidence_score: int
    reasoning: str
    invalidation_conditions: list[str]
    why_chosen: list[str]


class MarketDataError(RuntimeError):
    pass


class MarketDataProvider(Protocol):
    def get_preopen_watchlist(self, universe: list[str]) -> list[PreopenRow]:
        ...

    def get_intraday_candles(
        self, symbols: list[str], start: datetime, end: datetime, interval: str = "1m"
    ) -> dict[str, list[Candle]]:
        ...

    def get_prev_day_ohlc(self, symbols: list[str]) -> dict[str, PrevDayOHLC]:
        ...

    def get_52w_highlow(self, symbols: list[str]) -> dict[str, HighLow52W | None]:
        ...

    def get_sector_index_snapshot(self) -> dict[str, SectorSnapshot]:
        ...

    def get_index_intraday(
        self, symbols: list[str], start: datetime, end: datetime, interval: str = "1m"
    ) -> dict[str, list[Candle]]:
        ...
