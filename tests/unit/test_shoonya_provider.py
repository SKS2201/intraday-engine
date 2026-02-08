from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.providers.base import HighLow52W, MarketDataError
from src.providers.shoonya_provider import ShoonyaCreds, ShoonyaProvider


class FakeApi:
    def __init__(self) -> None:
        self.session_set = False

    def set_session(self, userid, password, usertoken):
        self.session_set = True
        return {"stat": "OK"}

    def login(self, **kwargs):
        return {"stat": "OK"}

    def searchscrip(self, exchange, searchtext):
        return {"stat": "OK", "values": [{"token": f"TKN_{searchtext}"}]}

    def get_time_price_series(self, exchange, token, starttime, endtime, interval):
        return [
            {"time": "2025-01-01 09:15:00", "into": "100", "inth": "101", "intl": "99", "intc": "100.5", "v": "1200"}
        ]

    def get_daily_price_series(self, exchange, tradingsymbol, token, startdate, enddate):
        return [
            {"inth": "120.0", "intl": "80.0", "intc": "100.0"},
            {"inth": "125.0", "intl": "85.0", "intc": "110.0"},
        ]

    def get_quotes(self, exchange, token):
        if token == "TKN_Nifty 50":
            return {"lp": "22000", "dp": "0.8"}
        if token == "TKN_Nifty IT":
            return {"lp": "40000", "dp": "1.2"}
        return {"lp": "100", "dp": "0.2", "h": "102", "l": "98", "c": "99"}

    def get_security_info(self, exchange, token):
        return {}


def _provider(tmp_path):
    cache = tmp_path / "symbol_cache.json"
    cache.write_text(
        json.dumps(
            {
                "INFY": "TKN_INFY",
                "Nifty 50": "TKN_Nifty 50",
                "Nifty Bank": "TKN_Nifty Bank",
                "Nifty IT": "TKN_Nifty IT",
            }
        ),
        encoding="utf-8",
    )
    creds = ShoonyaCreds(
        user_id="u",
        password="p",
        totp_secret="totp",
        vendor_code="v",
        api_secret="a",
        imei="imei",
        session_token="sess",
    )
    return ShoonyaProvider(
        creds=creds,
        symbol_master_cache_path=str(cache),
        symbol_master_max_age_hours=24,
        api_client=FakeApi(),
    )


def test_shoonya_normalize_candle(tmp_path):
    p = _provider(tmp_path)
    c = p._normalize_candle(
        {"time": "2025-01-01 09:15:00", "into": "100", "inth": "101", "intl": "99", "intc": "100.5", "v": "1200"}
    )
    assert c.open == 100.0
    assert c.high == 101.0
    assert c.low == 99.0
    assert c.close == 100.5
    assert c.volume == 1200.0


def test_symbol_cache_resolves_token_and_persists(tmp_path):
    p = _provider(tmp_path)
    token = p._resolve_token("INFY")
    assert token == "TKN_INFY"
    token2 = p._resolve_token("SBIN")
    assert token2 == "TKN_SBIN"
    data = json.loads((tmp_path / "symbol_cache.json").read_text(encoding="utf-8"))
    assert data["SBIN"] == "TKN_SBIN"


def test_52w_fallback_derived_from_daily_series(tmp_path):
    p = _provider(tmp_path)
    res = p.get_52w_highlow(["INFY"])
    assert isinstance(res["INFY"], HighLow52W)
    assert res["INFY"].high_52w == 125.0
    assert res["INFY"].low_52w == 80.0


def test_index_intraday_returns_sorted_candles(tmp_path):
    p = _provider(tmp_path)
    start = datetime.fromisoformat("2025-01-01T09:15:00+05:30")
    end = datetime.fromisoformat("2025-01-01T09:30:00+05:30")
    out = p.get_index_intraday(["NIFTY"], start, end, "1m")
    assert "NIFTY" in out
    assert len(out["NIFTY"]) == 1


def test_session_token_auth_path(tmp_path):
    p = _provider(tmp_path)
    p._ensure_login()
    assert p._api.session_set is True


def test_sector_snapshot_rel_strength(tmp_path):
    p = _provider(tmp_path)
    ss = p.get_sector_index_snapshot()
    assert "IT" in ss
    assert ss["IT"].rel_vs_nifty == pytest.approx(0.4)


def test_login_failure_raises(tmp_path):
    class FailLoginApi(FakeApi):
        def set_session(self, userid, password, usertoken):
            raise RuntimeError("expired")

        def login(self, **kwargs):
            return {"stat": "Not_Ok", "emsg": "bad auth"}

    cache = tmp_path / "symbol_cache.json"
    cache.write_text("{}", encoding="utf-8")
    creds = ShoonyaCreds(
        user_id="u",
        password="p",
        totp_secret="JBSWY3DPEHPK3PXP",
        vendor_code="v",
        api_secret="a",
        imei="imei",
        session_token="expired",
    )
    p = ShoonyaProvider(
        creds=creds,
        symbol_master_cache_path=str(cache),
        api_client=FailLoginApi(),
    )
    with pytest.raises(MarketDataError):
        p._ensure_login()
