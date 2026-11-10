from __future__ import annotations

from dataclasses import dataclass, field, asdict
from decimal import Decimal
from typing import Any


def money_zero() -> Decimal:
    return Decimal("0.00")


@dataclass
class PDFRecord:
    pdf_record_id: str
    run_id: str
    source_file: str
    source_path: str
    pdf_page_count: int = 0
    customer_name_original: str = ""
    customer_name_normalized: str = ""
    account_number_original: str = ""
    account_number_normalized: str = ""
    billing_reference_original: str = ""
    billing_reference_normalized: str = ""
    invoice_reference_original: str = ""
    invoice_reference_normalized: str = ""
    po_number_original: str = ""
    po_number_normalized: str = ""
    bill_title: str = ""
    site_name_original: str = ""
    site_name_normalized: str = ""
    site_code_original: str = ""
    site_code_normalized: str = ""
    location_original: str = ""
    location_normalized: str = ""
    town_original: str = ""
    town_normalized: str = ""
    billing_date: str = ""
    billing_period_start: str = ""
    billing_period_end: str = ""
    charge_period_start: str = ""
    charge_period_end: str = ""
    regular_charges: Decimal = field(default_factory=money_zero)
    one_off_charges: Decimal = field(default_factory=money_zero)
    usage_charges: Decimal = field(default_factory=money_zero)
    credits: Decimal = field(default_factory=money_zero)
    adjustments: Decimal = field(default_factory=money_zero)
    vat_amount: Decimal = field(default_factory=money_zero)
    total_excluding_vat: Decimal = field(default_factory=money_zero)
    total_including_vat: Decimal = field(default_factory=money_zero)
    currency: str = "GBP"
    invoice_template: str = ""
    extraction_method: str = "direct_text"
    extraction_confidence: Decimal = Decimal("0.00")
    extraction_status: str = "Pending"
    validation_status: str = ""
    warnings: str = ""

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PDFChargeLine:
    charge_line_id: str
    pdf_record_id: str
    run_id: str
    pdf_filename: str
    pdf_page: int
    customer_name_original: str = ""
    customer_name_normalized: str = ""
    billing_account_original: str = ""
    billing_account_normalized: str = ""
    billing_period_start: str = ""
    billing_period_end: str = ""
    site_name_original: str = ""
    site_name_normalized: str = ""
    location_original: str = ""
    location_normalized: str = ""
    charge_name_original: str = ""
    charge_name_normalized: str = ""
    charge_category: str = "Other"
    amount: Decimal = field(default_factory=money_zero)
    currency: str = "GBP"
    line_type: str = "Detail Line"
    extraction_confidence: Decimal = Decimal("0.80")
    included_in_aggregation: bool = True
    exclusion_reason: str = ""

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExcelRecord:
    excel_record_id: str
    run_id: str
    source_file: str
    worksheet_name: str
    source_row: int
    customer_name_original: str = ""
    customer_name_normalized: str = ""
    billing_account_original: str = ""
    billing_account_normalized: str = ""
    billing_reference_original: str = ""
    billing_reference_normalized: str = ""
    invoice_reference_original: str = ""
    invoice_reference_normalized: str = ""
    site_name_original: str = ""
    site_name_normalized: str = ""
    site_code_original: str = ""
    site_code_normalized: str = ""
    location_original: str = ""
    location_normalized: str = ""
    town_original: str = ""
    town_normalized: str = ""
    billing_period_start: str = ""
    billing_period_end: str = ""
    rentals: Decimal = field(default_factory=money_zero)
    prior_period: Decimal = field(default_factory=money_zero)
    expected_regular_charges: Decimal = field(default_factory=money_zero)
    one_time: Decimal = field(default_factory=money_zero)
    connection: Decimal = field(default_factory=money_zero)
    cessation: Decimal = field(default_factory=money_zero)
    expected_one_off_charges: Decimal = field(default_factory=money_zero)
    usage: Decimal = field(default_factory=money_zero)
    total: Decimal = field(default_factory=money_zero)
    currency: str = "GBP"
    record_type: str = "Summary"
    extraction_status: str = "Extracted"
    warnings: str = ""

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MatchResult:
    group_id: str
    run_id: str
    status: str
    match_type: str
    match_confidence: str
    match_score: int
    match_rule: str
    excel_record_id: str
    pdf_record_ids: list[str]
    pdf_filenames: list[str]
    regular_difference: Decimal = field(default_factory=money_zero)
    one_off_difference: Decimal = field(default_factory=money_zero)
    usage_difference: Decimal = field(default_factory=money_zero)
    total_difference: Decimal = field(default_factory=money_zero)
    comments: str = ""

    def asdict(self) -> dict[str, Any]:
        return asdict(self)
