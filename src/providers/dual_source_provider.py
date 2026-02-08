from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.providers.base import Candle, HighLow52W, MarketDataError, PrevDayOHLC, SectorSnapshot
from src.providers.fallback_policy import decide_fallback


@dataclass(frozen=True)
class ValidationIssue:
    symbol: str
    field: str
    primary_value: float
    secondary_value: float
    diff_pct: float
    severity: str


@dataclass(frozen=True)
class ValidationReport:
    source_primary: str
    source_secondary: str
    status: str
    issues: list[ValidationIssue]
    fail_reasons: list[str]


@dataclass(frozen=True)
class Stage1Context:
    data_source: str
    backup_interval_used: str
    validation: ValidationReport


@dataclass(frozen=True)
class Stage1Bundle:
    candles: dict[str, list[Candle]]
    prev: dict[str, PrevDayOHLC]
    hl52: dict[str, HighLow52W | None]
    sectors: dict[str, SectorSnapshot]
    idx: dict[str, list[Candle]]
    context: Stage1Context


def _safe_diff_pct(a: float, b: float) -> float:
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) * 100.0 / denom


class DualSourceProvider:
    """
    Orchestrates Shoonya primary and NSE secondary for backup and validation.
    It does not modify the base provider protocol and provides stage1 bundle helpers.
    """

    def __init__(
        self,
        primary: Any,
        secondary: Any,
        enable_nse_backup: bool = True,
        enable_cross_validation: bool = True,
        price_diff_tolerance_pct: float = 0.35,
        volume_diff_tolerance_pct: float = 10.0,
        fallback_intervals: list[str] | None = None,
        validation_audit_dir: str = "data/validation",
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.enable_nse_backup = enable_nse_backup
        self.enable_cross_validation = enable_cross_validation
        self.price_diff_tolerance_pct = price_diff_tolerance_pct
        self.volume_diff_tolerance_pct = volume_diff_tolerance_pct
        self.fallback_intervals = fallback_intervals or ["1m", "5m", "15m"]
        self.validation_audit_dir = Path(validation_audit_dir)
        self.source_mode = "auto"
        self.skip_validation = False

    def set_run_options(self, source_mode: str = "auto", skip_validation: bool = False) -> None:
        self.source_mode = source_mode
        self.skip_validation = skip_validation

    def set_reference_now(self, now_dt: datetime) -> None:
        if hasattr(self.primary, "set_reference_now"):
            self.primary.set_reference_now(now_dt)
        if hasattr(self.secondary, "set_reference_now"):
            self.secondary.set_reference_now(now_dt)

    def get_preopen_watchlist(self, universe: list[str]):
        if self.source_mode == "force_nse":
            return self.secondary.get_preopen_watchlist(universe)
        if self.source_mode == "force_shoonya":
            return self.primary.get_preopen_watchlist(universe)
        try:
            return self.primary.get_preopen_watchlist(universe)
        except Exception:
            return self.secondary.get_preopen_watchlist(universe)

    def _fetch_stage1_from(
        self,
        provider: Any,
        symbols: list[str],
        start: datetime,
        end: datetime,
        interval: str,
    ) -> tuple[
        dict[str, list[Candle]],
        dict[str, PrevDayOHLC],
        dict[str, HighLow52W | None],
        dict[str, SectorSnapshot],
        dict[str, list[Candle]],
    ]:
        candles = provider.get_intraday_candles(symbols, start, end, interval)
        prev = provider.get_prev_day_ohlc(symbols)
        hl52 = provider.get_52w_highlow(symbols)
        sectors = provider.get_sector_index_snapshot()
        idx = provider.get_index_intraday(["NIFTY", "BANKNIFTY"], start, end, interval)
        if self._require_intraday_data(start) and not self._candles_have_data(candles):
            raise MarketDataError(f"no_intraday_data_for_interval:{interval}")
        return candles, prev, hl52, sectors, idx

    @staticmethod
    def _candles_have_data(candles: dict[str, list[Candle]]) -> bool:
        if not candles:
            return False
        return any(len(v) > 0 for v in candles.values())

    @staticmethod
    def _require_intraday_data(start: datetime) -> bool:
        # For replay dates in the past, allow continuation with conservative proxy rows.
        return start.date() >= datetime.now().date()

    def _field_issues(
        self,
        primary_candles: dict[str, list[Candle]],
        secondary_candles: dict[str, list[Candle]],
        primary_prev: dict[str, PrevDayOHLC],
        secondary_prev: dict[str, PrevDayOHLC],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        shared = sorted(set(primary_candles).intersection(secondary_candles))
        for sym in shared:
            pcd = primary_candles[sym]
            scd = secondary_candles[sym]
            if not pcd or not scd:
                continue
            p_last = pcd[-1]
            s_last = scd[-1]
            for field, pv, sv, critical in [
                ("last", p_last.close, s_last.close, True),
                ("orh", max(x.high for x in pcd), max(x.high for x in scd), True),
                ("orl", min(x.low for x in pcd), min(x.low for x in scd), True),
                ("volume", p_last.volume, s_last.volume, False),
            ]:
                diff = _safe_diff_pct(float(pv), float(sv))
                threshold = (
                    self.price_diff_tolerance_pct
                    if field != "volume"
                    else self.volume_diff_tolerance_pct
                )
                if diff > threshold:
                    issues.append(
                        ValidationIssue(
                            symbol=sym,
                            field=field,
                            primary_value=float(pv),
                            secondary_value=float(sv),
                            diff_pct=diff,
                            severity="FAIL" if critical else "WARN",
                        )
                    )
            if sym in primary_prev and sym in secondary_prev:
                pp = primary_prev[sym]
                sp = secondary_prev[sym]
                for field, pv, sv in [
                    ("pdh", pp.pdh, sp.pdh),
                    ("pdl", pp.pdl, sp.pdl),
                    ("prev_close", pp.prev_close, sp.prev_close),
                ]:
                    diff = _safe_diff_pct(float(pv), float(sv))
                    if diff > self.price_diff_tolerance_pct:
                        issues.append(
                            ValidationIssue(
                                symbol=sym,
                                field=field,
                                primary_value=float(pv),
                                secondary_value=float(sv),
                                diff_pct=diff,
                                severity="FAIL",
                            )
                        )
        return issues

    def _to_report(
        self,
        issues: list[ValidationIssue],
        primary_name: str,
        secondary_name: str,
    ) -> ValidationReport:
        if not issues:
            return ValidationReport(
                source_primary=primary_name,
                source_secondary=secondary_name,
                status="PASS",
                issues=[],
                fail_reasons=[],
            )
        fail = [i for i in issues if i.severity == "FAIL"]
        warn = [i for i in issues if i.severity == "WARN"]
        status = "FAIL" if fail else ("WARN" if warn else "PASS")
        reasons = [f"{i.symbol}:{i.field}:{i.diff_pct:.2f}%" for i in fail]
        return ValidationReport(
            source_primary=primary_name,
            source_secondary=secondary_name,
            status=status,
            issues=issues,
            fail_reasons=reasons,
        )

    def _persist_validation(self, report: ValidationReport, run_date: datetime) -> None:
        try:
            self.validation_audit_dir.mkdir(parents=True, exist_ok=True)
            path = self.validation_audit_dir / f"{run_date.date().isoformat()}_stage1.json"
            payload = {
                "created_at": run_date.isoformat(),
                "report": {
                    "source_primary": report.source_primary,
                    "source_secondary": report.source_secondary,
                    "status": report.status,
                    "fail_reasons": report.fail_reasons,
                    "issues": [asdict(x) for x in report.issues],
                },
            }
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        except Exception:
            # Validation audit should never block the trading decision pipeline.
            return

    def prepare_stage1_data(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> Stage1Bundle:
        # Force NSE mode.
        if self.source_mode == "force_nse":
            if not self.enable_nse_backup:
                raise MarketDataError("nse_backup_disabled")
            last_exc: Exception | None = None
            for itv in self.fallback_intervals:
                try:
                    c, p, h, s, i = self._fetch_stage1_from(self.secondary, symbols, start, end, itv)
                    report = ValidationReport("NSE_BACKUP", "SHOONYA", "PASS", [], [])
                    return Stage1Bundle(c, p, h, s, i, Stage1Context("NSE_BACKUP", itv, report))
                except Exception as exc:
                    last_exc = exc
            raise MarketDataError(f"nse_force_failed:{last_exc}")

        # Primary path tries Shoonya first.
        primary_data = self._fetch_stage1_from(self.primary, symbols, start, end, "1m")
        p_c, p_prev, p_hl52, p_sec, p_idx = primary_data

        # Force Shoonya mode or disabled cross-validation.
        if self.source_mode == "force_shoonya" or not self.enable_cross_validation or self.skip_validation:
            report = ValidationReport("SHOONYA", "NSE_BACKUP", "PASS", [], [])
            return Stage1Bundle(p_c, p_prev, p_hl52, p_sec, p_idx, Stage1Context("SHOONYA", "1m", report))

        if not self.enable_nse_backup:
            report = ValidationReport("SHOONYA", "NSE_BACKUP", "PASS", [], [])
            return Stage1Bundle(p_c, p_prev, p_hl52, p_sec, p_idx, Stage1Context("SHOONYA", "1m", report))

        # Secondary path for validation. Best-effort, with interval fallback.
        secondary_data: tuple[
            dict[str, list[Candle]],
            dict[str, PrevDayOHLC],
            dict[str, HighLow52W | None],
            dict[str, SectorSnapshot],
            dict[str, list[Candle]],
        ] | None = None
        interval_used = "1m"
        for itv in self.fallback_intervals:
            try:
                secondary_data = self._fetch_stage1_from(self.secondary, symbols, start, end, itv)
                interval_used = itv
                break
            except Exception:
                continue
        if secondary_data is None:
            report = ValidationReport("SHOONYA", "NSE_BACKUP", "WARN", [], ["secondary_unavailable"])
            return Stage1Bundle(p_c, p_prev, p_hl52, p_sec, p_idx, Stage1Context("SHOONYA", "1m", report))

        s_c, s_prev, _, _, _ = secondary_data
        issues = self._field_issues(p_c, s_c, p_prev, s_prev)
        report = self._to_report(issues, "SHOONYA", "NSE_BACKUP")
        _ = decide_fallback(report.status, primary_ok=True, secondary_ok=True)
        self._persist_validation(report, start)
        return Stage1Bundle(
            p_c,
            p_prev,
            p_hl52,
            p_sec,
            p_idx,
            Stage1Context("SHOONYA", interval_used, report),
        )

    def prepare_stage1_data_with_backup(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> Stage1Bundle:
        """
        Auto mode with deterministic fallback:
        1) Try Shoonya (+ optional validation)
        2) If Shoonya fails, use NSE interval fallback chain
        """
        try:
            return self.prepare_stage1_data(symbols, start, end)
        except Exception as primary_exc:
            if not self.enable_nse_backup:
                raise
            last_exc: Exception | None = primary_exc
            for itv in self.fallback_intervals:
                try:
                    c, p, h, s, i = self._fetch_stage1_from(self.secondary, symbols, start, end, itv)
                    report = ValidationReport(
                        source_primary="NSE_BACKUP",
                        source_secondary="SHOONYA",
                        status="WARN",
                        issues=[],
                        fail_reasons=[f"primary_unavailable:{primary_exc}"],
                    )
                    self._persist_validation(report, start)
                    return Stage1Bundle(c, p, h, s, i, Stage1Context("NSE_BACKUP", itv, report))
                except Exception as exc:
                    last_exc = exc
            raise MarketDataError(f"both_sources_unavailable:{primary_exc};{last_exc}")
