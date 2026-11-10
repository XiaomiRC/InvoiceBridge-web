from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.formatting.rule import CellIsRule


def _value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return value


def _write_table(wb: Workbook, title: str, rows: list[dict]) -> None:
    ws = wb.create_sheet(title[:31])
    if not rows:
        ws.append(["No records"])
        return
    headers = list(rows[0].keys())
    ws.append(headers)
    for row in rows:
        ws.append([_value(row.get(header, "")) for header in headers])
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9EAF7")
    for col in ws.columns:
        letter = col[0].column_letter
        max_len = min(max(len(str(cell.value or "")) for cell in col) + 2, 45)
        ws.column_dimensions[letter].width = max_len
        for cell in col:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if isinstance(cell.value, float):
                cell.number_format = '#,##0.00;[Red]-#,##0.00'
    status_columns = [
        idx + 1
        for idx, header in enumerate(headers)
        if str(header).lower() in {"status", "match status", "overall match result", "result"}
    ]
    for row in range(2, ws.max_row + 1):
        status = " ".join(str(ws.cell(row=row, column=col).value or "") for col in status_columns) if status_columns else str(ws.cell(row=row, column=1).value or "")
        if "Mismatch" in status or "Missing" in status or "Failed" in status:
            for col in range(1, ws.max_column + 1):
                ws.cell(row=row, column=col).fill = PatternFill("solid", fgColor="FCE4D6")


def generate_report(
    output_path: Path,
    run_id: str,
    summary_rows: list[dict],
    billing_account_rows: list[dict],
    detailed_rows: list[dict],
    grouped_rows: list[dict],
    mapping_rows: list[dict],
    charge_mapping_rows: list[dict],
    aggregated_charge_rows: list[dict],
    field_mismatch_rows: list[dict],
    unused_pdf_rows: list[dict],
    missing_pdf_rows: list[dict],
    missing_excel_rows: list[dict],
    duplicate_rows: list[dict],
    manual_review_rows: list[dict],
    pdf_rows: list[dict],
    charge_rows: list[dict],
    excel_rows: list[dict],
    error_rows: list[dict],
    audit_rows: list[dict],
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)
    _write_table(wb, "Summary", summary_rows)
    _write_table(wb, "Billing Account Recon", billing_account_rows)
    _write_table(wb, "Detailed Comparison", detailed_rows)
    _write_table(wb, "Fully Matched", [r for r in detailed_rows if r.get("Status") == "Fully Matched"])
    _write_table(wb, "Grouped Matches", grouped_rows)
    _write_table(wb, "PDF-to-Excel Mapping", mapping_rows)
    _write_table(wb, "Charge Line Mapping", charge_mapping_rows)
    _write_table(wb, "Aggregated Charge Comparison", aggregated_charge_rows)
    _write_table(wb, "Field Mismatches", field_mismatch_rows)
    _write_table(wb, "Unused PDFs", unused_pdf_rows)
    _write_table(wb, "Missing in PDF", missing_pdf_rows)
    _write_table(wb, "Missing in Excel", missing_excel_rows)
    _write_table(wb, "Duplicate Records", duplicate_rows)
    _write_table(wb, "Manual Review", manual_review_rows)
    _write_table(wb, "PDF Extraction", pdf_rows)
    _write_table(wb, "PDF Charge Lines", charge_rows)
    _write_table(wb, "Excel Extraction", excel_rows)
    _write_table(wb, "Extraction Errors", error_rows)
    _write_table(wb, "Audit Log", audit_rows)
    wb.save(output_path)
    return output_path
