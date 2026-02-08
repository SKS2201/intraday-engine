from __future__ import annotations

from datetime import datetime

import pytest

from src.providers.nse_provider import NseWebProvider


class StubNse(NseWebProvider):
    def __init__(self):
        super().__init__(timeout_sec=1.0)

    def _bootstrap(self):
        self._bootstrapped = True

    def _get_json(self, url: str):
        if "quote-equity" in url:
            return {
                "priceInfo": {
                    "previousClose": 99.5,
                    "weekHighLow": {"max": 120.0, "min": 80.0},
                },
                "securityInfo": {},
                "metadata": {},
            }
        if "allIndices" in url:
            return {
                "data": [
                    {"index": "NIFTY 50", "last": 22000, "percentChange": 0.8},
                    {"index": "NIFTY IT", "last": 40000, "percentChange": 1.1},
                    {"index": "NIFTY BANK", "last": 48000, "percentChange": 0.6},
                ]
            }
        if "chart-databyindex" in url:
            return {"grapthData": [[1738564500000, 100.0], [1738564560000, 101.0], [1738564620000, 100.5]]}
        return {}

    def _fetch_bhavcopy_rows(self, day):
        return {}


class StubNseReplay(StubNse):
    def _get_json(self, url: str):
        if "chart-databyindex" in url:
            return {"grapthData": []}
        if "historical/cm/equity" in url:
            return {
                "data": [
                    {
                        "CH_OPENING_PRICE": 100.0,
                        "CH_TRADE_HIGH_PRICE": 103.0,
                        "CH_TRADE_LOW_PRICE": 99.0,
                        "CH_CLOSING_PRICE": 102.0,
                        "CH_TOT_TRADED_QTY": 3000,
                    }
                ]
            }
        return super()._get_json(url)


class StubNseBhavcopy(StubNse):
    def _fetch_bhavcopy_rows(self, day):
        return {
            "INFY": {"open": 1500.0, "high": 1525.0, "low": 1492.0, "close": 1510.0},
        }


def test_nse_parser_normalization_prev_and_52w():
    p = StubNse()
    prev = p.get_prev_day_ohlc(["INFY"])
    hl = p.get_52w_highlow(["INFY"])
    assert prev["INFY"].prev_close == 99.5
    assert hl["INFY"].high_52w == 120.0
    assert hl["INFY"].low_52w == 80.0


def test_nse_intraday_aggregation():
    p = StubNse()
    start = datetime.fromtimestamp(1738564500)
    end = datetime.fromtimestamp(1738564680)
    out = p.get_intraday_candles(["INFY"], start, end, "5m")
    assert "INFY" in out
    assert len(out["INFY"]) >= 1
    assert out["INFY"][0].open > 0


def test_nse_sector_snapshot():
    p = StubNse()
    snap = p.get_sector_index_snapshot()
    assert "IT" in snap
    assert snap["IT"].rel_vs_nifty == pytest.approx(0.3)


def test_nse_intraday_replay_falls_back_to_historical_daily():
    p = StubNseReplay()
    start = datetime.fromisoformat("2026-02-06T09:15:00")
    end = datetime.fromisoformat("2026-02-06T09:30:00")
    out = p.get_intraday_candles(["INFY"], start, end, "5m")
    assert "INFY" in out
    assert len(out["INFY"]) == 3
    assert out["INFY"][-1].close == pytest.approx(102.0)


def test_nse_prev_day_ohlc_prefers_bhavcopy():
    p = StubNseBhavcopy()
    prev = p.get_prev_day_ohlc(["INFY"])
    assert prev["INFY"].prev_open == pytest.approx(1500.0)
    assert prev["INFY"].pdh == pytest.approx(1525.0)
    assert prev["INFY"].pdl == pytest.approx(1492.0)
    assert prev["INFY"].prev_close == pytest.approx(1510.0)
