from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timedelta
from typing import Any

import requests

from src.providers.base import Candle, HighLow52W, MarketDataError, PreopenRow, PrevDayOHLC, SectorSnapshot


class NseWebProvider:
    """NSE public web provider used as pre-open source, backup data source, and cross-validation source."""

    BASE = "https://www.nseindia.com"
    PREOPEN_URL = f"{BASE}/api/market-data-pre-open?key=ALL"
    QUOTE_EQUITY_URL = f"{BASE}/api/quote-equity?symbol={{symbol}}"
    ALL_INDICES_URL = f"{BASE}/api/allIndices"
    INDEX_CHART_URL = f"{BASE}/api/chart-databyindex?index={{index_name}}"
    EQUITY_CHART_URL = f"{BASE}/api/chart-databyindex?index={{symbol}}"
    HIST_EQUITY_URL = f"{BASE}/api/historical/cm/equity?symbol={{symbol}}&series=[%22EQ%22]&from={{frm}}&to={{to}}"
    BHAVCOPY_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"

    def __init__(self, timeout_sec: float = 8.0, retries: int = 2) -> None:
        self.timeout_sec = timeout_sec
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept": "application/json, text/plain, */*",
                "Referer": self.BASE,
                "Connection": "keep-alive",
            }
        )
        self._bootstrapped = False
        self.reference_now: datetime | None = None
        self._bhavcopy_cache: dict[str, dict[str, dict[str, float]]] = {}

    def set_reference_now(self, now_dt: datetime) -> None:
        self.reference_now = now_dt

    def _bootstrap(self) -> None:
        if self._bootstrapped:
            return
        try:
            self.session.get(self.BASE, timeout=self.timeout_sec)
            self._bootstrapped = True
        except Exception as exc:
            raise MarketDataError(f"nse_bootstrap_failed:{exc}") from exc

    def _get_json(self, url: str) -> Any:
        self._bootstrap()
        last_err: Exception | None = None
        for _ in range(self.retries + 1):
            try:
                resp = self.session.get(url, timeout=self.timeout_sec)
                if resp.status_code == 401:
                    self._bootstrapped = False
                    self._bootstrap()
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                last_err = exc
        raise MarketDataError(f"nse_request_failed:{url}:{last_err}")

    @staticmethod
    def _to_float(v: Any, default: float = 0.0) -> float:
        try:
            return float(v)
        except Exception:
            return default

    @staticmethod
    def _parse_dt(v: Any) -> datetime:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(float(v) / 1000.0)
        if isinstance(v, str):
            for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(v, fmt)
                except ValueError:
                    continue
            try:
                return datetime.fromisoformat(v)
            except ValueError:
                pass
        return datetime.now()

    @staticmethod
    def _prev_day_candidates(anchor: datetime, max_lookback_days: int = 10) -> list[datetime]:
        out: list[datetime] = []
        day = anchor - timedelta(days=1)
        for _ in range(max_lookback_days):
            out.append(day)
            day = day - timedelta(days=1)
        return out

    def _fetch_bhavcopy_rows(self, day: datetime) -> dict[str, dict[str, float]]:
        key = day.date().isoformat()
        if key in self._bhavcopy_cache:
            return self._bhavcopy_cache[key]
        url = self.BHAVCOPY_URL.format(yyyymmdd=day.strftime("%Y%m%d"))
        resp = self.session.get(url, timeout=self.timeout_sec)
        if resp.status_code >= 400:
            self._bhavcopy_cache[key] = {}
            return {}
        try:
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            name = zf.namelist()[0]
            with zf.open(name) as fh:
                text = fh.read().decode("utf-8", errors="ignore")
            rows: dict[str, dict[str, float]] = {}
            for row in csv.DictReader(io.StringIO(text)):
                sym = str(row.get("TckrSymb", "")).strip().upper()
                series = str(row.get("SctySrs", "")).strip().upper()
                if not sym or series != "EQ":
                    continue
                rows[sym] = {
                    "open": self._to_float(row.get("OpnPric")),
                    "high": self._to_float(row.get("HghPric")),
                    "low": self._to_float(row.get("LwPric")),
                    "close": self._to_float(row.get("ClsPric")),
                }
            self._bhavcopy_cache[key] = rows
            return rows
        except Exception:
            self._bhavcopy_cache[key] = {}
            return {}

    @staticmethod
    def _bucket_start(ts: datetime, interval_min: int) -> datetime:
        minute = (ts.minute // interval_min) * interval_min
        return ts.replace(minute=minute, second=0, microsecond=0)

    def _aggregate_prices(
        self, points: list[tuple[datetime, float]], interval_min: int
    ) -> list[Candle]:
        if not points:
            return []
        buckets: dict[datetime, list[float]] = {}
        for ts, price in points:
            key = self._bucket_start(ts, interval_min)
            buckets.setdefault(key, []).append(price)
        candles: list[Candle] = []
        for ts in sorted(buckets):
            vals = buckets[ts]
            candles.append(
                Candle(
                    ts=ts,
                    open=vals[0],
                    high=max(vals),
                    low=min(vals),
                    close=vals[-1],
                    volume=0.0,
                )
            )
        return candles

    @staticmethod
    def _today_date() -> datetime.date:
        return datetime.now().date()

    def _historical_daily_row(self, symbol: str, day: datetime.date) -> dict[str, Any] | None:
        frm = day.strftime("%d-%m-%Y")
        to = day.strftime("%d-%m-%Y")
        url = self.HIST_EQUITY_URL.format(symbol=symbol.replace("&", "%26"), frm=frm, to=to)
        data = self._get_json(url).get("data", [])
        if not data:
            return None
        return data[-1]

    def _synth_intraday_from_daily(
        self,
        daily: dict[str, Any],
        start: datetime,
        end: datetime,
        interval_min: int,
    ) -> list[Candle]:
        o = self._to_float(daily.get("CH_OPENING_PRICE"))
        h = self._to_float(daily.get("CH_TRADE_HIGH_PRICE"))
        l = self._to_float(daily.get("CH_TRADE_LOW_PRICE"))
        c = self._to_float(daily.get("CH_CLOSING_PRICE"))
        v = self._to_float(daily.get("CH_TOT_TRADED_QTY") or daily.get("CH_TTL_TRD_QNTY") or 0.0)
        if o <= 0 or h <= 0 or l <= 0 or c <= 0:
            return []

        # Build a small synthetic path across the requested opening range for replay analytics.
        mid_ts = start + timedelta(minutes=max(1, interval_min))
        end_ts = min(end, start + timedelta(minutes=max(2, interval_min * 2)))
        v3 = max(1.0, v / 3.0)
        return [
            Candle(ts=start, open=o, high=max(o, h), low=min(o, l), close=(o + c) / 2.0, volume=v3),
            Candle(ts=mid_ts, open=(o + c) / 2.0, high=h, low=l, close=(o + c) / 2.0, volume=v3),
            Candle(ts=end_ts, open=(o + c) / 2.0, high=max(c, h), low=min(c, l), close=c, volume=v3),
        ]

    @staticmethod
    def _interval_to_minutes(interval: str) -> int:
        mapping = {"1m": 1, "5m": 5, "15m": 15}
        if interval in mapping:
            return mapping[interval]
        if interval.endswith("m"):
            return max(1, int(interval[:-1]))
        return 1

    def _quote(self, symbol: str) -> dict[str, Any]:
        url = self.QUOTE_EQUITY_URL.format(symbol=symbol.replace("&", "%26"))
        payload = self._get_json(url)
        info = payload.get("priceInfo", payload.get("info", {}))
        security = payload.get("securityInfo", {})
        return {"priceInfo": info, "securityInfo": security, "metadata": payload.get("metadata", {})}

    def _index_price_map(self) -> dict[str, dict[str, Any]]:
        data = self._get_json(self.ALL_INDICES_URL).get("data", [])
        out: dict[str, dict[str, Any]] = {}
        for row in data:
            name = str(row.get("index", "")).strip()
            if name:
                out[name] = row
        return out

    def _index_chart_points(self, index_name: str) -> list[tuple[datetime, float]]:
        url = self.INDEX_CHART_URL.format(index_name=index_name.replace(" ", "%20").replace("&", "%26"))
        payload = self._get_json(url)
        graph = payload.get("grapthData") or payload.get("graphData") or []
        points: list[tuple[datetime, float]] = []
        for row in graph:
            if not isinstance(row, list) or len(row) < 2:
                continue
            ts = self._parse_dt(row[0])
            price = self._to_float(row[1])
            if price > 0:
                points.append((ts, price))
        return points

    def _equity_chart_points(self, symbol: str) -> list[tuple[datetime, float]]:
        url = self.EQUITY_CHART_URL.format(symbol=symbol.replace("&", "%26"))
        payload = self._get_json(url)
        graph = payload.get("grapthData") or payload.get("graphData") or []
        points: list[tuple[datetime, float]] = []
        for row in graph:
            if not isinstance(row, list) or len(row) < 2:
                continue
            ts = self._parse_dt(row[0])
            price = self._to_float(row[1])
            if price > 0:
                points.append((ts, price))
        return points

    def get_preopen_watchlist(self, universe: list[str]) -> list[PreopenRow]:
        try:
            data = self._get_json(self.PREOPEN_URL).get("data", [])
        except Exception as exc:
            raise MarketDataError(f"nse_preopen_failed:{exc}") from exc

        by_symbol: dict[str, PreopenRow] = {}
        for row in data:
            detail = row.get("detail", {})
            symbol = detail.get("symbol")
            if not symbol:
                continue
            pre = detail.get("preOpenMarket", {})
            by_symbol[symbol.upper()] = PreopenRow(
                symbol=symbol.upper(),
                indicative_price=float(pre.get("lastPrice", 0)),
                change_pct=float(pre.get("pChange", 0)),
                volume=float(pre.get("totalTradedVolume", 0)),
                status=str(pre.get("finalPrice", "UNKNOWN")),
            )
        return [by_symbol[s] for s in universe if s in by_symbol]

    def get_intraday_candles(
        self, symbols: list[str], start: datetime, end: datetime, interval: str = "1m"
    ) -> dict[str, list[Candle]]:
        interval_min = self._interval_to_minutes(interval)
        out: dict[str, list[Candle]] = {}
        replay_day = start.date()
        use_historical_fallback = replay_day < self._today_date()
        for sym in symbols:
            points = [p for p in self._equity_chart_points(sym) if start <= p[0] <= end]
            if not points:
                if use_historical_fallback:
                    try:
                        daily = self._historical_daily_row(sym, replay_day)
                        if daily:
                            synth = self._synth_intraday_from_daily(daily, start, end, interval_min)
                            out[sym] = synth
                            continue
                    except Exception:
                        pass
                out[sym] = []
                continue
            out[sym] = self._aggregate_prices(points, interval_min)
        return out

    def get_prev_day_ohlc(self, symbols: list[str]) -> dict[str, PrevDayOHLC]:
        out: dict[str, PrevDayOHLC] = {}
        now = self.reference_now or datetime.now()

        # Preferred: previous trading day bhavcopy (authoritative OHLC for EQ symbols).
        bhav_rows: dict[str, dict[str, float]] = {}
        for day in self._prev_day_candidates(now, max_lookback_days=10):
            rows = self._fetch_bhavcopy_rows(day)
            if rows:
                bhav_rows = rows
                break

        for sym in symbols:
            b = bhav_rows.get(sym.upper())
            if b:
                out[sym] = PrevDayOHLC(
                    pdh=b["high"],
                    pdl=b["low"],
                    prev_close=b["close"],
                    prev_open=b["open"] if b["open"] > 0 else b["close"],
                )
                continue

            pdh = 0.0
            pdl = 0.0
            prev_close = 0.0
            prev_open = 0.0
            try:
                q = self._quote(sym)
                info = q.get("priceInfo", {})
                pdh = self._to_float(info.get("previousClose") or info.get("intraDayHighLow", {}).get("max"))
                pdl = self._to_float(info.get("previousClose") or info.get("intraDayHighLow", {}).get("min"))
                prev_close = self._to_float(info.get("previousClose"))
                prev_open = self._to_float(info.get("open"), prev_close)
            except Exception:
                pass
            if prev_close <= 0:
                try:
                    frm = (now - timedelta(days=7)).strftime("%d-%m-%Y")
                    to = now.strftime("%d-%m-%Y")
                    url = self.HIST_EQUITY_URL.format(symbol=sym.replace("&", "%26"), frm=frm, to=to)
                    hist = self._get_json(url).get("data", [])
                    if hist:
                        last = hist[-1]
                        prev_open = self._to_float(last.get("CH_OPENING_PRICE"))
                        pdh = self._to_float(last.get("CH_TRADE_HIGH_PRICE"))
                        pdl = self._to_float(last.get("CH_TRADE_LOW_PRICE"))
                        prev_close = self._to_float(last.get("CH_CLOSING_PRICE"))
                except Exception:
                    pass
            out[sym] = PrevDayOHLC(
                pdh=pdh if pdh > 0 else prev_close,
                pdl=pdl if pdl > 0 else prev_close,
                prev_close=prev_close,
                prev_open=prev_open if prev_open > 0 else prev_close,
            )
        return out

    def get_52w_highlow(self, symbols: list[str]) -> dict[str, HighLow52W | None]:
        out: dict[str, HighLow52W | None] = {}
        for sym in symbols:
            try:
                q = self._quote(sym)
                whl = q.get("priceInfo", {}).get("weekHighLow", {})
                hi = self._to_float(whl.get("max"))
                lo = self._to_float(whl.get("min"))
                out[sym] = HighLow52W(high_52w=hi, low_52w=lo) if hi > 0 and lo > 0 else None
            except Exception:
                out[sym] = None
        return out

    def get_sector_index_snapshot(self) -> dict[str, SectorSnapshot]:
        idx = self._index_price_map()
        nifty = idx.get("NIFTY 50", {})
        nifty_chg = self._to_float(nifty.get("percentChange"))
        it = idx.get("NIFTY IT", {})
        it_chg = self._to_float(it.get("percentChange"))
        bank = idx.get("NIFTY BANK", {})
        out: dict[str, SectorSnapshot] = {
            "IT": SectorSnapshot(
                name="IT",
                last=self._to_float(it.get("last")),
                change_pct=it_chg,
                rel_vs_nifty=it_chg - nifty_chg,
            ),
            "OTHER": SectorSnapshot(
                name="OTHER",
                last=self._to_float(nifty.get("last")),
                change_pct=nifty_chg,
                rel_vs_nifty=0.0,
            ),
            "BANK": SectorSnapshot(
                name="BANK",
                last=self._to_float(bank.get("last")),
                change_pct=self._to_float(bank.get("percentChange")),
                rel_vs_nifty=self._to_float(bank.get("percentChange")) - nifty_chg,
            ),
        }
        return out

    def get_index_intraday(
        self, symbols: list[str], start: datetime, end: datetime, interval: str = "1m"
    ) -> dict[str, list[Candle]]:
        mapping = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK"}
        interval_min = self._interval_to_minutes(interval)
        out: dict[str, list[Candle]] = {}
        for sym in symbols:
            idx_name = mapping.get(sym, sym)
            points = [p for p in self._index_chart_points(idx_name) if start <= p[0] <= end]
            out[sym] = self._aggregate_prices(points, interval_min)
        return out
