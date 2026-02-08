from __future__ import annotations

import argparse
import sys
from datetime import datetime

from src.config import IST, load_settings
from src.engine.replay_time import resolve_replay_date
from src.engine.stage0_preopen import run_stage0
from src.engine.stage1_openingrange import run_stage1_report
from src.notifications.telegram import TelegramNotifier
from src.providers.dual_source_provider import DualSourceProvider
from src.providers.nse_provider import NseWebProvider
from src.providers.shoonya_provider import ShoonyaCreds, ShoonyaProvider
from src.reports.xlsx_report import write_stage1_report_xlsx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Indian Intraday Trading Assistant runner")
    parser.add_argument(
        "--stage",
        choices=["preopen", "openingrange", "both"],
        required=True,
        help="Which stage to run",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print message instead of sending Telegram")
    parser.add_argument("--source-auto", action="store_true", help="Use source selection policy (default).")
    parser.add_argument(
        "--source-force",
        choices=["shoonya", "nse"],
        default=None,
        help="Force a single source for Stage-1.",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip cross-source validation for manual emergency runs.",
    )
    parser.add_argument("--test-mode", action="store_true", help="Replay mode using last trading day data windows.")
    parser.add_argument("--replay-date", default=None, help="Replay date in YYYY-MM-DD (test mode only).")
    parser.add_argument("--test-send", action="store_true", help="Allow Telegram send in test mode.")
    parser.add_argument("--show-process", action="store_true", help="Print execution process details.")
    return parser.parse_args()


def _parse_stage_time(now_ist: datetime, hhmm: str) -> datetime:
    date_prefix = now_ist.date().isoformat()
    return datetime.fromisoformat(f"{date_prefix}T{hhmm}:00+05:30")


def main() -> int:
    args = parse_args()
    settings = load_settings()

    test_mode = args.test_mode or settings.test_mode
    now_ist = datetime.now(tz=IST)
    replay_date = None
    if test_mode:
        explicit = args.replay_date or settings.test_replay_date or None
        if not settings.test_replay_auto_last_trading_day and explicit is None:
            raise RuntimeError("test_replay_date_required_when_auto_last_trading_day_disabled")
        replay = resolve_replay_date(now_ist, explicit_date=explicit)
        replay_date = replay.isoformat()
        now_ist = datetime.fromisoformat(f"{replay_date}T{settings.test_time_openingrange}:00+05:30")
        if args.show_process:
            print(f"[process] test_mode=on replay_date={replay_date}")

    # In test mode, default to no-send unless explicitly enabled.
    can_send_in_test = args.test_send or settings.test_send_to_telegram
    dry_run = args.dry_run or settings.dry_run or (test_mode and not can_send_in_test)

    if settings.data_provider != "SHOONYA":
        print(f"fatal_error:unsupported_data_provider:{settings.data_provider}")
        return 1

    market_provider = None
    if args.stage in {"openingrange", "both"}:
        shoonya = ShoonyaProvider(
            creds=ShoonyaCreds(
                user_id=settings.shoonya_user_id,
                password=settings.shoonya_password,
                totp_secret=settings.shoonya_totp_secret,
                vendor_code=settings.shoonya_vendor_code,
                api_secret=settings.shoonya_api_secret,
                imei=settings.shoonya_imei,
                session_token=settings.shoonya_session_token,
            ),
            symbol_master_cache_path=settings.symbol_master_cache_path,
            symbol_master_max_age_hours=settings.symbol_master_max_age_hours,
        )
        nse_for_stage1 = NseWebProvider()
        market_provider = DualSourceProvider(
            primary=shoonya,
            secondary=nse_for_stage1,
            enable_nse_backup=settings.enable_nse_backup,
            enable_cross_validation=settings.enable_cross_validation,
            price_diff_tolerance_pct=settings.price_diff_tolerance_pct,
            volume_diff_tolerance_pct=settings.volume_diff_tolerance_pct,
            fallback_intervals=settings.nse_fallback_intervals,
            validation_audit_dir=settings.validation_audit_dir,
        )
        if args.source_force == "nse":
            market_provider.set_run_options(source_mode="force_nse", skip_validation=args.skip_validation)
        elif args.source_force == "shoonya":
            market_provider.set_run_options(source_mode="force_shoonya", skip_validation=args.skip_validation)
        else:
            market_provider.set_run_options(source_mode="auto", skip_validation=args.skip_validation)
        if test_mode and hasattr(market_provider, "set_reference_now"):
            market_provider.set_reference_now(now_ist)

    nse = NseWebProvider()
    if test_mode and hasattr(nse, "set_reference_now"):
        nse.set_reference_now(now_ist)
    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)

    try:
        if settings.admin_status_notifications:
            notifier.send(
                f"Job started: stage={args.stage} source_force={args.source_force or 'auto'} at {now_ist.strftime('%Y-%m-%d %H:%M:%S %Z')}",
                dry_run=dry_run,
                parse_mode=settings.telegram_parse_mode,
            )

        if args.stage in {"preopen", "both"}:
            stage0_now = _parse_stage_time(now_ist, settings.test_time_preopen) if test_mode else now_ist
            msg0 = run_stage0(
                settings,
                nse,
                None,
                stage0_now,
                test_mode=test_mode,
                replay_date=replay_date,
                show_process=args.show_process,
            )
            if test_mode and not msg0.startswith("TEST MODE: ON"):
                msg0 = "[TEST MODE]\n" + msg0
            notifier.send_chunked(
                msg0,
                max_chars=settings.telegram_max_chars,
                dry_run=dry_run,
                parse_mode=settings.telegram_parse_mode if settings.telegram_enable_rich_format else None,
            )

        if args.stage in {"openingrange", "both"}:
            if market_provider is None:
                raise RuntimeError("stage1_provider_not_initialized")
            stage1_now = _parse_stage_time(now_ist, settings.test_time_openingrange) if test_mode else now_ist
            report = run_stage1_report(
                settings,
                market_provider,
                stage1_now,
                test_mode=test_mode,
                replay_date=replay_date,
                show_process=args.show_process,
            )
            msg1 = report.message_text
            if test_mode and not msg1.startswith("TEST MODE: ON") and not msg1.startswith("[TEST MODE]"):
                msg1 = "[TEST MODE]\n" + msg1
            report_path = write_stage1_report_xlsx(report, settings.reports_dir, stage1_now)
            msg1 = msg1 + f"\n\nWorkbook attached: {report.workbook_name}"
            notifier.send_chunked(
                msg1,
                max_chars=settings.telegram_max_chars,
                dry_run=dry_run,
                parse_mode=settings.telegram_parse_mode if settings.telegram_enable_rich_format else None,
            )
            if settings.telegram_attach_xlsx:
                caption = "[TEST MODE] Stage-1 audit workbook" if test_mode else "Stage-1 audit workbook"
                notifier.send_document(report_path, caption=caption, dry_run=dry_run)
    except Exception as exc:
        if settings.admin_status_notifications:
            try:
                notifier.send(
                    f"Job failed: stage={args.stage} source_force={args.source_force or 'auto'} error={exc}",
                    dry_run=dry_run,
                    parse_mode=settings.telegram_parse_mode,
                )
            except Exception:
                pass
        print(f"fatal_error:{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
