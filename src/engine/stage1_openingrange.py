from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from html import escape
from typing import Any

from src.analytics.market_regime import classify_market_regime
from src.analytics.opening_range import OpeningRangeMetrics, compute_opening_range_metrics
from src.analytics.risk import build_risk_plan
from src.analytics.scoring import CandidateInput, ScoredCandidate, score_candidate, split_ranked_candidates
from src.analytics.sector_strength import compute_sector_strength
from src.config import Settings
from src.providers.base import HighLow52W, MarketDataProvider, PreopenRow, PrevDayOHLC, TradePlan


IT_SYMBOLS = {"INFY", "TCS", "WIPRO", "HCLTECH", "TECHM"}
BANK_SYMBOLS = {"AXISBANK", "HDFCBANK", "ICICIBANK", "INDUSINDBK", "KOTAKBANK", "SBIN"}


@dataclass(frozen=True)
class PreparedSymbol:
    symbol: str
    prev: PrevDayOHLC
    hl52: HighLow52W | None
    metrics: OpeningRangeMetrics
    scored: ScoredCandidate
    sector_trend: str
    now_ohlc: tuple[float, float, float, float]
    candles_len: int


@dataclass(frozen=True)
class CandidateView:
    plan: TradePlan
    status: str
    why_not_actionable: str
    score: float
    symbol: str
    metrics: OpeningRangeMetrics
    prev: PrevDayOHLC
    hl52: HighLow52W | None
    now_ohlc: tuple[float, float, float, float]
    candles_len: int
    preopen_indicative_price: float = 0.0
    preopen_change_pct: float = 0.0
    preopen_volume: float = 0.0
    price_at_0915: float = 0.0
    price_at_0930: float = 0.0
    suggested_alloc_pct: float = 0.0
    suggested_alloc_amount: float = 0.0
    deployable_alloc_pct: float = 0.0
    deployable_alloc_amount: float = 0.0


@dataclass(frozen=True)
class Stage1RunReport:
    message_text: str
    run_summary: dict[str, str]
    long_rows: list[dict[str, str]]
    short_rows: list[dict[str, str]]
    metrics_rows: list[dict[str, str]]
    validation_rows: list[dict[str, str]]
    process_log: list[str]
    workbook_name: str


def _is_weekend(now_ist: datetime) -> bool:
    return now_ist.weekday() >= 5


def symbol_sector(symbol: str) -> str:
    if symbol in IT_SYMBOLS:
        return "IT"
    if symbol in BANK_SYMBOLS:
        return "BANK"
    return "OTHER"


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _escape(v: Any) -> str:
    return escape(_fmt(v))


def _ohlc_line(symbol: str, prev: PrevDayOHLC, now_candles_len: int, now_ohlc: tuple[float, float, float, float]) -> str:
    opn, high, low, last = now_ohlc
    return (
        f"Refs: PrevOpen {prev.prev_open:.2f} | PDH {prev.pdh:.2f} | PDL {prev.pdl:.2f} | PrevClose {prev.prev_close:.2f} | "
        f"Open {opn:.2f} | High {high:.2f} | Low {low:.2f} | Last {last:.2f} | Candles {now_candles_len}"
    )


def _format_validation_failure(report: object) -> str:
    issues = getattr(report, "issues", [])
    lines = [
        "NO TRADE - CONDITIONS NOT FAVORABLE",
        "Reason: Data validation mismatch between Shoonya and NSE exceeded thresholds.",
        "Manual confirmation required: rerun with --source-force shoonya or --source-force nse.",
        "Mismatch Summary:",
    ]
    for issue in issues[:12]:
        lines.append(
            f"- {issue.symbol} {issue.field}: shoonya={issue.primary_value:.4f}, "
            f"nse={issue.secondary_value:.4f}, diff={issue.diff_pct:.2f}% ({issue.severity})"
        )
    return "\n".join(lines)


def _build_trade_plan(
    p: PreparedSymbol,
    market_regime: str,
    settings: Settings,
) -> tuple[TradePlan, str]:
    m = p.metrics
    direction = p.scored.direction
    if direction == "BUY":
        entry = max(p.prev.pdh, m.orh)
        stop = min(m.orl, m.vwap, p.prev.pdl)
        invalidation = [
            "15m candle closes back below PDH",
            "Break below ORL",
            "Sustained VWAP rejection after trigger",
        ]
    else:
        entry = min(p.prev.pdl, m.orl)
        stop = max(m.orh, m.vwap, p.prev.pdh)
        invalidation = [
            "15m candle closes back above PDL",
            "Break above ORH",
            "Sustained VWAP rejection (upside) after trigger",
        ]

    why_not = ""
    risk_plan = build_risk_plan(
        capital=settings.capital,
        risk_pct=settings.risk_per_trade_pct,
        entry=entry,
        stop=stop,
        min_rr=settings.min_rr,
        include_t2=True,
    )
    if risk_plan is None:
        why_not = "rr_below_min_or_invalid_structure_stop"
        risk = abs(entry - stop)
        t1 = entry + (1.8 * risk) if direction == "BUY" else entry - (1.8 * risk)
        target_line = f"T1 {t1:.2f}"
        rr_text = "N/A"
    else:
        target_line = f"T1 {risk_plan.t1:.2f}, T2 {risk_plan.t2:.2f}" if risk_plan.t2 else f"T1 {risk_plan.t1:.2f}"
        rr_text = f"{risk_plan.rr:.2f}"

    highlow = (
        f"52W High/Low: {p.hl52.high_52w:.2f}/{p.hl52.low_52w:.2f}"
        if p.hl52 is not None
        else "52W High/Low: Unavailable"
    )
    reasons = (
        f"Price action: ORH {m.orh:.2f}, ORL {m.orl:.2f}, OR close {m.or_close:.2f}, VWAP {m.vwap:.2f}. "
        f"Volume: RVOL {m.rvol:.2f}x. "
        f"Market/Sector: {market_regime}, {p.sector_trend}. "
        "News: Placeholder (manual check advised). "
        + _ohlc_line(p.symbol, p.prev, p.candles_len, p.now_ohlc)
        + f" | {highlow}"
    )
    plan = TradePlan(
        stock_name=p.symbol,
        trade_direction=direction,
        market_regime=market_regime,
        sector_trend=p.sector_trend,
        selection_criteria_summary=[
            f"Gap/structure: gap {m.gap_pct:.2f}% and {'retained' if m.retained_gap else 'faded'}",
            f"OR strength: ORH {m.orh:.2f}, ORL {m.orl:.2f}, close {m.or_close:.2f}",
            f"VWAP acceptance: {'yes' if m.vwap_accepted else 'no'}",
            f"Relative volume: {m.rvol:.2f}x",
            f"Sector strength aligned: {p.sector_trend}",
            f"Market context aligned: {market_regime}",
        ],
        entry_zone=f"{entry:.2f} (trigger after 15m sustain)",
        stop_loss=f"{stop:.2f} (structure-based)",
        targets=target_line,
        reward_risk=rr_text,
        confidence_score=p.scored.confidence,
        reasoning=reasons,
        invalidation_conditions=invalidation,
        why_chosen=p.scored.reasons,
    )
    return plan, why_not


