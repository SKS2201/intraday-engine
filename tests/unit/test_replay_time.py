from datetime import datetime

from src.engine.replay_time import resolve_replay_date


def test_resolve_replay_date_weekend_to_friday():
    now = datetime.fromisoformat("2026-02-08T12:00:00+05:30")  # Sunday
    got = resolve_replay_date(now)
    assert got.isoformat() == "2026-02-06"


def test_resolve_replay_date_explicit_override():
    now = datetime.fromisoformat("2026-02-08T12:00:00+05:30")
    got = resolve_replay_date(now, explicit_date="2026-02-05")
    assert got.isoformat() == "2026-02-05"


def test_resolve_replay_date_weekday_previous_day():
    now = datetime.fromisoformat("2026-02-09T06:00:00+05:30")  # Monday
    got = resolve_replay_date(now)
    assert got.isoformat() == "2026-02-06"
