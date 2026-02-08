from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from src.engine.stage1_openingrange import Stage1RunReport


def _write_sheet_rows(wb: Workbook, sheet_name: str, rows: list[dict[str, str]]) -> None:
    ws = wb.create_sheet(title=sheet_name)
    if not rows:
        ws.append(["empty"])
        ws.append(["no_rows"])
        return
    headers = list(rows[0].keys())
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h, "") for h in headers])


def write_stage1_report_xlsx(
    report: Stage1RunReport,
    reports_dir: str,
    now_ist: datetime,
) -> str:
    out_dir = Path(reports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = report.workbook_name or f"stage1_{now_ist.strftime('%Y-%m-%d_%H%M%S')}_LIVE.xlsx"
    path = out_dir / filename

    wb = Workbook()
    ws0 = wb.active
    ws0.title = "Run_Summary"
    ws0.append(["key", "value"])
    for key, value in report.run_summary.items():
        ws0.append([key, value])

    _write_sheet_rows(wb, "Long_Ranked", report.long_rows)
    _write_sheet_rows(wb, "Short_Ranked", report.short_rows)
    _write_sheet_rows(wb, "Metrics", report.metrics_rows)
    _write_sheet_rows(wb, "Validation", report.validation_rows)
    _write_sheet_rows(
        wb,
        "Process_Log",
        [{"step": str(i + 1), "detail": line} for i, line in enumerate(report.process_log)] or [{"step": "1", "detail": "none"}],
    )
    wb.save(path)
    return str(path)