def _placeholder(direction: str, regime: str) -> CandidateView:
    synthetic_prev = PrevDayOHLC(pdh=0.0, pdl=0.0, prev_close=0.0)
    synthetic_metrics = OpeningRangeMetrics(
        orh=0.0,
        orl=0.0,
        or_close=0.0,
        vwap=0.0,
        rvol=0.0,
        gap_pct=0.0,
        retained_gap=False,
        long_trigger_ok=False,
        short_trigger_ok=False,
        opening_move_pct=0.0,
        vwap_accepted=False,
    )
    plan = TradePlan(
        stock_name="NO CANDIDATE",
        trade_direction=direction,
        market_regime=regime,
        sector_trend="N/A",
        selection_criteria_summary=["Insufficient valid directional setups in current scan."],
        entry_zone="N/A",
        stop_loss="N/A",
        targets="N/A",
        reward_risk="N/A",
        confidence_score=0,
        reasoning="Watchlist placeholder only.",
        invalidation_conditions=["N/A"],
        why_chosen=["N/A"],
    )
    return CandidateView(
        plan=plan,
        status="NON-ACTIONABLE",
        why_not_actionable="insufficient_directional_setups",
        score=0.0,
        symbol=plan.stock_name,
        metrics=synthetic_metrics,
        prev=synthetic_prev,
        hl52=None,
        now_ohlc=(0.0, 0.0, 0.0, 0.0),
        candles_len=0,
    )


def _reject_reason_for_symbol(
    symbol: str,
    candles: dict[str, list],
    prev: dict[str, PrevDayOHLC],
    metrics_map: dict[str, OpeningRangeMetrics],
    scored_map: dict[str, ScoredCandidate],
) -> str:
    if symbol not in candles:
        return "missing_intraday_candles"
    if symbol not in prev:
        return "missing_prev_day_ohlc"
    if symbol not in metrics_map:
        return "opening_range_metrics_unavailable"
    if symbol not in scored_map:
        return "trigger_not_confirmed_or_vwap_rejected"
    return ""


def _extract_window_prices(sym_candles: list) -> tuple[float, float]:
    if not sym_candles:
        return 0.0, 0.0
    return float(sym_candles[0].open), float(sym_candles[-1].close)


def _build_preopen_map(
    universe: list[str],
    candles: dict[str, list],
    prev: dict[str, PrevDayOHLC],
    preopen_rows: list[PreopenRow],
) -> dict[str, PreopenRow]:
    preopen_map = {r.symbol.upper(): r for r in preopen_rows if r.symbol}
    for symbol in universe:
        if symbol in preopen_map:
            continue
        sym_candles = candles.get(symbol, [])
        prev_ref = prev.get(symbol)
        if not sym_candles or prev_ref is None:
            preopen_map[symbol] = PreopenRow(
                symbol=symbol,
                indicative_price=0.0,
                change_pct=0.0,
                volume=0.0,
                status="UNAVAILABLE",
            )
            continue
        first_open = float(sym_candles[0].open)
        ref = float(prev_ref.prev_close) if prev_ref.prev_close else first_open
        change_pct = ((first_open - ref) / ref) * 100.0 if ref else 0.0
        preopen_map[symbol] = PreopenRow(
            symbol=symbol,
            indicative_price=first_open,
            change_pct=change_pct,
            volume=float(sym_candles[0].volume),
            status="SYNTH_FROM_OPEN",
        )
    return preopen_map


