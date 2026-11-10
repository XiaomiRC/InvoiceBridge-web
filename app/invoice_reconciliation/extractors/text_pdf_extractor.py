from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import re

from pypdf import PdfReader

from invoice_reconciliation.processing.models import PDFRecord, PDFChargeLine
from invoice_reconciliation.processing.normalizer import (
    normalize_account,
    normalize_customer,
    normalize_reference,
    normalize_site,
    normalize_charge_name,
    parse_amount,
    parse_date,
)


MONEY = r"£?\s*-?\(?[\d,]+(?:\.\d{2})?\)?"


def _first(pattern: str, text: str, flags: int = re.I) -> str:
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else ""


def _amount_after(label: str, text: str) -> Decimal:
    value = _first(rf"{re.escape(label)}\s+({MONEY})", text)
    return parse_amount(value)


def _period_from_text(text: str) -> tuple[str, str]:
    match = re.search(r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4})\s*-\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4})", text)
    if match:
        return parse_date(match.group(1)), parse_date(match.group(2))
    return "", ""


def read_pdf_pages(pdf_path: Path) -> list[str]:
    reader = PdfReader(str(pdf_path))
    return [page.extract_text() or "" for page in reader.pages]


def extract_pdf(pdf_path: Path, run_id: str, sequence: int) -> tuple[PDFRecord, list[PDFChargeLine]]:
    pages = read_pdf_pages(pdf_path)
    text = "\n".join(pages)
    record = PDFRecord(
        pdf_record_id=f"PDF-{sequence:04d}",
        run_id=run_id,
        source_file=pdf_path.name,
        source_path=str(pdf_path),
        pdf_page_count=len(pages),
        invoice_template="BT business bill",
        extraction_confidence=Decimal("0.90") if text.strip() else Decimal("0.00"),
        extraction_status="Extracted" if text.strip() else "Unreadable PDF",
    )
    record.account_number_original = _first(r"Account Number\s+([A-Z0-9_-]+)", text)
    record.account_number_normalized = normalize_account(record.account_number_original)
    record.invoice_reference_original = _first(r"Invoice Reference\s+([A-Z0-9]+)", text)
    record.invoice_reference_normalized = normalize_reference(record.invoice_reference_original)
    record.po_number_original = _first(r"PO Number:\s*([^\n]+)", text)
    record.po_number_normalized = normalize_reference(record.po_number_original)
    record.billing_date = parse_date(_first(r"Invoice Date\s+([0-9]{1,2}\s+[A-Za-z]{3}\s+\d{4})", text))
    record.bill_title = _first(r"Your business bill\s*\n([^\n]+)", text)
    record.customer_name_original = _first(r"Your VAT registration number\s+[A-Z0-9]+\s*\n([^\n]+)", text)
    record.customer_name_normalized = normalize_customer(record.customer_name_original)
    record.site_name_original = record.account_number_original
    record.site_name_normalized = normalize_site(record.site_name_original)
    period_start, period_end = _period_from_text(text)
    record.billing_period_start = period_start
    record.billing_period_end = period_end
    record.charge_period_start = period_start
    record.charge_period_end = period_end
    record.regular_charges = _amount_after("Regular charges", text)
    record.one_off_charges = _amount_after("One-off charges and credits", text)
    record.usage_charges = _amount_after("Usage charges", text)
    record.total_excluding_vat = _amount_after("Total excluding VAT", text)
    record.vat_amount = _amount_after("Total VAT", text)
    record.total_including_vat = _amount_after("Total for this bill", text)
    charges = extract_charge_lines(record, pages)
    return record, charges


def extract_charge_lines(record: PDFRecord, pages: list[str]) -> list[PDFChargeLine]:
    lines: list[PDFChargeLine] = []
    seen_source: set[tuple[int, str, str, str]] = set()
    index = 1
    for page_number, page_text in enumerate(pages, start=1):
        source_lines = [line.strip() for line in page_text.splitlines() if line.strip()]
        current_category = "Other"
        current_type = "Detail Line"
        pending_description = ""
        pending_period = ""
        for raw in source_lines:
            lowered = raw.lower()
            if "regular charges" in lowered:
                current_category = "Regular"
                current_type = "Section Subtotal" if "£" in raw else "Header"
            elif "one-off charges" in lowered or "one off charges" in lowered:
                current_category = "One-off"
                current_type = "Section Subtotal" if "£" in raw else "Header"
            elif "usage charges" in lowered:
                current_category = "Usage"
                current_type = "Section Subtotal" if "£" in raw else "Header"
            elif "total vat" in lowered or re.match(r"vat at", lowered):
                current_category = "VAT"
                current_type = "VAT"
            elif "total excluding vat" in lowered:
                current_category = "Other"
                current_type = "Invoice Subtotal"
            elif "total for this bill" in lowered or lowered.startswith("total which includes"):
                current_category = "Other"
                current_type = "Grand Total"

            amount_match = re.search(rf"({MONEY})\s*(?:20%|$)", raw)
            if amount_match and "£" in raw:
                amount = parse_amount(amount_match.group(1))
                description = raw[: amount_match.start()].strip(" :-")
                amount_only = not description
                if not description and pending_description:
                    description = pending_description
                if not description:
                    description = current_category
                line_type = current_type
                if description.lower().startswith("charge total") or "total for items" in lowered:
                    line_type = "Section Subtotal"
                if description.strip().upper() == "TOTAL":
                    line_type = "Section Subtotal"
                if description.startswith("•"):
                    line_type = "Section Subtotal"
                if amount_only and pending_description and line_type == "Header":
                    line_type = "Detail Line"
                if "total excluding vat" in lowered:
                    line_type = "Invoice Subtotal"
                if "total for this bill" in lowered or lowered.startswith("total which includes"):
                    line_type = "Grand Total"
                if "vat" in lowered and line_type not in {"Invoice Subtotal", "Grand Total"}:
                    line_type = "VAT"
                    current_category = "VAT"
                source_key = (page_number, raw, str(amount), line_type)
                included = True
                reason = ""
                if source_key in seen_source:
                    included = False
                    reason = "Duplicate extraction artefact"
                seen_source.add(source_key)
                lines.append(
                    PDFChargeLine(
                        charge_line_id=f"CL-{record.pdf_record_id}-{index:04d}",
                        pdf_record_id=record.pdf_record_id,
                        run_id=record.run_id,
                        pdf_filename=record.source_file,
                        pdf_page=page_number,
                        customer_name_original=record.customer_name_original,
                        customer_name_normalized=record.customer_name_normalized,
                        billing_account_original=record.account_number_original,
                        billing_account_normalized=record.account_number_normalized,
                        billing_period_start=record.billing_period_start,
                        billing_period_end=record.billing_period_end,
                        site_name_original=record.site_name_original,
                        site_name_normalized=record.site_name_normalized,
                        charge_name_original=description,
                        charge_name_normalized=normalize_charge_name(description),
                        charge_category=current_category,
                        amount=amount,
                        currency=record.currency,
                        line_type=line_type,
                        included_in_aggregation=included,
                        exclusion_reason=reason,
                    )
                )
                index += 1
                pending_description = ""
                current_type = "Detail Line"
            elif re.match(r"^\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}", raw):
                pending_period = raw
            elif raw and not raw.startswith("Page ") and len(raw) < 120:
                if current_category in {"Regular", "One-off", "Usage"} and "charges" not in lowered:
                    pending_description = raw if not pending_period else raw
    return lines
