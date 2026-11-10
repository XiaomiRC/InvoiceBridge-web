from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from invoice_reconciliation.processing.models import PDFChargeLine
from invoice_reconciliation.processing.normalizer import amount_close


def validate_subtotals(lines: list[PDFChargeLine]) -> list[str]:
    warnings: list[str] = []
    grouped: dict[tuple[str, str], dict[str, object]] = defaultdict(lambda: {"detail": Decimal("0.00"), "subtotals": set()})
    for line in lines:
        if not line.included_in_aggregation:
            continue
        key = (line.pdf_record_id, line.charge_category)
        if line.line_type == "Detail Line":
            grouped[key]["detail"] = grouped[key]["detail"] + line.amount
        elif line.line_type == "Section Subtotal":
            grouped[key]["subtotals"].add(line.amount)
    for (pdf_record_id, category), totals in grouped.items():
        detail = totals["detail"]
        subtotals = totals["subtotals"]
        if detail and subtotals and not any(amount_close(detail, subtotal) for subtotal in subtotals):
            warnings.append(f"PDF Internal Total Mismatch: {pdf_record_id} {category}")
    return warnings