def _rejected_views_for_direction(
    direction: str,
    limit: int,
    regime: str,
    universe: list[str],
    candles: dict[str, list],
    prev: dict[str, PrevDayOHLC],
    hl52: dict[str, HighLow52W | None],
    metrics_map: dict[str, OpeningRangeMetrics],
    scored_map: dict[str, ScoredCandidate],
    rel_map: dict[str, float],
    preopen_map: dict[str, PreopenRow],
) -> list[CandidateView]:
    rows: list[tuple[int, float, str, CandidateView]] = []
    for symbol in universe:
        scored = scored_map.get(symbol)
        if scored is not None and scored.direction == direction:
            # Already represented in this side's ranked candidates.
            continue
        reason = _reject_reason_for_symbol(symbol, candles, prev, metrics_map, scored_map)
        if scored is not None and scored.direction != direction:
            reason = "direction_filtered_out"
        met = metrics_map.get(symbol)
        prev_ref = prev.get(symbol, PrevDayOHLC(pdh=0.0, pdl=0.0, prev_close=0.0))
        sym_candles = candles.get(symbol, [])
        if sym_candles:
            opn = sym_candles[0].open
            high = max(c.high for c in sym_candles)
            low = min(c.low for c in sym_candles)
            last = sym_candles[-1].close
            candles_len = len(sym_candles)
        else:
            opn = high = low = last = 0.0
            candles_len = 0
        price_0915, price_0930 = _extract_window_prices(sym_candles)
        preopen = preopen_map.get(symbol, PreopenRow(symbol=symbol, indicative_price=0.0, change_pct=0.0, volume=0.0, status="UNAVAILABLE"))

        sect = symbol_sector(symbol)
        sect_trend = "Outperforming vs NIFTY" if rel_map.get(sect, 0.0) > 0 else "Weak/Neutral vs NIFTY"
        if scored is not None:
            direction_hint = scored.direction
            proxy_score = scored.score
        elif met is not None:
            direction_hint = "BUY" if met.or_close >= prev_ref.prev_close else "SELL"
            proxy_score = min(99.0, max(0.0, 20.0 + abs(met.gap_pct) + (met.rvol * 10.0)))
        else:
            # Reject ranking fallback when opening-range metrics are unavailable:
            # use pre-market move + liquidity + previous-day range.
            if preopen.change_pct < 0:
                direction_hint = "SELL"
            elif preopen.change_pct > 0:
                direction_hint = "BUY"
            else:
                direction_hint = "SELL" if prev_ref.prev_close < prev_ref.prev_open else "BUY"
            prev_range_pct = 0.0
            if prev_ref.prev_close > 0 and prev_ref.pdh >= prev_ref.pdl:
                prev_range_pct = ((prev_ref.pdh - prev_ref.pdl) / prev_ref.prev_close) * 100.0
            liquidity_component = min(12.0, max(0.0, preopen.volume / 200000.0))
            proxy_score = min(
                99.0,
                max(
                    0.0,
                    (abs(preopen.change_pct) * 8.0) + (prev_range_pct * 3.0) + liquidity_component,
                ),
            )

        # Prioritize rejects aligned with the side being shown.
        priority = 0 if direction_hint == direction else 1

        plan = TradePlan(
            stock_name=symbol,
            trade_direction=direction,
            market_regime=regime,
            sector_trend=sect_trend,
            selection_criteria_summary=[
                f"Rejected during conservative filtering: {reason}",
                f"Directional audit list for {direction} side.",
                (
                    f"Latest refs: PrevOpen {prev_ref.prev_open:.2f}, "
                    f"PDH {prev_ref.pdh:.2f}, PDL {prev_ref.pdl:.2f}, PrevClose {prev_ref.prev_close:.2f}"
                ),
            ],
            entry_zone="N/A (rejected candidate)",
            stop_loss="N/A (rejected candidate)",
            targets="N/A",
            reward_risk="N/A",
            confidence_score=int(round(proxy_score)),
            reasoning=(
                f"Rejected symbol surfaced for transparency. "
                f"OHLC now: O {opn:.2f}, H {high:.2f}, L {low:.2f}, Last {last:.2f}, Candles {candles_len}."
            ),
            invalidation_conditions=["N/A (rejected candidate)"],
            why_chosen=["Included for audit visibility when no actionable setup exists."],
        )
        rows.append(
            (
                priority,
                -proxy_score,
                symbol,
                CandidateView(
                    plan=plan,
                    status="NON-ACTIONABLE",
                    why_not_actionable=reason,
                    score=proxy_score,
                    symbol=symbol,
                    metrics=met
                    if met is not None
                    else OpeningRangeMetrics(
                        orh=prev_ref.pdh,
                        orl=prev_ref.pdl,
                        or_close=prev_ref.prev_close,
                        vwap=prev_ref.prev_close,
                        rvol=0.0,
                        gap_pct=0.0,
                        retained_gap=False,
                        long_trigger_ok=False,
                        short_trigger_ok=False,
                        opening_move_pct=0.0,
                        vwap_accepted=False,
                    ),
                    prev=prev_ref,
                    hl52=hl52.get(symbol),
                    now_ohlc=(opn, high, low, last),
                    candles_len=candles_len,
                    preopen_indicative_price=preopen.indicative_price,
                    preopen_change_pct=preopen.change_pct,
                    preopen_volume=preopen.volume,
                    price_at_0915=price_0915,
                    price_at_0930=price_0930,
                ),
            )
        )
    rows.sort(key=lambda x: (x[0], x[1], x[2]))
    return [row[3] for row in rows[:limit]]


def _format_candidate_rich(view: CandidateView) -> str:
    plan = view.plan
    status_emoji = "✅" if view.status == "ACTIONABLE" else "⛔"
    lines = [
        f"<b>Stock Name:</b> {_escape(plan.stock_name)}",
        f"<b>Trade Direction:</b> {_escape(plan.trade_direction)}",
        f"<b>Status:</b> {status_emoji} {_escape(view.status)}",
        f"<b>Market Regime:</b> {_escape(plan.market_regime)}",
        f"<b>Sector Trend:</b> {_escape(plan.sector_trend)}",
        "<b>Selection Criteria Summary:</b>",
    ]
    lines.extend([f"• {_escape(x)}" for x in plan.selection_criteria_summary])
    lines += [
        f"<b>Entry Zone:</b> {_escape(plan.entry_zone)}",
        f"<b>Stop Loss:</b> {_escape(plan.stop_loss)}",
        f"<b>Target(s):</b> {_escape(plan.targets)}",
        f"<b>Reward : Risk:</b> {_escape(plan.reward_risk)}",
        (
            f"<b>Capital Allocation (Suggested):</b> "
            f"{view.suggested_alloc_pct:.2f}% (INR {view.suggested_alloc_amount:.2f})"
        ),
        (
            f"<b>Capital Allocation (Deployable Now):</b> "
            f"{view.deployable_alloc_pct:.2f}% (INR {view.deployable_alloc_amount:.2f})"
        ),
        f"<b>Confidence Score (0-100):</b> {_escape(plan.confidence_score)}",
        f"<b>Why chosen:</b> {_escape('; '.join(plan.why_chosen))}",
        f"<b>Reasoning:</b> {_escape(plan.reasoning)}",
    ]
    if view.status == "NON-ACTIONABLE":
        lines.append(f"<b>Why not actionable:</b> {_escape(view.why_not_actionable)}")
    lines.append("<b>Invalidation Conditions:</b>")
    lines.extend([f"• {_escape(x)}" for x in plan.invalidation_conditions])
    return "\n".join(lines)


def _determine_nifty_directional_bias(
    idx: dict[str, list],
    sectors: dict[str, Any],
) -> tuple[str, list[str]]:
    nifty = idx.get("NIFTY", [])
    if not nifty:
        return "BALANCED", ["NIFTY intraday candles unavailable; using balanced allocation."]

    first = nifty[0]
    last = nifty[-1]
    day_change_pct = 0.0
    if sectors.get("OTHER") is not None:
        day_change_pct = float(getattr(sectors["OTHER"], "change_pct", 0.0))

    opening_move = ((last.close - first.open) / first.open) * 100.0 if first.open > 0 else 0.0

    bullish = 0
    bearish = 0
    reasons: list[str] = []

    if day_change_pct >= 0.25:
        bullish += 1
        reasons.append(f"NIFTY day change positive ({day_change_pct:+.2f}%).")
    elif day_change_pct <= -0.25:
        bearish += 1
        reasons.append(f"NIFTY day change negative ({day_change_pct:+.2f}%).")
    else:
        reasons.append(f"NIFTY day change neutral ({day_change_pct:+.2f}%).")

    if opening_move >= 0.20:
        bullish += 1
        reasons.append(f"09:15-09:30 direction bullish ({opening_move:+.2f}%).")
    elif opening_move <= -0.20:
        bearish += 1
        reasons.append(f"09:15-09:30 direction bearish ({opening_move:+.2f}%).")
    else:
        reasons.append(f"09:15-09:30 direction neutral ({opening_move:+.2f}%).")

    if bullish >= 2 and bearish == 0:
        return "LONG_BIAS", reasons
    if bearish >= 2 and bullish == 0:
        return "SHORT_BIAS", reasons
    return "BALANCED", reasons


