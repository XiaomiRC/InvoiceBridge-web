from __future__ import annotations

from datetime import datetime
from pathlib import Path
from openpyxl import load_workbook

from invoice_reconciliation.processing.models import ExcelRecord
from invoice_reconciliation.processing.normalizer import normalize_account, normalize_customer, normalize_site, parse_amount, parse_date


SUMMARY_HEADERS = {
    "billingaccount": "billing_account",
    "one-time": "one_time",
    "connection": "connection",
    "rentals": "rentals",
    "cessation": "cessation",
    "usage": "usage",
    "priorperiod": "prior_period",
    "total": "total",
}


def _safe(value):
    return "" if value is None else value


def analyse_workbook(path: Path) -> dict:
    wb = load_workbook(path, data_only=False, read_only=False)
    info = {"workbook": path.name, "worksheets": []}
    for ws in wb.worksheets:
        formulas = 0
        nonempty = 0
        candidate_headers = []
        for row in ws.iter_rows():
            vals = [cell.value for cell in row]
            if any(v is not None for v in vals):
                nonempty += 1
                joined = " | ".join(str(v) for v in vals if v is not None)
                if any(k in joined.lower() for k in ["billing", "account", "rental", "usage", "total", "site"]):
                    candidate_headers.append(row[0].row)
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    formulas += 1
        info["worksheets"].append(
            {
                "name": ws.title,
                "state": ws.sheet_state,
                "max_row": ws.max_row,
                "max_column": ws.max_column,
                "merged_cells": len(ws.merged_cells.ranges),
                "formula_cells": formulas,
                "nonempty_rows": nonempty,
                "candidate_header_rows": sorted(set(candidate_headers))[:10],
            }
        )
    return info


def extract_excel_records(path: Path, run_id: str, start_sequence: int = 1) -> list[ExcelRecord]:
    wb_formula = load_workbook(path, data_only=False, read_only=False)
    wb_values = load_workbook(path, data_only=True, read_only=True)
    records: list[ExcelRecord] = []
    seq = start_sequence
    currency = "GBP"
    for sheet in wb_values.sheetnames:
        if sheet != "Summary":
            continue
        ws_values = wb_values[sheet]
        ws_formula = wb_formula[sheet]
        for row in ws_values.iter_rows(min_row=1, max_row=min(ws_values.max_row, 4), values_only=True):
            if row and "Currency" in [str(v) for v in row if v is not None]:
                values = list(row)
                try:
                    currency = str(values[values.index("Currency") + 1])
                except Exception:
                    currency = "GBP"
        rental_period = ""
        for row in ws_values.iter_rows(min_row=1, max_row=4, values_only=True):
            vals = list(row)
            if vals and vals[0] == "Rental Period":
                rental_period = parse_date(vals[1])
        header_row = None
        headers: dict[int, str] = {}
        for row_number, row in enumerate(ws_values.iter_rows(), start=1):
            vals = [cell.value for cell in row]
            normalized = [str(v).strip().lower() if v is not None else "" for v in vals]
            if "billingaccount" in normalized and "rentals" in normalized and "total" in normalized:
                header_row = row_number
                headers = {idx: SUMMARY_HEADERS.get(str(value).strip().lower(), str(value).strip().lower()) for idx, value in enumerate(vals) if value is not None}
                break
        if not header_row:
            continue
        for row_number, row in enumerate(ws_values.iter_rows(min_row=header_row + 1), start=header_row + 1):
            vals = {headers[idx]: cell.value for idx, cell in enumerate(row) if idx in headers}
            account = _safe(vals.get("billing_account"))
            if not account or str(account).strip().lower() == "total":
                if str(account).strip().lower() == "total":
                    break
                continue
            formula_cells = [
                ws_formula.cell(row=row_number, column=idx + 1).value
                for idx in headers
                if isinstance(ws_formula.cell(row=row_number, column=idx + 1).value, str)
                and ws_formula.cell(row=row_number, column=idx + 1).value.startswith("=")
            ]
            rec = ExcelRecord(
                excel_record_id=f"EXCEL-{seq:04d}",
                run_id=run_id,
                source_file=path.name,
                worksheet_name=sheet,
                source_row=row_number,
                customer_name_original=path.name.split("_")[0],
                billing_account_original=str(account),
                billing_reference_original=str(account),
                site_name_original=str(account),
                billing_period_start=rental_period,
                billing_period_end=rental_period,
                rentals=parse_amount(vals.get("rentals")),
                prior_period=parse_amount(vals.get("prior_period")),
                one_time=parse_amount(vals.get("one_time")),
                connection=parse_amount(vals.get("connection")),
                cessation=parse_amount(vals.get("cessation")),
                usage=parse_amount(vals.get("usage")),
                total=parse_amount(vals.get("total")),
                currency=currency,
                warnings="Formula cells present" if formula_cells else "",
            )
            rec.customer_name_normalized = normalize_customer(rec.customer_name_original)
            rec.billing_account_normalized = normalize_account(rec.billing_account_original)
            rec.billing_reference_normalized = normalize_account(rec.billing_reference_original)
            rec.site_name_normalized = normalize_site(rec.site_name_original)
            rec.expected_regular_charges = rec.rentals + rec.prior_period
            rec.expected_one_off_charges = rec.one_time + rec.connection + rec.cessation
            records.append(rec)
            seq += 1
    return records
