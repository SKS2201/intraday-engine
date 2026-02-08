from __future__ import annotations

import math
from datetime import datetime, timedelta

from src.config import Settings
from src.engine.stage0_replay import (
    SyntheticPreopenInput,
    build_stage0_replay_header,
    build_synthetic_preopen_rows,
)
from src.providers.base import MarketDataError, MarketDataProvider, PreopenRow


def _is_weekend(now_ist: datetime) -> bool:
    return now_ist.weekday() >= 5


def _rank_preopen(
    rows: list[PreopenRow],
    top_n: int,
    prev_map: dict[str, object] | None = None,
    hl52_map: dict[str, object] | None = None,
) -> list[PreopenRow]:
    prev_map = prev_map or {}
    hl52_map = hl52_map or {}

    def _score(row: PreopenRow) -> tuple[float, str]:
        # Pre-market momentum and liquidity are primary for Stage-0.
        momentum_score = min(45.0, abs(row.change_pct) * 7.0)
        liquidity_score = min(25.0, math.log10(max(1.0, row.volume)) * 5.0)

        # Previous-day context: wider prior range means better potential.
        prev_score = 0.0
        prev_ref = prev_map.get(row.symbol)
        if prev_ref is not None:
            pdh = float(getattr(prev_ref, "pdh", 0.0))
            pdl = float(getattr(prev_ref, "pdl", 0.0))
            prev_close = float(getattr(prev_ref, "prev_close", 0.0))
            if prev_close > 0 and pdh >= pdl:
                prev_range_pct = ((pdh - pdl) / prev_close) * 100.0
                prev_score = min(20.0, max(0.0, prev_range_pct * 2.0))

        # Historical context using 52W stretch (if available).
        hist_score = 0.0
        hl_ref = hl52_map.get(row.symbol)
        if hl_ref is not None:
            h52 = float(getattr(hl_ref, "high_52w", 0.0))
            l52 = float(getattr(hl_ref, "low_52w", 0.0))
            if h52 > l52:
                pos = (row.indicative_price - l52) / (h52 - l52)
                pos = min(1.0, max(0.0, pos))
                stretch = abs(pos - 0.5) * 2.0  # 0 near midpoint, 1 near extremes
                hist_score = min(10.0, stretch * 10.0)

        total = momentum_score + liquidity_score + prev_score + hist_score
        return total, row.symbol

    ranked = sorted(rows, key=_score, reverse=True)
    return ranked[:top_n]


def build_preopen_message(
    rows: list[PreopenRow],
    asof: datetime,
    replay_header_lines: list[str] | None = None,
    replay_date: str | None = None,
    next_window_start: str = "09:00",
    next_window_end: str = "09:15",
) -> str:
    lines = []
    if replay_header_lines:
        lines.extend(replay_header_lines)
        lines.append("")
    lines.extend(
        [
            "Indian Intraday Assistant - Pre-open Watchlist",
            f"As of: {asof.strftime('%Y-%m-%d %H:%M:%S %Z')}",
            "",
            "Selection Criteria: pre-market momentum, previous-day OHLC context, historical stretch (52W if available), liquidity.",
        ]
    )
    if replay_header_lines:
        lines.append(
            f"Replay mode: Stage-1 opening-range replay will be sent for {replay_date or asof.date().isoformat()}."
        )
    else:
        lines.append(
            f"This is NOT final. Wait for {next_window_start}-{next_window_end} performance confirmation."
        )
    lines.append("")
    if not rows:
        lines.append("NO TRADE — CONDITIONS NOT FAVORABLE")
        lines.append("Reason: Pre-open data unavailable or no symbols passed pre-open filters.")
        return "\n".join(lines)
    for row in rows:
        lines.append(
            f"- {row.symbol}: Indicative {row.indicative_price:.2f}, Chg {row.change_pct:.2f}%, Vol {int(row.volume)}"
        )
    return "\n".join(lines)