def _rank_weight_pcts(n: int) -> list[float]:
    if n <= 0:
        return []
    denom = sum(range(1, n + 1))
    return [((n - i) / denom) for i in range(n)]


def _apply_rank_allocations(
    long_views: list[CandidateView],
    short_views: list[CandidateView],
    capital: float,
    directional_bias: str,
) -> tuple[list[CandidateView], list[CandidateView]]:
    has_longs = len(long_views) > 0
    has_shorts = len(short_views) > 0
    if not has_longs and not has_shorts:
        return long_views, short_views

    # Directional suggestion driven by NIFTY pre-market/day-change + 09:15 behavior.
    if directional_bias == "LONG_BIAS":
        long_book_share, short_book_share = 1.0, 0.0
    elif directional_bias == "SHORT_BIAS":
        long_book_share, short_book_share = 0.0, 1.0
    else:
        if has_longs and has_shorts:
            long_book_share, short_book_share = 0.5, 0.5
        elif has_longs:
            long_book_share, short_book_share = 1.0, 0.0
        else:
            long_book_share, short_book_share = 0.0, 1.0

    lw = _rank_weight_pcts(len(long_views))
    sw = _rank_weight_pcts(len(short_views))

    out_longs: list[CandidateView] = []
    for i, v in enumerate(long_views):
        suggested_pct = lw[i] * long_book_share * 100.0
        deploy_pct = suggested_pct if v.status == "ACTIONABLE" else 0.0
        out_longs.append(
            replace(
                v,
                suggested_alloc_pct=suggested_pct,
                suggested_alloc_amount=(capital * suggested_pct / 100.0),
                deployable_alloc_pct=deploy_pct,
                deployable_alloc_amount=(capital * deploy_pct / 100.0),
            )
        )

    out_shorts: list[CandidateView] = []
    for i, v in enumerate(short_views):
        suggested_pct = sw[i] * short_book_share * 100.0
        deploy_pct = suggested_pct if v.status == "ACTIONABLE" else 0.0
        out_shorts.append(
            replace(
                v,
                suggested_alloc_pct=suggested_pct,
                suggested_alloc_amount=(capital * suggested_pct / 100.0),
                deployable_alloc_pct=deploy_pct,
                deployable_alloc_amount=(capital * deploy_pct / 100.0),
            )
        )

    return out_longs, out_shorts


def _to_views(
    picks: list[ScoredCandidate],
    metrics_map: dict[str, OpeningRangeMetrics],
    candles: dict[str, list],
    prev: dict[str, PrevDayOHLC],
    hl52: dict[str, HighLow52W | None],
    rel_map: dict[str, float],
    preopen_map: dict[str, PreopenRow],
    regime: str,
    settings: Settings,
) -> list[CandidateView]:
    out: list[CandidateView] = []
    for s in picks:
        sym_candles = candles.get(s.symbol, [])
        if not sym_candles or s.symbol not in prev or s.symbol not in metrics_map:
            continue
        opn = sym_candles[0].open
        high = max(c.high for c in sym_candles)
        low = min(c.low for c in sym_candles)
        last = sym_candles[-1].close
        price_0915, price_0930 = _extract_window_prices(sym_candles)
        preopen = preopen_map.get(
            s.symbol,
            PreopenRow(symbol=s.symbol, indicative_price=0.0, change_pct=0.0, volume=0.0, status="UNAVAILABLE"),
        )
        sect = symbol_sector(s.symbol)
        sect_trend = "Outperforming vs NIFTY" if rel_map.get(sect, 0.0) > 0 else "Weak/Neutral vs NIFTY"
        prepared = PreparedSymbol(
            symbol=s.symbol,
            prev=prev[s.symbol],
            hl52=hl52.get(s.symbol),
            metrics=metrics_map[s.symbol],
            scored=s,
            sector_trend=sect_trend,
            now_ohlc=(opn, high, low, last),
            candles_len=len(sym_candles),
        )
        plan, why_not = _build_trade_plan(prepared, regime, settings)
        status = "NON-ACTIONABLE" if why_not else "ACTIONABLE"
        out.append(
            CandidateView(
                plan=plan,
                status=status,
                why_not_actionable=why_not,
                score=s.score,
                symbol=s.symbol,
                metrics=metrics_map[s.symbol],
                prev=prev[s.symbol],
                hl52=hl52.get(s.symbol),
                now_ohlc=(opn, high, low, last),
                candles_len=len(sym_candles),
                preopen_indicative_price=preopen.indicative_price,
                preopen_change_pct=preopen.change_pct,
                preopen_volume=preopen.volume,
                price_at_0915=price_0915,
                price_at_0930=price_0930,
            )
        )
    return out


