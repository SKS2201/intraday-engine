from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


try:
    IST = ZoneInfo("Asia/Kolkata")
except Exception:
    IST = timezone(timedelta(hours=5, minutes=30), name="IST")

NIFTY50_SYMBOLS = [
    "ADANIENT",
    "ADANIPORTS",
    "APOLLOHOSP",
    "ASIANPAINT",
    "AXISBANK",
    "BAJAJ-AUTO",
    "BAJFINANCE",
    "BAJAJFINSV",
    "BEL",
    "BHARTIARTL",
    "BPCL",
    "BRITANNIA",
    "CIPLA",
    "COALINDIA",
    "DRREDDY",
    "EICHERMOT",
    "ETERNAL",
    "GRASIM",
    "HCLTECH",
    "HDFCBANK",
    "HDFCLIFE",
    "HEROMOTOCO",
    "HINDALCO",
    "HINDUNILVR",
    "ICICIBANK",
    "INDUSINDBK",
    "INFY",
    "ITC",
    "JIOFIN",
    "JSWSTEEL",
    "KOTAKBANK",
    "LT",
    "M&M",
    "MARUTI",
    "NESTLEIND",
    "NTPC",
    "ONGC",
    "POWERGRID",
    "RELIANCE",
    "SBILIFE",
    "SBIN",
    "SHRIRAMFIN",
    "SUNPHARMA",
    "TATACONSUM",
    "TATAMOTORS",
    "TATASTEEL",
    "TCS",
    "TECHM",
    "TITAN",
    "ULTRACEMCO",
    "WIPRO",
]


