from __future__ import annotations

from datetime import datetime

import pytest

from src.providers.base import Candle, HighLow52W, PrevDayOHLC, SectorSnapshot
from src.providers.dual_source_provider import DualSourceProvider


class FakeProvider:
    def __init__(self, drift_pct: float = 0.0, fail: bool = False) -> None:
        self.drift_pct = drift_pct
        self.fail = fail
        self.calls: list[str] = []

    def _m(self, v: float) -> float:
        return v * (1 + self.drift_pct / 100.0)

    def get_intraday_candles(self, symbols, start, end, interval="1m"):
        self.calls.append(f"intraday:{interval}")
        if self.fail:
            raise RuntimeError("intraday_fail")
        return {
            s: [
                Candle(start, self._m(100), self._m(101), self._m(99), self._m(100.5), self._m(1000)),
                Candle(end, self._m(100.5), self._m(102), self._m(100), self._m(101.2), self._m(1500)),
            ]
            for s in symbols
        }

    def get_prev_day_ohlc(self, symbols):
        self.calls.append("prev")
        if self.fail:
            raise RuntimeError("prev_fail")
        return {s: PrevDayOHLC(pdh=self._m(100), pdl=self._m(98), prev_close=self._m(99)) for s in symbols}

    def get_52w_highlow(self, symbols):
        self.calls.append("52w")
        if self.fail:
            raise RuntimeError("52w_fail")
        return {s: HighLow52W(high_52w=200, low_52w=50) for s in symbols}

    def get_sector_index_snapshot(self):
        self.calls.append("sectors")
        if self.fail:
            raise RuntimeError("sector_fail")
        return {"IT": SectorSnapshot(name="IT", last=40000, change_pct=1.0, rel_vs_nifty=0.5)}

    def get_index_intraday(self, symbols, start, end, interval="1m"):
        self.calls.append(f"index:{interval}")
        if self.fail:
            raise RuntimeError("index_fail")
        return {
            "NIFTY": [Candle(start, 22000, 22020, 21990, 22010, 0), Candle(end, 22010, 22030, 22000, 22020, 0)],
            "BANKNIFTY": [Candle(start, 48000, 48020, 47980, 48010, 0), Candle(end, 48010, 48030, 48000, 48020, 0)],
        }


class FallbackProvider(FakeProvider):
    def __init__(self):
        super().__init__(drift_pct=0.0, fail=False)

    def get_intraday_candles(self, symbols, start, end, interval="1m"):
        self.calls.append(f"intraday:{interval}")
        if interval == "1m":
            raise RuntimeError("1m_unavailable")
        return super().get_intraday_candles(symbols, start, end, interval)


class EmptyOneMinuteProvider(FakeProvider):
    def get_intraday_candles(self, symbols, start, end, interval="1m"):
        self.calls.append(f"intraday:{interval}")
        if interval == "1m":
            return {s: [] for s in symbols}
        return super().get_intraday_candles(symbols, start, end, interval)


def test_validation_pass_within_thresholds(tmp_path):
    p = FakeProvider(drift_pct=0.0)
    s = FakeProvider(drift_pct=0.2)  # below 0.35
    dsp = DualSourceProvider(
        primary=p,
        secondary=s,
        price_diff_tolerance_pct=0.35,
        volume_diff_tolerance_pct=10.0,
        validation_audit_dir=str(tmp_path),
    )
    start = datetime.fromisoformat("2025-02-03T09:15:00+05:30")
    end = datetime.fromisoformat("2025-02-03T09:30:00+05:30")
    bundle = dsp.prepare_stage1_data_with_backup(["INFY"], start, end)
    assert bundle.context.validation.status == "PASS"


def test_validation_fail_above_thresholds(tmp_path):
    p = FakeProvider(drift_pct=0.0)
    s = FakeProvider(drift_pct=1.0)  # above 0.35
    dsp = DualSourceProvider(
        primary=p,
        secondary=s,
        price_diff_tolerance_pct=0.35,
        volume_diff_tolerance_pct=10.0,
        validation_audit_dir=str(tmp_path),
    )
    start = datetime.fromisoformat("2025-02-03T09:15:00+05:30")
    end = datetime.fromisoformat("2025-02-03T09:30:00+05:30")
    bundle = dsp.prepare_stage1_data_with_backup(["INFY"], start, end)
    assert bundle.context.validation.status == "FAIL"
    assert bundle.context.validation.fail_reasons


def test_interval_fallback_uses_5m_when_1m_missing(tmp_path):
    primary = FakeProvider(fail=True)
    secondary = FallbackProvider()
    dsp = DualSourceProvider(
        primary=primary,
        secondary=secondary,
        fallback_intervals=["1m", "5m", "15m"],
        validation_audit_dir=str(tmp_path),
    )
    start = datetime.fromisoformat("2025-02-03T09:15:00+05:30")
    end = datetime.fromisoformat("2025-02-03T09:30:00+05:30")
    bundle = dsp.prepare_stage1_data_with_backup(["INFY"], start, end)
    assert bundle.context.data_source == "NSE_BACKUP"
    assert bundle.context.backup_interval_used == "5m"


def test_interval_fallback_uses_5m_when_1m_empty(tmp_path):
    primary = FakeProvider(fail=True)
    secondary = EmptyOneMinuteProvider()
    dsp = DualSourceProvider(
        primary=primary,
        secondary=secondary,
        fallback_intervals=["1m", "5m", "15m"],
        validation_audit_dir=str(tmp_path),
    )
    start = datetime.fromisoformat("2099-02-03T09:15:00+05:30")
    end = datetime.fromisoformat("2099-02-03T09:30:00+05:30")
    bundle = dsp.prepare_stage1_data_with_backup(["INFY"], start, end)
    assert bundle.context.data_source == "NSE_BACKUP"
    assert bundle.context.backup_interval_used == "5m"


def test_both_sources_unavailable_raises(tmp_path):
    p = FakeProvider(fail=True)
    s = FakeProvider(fail=True)
    dsp = DualSourceProvider(primary=p, secondary=s, validation_audit_dir=str(tmp_path))
    start = datetime.fromisoformat("2025-02-03T09:15:00+05:30")
    end = datetime.fromisoformat("2025-02-03T09:30:00+05:30")
    with pytest.raises(Exception):
        dsp.prepare_stage1_data_with_backup(["INFY"], start, end)
