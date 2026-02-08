from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from src.config import Settings
from src.engine.stage0_preopen import run_stage0
from src.engine.stage1_openingrange import run_stage1
from src.providers.base import Candle, HighLow52W, PreopenRow, PrevDayOHLC, SectorSnapshot
from src.providers.dual_source_provider import (
    Stage1Bundle,
    Stage1Context,
    ValidationIssue,
    ValidationReport,
)


class MockProvider:
    def __init__(self, fail_intraday: bool = False) -> None:
        self.fail_intraday = fail_intraday

    def get_preopen_watchlist(self, universe: list[str]) -> list[PreopenRow]:
        return [
            PreopenRow(symbol="INFY", indicative_price=1800.0, change_pct=1.2, volume=100000, status="OK"),
            PreopenRow(symbol="TCS", indicative_price=3900.0, change_pct=0.9, volume=80000, status="OK"),
        ]

    def get_intraday_candles(self, symbols, start, end, interval="1m"):
        if self.fail_intraday:
            raise RuntimeError("intraday_down")
        out = {}
        for i, sym in enumerate(symbols):
            base = 100 + i
            out[sym] = [
                Candle(start, base, base + 0.4, base - 0.1, base + 0.2, 1000),
                Candle(start, base + 0.2, base + 0.8, base + 0.1, base + 0.7, 1500),
                Candle(start, base + 0.7, base + 1.4, base + 0.6, base + 1.2, 1800),
            ]
        return out

    def get_prev_day_ohlc(self, symbols):
        return {sym: PrevDayOHLC(pdh=99.0 + i, pdl=96.0 + i, prev_close=98.5 + i) for i, sym in enumerate(symbols)}

    def get_52w_highlow(self, symbols):
        return {sym: HighLow52W(high_52w=200 + i, low_52w=50 + i) for i, sym in enumerate(symbols)}

    def get_sector_index_snapshot(self):
        return {
            "IT": SectorSnapshot(name="IT", last=40000, change_pct=1.1, rel_vs_nifty=0.6),
            "OTHER": SectorSnapshot(name="OTHER", last=50000, change_pct=0.2, rel_vs_nifty=0.1),
        }

    def get_index_intraday(self, symbols, start, end, interval="1m"):
        return {
            "NIFTY": [
                Candle(start, 22000, 22020, 21990, 22015, 1000),
                Candle(start, 22015, 22040, 22010, 22035, 1000),
            ],
            "BANKNIFTY": [
                Candle(start, 48000, 48030, 47980, 48020, 1000),
                Candle(start, 48020, 48050, 48010, 48045, 1000),
            ],
        }


class MockDualProvider:
    def __init__(self, fail=False, validation_fail=False, nse_backup=False, interval="1m") -> None:
        self.fail = fail
        self.validation_fail = validation_fail
        self.nse_backup = nse_backup
        self.interval = interval
        self.base = MockProvider()

    def prepare_stage1_data_with_backup(self, symbols, start, end):
        if self.fail:
            raise RuntimeError("both_sources_unavailable")
        candles = self.base.get_intraday_candles(symbols, start, end, "1m")
        prev = self.base.get_prev_day_ohlc(symbols)
        hl52 = self.base.get_52w_highlow(symbols)
        sectors = self.base.get_sector_index_snapshot()
        idx = self.base.get_index_intraday(["NIFTY", "BANKNIFTY"], start, end, "1m")
        report = ValidationReport(
            source_primary="SHOONYA",
            source_secondary="NSE_BACKUP",
            status="FAIL" if self.validation_fail else "PASS",
            issues=(
                [
                    ValidationIssue(
                        symbol="INFY",
                        field="last",
                        primary_value=100.0,
                        secondary_value=101.0,
                        diff_pct=0.99,
                        severity="FAIL",
                    )
                ]
                if self.validation_fail
                else []
            ),
            fail_reasons=["INFY:last:0.99%"] if self.validation_fail else [],
        )
        context = Stage1Context(
            data_source="NSE_BACKUP" if self.nse_backup else "SHOONYA",
            backup_interval_used=self.interval,
            validation=report,
        )
        return Stage1Bundle(candles, prev, hl52, sectors, idx, context)


