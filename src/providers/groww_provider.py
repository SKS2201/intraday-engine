from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import requests

from src.providers.base import (
    Candle,
    HighLow52W,
    MarketDataError,
    PreopenRow,
    PrevDayOHLC,
    SectorSnapshot,
)


class GrowwProvider:
    """Primary provider. Endpoints are kept configurable for low-maintenance adapter updates."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str = "",
        timeout_sec: float = 8.0,
        max_retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}" if api_key else "",
                "X-API-SECRET": api_secret,
                "Accept": "application/json",
            }
        )

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        last_err: Exception | None = None
        url = f"{self.base_url}{path}"
        for attempt in range(self.max_retries):
            try:
                resp = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    timeout=self.timeout_sec,
                )
                if resp.status_code >= 500:
                    raise MarketDataError(f"server_error:{resp.status_code}")
                if resp.status_code >= 400:
                    raise MarketDataError(f"client_error:{resp.status_code}:{resp.text[:200]}")
                return resp.json()
            except Exception as exc:
                last_err = exc
                if attempt < self.max_retries - 1:
                    time.sleep(0.4 * (2**attempt))
        raise MarketDataError(f"groww_request_failed:{path}:{last_err}")

    @staticmethod
    def _candle_from_raw(raw: dict[str, Any]) -> Candle:
        return Candle(
            ts=datetime.fromisoformat(raw["ts"]),
            open=float(raw["open"]),
            high=float(raw["high"]),
            low=float(raw["low"]),
            close=float(raw["close"]),
            volume=float(raw.get("volume", 0)),
        )

    def get_preopen_watchlist(self, universe: list[str]) -> list[PreopenRow]:
        payload = self._request("GET", "/v1/market/preopen", {"symbols": ",".join(universe)})
        rows = payload.get("data", [])
        return [
            PreopenRow(
                symbol=str(r["symbol"]),
                indicative_price=float(r.get("indicative_price", 0)),
                change_pct=float(r.get("change_pct", 0)),
                volume=float(r.get("volume", 0)),
                status=str(r.get("status", "UNKNOWN")),
            )
            for r in rows
        ]

    def get_intraday_candles(
        self, symbols: list[str], start: datetime, end: datetime, interval: str = "1m"
    ) -> dict[str, list[Candle]]:
        payload = self._request(
            "GET",
            "/v1/market/candles",
            {
                "symbols": ",".join(symbols),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "interval": interval,
            },
        )
        data: dict[str, list[Candle]] = {}
        for sym, rows in payload.get("data", {}).items():
            data[sym] = [self._candle_from_raw(r) for r in rows]
        return data

    def get_prev_day_ohlc(self, symbols: list[str]) -> dict[str, PrevDayOHLC]:
        payload = self._request("GET", "/v1/market/prev-day", {"symbols": ",".join(symbols)})
        return {
            sym: PrevDayOHLC(
                pdh=float(v["high"]),
                pdl=float(v["low"]),
                prev_close=float(v["close"]),
                prev_open=float(v.get("open", v["close"])),
            )
            for sym, v in payload.get("data", {}).items()
        }

    def get_52w_highlow(self, symbols: list[str]) -> dict[str, HighLow52W | None]:
        payload = self._request("GET", "/v1/market/52w", {"symbols": ",".join(symbols)})
        out: dict[str, HighLow52W | None] = {}
        for sym, v in payload.get("data", {}).items():
            if v is None:
                out[sym] = None
            else:
                out[sym] = HighLow52W(
                    high_52w=float(v["high_52w"]),
                    low_52w=float(v["low_52w"]),
                )
        return out

    def get_sector_index_snapshot(self) -> dict[str, SectorSnapshot]:
        payload = self._request("GET", "/v1/market/sectors")
        return {
            s["name"]: SectorSnapshot(
                name=s["name"],
                last=float(s["last"]),
                change_pct=float(s["change_pct"]),
                rel_vs_nifty=float(s.get("rel_vs_nifty", 0)),
            )
            for s in payload.get("data", [])
        }

    def get_index_intraday(
        self, symbols: list[str], start: datetime, end: datetime, interval: str = "1m"
    ) -> dict[str, list[Candle]]:
        payload = self._request(
            "GET",
            "/v1/market/index-candles",
            {
                "symbols": ",".join(symbols),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "interval": interval,
            },
        )
        out: dict[str, list[Candle]] = {}
        for sym, rows in payload.get("data", {}).items():
            out[sym] = [self._candle_from_raw(r) for r in rows]
        return out
