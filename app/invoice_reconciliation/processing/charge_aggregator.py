from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from invoice_reconciliation.processing.models import PDFChargeLine, PDFRecord


AGGREGATING_LINE_TYPES = {"Detail Line", "Section Subtotal"}


def charge_aggregation_key(line: PDFChargeLine) -> tuple:
    return (
        line.customer_name_normalized,
        line.billing_account_normalized,
        line.billing_period_start,
        line.billing_period_end,
        line.site_name_normalized,
        line.charge_name_normalized,
        line.charge_category,
        line.currency,
    )


def aggregate_charge_lines(lines: list[PDFChargeLine]) -> dict[tuple, Decimal]:
    totals: dict[tuple, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for line in lines:
        if not line.included_in_aggregation:
            continue
        if line.line_type not in AGGREGATING_LINE_TYPES:
            continue
        if line.charge_category in {"VAT", "Other"}:
            continue
        totals[charge_aggregation_key(line)] += line.amount
    return dict(totals)


def aggregate_invoice_records(records: list[PDFRecord]) -> dict[str, Decimal]:
    fields = [
        "regular_charges",
        "one_off_charges",
        "usage_charges",
        "credits",
        "adjustments",
        "vat_amount",
        "total_excluding_vat",
        "total_including_vat",
    ]
    return {field: sum((getattr(record, field) for record in records), Decimal("0.00")) for field in fields}