def _to_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_parse_mode: str
    telegram_enable_rich_format: bool
    telegram_max_chars: int
    telegram_attach_xlsx: bool
    reports_dir: str
    report_include_candles: bool
    data_provider: str
    shoonya_user_id: str
    shoonya_password: str
    shoonya_totp_secret: str
    shoonya_vendor_code: str
    shoonya_api_secret: str
    shoonya_imei: str
    shoonya_session_token: str
    symbol_master_cache_path: str
    symbol_master_max_age_hours: int
    enable_nse_backup: bool
    enable_cross_validation: bool
    price_diff_tolerance_pct: float
    volume_diff_tolerance_pct: float
    nse_fallback_intervals: list[str]
    validation_audit_dir: str
    capital: float
    risk_per_trade_pct: float
    max_trades_per_day: int
    min_rr: float
    universe_name: str
    top_candidates: int
    top_long_candidates: int
    top_short_candidates: int
    fill_empty_slots: bool
    allow_vwap_rejection_filter: bool
    dry_run: bool
    admin_status_notifications: bool
    test_mode: bool
    test_replay_auto_last_trading_day: bool
    test_replay_date: str
    test_send_to_telegram: bool
    test_time_preopen: str
    test_time_openingrange: str
    open_range_start: str
    open_range_end: str
    stage0_time: str
    timezone: tzinfo

    @property
    def risk_amount(self) -> float:
        return self.capital * (self.risk_per_trade_pct / 100.0)

    @property
    def universe(self) -> list[str]:
        if self.universe_name.upper() == "NIFTY50":
            return NIFTY50_SYMBOLS
        raw = os.getenv("UNIVERSE_SYMBOLS", "")
        return [x.strip().upper() for x in raw.split(",") if x.strip()]


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        telegram_parse_mode=os.getenv("TELEGRAM_PARSE_MODE", "HTML"),
        telegram_enable_rich_format=_to_bool(os.getenv("TELEGRAM_ENABLE_RICH_FORMAT"), True),
        telegram_max_chars=int(os.getenv("TELEGRAM_MAX_CHARS", "3900")),
        telegram_attach_xlsx=_to_bool(os.getenv("TELEGRAM_ATTACH_XLSX"), True),
        reports_dir=os.getenv("REPORTS_DIR", "data/reports"),
        report_include_candles=_to_bool(os.getenv("REPORT_INCLUDE_CANDLES"), False),
        data_provider=os.getenv("DATA_PROVIDER", "SHOONYA").upper(),
        shoonya_user_id=os.getenv("SHOONYA_USER_ID", ""),
        shoonya_password=os.getenv("SHOONYA_PASSWORD", ""),
        shoonya_totp_secret=os.getenv("SHOONYA_TOTP_SECRET", ""),
        shoonya_vendor_code=os.getenv("SHOONYA_VENDOR_CODE", ""),
        shoonya_api_secret=os.getenv("SHOONYA_API_SECRET", ""),
        shoonya_imei=os.getenv("SHOONYA_IMEI", "abc1234"),
        shoonya_session_token=os.getenv("SHOONYA_SESSION_TOKEN", ""),
        symbol_master_cache_path=os.getenv("SYMBOL_MASTER_CACHE_PATH", "data/symbol_master_cache.json"),
        symbol_master_max_age_hours=int(os.getenv("SYMBOL_MASTER_MAX_AGE_HOURS", "24")),
        enable_nse_backup=_to_bool(os.getenv("ENABLE_NSE_BACKUP"), True),
        enable_cross_validation=_to_bool(os.getenv("ENABLE_CROSS_VALIDATION"), True),
        price_diff_tolerance_pct=float(os.getenv("PRICE_DIFF_TOLERANCE_PCT", "0.35")),
        volume_diff_tolerance_pct=float(os.getenv("VOLUME_DIFF_TOLERANCE_PCT", "10.0")),
        nse_fallback_intervals=[
            x.strip()
            for x in os.getenv("NSE_FALLBACK_INTERVALS", "1m,5m,15m").split(",")
            if x.strip()
        ],
        validation_audit_dir=os.getenv("VALIDATION_AUDIT_DIR", "data/validation"),
        capital=float(os.getenv("CAPITAL", "51000")),
        risk_per_trade_pct=float(os.getenv("RISK_PER_TRADE_PCT", "1.0")),
        max_trades_per_day=int(os.getenv("MAX_TRADES_PER_DAY", "3")),
        min_rr=float(os.getenv("MIN_RR", "1.8")),
        universe_name=os.getenv("UNIVERSE", "NIFTY50"),
        top_candidates=int(os.getenv("TOP_CANDIDATES", "5")),
        top_long_candidates=int(os.getenv("TOP_LONG_CANDIDATES", "5")),
        top_short_candidates=int(os.getenv("TOP_SHORT_CANDIDATES", "5")),
        fill_empty_slots=_to_bool(os.getenv("FILL_EMPTY_SLOTS"), True),
        allow_vwap_rejection_filter=_to_bool(
            os.getenv("ALLOW_VWAP_REJECTION_FILTER"), True
        ),
        dry_run=_to_bool(os.getenv("DRY_RUN"), False),
        admin_status_notifications=_to_bool(os.getenv("ADMIN_STATUS_NOTIFICATIONS"), False),
        test_mode=_to_bool(os.getenv("TEST_MODE"), False),
        test_replay_auto_last_trading_day=_to_bool(
            os.getenv("TEST_REPLAY_AUTO_LAST_TRADING_DAY"), True
        ),
        test_replay_date=os.getenv("TEST_REPLAY_DATE", "").strip(),
        test_send_to_telegram=_to_bool(os.getenv("TEST_SEND_TO_TELEGRAM"), False),
        test_time_preopen=os.getenv("TEST_TIME_PREOPEN", "09:10"),
        test_time_openingrange=os.getenv("TEST_TIME_OPENINGRANGE", "09:30"),
        open_range_start=os.getenv("OPEN_RANGE_START", "09:15"),
        open_range_end=os.getenv("OPEN_RANGE_END", "09:30"),
        stage0_time=os.getenv("STAGE0_TIME", "09:10"),
        timezone=IST,
    )
