from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from itertools import combinations

from invoice_reconciliation.processing.charge_aggregator import aggregate_invoice_records
from invoice_reconciliation.processing.models import ExcelRecord, PDFRecord, MatchResult
from invoice_reconciliation.processing.normalizer import amount_close


def confidence(score: int) -> str:
    if score >= 80:
        return "High"
    if score >= 60:
        return "Medium"
    return "Low"


def compare_status(excel: ExcelRecord, pdfs: list[PDFRecord], tolerance: Decimal) -> tuple[str, dict[str, Decimal]]:
    agg = aggregate_invoice_records(pdfs)
    diffs = {
        "regular": agg["regular_charges"] - excel.expected_regular_charges,
        "one_off": agg["one_off_charges"] - excel.expected_one_off_charges,
        "usage": agg["usage_charges"] - excel.usage,
        "total": agg["total_excluding_vat"] - excel.total,
    }
    all_close = all(amount_close(value, Decimal("0.00"), tolerance) for value in diffs.values())
    if all_close:
        return ("Fully Matched" if len(pdfs) == 1 else "Automatic Grouped Match"), diffs
    if not amount_close(diffs["regular"], Decimal("0.00"), tolerance):
        return "Regular Charge Mismatch", diffs
    if not amount_close(diffs["one_off"], Decimal("0.00"), tolerance):
        return "One-off Charge Mismatch", diffs
    if not amount_close(diffs["usage"], Decimal("0.00"), tolerance):
        return "Usage Charge Mismatch", diffs
    return "Amount Mismatch", diffs


def _candidate_score(excel: ExcelRecord, pdf: PDFRecord) -> int:
    score = 0
    if excel.billing_account_normalized and excel.billing_account_normalized == pdf.account_number_normalized:
        score += 40
    if excel.billing_reference_normalized and excel.billing_reference_normalized == pdf.account_number_normalized:
        score += 40
    if excel.site_name_normalized and excel.site_name_normalized == pdf.site_name_normalized:
        score += 15
    if excel.customer_name_normalized and excel.customer_name_normalized == pdf.customer_name_normalized:
        score += 10
    if excel.billing_period_start and pdf.billing_period_start and excel.billing_period_start == pdf.billing_period_start:
        score += 25
    if excel.total and amount_close(excel.total, pdf.total_excluding_vat):
        score += 20
    if excel.billing_account_normalized and excel.billing_account_normalized not in {pdf.account_number_normalized, pdf.billing_reference_normalized}:
        score -= 20
    return score


def reconcile_records(
    excel_records: list[ExcelRecord],
    pdf_records: list[PDFRecord],
    run_id: str,
    tolerance: Decimal = Decimal("0.01"),
    max_group_size: int = 4,
) -> tuple[list[MatchResult], list[ExcelRecord], list[PDFRecord]]:
    assigned_pdf_ids: set[str] = set()
    results: list[MatchResult] = []
    missing_excel: list[ExcelRecord] = []
    group_index = 1

    for excel in sorted(excel_records, key=lambda r: (r.billing_account_normalized, r.source_file, r.source_row)):
        candidates = [pdf for pdf in pdf_records if pdf.pdf_record_id not in assigned_pdf_ids and _candidate_score(excel, pdf) > 0]
        candidates.sort(key=lambda pdf: _candidate_score(excel, pdf), reverse=True)
        chosen: list[PDFRecord] | None = None
        chosen_rule = "Manual review"
        chosen_score = 0

        for pdf in candidates:
            status, diffs = compare_status(excel, [pdf], tolerance)
            if status == "Fully Matched":
                chosen = [pdf]
                chosen_rule = "Billing account/site plus pre-VAT total"
                chosen_score = _candidate_score(excel, pdf)
                break

        if chosen is None and len(candidates) > 1:
            narrowed = [pdf for pdf in candidates if _candidate_score(excel, pdf) >= 35]
            for size in range(2, min(max_group_size, len(narrowed)) + 1):
                for combo in combinations(narrowed, size):
                    status, diffs = compare_status(excel, list(combo), tolerance)
                    if status == "Automatic Grouped Match":
                        chosen = list(combo)
                        chosen_rule = "Grouped account/site candidates plus combined pre-VAT total"
                        chosen_score = min(100, sum(_candidate_score(excel, pdf) for pdf in combo) // size + 20)
                        break
                if chosen:
                    break

        if chosen is None and candidates:
            best = candidates[: min(max_group_size, len(candidates))]
            status, diffs = compare_status(excel, best, tolerance)
            chosen = best if status != "Amount Mismatch" else None
            chosen_rule = "Best candidate group awaiting review"
            chosen_score = max((_candidate_score(excel, pdf) for pdf in best), default=0)

        if chosen:
            status, diffs = compare_status(excel, chosen, tolerance)
            group_id = f"GROUP-{run_id}-{group_index:04d}"
            match_type = "automatic_grouped_match" if len(chosen) > 1 and status in {"Automatic Grouped Match", "Fully Matched"} else "automatic_match"
            results.append(
                MatchResult(
                    group_id=group_id,
                    run_id=run_id,
                    status=status,
                    match_type=match_type,
                    match_confidence=confidence(chosen_score),
                    match_score=chosen_score,
                    match_rule=chosen_rule,
                    excel_record_id=excel.excel_record_id,
                    pdf_record_ids=[pdf.pdf_record_id for pdf in chosen],
                    pdf_filenames=[pdf.source_file for pdf in chosen],
                    regular_difference=diffs["regular"],
                    one_off_difference=diffs["one_off"],
                    usage_difference=diffs["usage"],
                    total_difference=diffs["total"],
                    comments="Repeated Values Aggregated" if len(chosen) > 1 else "",
                )
            )
            assigned_pdf_ids.update(pdf.pdf_record_id for pdf in chosen)
            group_index += 1
        else:
            missing_excel.append(excel)

    missing_pdfs = [pdf for pdf in pdf_records if pdf.pdf_record_id not in assigned_pdf_ids]
    return results, missing_excel, missing_pdfs


def duplicate_accounts(pdf_records: list[PDFRecord], excel_records: list[ExcelRecord]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = defaultdict(list)
    for pdf in pdf_records:
        values[f"PDF:{pdf.account_number_normalized}"].append(pdf.pdf_record_id)
    for excel in excel_records:
        values[f"EXCEL:{excel.billing_account_normalized}"].append(excel.excel_record_id)
    return {key: ids for key, ids in values.items() if len(ids) > 1}