def _candidate_rows(views: list[CandidateView]) -> list[dict[str, str]]:
    def _extract_number(text: str) -> float | None:
        try:
            return float(text.split()[0])
        except Exception:
            return None

    def _extract_targets(text: str) -> tuple[float | None, float | None]:
        t1: float | None = None
        t2: float | None = None
        for part in [x.strip() for x in text.split(",")]:
            if part.upper().startswith("T1"):
                try:
                    t1 = float(part.split()[1])
                except Exception:
                    pass
            if part.upper().startswith("T2"):
                try:
                    t2 = float(part.split()[1])
                except Exception:
                    pass
        return t1, t2

    def _proxy_levels(view: CandidateView) -> tuple[float | None, float | None, float | None, float | None]:
        m = view.metrics
        p = view.prev
        if view.plan.trade_direction == "BUY":
            entry = max(m.orh, p.pdh)
            stop = min(m.orl, p.pdl, m.vwap)
            if stop >= entry:
                return None, None, None, None
            r = entry - stop
            return entry, stop, entry + (1.8 * r), entry + (2.5 * r)
        entry = min(m.orl, p.pdl)
        stop = max(m.orh, p.pdh, m.vwap)
        if stop <= entry:
            return None, None, None, None
        r = stop - entry
        return entry, stop, entry - (1.8 * r), entry - (2.5 * r)

    rows: list[dict[str, str]] = []
    for idx, view in enumerate(views, start=1):
        m = view.metrics
        prev = view.prev
        entry_level: float | None = None
        stop_level: float | None = None
        t1_level: float | None = None
        t2_level: float | None = None
        if view.status == "ACTIONABLE":
            entry_level = _extract_number(view.plan.entry_zone)
            stop_level = _extract_number(view.plan.stop_loss)
            t1_level, t2_level = _extract_targets(view.plan.targets)
            if entry_level is None or stop_level is None:
                p_entry, p_stop, p_t1, p_t2 = _proxy_levels(view)
                entry_level = entry_level if entry_level is not None else p_entry
                stop_level = stop_level if stop_level is not None else p_stop
                t1_level = t1_level if t1_level is not None else p_t1
                t2_level = t2_level if t2_level is not None else p_t2
        rows.append(
            {
                "rank_position": str(idx),
                "stock_symbol": view.plan.stock_name,
                "trade_direction": view.plan.trade_direction,
                "status_actionability": view.status,
                "composite_score": f"{view.score:.2f}",
                "confidence_score_0_to_100": str(view.plan.confidence_score),
                "entry_zone_text": view.plan.entry_zone,
                "stop_loss_text": view.plan.stop_loss,
                "targets_text": view.plan.targets,
                "reward_to_risk_text": view.plan.reward_risk,
                "rejection_reason_if_non_actionable": view.why_not_actionable,
                "market_regime_label": view.plan.market_regime,
                "sector_trend_label": view.plan.sector_trend,
                "entry_price_level": f"{entry_level:.2f}" if entry_level is not None else "",
                "stop_loss_price_level": f"{stop_level:.2f}" if stop_level is not None else "",
                "target_1_price_level": f"{t1_level:.2f}" if t1_level is not None else "",
                "target_2_price_level": f"{t2_level:.2f}" if t2_level is not None else "",
                "capital_allocation_suggested_percent": f"{view.suggested_alloc_pct:.2f}",
                "capital_allocation_suggested_inr": f"{view.suggested_alloc_amount:.2f}",
                "capital_allocation_deployable_percent": f"{view.deployable_alloc_pct:.2f}",
                "capital_allocation_deployable_inr": f"{view.deployable_alloc_amount:.2f}",
                "previous_day_high_price": f"{prev.pdh:.2f}",
                "previous_day_low_price": f"{prev.pdl:.2f}",
                "previous_day_close_price": f"{prev.prev_close:.2f}",
                "previous_day_open_price": f"{prev.prev_open:.2f}",
                "pre_market_indicative_price": f"{view.preopen_indicative_price:.2f}",
                "pre_market_change_percent": f"{view.preopen_change_pct:.2f}",
                "pre_market_volume": f"{view.preopen_volume:.0f}",
                "price_at_0915_window_start": f"{view.price_at_0915:.2f}",
                "price_at_0930_window_end": f"{view.price_at_0930:.2f}",
                "opening_range_high_price": f"{m.orh:.2f}",
                "opening_range_low_price": f"{m.orl:.2f}",
                "opening_range_close_price": f"{m.or_close:.2f}",
                "opening_range_vwap_price": f"{m.vwap:.2f}",
                "relative_volume_ratio": f"{m.rvol:.2f}",
                "gap_percent_vs_previous_close": f"{m.gap_pct:.2f}",
                "performance_percent_0915_to_0930": f"{m.opening_move_pct:.2f}",
                "candles_count_in_window": str(view.candles_len),
                "reasoning_summary": view.plan.reasoning,
                "selection_reason_summary": "; ".join(view.plan.why_chosen),
                "invalidation_conditions_summary": "; ".join(view.plan.invalidation_conditions),
            }
        )
    return rows