def _build_simulated_preopen_rows(
    settings: Settings,
    provider: MarketDataProvider,
    now_ist: datetime,
) -> list[PreopenRow]:
    # Reconstruct "pre-open style" context from earliest available replay-day candles.
    date_prefix = now_ist.date().isoformat()
    start = datetime.fromisoformat(f"{date_prefix}T09:15:00+05:30")
    end = datetime.fromisoformat(f"{date_prefix}T09:20:00+05:30")
    candles = provider.get_intraday_candles(settings.universe, start, end, "1m")
    prev = provider.get_prev_day_ohlc(settings.universe)

    synth: list[SyntheticPreopenInput] = []
    for sym in settings.universe:
        cs = candles.get(sym, [])
        if not cs or sym not in prev:
            continue
        c0 = cs[0]
        synth.append(
            SyntheticPreopenInput(
                symbol=sym,
                prev=prev[sym],
                first_open=c0.open,
                first_close=c0.close,
                first_volume=c0.volume,
            )
        )
    return build_synthetic_preopen_rows(synth)


def run_stage0(
    settings: Settings,
    primary_provider: MarketDataProvider,
    fallback_provider: MarketDataProvider | None,
    now_ist: datetime,
    test_mode: bool = False,
    replay_date: str | None = None,
    show_process: bool = False,
) -> str:
    if _is_weekend(now_ist) and not test_mode:
        return "NO TRADE DAY (Weekend)"

    rows: list[PreopenRow] = []
    errors: list[str] = []
    replay_headers: list[str] | None = None
    prev_map: dict[str, object] = {}
    hl52_map: dict[str, object] = {}

    if test_mode:
        if show_process:
            print(f"[process] stage0 replay mode active date={replay_date or now_ist.date().isoformat()}")
        try:
            rows = _build_simulated_preopen_rows(settings, primary_provider, now_ist)
            try:
                prev_map = primary_provider.get_prev_day_ohlc(settings.universe)
            except Exception as exc:
                errors.append(f"replay_prevday_unavailable:{exc}")
            try:
                hl52_map = primary_provider.get_52w_highlow(settings.universe)
            except Exception as exc:
                errors.append(f"replay_52w_unavailable:{exc}")
            replay_headers = build_stage0_replay_header(
                asof=now_ist, replay_date=replay_date or now_ist.date().isoformat()
            )
        except Exception as exc:
            errors.append(f"replay_preopen_failed:{exc}")
    else:
        try:
            rows = primary_provider.get_preopen_watchlist(settings.universe)
        except Exception as exc:
            errors.append(f"primary_preopen_failed:{exc}")
        try:
            prev_map = primary_provider.get_prev_day_ohlc(settings.universe)
        except Exception as exc:
            errors.append(f"primary_prevday_failed:{exc}")
        try:
            hl52_map = primary_provider.get_52w_highlow(settings.universe)
        except Exception as exc:
            errors.append(f"primary_52w_failed:{exc}")
        if not rows and fallback_provider is not None:
            try:
                rows = fallback_provider.get_preopen_watchlist(settings.universe)
            except MarketDataError as exc:
                errors.append(str(exc))
        if not prev_map and fallback_provider is not None:
            try:
                prev_map = fallback_provider.get_prev_day_ohlc(settings.universe)
            except Exception as exc:
                errors.append(f"fallback_prevday_failed:{exc}")
        if not hl52_map and fallback_provider is not None:
            try:
                hl52_map = fallback_provider.get_52w_highlow(settings.universe)
            except Exception as exc:
                errors.append(f"fallback_52w_failed:{exc}")

    msg = build_preopen_message(
        _rank_preopen(rows, settings.top_candidates, prev_map=prev_map, hl52_map=hl52_map),
        now_ist,
        replay_headers,
        replay_date=replay_date,
        next_window_start=settings.open_range_start,
        next_window_end=settings.open_range_end,
    )
    if errors:
        msg += f"\n\nNote: {'; '.join(errors)}"
    return msg
