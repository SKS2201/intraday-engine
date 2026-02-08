from __future__ import annotations

from dataclasses import replace

from src.config import Settings
from src.engine import runner
from src.engine.stage1_openingrange import Stage1RunReport


def _settings() -> Settings:
    return Settings(
        telegram_bot_token="x",
        telegram_chat_id="y",
        telegram_message_prefix="",
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


class DummyDual:
    def __init__(self, *args, **kwargs):
        self.options = None

    def set_run_options(self, source_mode: str = "auto", skip_validation: bool = False):
        self.options = (source_mode, skip_validation)

    def set_reference_now(self, now_dt):
        return


def _report() -> Stage1RunReport:
    return Stage1RunReport(
        message_text="ok",
        run_summary={"decision": "ok"},
        long_rows=[],
        short_rows=[],
        metrics_rows=[],
        validation_rows=[],
        process_log=[],
        workbook_name="stage1_test.xlsx",
    )


def test_runner_force_nse_sets_options(mocker):
    dual = DummyDual()
    mocker.patch("src.engine.runner.load_settings", return_value=_settings())
    mocker.patch("src.engine.runner.ShoonyaProvider", return_value=object())
    mocker.patch("src.engine.runner.NseWebProvider", return_value=object())
    mocker.patch("src.engine.runner.DualSourceProvider", return_value=dual)
    mocker.patch("src.engine.runner.run_stage1_report", return_value=_report())
    mocker.patch("src.engine.runner.write_stage1_report_xlsx", return_value="data/reports/stage1_test.xlsx")
    send_chunked = mocker.patch("src.engine.runner.TelegramNotifier.send_chunked", return_value=None)
    mocker.patch("src.engine.runner.TelegramNotifier.send_document", return_value=None)
    mocker.patch(
        "src.engine.runner.parse_args",
        return_value=type(
            "Args",
            (),
            {
                "stage": "openingrange",
                "dry_run": True,
                "source_auto": False,
                "source_force": "nse",
                "skip_validation": False,
                "test_mode": False,
                "replay_date": None,
                "test_send": False,
                "show_process": False,
            },
        )(),
    )
    rc = runner.main()
    assert rc == 0
    assert dual.options == ("force_nse", False)
    assert send_chunked.called


def test_runner_force_shoonya_skip_validation(mocker):
    dual = DummyDual()
    mocker.patch("src.engine.runner.load_settings", return_value=_settings())
    mocker.patch("src.engine.runner.ShoonyaProvider", return_value=object())
    mocker.patch("src.engine.runner.NseWebProvider", return_value=object())
    mocker.patch("src.engine.runner.DualSourceProvider", return_value=dual)
    mocker.patch("src.engine.runner.run_stage1_report", return_value=_report())
    mocker.patch("src.engine.runner.write_stage1_report_xlsx", return_value="data/reports/stage1_test.xlsx")
    mocker.patch("src.engine.runner.TelegramNotifier.send_chunked", return_value=None)
    mocker.patch("src.engine.runner.TelegramNotifier.send_document", return_value=None)
    mocker.patch(
        "src.engine.runner.parse_args",
        return_value=type(
            "Args",
            (),
            {
                "stage": "openingrange",
                "dry_run": True,
                "source_auto": False,
                "source_force": "shoonya",
                "skip_validation": True,
                "test_mode": False,
                "replay_date": None,
                "test_send": False,
                "show_process": False,
            },
        )(),
    )
    rc = runner.main()
    assert rc == 0
    assert dual.options == ("force_shoonya", True)


def test_runner_test_mode_defaults_to_no_send(mocker):
    dual = DummyDual()
    settings = _settings()
    settings = replace(settings, test_mode=True, dry_run=False, test_send_to_telegram=False)
    mocker.patch("src.engine.runner.load_settings", return_value=settings)
    mocker.patch("src.engine.runner.ShoonyaProvider", return_value=object())
    mocker.patch("src.engine.runner.NseWebProvider", return_value=object())
    mocker.patch("src.engine.runner.DualSourceProvider", return_value=dual)
    mocker.patch(
        "src.engine.runner.run_stage1_report",
        return_value=replace(_report(), message_text="TEST MODE: ON\nReplay Date: 2026-02-06\nok"),
    )
    mocker.patch("src.engine.runner.write_stage1_report_xlsx", return_value="data/reports/stage1_test.xlsx")
    send_chunked = mocker.patch("src.engine.runner.TelegramNotifier.send_chunked", return_value=None)
    mocker.patch("src.engine.runner.TelegramNotifier.send_document", return_value=None)
    mocker.patch(
        "src.engine.runner.parse_args",
        return_value=type(
            "Args",
            (),
            {
                "stage": "openingrange",
                "dry_run": False,
                "source_auto": False,
                "source_force": "nse",
                "skip_validation": False,
                "test_mode": True,
                "replay_date": None,
                "test_send": False,
                "show_process": False,
            },
        )(),
    )
    rc = runner.main()
    assert rc == 0
    assert send_chunked.called


def test_runner_applies_telegram_message_prefix(mocker):
    dual = DummyDual()
    settings = replace(_settings(), telegram_message_prefix="[STAGING]")
    mocker.patch("src.engine.runner.load_settings", return_value=settings)
    mocker.patch("src.engine.runner.ShoonyaProvider", return_value=object())
    mocker.patch("src.engine.runner.NseWebProvider", return_value=object())
    mocker.patch("src.engine.runner.DualSourceProvider", return_value=dual)
    mocker.patch("src.engine.runner.run_stage1_report", return_value=_report())
    mocker.patch("src.engine.runner.write_stage1_report_xlsx", return_value="data/reports/stage1_test.xlsx")
    send_chunked = mocker.patch("src.engine.runner.TelegramNotifier.send_chunked", return_value=None)
    mocker.patch("src.engine.runner.TelegramNotifier.send_document", return_value=None)
    mocker.patch(
        "src.engine.runner.parse_args",
        return_value=type(
            "Args",
            (),
            {
                "stage": "openingrange",
                "dry_run": True,
                "source_auto": False,
                "source_force": "nse",
                "skip_validation": False,
                "test_mode": False,
                "replay_date": None,
                "test_send": False,
                "show_process": False,
            },
        )(),
    )
    rc = runner.main()
    assert rc == 0
    assert send_chunked.called
    sent_text = send_chunked.call_args.args[0]
    assert sent_text.startswith("[STAGING]\n")
