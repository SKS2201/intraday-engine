from __future__ import annotations

from datetime import date, datetime, timedelta


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5


def last_trading_day(now_ist: datetime) -> date:
    d = now_ist.date()
    if is_trading_day(d):
        d = d - timedelta(days=1)
    else:
        d = d - timedelta(days=1)
    while not is_trading_day(d):
        d = d - timedelta(days=1)
    return d


def resolve_replay_date(now_ist: datetime, explicit_date: str | None = None) -> date:
    if explicit_date:
        return date.fromisoformat(explicit_date)
    return last_trading_day(now_ist)