def _metrics_rows(views: list[CandidateView]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for view in views:
        m = view.metrics
        opn, high, low, last = view.now_ohlc
        row = {
            "stock_symbol": view.plan.stock_name,
            "opening_range_high_price": f"{m.orh:.2f}",
            "opening_range_low_price": f"{m.orl:.2f}",
            "opening_range_close_price": f"{m.or_close:.2f}",
            "opening_range_vwap_price": f"{m.vwap:.2f}",
            "relative_volume_ratio": f"{m.rvol:.2f}",
            "gap_percent_vs_previous_close": f"{m.gap_pct:.2f}",
            "performance_percent_0915_to_0930": f"{m.opening_move_pct:.2f}",
            "gap_retained_flag": str(m.retained_gap),
            "long_trigger_passed_flag": str(m.long_trigger_ok),
            "short_trigger_passed_flag": str(m.short_trigger_ok),
            "vwap_acceptance_flag": str(m.vwap_accepted),
            "previous_day_high_price": f"{view.prev.pdh:.2f}",
            "previous_day_low_price": f"{view.prev.pdl:.2f}",
            "previous_day_close_price": f"{view.prev.prev_close:.2f}",
            "previous_day_open_price": f"{view.prev.prev_open:.2f}",
            "pre_market_indicative_price": f"{view.preopen_indicative_price:.2f}",
            "pre_market_change_percent": f"{view.preopen_change_pct:.2f}",
            "pre_market_volume": f"{view.preopen_volume:.0f}",
            "price_at_0915_window_start": f"{view.price_at_0915:.2f}",
            "price_at_0930_window_end": f"{view.price_at_0930:.2f}",
            "window_open_price": f"{opn:.2f}",
            "window_high_price": f"{high:.2f}",
            "window_low_price": f"{low:.2f}",
            "window_last_price": f"{last:.2f}",
            "candles_count_in_window": str(view.candles_len),
            "fifty_two_week_high_price": f"{view.hl52.high_52w:.2f}" if view.hl52 else "Unavailable",
            "fifty_two_week_low_price": f"{view.hl52.low_52w:.2f}" if view.hl52 else "Unavailable",
        }
        rows.append(row)
    return rows


def _full_universe_metrics_rows(
    universe: list[str],
    candles: dict[str, list],
    prev: dict[str, PrevDayOHLC],
    hl52: dict[str, HighLow52W | None],
    metrics_map: dict[str, OpeningRangeMetrics],
    scored_map: dict[str, ScoredCandidate],
    sectors: dict[str, Any],
    preopen_map: dict[str, PreopenRow],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for symbol in universe:
        sym_candles = candles.get(symbol, [])
        prev_ref = prev.get(symbol)
        met = metrics_map.get(symbol)
        scored = scored_map.get(symbol)

        if sym_candles:
            opn = sym_candles[0].open
            high = max(c.high for c in sym_candles)
            low = min(c.low for c in sym_candles)
            last = sym_candles[-1].close
            candles_len = len(sym_candles)
        else:
            opn = high = low = last = 0.0
            candles_len = 0
        price_0915, price_0930 = _extract_window_prices(sym_candles)

        if prev_ref is None:
            prev_ref = PrevDayOHLC(pdh=0.0, pdl=0.0, prev_close=0.0)

        reject_reason = _reject_reason_for_symbol(symbol, candles, prev, metrics_map, scored_map)
        eligibility = "ELIGIBLE" if scored is not None else "REJECTED"

        fallback_orh = prev_ref.pdh
        fallback_orl = prev_ref.pdl
        fallback_close = prev_ref.prev_close
        fallback_vwap = prev_ref.prev_close
        fallback_rvol = 0.0
        fallback_gap = 0.0

        if met is None and reject_reason in {"opening_range_metrics_unavailable", "missing_intraday_candles"}:
            reject_reason = "opening_range_unavailable_using_prevday_proxy"

        row = {
            "stock_symbol": symbol,
            "sector_name": symbol_sector(symbol),
            "sector_change_percent": f"{sectors.get(symbol_sector(symbol)).change_pct:.2f}"
            if sectors.get(symbol_sector(symbol)) is not None
            else "",
            "sector_relative_strength_vs_nifty": f"{sectors.get(symbol_sector(symbol)).rel_vs_nifty:.2f}"
            if sectors.get(symbol_sector(symbol)) is not None
            else "",
            "eligibility_status": eligibility,
            "rejection_reason_if_any": reject_reason,
            "composite_score": f"{scored.score:.2f}" if scored else "",
            "trade_direction": scored.direction if scored else "",
            "confidence_score_0_to_100": str(scored.confidence) if scored else "",
            "opening_range_high_price": f"{met.orh:.2f}" if met else f"{fallback_orh:.2f}",
            "opening_range_low_price": f"{met.orl:.2f}" if met else f"{fallback_orl:.2f}",
            "opening_range_close_price": f"{met.or_close:.2f}" if met else f"{fallback_close:.2f}",
            "opening_range_vwap_price": f"{met.vwap:.2f}" if met else f"{fallback_vwap:.2f}",
            "relative_volume_ratio": f"{met.rvol:.2f}" if met else f"{fallback_rvol:.2f}",
            "gap_percent_vs_previous_close": f"{met.gap_pct:.2f}" if met else f"{fallback_gap:.2f}",
            "performance_percent_0915_to_0930": f"{met.opening_move_pct:.2f}" if met else "0.00",
            "gap_retained_flag": str(met.retained_gap) if met else "False",
            "long_trigger_passed_flag": str(met.long_trigger_ok) if met else "False",
            "short_trigger_passed_flag": str(met.short_trigger_ok) if met else "False",
            "vwap_acceptance_flag": str(met.vwap_accepted) if met else "False",
            "previous_day_high_price": f"{prev_ref.pdh:.2f}",
            "previous_day_low_price": f"{prev_ref.pdl:.2f}",
            "previous_day_close_price": f"{prev_ref.prev_close:.2f}",
            "previous_day_open_price": f"{prev_ref.prev_open:.2f}",
            "pre_market_indicative_price": f"{preopen_map.get(symbol, PreopenRow(symbol=symbol, indicative_price=0.0, change_pct=0.0, volume=0.0, status='UNAVAILABLE')).indicative_price:.2f}",
            "pre_market_change_percent": f"{preopen_map.get(symbol, PreopenRow(symbol=symbol, indicative_price=0.0, change_pct=0.0, volume=0.0, status='UNAVAILABLE')).change_pct:.2f}",
            "pre_market_volume": f"{preopen_map.get(symbol, PreopenRow(symbol=symbol, indicative_price=0.0, change_pct=0.0, volume=0.0, status='UNAVAILABLE')).volume:.0f}",
            "price_at_0915_window_start": f"{price_0915:.2f}",
            "price_at_0930_window_end": f"{price_0930:.2f}",
            "window_open_price": f"{opn:.2f}",
            "window_high_price": f"{high:.2f}",
            "window_low_price": f"{low:.2f}",
            "window_last_price": f"{last:.2f}",
            "candles_count_in_window": str(candles_len),
            "fifty_two_week_high_price": f"{hl52[symbol].high_52w:.2f}" if symbol in hl52 and hl52[symbol] else "Unavailable",
            "fifty_two_week_low_price": f"{hl52[symbol].low_52w:.2f}" if symbol in hl52 and hl52[symbol] else "Unavailable",
        }
        rows.append(row)
    return rows


def _build_run_report(
    settings: Settings,
    now_ist: datetime,
    test_mode: bool,
    replay_date: str | None,
    top_line: str,
    regime: str,
    source_label: str,
    validation_status: str,
    backup_interval: str,
    directional_bias: str,
    directional_bias_reasons: list[str],
    long_views: list[CandidateView],
    short_views: list[CandidateView],
    metrics_rows: list[dict[str, str]],
    sector_lines: list[str],
    mismatch_lines: list[str],
    process_log: list[str],
) -> Stage1RunReport:
    lines: list[str] = []
    if test_mode:
        lines.extend(
            [
                "🧪 <b>TEST MODE: ON</b>",
                f"<b>Replay Date:</b> {_escape(replay_date or now_ist.date().isoformat())}",
                "<b>Run Context:</b> Weekend/Off-hours replay",
            ]
        )
    lines.extend(
        [
            "📊 <b>Indian Intraday Assistant - Opening Range Decision Support</b>",
            f"<b>As of:</b> {_escape(now_ist.strftime('%Y-%m-%d %H:%M:%S %Z'))}",
            f"<b>Data Source:</b> {_escape(source_label)}",
            f"<b>Validation:</b> {_escape(validation_status)}",
            f"<b>Backup Interval Used:</b> {_escape(backup_interval)}",
            f"<b>Market Regime:</b> {_escape(regime)}",
            f"<b>Performance Window:</b> {_escape(settings.open_range_start)}-{_escape(settings.open_range_end)}",
            f"<b>Directional Bias (NIFTY):</b> {_escape(directional_bias)}",
        ]
    )
    lines.append("<b>NIFTY Bias Inputs:</b>")
    lines.extend([f"• {_escape(x)}" for x in directional_bias_reasons])
    if top_line.startswith("NO TRADE"):
        lines.append("⚠️ <b>NO TRADE - CONDITIONS NOT FAVORABLE</b>")
        lines.append("Reason: Both directional lists are non-actionable under conservative filters.")
    lines.append("<b>Sector Strength Scoreboard:</b>")
    lines.extend([f"• {_escape(line)}" for line in sector_lines])
    if mismatch_lines:
        lines.append("⚠️ <b>Mismatch Summary:</b>")
        lines.extend([f"• {_escape(line)}" for line in mismatch_lines])
    if source_label == "NSE_BACKUP" and backup_interval != "1m":
        lines.append(f"⚠️ Backup mode: NSE data, interval={_escape(backup_interval)}, reduced confidence.")
    lines.append("Note: Intraday only. Close positions before market close.")
    lines.append("")
    lines.append(f"🟢 <b>Top {settings.top_long_candidates} LONG (Ranked)</b>")
    lines.append("")
    for view in long_views[: settings.top_long_candidates]:
        lines.append(_format_candidate_rich(view))
        lines.append("")
    lines.append(f"🔴 <b>Top {settings.top_short_candidates} SHORT (Ranked)</b>")
    lines.append("")
    for view in short_views[: settings.top_short_candidates]:
        lines.append(_format_candidate_rich(view))
        lines.append("")

    validation_rows: list[dict[str, str]] = []
    for row in mismatch_lines:
        validation_rows.append({"issue": row})

    workbook_name = f"stage1_{now_ist.strftime('%Y-%m-%d_%H%M%S')}_{'TEST' if test_mode else 'LIVE'}.xlsx"
    run_summary = {
        "mode": "TEST" if test_mode else "LIVE",
        "replay_date": replay_date or "",
        "as_of": now_ist.isoformat(),
        "source": source_label,
        "validation": validation_status,
        "backup_interval": backup_interval,
        "market_regime": regime,
        "directional_bias": directional_bias,
        "decision": top_line,
    }
    return Stage1RunReport(
        message_text="\n".join(lines).strip(),
        run_summary=run_summary,
        long_rows=_candidate_rows(long_views),
        short_rows=_candidate_rows(short_views),
        metrics_rows=metrics_rows,
        validation_rows=validation_rows,
        process_log=process_log,
        workbook_name=workbook_name,
    )


def _no_trade_report(reason: str, now_ist: datetime, test_mode: bool, replay_date: str | None) -> Stage1RunReport:
    lines = []
    if test_mode:
        lines.extend(
            [
                "🧪 <b>TEST MODE: ON</b>",
                f"<b>Replay Date:</b> {_escape(replay_date or now_ist.date().isoformat())}",
                "<b>Run Context:</b> Weekend/Off-hours replay",
            ]
        )
    lines.extend(
        [
            "⚠️ <b>NO TRADE - CONDITIONS NOT FAVORABLE</b>",
            f"Reason: {_escape(reason)}",
        ]
    )
    return Stage1RunReport(
        message_text="\n".join(lines),
        run_summary={
            "mode": "TEST" if test_mode else "LIVE",
            "replay_date": replay_date or "",
            "as_of": now_ist.isoformat(),
            "source": "UNKNOWN",
            "validation": "UNKNOWN",
            "backup_interval": "UNKNOWN",
            "market_regime": "UNKNOWN",
            "decision": "NO TRADE - CONDITIONS NOT FAVORABLE",
        },
        long_rows=[],
        short_rows=[],
        metrics_rows=[],
        validation_rows=[],
        process_log=[reason],
        workbook_name=f"stage1_{now_ist.strftime('%Y-%m-%d_%H%M%S')}_{'TEST' if test_mode else 'LIVE'}.xlsx",
    )


def run_stage1_report(
    settings: Settings,
    provider: MarketDataProvider,
    now_ist: datetime,
    test_mode: bool = False,
    replay_date: str | None = None,
    show_process: bool = False,
) -> Stage1RunReport:
    if _is_weekend(now_ist) and not test_mode:
        return _no_trade_report("NO TRADE DAY (Weekend)", now_ist, test_mode, replay_date)

    date_prefix = now_ist.date().isoformat()
    start = datetime.fromisoformat(f"{date_prefix}T{settings.open_range_start}:00+05:30")
    end = datetime.fromisoformat(f"{date_prefix}T{settings.open_range_end}:00+05:30")

    process_log: list[str] = []
    context = None
    try:
        if hasattr(provider, "prepare_stage1_data_with_backup"):
            process_log.append("source_path=dual_source_provider")
            if show_process:
                print("[process] stage1 source_path=dual_source_provider")
            bundle = provider.prepare_stage1_data_with_backup(settings.universe, start, end)  # type: ignore[attr-defined]
            candles = bundle.candles
            prev = bundle.prev
            hl52 = bundle.hl52
            sectors = bundle.sectors
            idx = bundle.idx
            context = bundle.context
        else:
            candles = provider.get_intraday_candles(settings.universe, start, end, "1m")
            prev = provider.get_prev_day_ohlc(settings.universe)
            hl52 = provider.get_52w_highlow(settings.universe)
            sectors = provider.get_sector_index_snapshot()
            idx = provider.get_index_intraday(["NIFTY", "BANKNIFTY"], start, end, "1m")
    except Exception as exc:
        return _no_trade_report(
            f"Stage-1 unavailable due to data failure ({exc}); no guess made.",
            now_ist,
            test_mode,
            replay_date,
        )

    if context is not None and getattr(context.validation, "status", "PASS") == "FAIL":
        msg = _format_validation_failure(context.validation)
        return Stage1RunReport(
            message_text=msg,
            run_summary={
                "mode": "TEST" if test_mode else "LIVE",
                "replay_date": replay_date or "",
                "as_of": now_ist.isoformat(),
                "source": context.data_source,
                "validation": context.validation.status,
                "backup_interval": context.backup_interval_used,
                "market_regime": "UNKNOWN",
                "decision": "NO TRADE - CONDITIONS NOT FAVORABLE",
            },
            long_rows=[],
            short_rows=[],
            metrics_rows=[],
            validation_rows=[
                {
                    "symbol": i.symbol,
                    "field": i.field,
                    "primary_value": f"{i.primary_value:.4f}",
                    "secondary_value": f"{i.secondary_value:.4f}",
                    "diff_pct": f"{i.diff_pct:.2f}",
                    "severity": i.severity,
                }
                for i in context.validation.issues
            ],
            process_log=process_log + ["validation_failed"],
            workbook_name=f"stage1_{now_ist.strftime('%Y-%m-%d_%H%M%S')}_{'TEST' if test_mode else 'LIVE'}.xlsx",
        )
    if not candles:
        return _no_trade_report("Intraday candles missing for entire universe.", now_ist, test_mode, replay_date)

    regime_enum = classify_market_regime(idx)
    regime = regime_enum.value
    sector_strength = compute_sector_strength(sectors)
    rel_map = {k: v.rel_vs_nifty for k, v in sectors.items()}
    directional_bias, directional_bias_reasons = _determine_nifty_directional_bias(idx, sectors)
    preopen_rows: list[PreopenRow] = []
    try:
        preopen_rows = provider.get_preopen_watchlist(settings.universe)  # type: ignore[call-arg]
        process_log.append(f"preopen_rows_fetched={len(preopen_rows)}")
    except Exception as exc:
        process_log.append(f"preopen_unavailable:{exc}")
    preopen_map = _build_preopen_map(settings.universe, candles, prev, preopen_rows)

    candidate_inputs: list[CandidateInput] = []
    metrics_map: dict[str, OpeningRangeMetrics] = {}
    for sym in settings.universe:
        if sym not in candles or sym not in prev:
            continue
        met = compute_opening_range_metrics(candles[sym], prev[sym], settings.allow_vwap_rejection_filter)
        if met is None:
            continue
        metrics_map[sym] = met
        sect = symbol_sector(sym)
        candidate_inputs.append(
            CandidateInput(
                symbol=sym,
                sector=sect,
                metrics=met,
                market_regime=regime_enum,
                sector_rel_strength=rel_map.get(sect, 0.0),
                price_above_prev=met.or_close > prev[sym].prev_close,
                price_below_prev=met.or_close < prev[sym].prev_close,
                preopen_change_pct=preopen_map.get(
                    sym,
                    PreopenRow(symbol=sym, indicative_price=0.0, change_pct=0.0, volume=0.0, status="UNAVAILABLE"),
                ).change_pct,
                preopen_indicative_price=preopen_map.get(
                    sym,
                    PreopenRow(symbol=sym, indicative_price=0.0, change_pct=0.0, volume=0.0, status="UNAVAILABLE"),
                ).indicative_price,
                preopen_volume=preopen_map.get(
                    sym,
                    PreopenRow(symbol=sym, indicative_price=0.0, change_pct=0.0, volume=0.0, status="UNAVAILABLE"),
                ).volume,
            )
        )

    long_limit = max(1, settings.top_long_candidates)
    short_limit = max(1, settings.top_short_candidates)
    ranked_longs, ranked_shorts = split_ranked_candidates(
        candidate_inputs,
        long_limit=long_limit,
        short_limit=short_limit,
    )
    scored_map: dict[str, ScoredCandidate] = {}
    for c in candidate_inputs:
        s = score_candidate(c)
        if s is not None:
            scored_map[c.symbol] = s

    long_views = _to_views(ranked_longs, metrics_map, candles, prev, hl52, rel_map, preopen_map, regime, settings)
    short_views = _to_views(ranked_shorts, metrics_map, candles, prev, hl52, rel_map, preopen_map, regime, settings)

    if settings.fill_empty_slots:
        rejected_long = _rejected_views_for_direction(
            direction="BUY",
            limit=long_limit,
            regime=regime,
            universe=settings.universe,
            candles=candles,
            prev=prev,
            hl52=hl52,
            metrics_map=metrics_map,
            scored_map=scored_map,
            rel_map=rel_map,
            preopen_map=preopen_map,
        )
        rejected_short = _rejected_views_for_direction(
            direction="SELL",
            limit=short_limit,
            regime=regime,
            universe=settings.universe,
            candles=candles,
            prev=prev,
            hl52=hl52,
            metrics_map=metrics_map,
            scored_map=scored_map,
            rel_map=rel_map,
            preopen_map=preopen_map,
        )
        existing_long = {v.plan.stock_name for v in long_views}
        existing_short = {v.plan.stock_name for v in short_views}
        for v in rejected_long:
            if len(long_views) >= long_limit:
                break
            if v.plan.stock_name in existing_long:
                continue
            long_views.append(v)
            existing_long.add(v.plan.stock_name)
        for v in rejected_short:
            if len(short_views) >= short_limit:
                break
            if v.plan.stock_name in existing_short:
                continue
            short_views.append(v)
            existing_short.add(v.plan.stock_name)
        while len(long_views) < long_limit:
            long_views.append(_placeholder("BUY", regime))
        while len(short_views) < short_limit:
            short_views.append(_placeholder("SELL", regime))

    long_views, short_views = _apply_rank_allocations(
        long_views=long_views,
        short_views=short_views,
        capital=settings.capital,
        directional_bias=directional_bias,
    )

    all_views = long_views + short_views
    top_line = (
        "NO TRADE - CONDITIONS NOT FAVORABLE"
        if all_views and all(v.status == "NON-ACTIONABLE" for v in all_views)
        else "Indian Intraday Assistant - Opening Range Decision Support"
    )

    source_label = "SHOONYA"
    validation_status = "PASS"
    backup_interval = "1m"
    mismatch_lines: list[str] = []
    if context is not None:
        source_label = context.data_source
        validation_status = context.validation.status
        backup_interval = context.backup_interval_used
        if show_process:
            print(
                f"[process] stage1 data_source={source_label} validation={validation_status} backup_interval={backup_interval}"
            )
        for issue in context.validation.issues[:8]:
            mismatch_lines.append(
                f"{issue.symbol} {issue.field}: {issue.diff_pct:.2f}% "
                f"({issue.primary_value:.4f} vs {issue.secondary_value:.4f})"
            )

    sector_lines = [f"{s.name}: {s.rel_vs_nifty:+.2f} rel vs NIFTY ({s.change_pct:+.2f}%)" for s in sector_strength.scoreboard[:6]]
    all_metrics_rows = _full_universe_metrics_rows(
        universe=settings.universe,
        candles=candles,
        prev=prev,
        hl52=hl52,
        metrics_map=metrics_map,
        scored_map=scored_map,
        sectors=sectors,
        preopen_map=preopen_map,
    )
    report = _build_run_report(
        settings=settings,
        now_ist=now_ist,
        test_mode=test_mode,
        replay_date=replay_date,
        top_line=top_line,
        regime=regime,
        source_label=source_label,
        validation_status=validation_status,
        backup_interval=backup_interval,
        directional_bias=directional_bias,
        directional_bias_reasons=directional_bias_reasons,
        long_views=long_views,
        short_views=short_views,
        metrics_rows=all_metrics_rows,
        sector_lines=sector_lines,
        mismatch_lines=mismatch_lines,
        process_log=process_log,
    )
    return report


def run_stage1(
    settings: Settings,
    provider: MarketDataProvider,
    now_ist: datetime,
    test_mode: bool = False,
    replay_date: str | None = None,
    show_process: bool = False,
) -> str:
    return run_stage1_report(
        settings=settings,
        provider=provider,
        now_ist=now_ist,
        test_mode=test_mode,
        replay_date=replay_date,
        show_process=show_process,
    ).message_text