def _settings() -> Settings:
    return Settings(
        telegram_bot_token="x",
        telegram_chat_id="y",
        telegram_parse_mode="HTML",
        telegram_enable_rich_format=True,
        telegram_max_chars=3900,
        telegram_attach_xlsx=True,
        reports_dir="data/reports",
        report_include_candles=False,
        data_provider="SHOONYA",
        shoonya_user_id="u",
        shoonya_password="p",
        shoonya_totp_secret="totp",
        shoonya_vendor_code="v",
        shoonya_api_secret="a",
        shoonya_imei="imei",
        shoonya_session_token="",
        symbol_master_cache_path="data/test_symbol_cache.json",
        symbol_master_max_age_hours=24,
        enable_nse_backup=True,
        enable_cross_validation=True,
        price_diff_tolerance_pct=0.35,
        volume_diff_tolerance_pct=10.0,
        nse_fallback_intervals=["1m", "5m", "15m"],
        validation_audit_dir="data/validation",
        capital=51000,
        risk_per_trade_pct=1.0,
        max_trades_per_day=3,
        min_rr=1.8,
        universe_name="NIFTY50",
        top_candidates=5,
        top_long_candidates=5,
        top_short_candidates=5,
        fill_empty_slots=True,
        allow_vwap_rejection_filter=True,
        dry_run=True,
        admin_status_notifications=False,
        test_mode=False,
        test_replay_auto_last_trading_day=True,
        test_replay_date="",
        test_send_to_telegram=False,
        test_time_preopen="09:07",
        test_time_openingrange="09:30",
        open_range_start="09:15",
        open_range_end="09:30",
        stage0_time="09:07",
        timezone=None,  # type: ignore[arg-type]
    )


def test_stage0_formats_watchlist_message():
    settings = _settings()
    now = datetime.fromisoformat("2025-02-03T09:07:00+05:30")
    msg = run_stage0(settings, MockProvider(), None, now)
    assert "Pre-open Watchlist" in msg
    assert "INFY" in msg
    assert "Wait for 09:15-09:30 performance confirmation." in msg


def test_stage0_test_mode_replay_on_weekend():
    settings = _settings()
    now = datetime.fromisoformat("2026-02-08T09:07:00+05:30")  # Sunday
    msg = run_stage0(settings, MockProvider(), None, now, test_mode=True, replay_date="2026-02-06")
    assert "TEST MODE: ON" in msg
    assert "SIMULATED PRE-OPEN" in msg
    assert "Replay mode: Stage-1 opening-range replay will be sent for 2026-02-06." in msg


def test_stage1_produces_dual_top5_blocks():
    settings = _settings()
    settings = replace(settings, top_long_candidates=2, top_short_candidates=2)
    now = datetime.fromisoformat("2025-02-03T09:30:00+05:30")
    msg = run_stage1(settings, MockProvider(), now)
    assert "Top 2 LONG (Ranked)" in msg
    assert "Top 2 SHORT (Ranked)" in msg
    assert "Status:" in msg
    assert "NO CANDIDATE" not in msg
    assert "Invalidation Conditions:" in msg


def test_stage1_intraday_failure_forces_no_trade():
    settings = _settings()
    now = datetime.fromisoformat("2025-02-03T09:30:00+05:30")
    msg = run_stage1(settings, MockProvider(fail_intraday=True), now)
    assert "NO TRADE - CONDITIONS NOT FAVORABLE" in msg


def test_stage1_validation_fail_forces_no_trade():
    settings = _settings()
    now = datetime.fromisoformat("2025-02-03T09:30:00+05:30")
    msg = run_stage1(settings, MockDualProvider(validation_fail=True), now)
    assert "NO TRADE - CONDITIONS NOT FAVORABLE" in msg
    assert "Mismatch Summary:" in msg


def test_stage1_nse_backup_includes_disclaimer():
    settings = _settings()
    now = datetime.fromisoformat("2025-02-03T09:30:00+05:30")
    msg = run_stage1(settings, MockDualProvider(nse_backup=True, interval="5m"), now)
    assert "Data Source:</b> NSE_BACKUP" in msg
    assert "reduced confidence" in msg


def test_stage1_test_mode_weekend_replay_headers():
    settings = _settings()
    now = datetime.fromisoformat("2026-02-08T09:30:00+05:30")  # Sunday
    msg = run_stage1(
        settings,
        MockDualProvider(nse_backup=False, interval="1m"),
        now,
        test_mode=True,
        replay_date="2026-02-06",
    )
    assert "TEST MODE: ON" in msg
    assert "Replay Date:" in msg
    assert "2026-02-06" in msg
