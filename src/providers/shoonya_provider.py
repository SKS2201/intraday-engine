from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pyotp

from src.providers.base import (
    Candle,
    HighLow52W,
    MarketDataError,
    PreopenRow,
    PrevDayOHLC,
    SectorSnapshot,
)


@dataclass(frozen=True)
class ShoonyaCreds:
    user_id: str
    password: str
    totp_secret: str
    vendor_code: str
    api_secret: str
    imei: str
    session_token: str = ""


class ShoonyaProvider:
    """
    Shoonya market data adapter built on the official Noren API client.
    Stage-0 pre-open is intentionally unsupported here; NSE fallback handles it.
    """

    SECTOR_SYMBOLS = {
        "NIFTY": "Nifty 50",
        "BANKNIFTY": "Nifty Bank",
        "IT": "Nifty IT",
    }

    def __init__(
        self,
        creds: ShoonyaCreds,
        symbol_master_cache_path: str,
        symbol_master_max_age_hours: int = 24,
        api_client: Any | None = None,
    ) -> None:
        self.creds = creds
        self.symbol_master_cache_path = Path(symbol_master_cache_path)
        self.symbol_master_max_age_hours = symbol_master_max_age_hours
        self._cache: dict[str, str] = {}
        self._authed = False
        self._api = api_client or self._build_api()
        self.reference_now: datetime | None = None

    def set_reference_now(self, now_dt: datetime) -> None:
        self.reference_now = now_dt

    def _build_api(self) -> Any:
        try:
            from NorenRestApiPy.NorenApi import NorenApi  # type: ignore[import-not-found]
        except Exception as exc:
            raise MarketDataError(
                f"shoonya_sdk_missing:{exc}. Install NorenRestApiPy."
            ) from exc

        class _Client(NorenApi):  # type: ignore[misc]
            def __init__(self) -> None:
                super().__init__(
                    host="https://api.shoonya.com/NorenWClientTP/",
                    websocket="wss://api.shoonya.com/NorenWSTP/",
                )

        return _Client()

    def _ensure_login(self) -> None:
        if self._authed:
            return

        if self.creds.session_token:
            try:
                self._api.set_session(
                    userid=self.creds.user_id,
                    password=self.creds.password,
                    usertoken=self.creds.session_token,
                )
                self._authed = True
                return
            except Exception:
                pass

        if not all(
            [
                self.creds.user_id,
                self.creds.password,
                self.creds.totp_secret,
                self.creds.vendor_code,
                self.creds.api_secret,
            ]
        ):
            raise MarketDataError("shoonya_credentials_missing")

        factor2 = pyotp.TOTP(self.creds.totp_secret).now()
        resp = self._api.login(
            userid=self.creds.user_id,
            password=self.creds.password,
            twoFA=factor2,
            vendor_code=self.creds.vendor_code,
            api_secret=self.creds.api_secret,
            imei=self.creds.imei,
        )
        if not resp or str(resp.get("stat", "")).upper() != "OK":
            raise MarketDataError(f"shoonya_login_failed:{resp}")
        self._authed = True

    def _to_epoch(self, dt: datetime) -> int:
        return int(dt.timestamp())

    def _parse_float(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    def _parse_ts(self, raw: dict[str, Any]) -> datetime:
        ts_val = raw.get("ssboe") or raw.get("time") or raw.get("ft") or raw.get("ts")
        if ts_val is None:
            return datetime.now().astimezone()
        if isinstance(ts_val, (int, float)) or (isinstance(ts_val, str) and ts_val.isdigit()):
            return datetime.fromtimestamp(int(ts_val)).astimezone()
        if isinstance(ts_val, str):
            for fmt in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
                try:
                    return datetime.strptime(ts_val, fmt).astimezone()
                except ValueError:
                    continue
            try:
                return datetime.fromisoformat(ts_val).astimezone()
            except ValueError:
                return datetime.now().astimezone()
        return datetime.now().astimezone()

    def _normalize_candle(self, raw: dict[str, Any]) -> Candle:
        return Candle(
            ts=self._parse_ts(raw),
            open=self._parse_float(raw.get("into") or raw.get("open")),
            high=self._parse_float(raw.get("inth") or raw.get("high")),
            low=self._parse_float(raw.get("intl") or raw.get("low")),
            close=self._parse_float(raw.get("intc") or raw.get("close")),
            volume=self._parse_float(raw.get("v") or raw.get("volume")),
        )

    def _cache_is_fresh(self) -> bool:
        if not self.symbol_master_cache_path.exists():
            return False
        age = datetime.now() - datetime.fromtimestamp(self.symbol_master_cache_path.stat().st_mtime)
        return age <= timedelta(hours=self.symbol_master_max_age_hours)

    def _load_cache(self) -> None:
        if self._cache:
            return
        if not self._cache_is_fresh():
            self._refresh_symbol_cache()
            return
        try:
            self._cache = json.loads(self.symbol_master_cache_path.read_text(encoding="utf-8"))
        except Exception:
            self._cache = {}

    def _save_cache(self) -> None:
        self.symbol_master_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.symbol_master_cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=True, indent=2), encoding="utf-8"
        )

    def _search_token(self, symbol_name: str, exchange: str = "NSE") -> str:
        self._ensure_login()
        resp = self._api.searchscrip(exchange=exchange, searchtext=symbol_name)
        if not resp or str(resp.get("stat", "")).upper() != "OK":
            raise MarketDataError(f"shoonya_searchscrip_failed:{symbol_name}:{resp}")
        values = resp.get("values") or []
        if not values:
            raise MarketDataError(f"shoonya_symbol_not_found:{symbol_name}")
        token = str(values[0].get("token", ""))
        if not token:
            raise MarketDataError(f"shoonya_token_missing:{symbol_name}")
        return token

    def _refresh_symbol_cache(self) -> None:
        symbols = set(self.SECTOR_SYMBOLS.values())
        self._load_cache_if_exists_stale_ok()
        symbols.update(self._cache.keys())
        for sym in list(symbols):
            try:
                token = self._search_token(sym)
                self._cache[sym] = token
                time.sleep(0.05)
            except Exception:
                continue
        self._save_cache()

    def _load_cache_if_exists_stale_ok(self) -> None:
        if self._cache:
            return
        if self.symbol_master_cache_path.exists():
            try:
                self._cache = json.loads(self.symbol_master_cache_path.read_text(encoding="utf-8"))
            except Exception:
                self._cache = {}

    def _resolve_token(self, symbol: str) -> str:
        self._load_cache()
        if symbol in self._cache:
            return self._cache[symbol]
        token = self._search_token(symbol)
        self._cache[symbol] = token
        self._save_cache()
        return token

    def _series(
        self,
        symbol_or_name: str,
        start: datetime,
        end: datetime,
        interval: str = "1",
    ) -> list[dict[str, Any]]:
        self._ensure_login()
        token = self._resolve_token(symbol_or_name)
        rows = self._api.get_time_price_series(
            exchange="NSE",
            token=token,
            starttime=self._to_epoch(start),
            endtime=self._to_epoch(end),
            interval=interval,
        )
        if rows is None:
            return []
        if isinstance(rows, dict) and str(rows.get("stat", "")).upper() == "NOT_OK":
            raise MarketDataError(f"shoonya_series_failed:{symbol_or_name}:{rows}")
        if isinstance(rows, list):
            return rows
        return []

    def get_preopen_watchlist(self, universe: list[str]) -> list[PreopenRow]:
        raise MarketDataError("shoonya_preopen_not_supported_use_nse_provider")

    def get_intraday_candles(
        self, symbols: list[str], start: datetime, end: datetime, interval: str = "1m"
    ) -> dict[str, list[Candle]]:
        minute_interval = "1" if interval == "1m" else interval
        out: dict[str, list[Candle]] = {}
        for sym in symbols:
            rows = self._series(sym, start, end, minute_interval)
            candles = [self._normalize_candle(r) for r in rows]
            candles.sort(key=lambda x: x.ts)
            out[sym] = candles
        return out

    def get_prev_day_ohlc(self, symbols: list[str]) -> dict[str, PrevDayOHLC]:
        self._ensure_login()
        out: dict[str, PrevDayOHLC] = {}
        now = (self.reference_now or datetime.now().astimezone())
        start = now - timedelta(days=7)
        for sym in symbols:
            token = self._resolve_token(sym)
            rows = self._api.get_daily_price_series(
                exchange="NSE",
                tradingsymbol=sym,
                token=token,
                startdate=start.strftime("%d-%m-%Y"),
                enddate=now.strftime("%d-%m-%Y"),
            )
            if isinstance(rows, list) and rows:
                r = rows[-1]
                out[sym] = PrevDayOHLC(
                    pdh=self._parse_float(r.get("inth") or r.get("high")),
                    pdl=self._parse_float(r.get("intl") or r.get("low")),
                    prev_close=self._parse_float(r.get("intc") or r.get("close")),
                    prev_open=self._parse_float(r.get("into") or r.get("open")),
                )
                continue

            quote = self._api.get_quotes(exchange="NSE", token=token) or {}
            out[sym] = PrevDayOHLC(
                pdh=self._parse_float(quote.get("h")),
                pdl=self._parse_float(quote.get("l")),
                prev_close=self._parse_float(quote.get("c")),
                prev_open=self._parse_float(quote.get("o") or quote.get("op"), self._parse_float(quote.get("c"))),
            )
        return out

    def get_52w_highlow(self, symbols: list[str]) -> dict[str, HighLow52W | None]:
        self._ensure_login()
        out: dict[str, HighLow52W | None] = {}
        now = (self.reference_now or datetime.now().astimezone())
        one_year = now - timedelta(days=370)
        for sym in symbols:
            token = self._resolve_token(sym)
            sec = self._api.get_security_info(exchange="NSE", token=token) or {}
            hi = self._parse_float(sec.get("52W_High") or sec.get("h52"), default=0.0)
            lo = self._parse_float(sec.get("52W_Low") or sec.get("l52"), default=0.0)
            if hi > 0 and lo > 0:
                out[sym] = HighLow52W(high_52w=hi, low_52w=lo)
                continue

            rows = self._api.get_daily_price_series(
                exchange="NSE",
                tradingsymbol=sym,
                token=token,
                startdate=one_year.strftime("%d-%m-%Y"),
                enddate=now.strftime("%d-%m-%Y"),
            )
            if not isinstance(rows, list) or not rows:
                out[sym] = None
                continue
            highs = [self._parse_float(r.get("inth") or r.get("high")) for r in rows]
            lows = [self._parse_float(r.get("intl") or r.get("low")) for r in rows]
            highs = [x for x in highs if x > 0]
            lows = [x for x in lows if x > 0]
            out[sym] = HighLow52W(high_52w=max(highs), low_52w=min(lows)) if highs and lows else None
        return out

    def get_sector_index_snapshot(self) -> dict[str, SectorSnapshot]:
        self._ensure_login()
        nifty_token = self._resolve_token(self.SECTOR_SYMBOLS["NIFTY"])
        nifty = self._api.get_quotes(exchange="NSE", token=nifty_token) or {}
        nifty_chg = self._parse_float(nifty.get("dp") or nifty.get("pc"), 0.0)

        it_token = self._resolve_token(self.SECTOR_SYMBOLS["IT"])
        it = self._api.get_quotes(exchange="NSE", token=it_token) or {}
        it_chg = self._parse_float(it.get("dp") or it.get("pc"), 0.0)
        it_last = self._parse_float(it.get("lp"), 0.0)

        nifty_last = self._parse_float(nifty.get("lp"), 0.0)
        return {
            "IT": SectorSnapshot(
                name="IT",
                last=it_last,
                change_pct=it_chg,
                rel_vs_nifty=it_chg - nifty_chg,
            ),
            "OTHER": SectorSnapshot(
                name="OTHER",
                last=nifty_last,
                change_pct=nifty_chg,
                rel_vs_nifty=0.0,
            ),
        }

    def get_index_intraday(
        self, symbols: list[str], start: datetime, end: datetime, interval: str = "1m"
    ) -> dict[str, list[Candle]]:
        mapped = {
            "NIFTY": self.SECTOR_SYMBOLS["NIFTY"],
            "BANKNIFTY": self.SECTOR_SYMBOLS["BANKNIFTY"],
        }
        minute_interval = "1" if interval == "1m" else interval
        out: dict[str, list[Candle]] = {}
        for sym in symbols:
            name = mapped.get(sym, sym)
            rows = self._series(name, start, end, minute_interval)
            candles = [self._normalize_candle(r) for r in rows]
            candles.sort(key=lambda x: x.ts)
            out[sym] = candles
        return out
