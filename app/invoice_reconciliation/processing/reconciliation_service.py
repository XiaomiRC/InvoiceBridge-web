from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import json

from invoice_reconciliation.extractors.excel_extractor import analyse_workbook, extract_excel_records
from invoice_reconciliation.extractors.text_pdf_extractor import extract_pdf
from invoice_reconciliation.persistence.audit_repository import audit, list_audit
from invoice_reconciliation.persistence.database import connect
from invoice_reconciliation.persistence.mapping_repository import save_mapping
from invoice_reconciliation.processing.charge_aggregator import aggregate_charge_lines, aggregate_invoice_records, charge_aggregation_key
from invoice_reconciliation.processing.matcher import compare_status, duplicate_accounts, reconcile_records
from invoice_reconciliation.processing.models import ExcelRecord, PDFChargeLine, PDFRecord, MatchResult
from invoice_reconciliation.processing.subtotal_validator import validate_subtotals
from invoice_reconciliation.reports.excel_report_generator import generate_report
from invoice_reconciliation.reports.pdf_analysis_report_generator import generate_pdf_analysis_report
from invoice_reconciliation.utils.logging_config import configure_logging
from invoice_reconciliation.utils.zip_utils import safe_extract_zip


def make_run_id(base: Path) -> str:
    prefix = datetime.now().strftime("RUN-%Y-%m-%d")
    existing = sorted(base.glob(prefix + "-*"))
    return f"{prefix}-{len(existing) + 1:03d}"


def _serial(row: dict) -> dict:
    out = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            out[key] = str(value)
        else:
            out[key] = value
    return out


def _as_rows(items) -> list[dict]:
    return [_serial(item.asdict() if hasattr(item, "asdict") else dict(item)) for item in items]


class ReconciliationRun:
    def __init__(self, project_root: Path, run_id: str | None = None):
        self.project_root = project_root
        self.run_id = run_id or make_run_id(project_root / "data" / "working")
        self.working_dir = project_root / "data" / "working" / self.run_id
        self.extracted_dir = self.working_dir / "extracted"
        self.output_dir = project_root / "data" / "output" / self.run_id
        self.log_path = project_root / "data" / "logs" / f"{self.run_id}.log"
        self.db_path = project_root / "data" / "database" / "reconciliation.sqlite"
        self.logger = configure_logging(self.log_path)
        self.pdf_records: list[PDFRecord] = []
        self.charge_lines: list[PDFChargeLine] = []
        self.excel_records: list[ExcelRecord] = []
        self.workbook_analysis: list[dict] = []
        self.matches = []
        self.missing_excel_records: list[ExcelRecord] = []
        self.missing_pdf_records: list[PDFRecord] = []

    def extract_zip(self, zip_path: Path) -> list[Path]:
        files = safe_extract_zip(zip_path, self.extracted_dir)
        self.logger.info("Extracted ZIP with %s files", len(files))
        return files

    def discover(self) -> dict:
        pdfs = sorted(self.extracted_dir.rglob("*.pdf"))
        excels = sorted([*self.extracted_dir.rglob("*.xlsx"), *self.extracted_dir.rglob("*.xlsm")])
        others = [p for p in self.extracted_dir.rglob("*") if p.is_file() and p.suffix.lower() not in {".pdf", ".xlsx", ".xlsm"}]
        duplicate_names = [name for name, count in Counter(p.name for p in pdfs + excels + others).items() if count > 1]
        return {"pdfs": pdfs, "excels": excels, "others": others, "duplicate_names": duplicate_names}

    def analyse_and_extract(self) -> None:
        inv = self.discover()
        for i, pdf in enumerate(inv["pdfs"], start=1):
            try:
                record, lines = extract_pdf(pdf, self.run_id, i)
                self.pdf_records.append(record)
                self.charge_lines.extend(lines)
                self.logger.info("PDF extracted %s pages=%s charge_lines=%s", record.pdf_record_id, record.pdf_page_count, len(lines))
            except Exception as exc:
                self.logger.exception("PDF extraction failed for PDF-%04d", i)
                self.pdf_records.append(PDFRecord(pdf_record_id=f"PDF-{i:04d}", run_id=self.run_id, source_file=pdf.name, source_path=str(pdf), extraction_status="Extraction Failed", warnings=str(exc)))
        seq = 1
        for excel_index, excel in enumerate(inv["excels"], start=1):
            try:
                self.workbook_analysis.append(analyse_workbook(excel))
                rows = extract_excel_records(excel, self.run_id, seq)
                self.excel_records.extend(rows)
                seq += len(rows)
                self.logger.info("Excel file %s extracted records=%s", excel_index, len(rows))
            except Exception as exc:
                self.logger.exception("Excel extraction failed for file %s", excel_index)
        for warning in validate_subtotals(self.charge_lines):
            self.logger.info(warning)
        self.write_analysis_report(inv)

    def reconcile(self) -> None:
        settings = self.load_settings()
        tolerance = Decimal(str(settings.get("amount_tolerance", "0.01")))
        max_group = int(settings.get("max_grouped_pdfs", 4))
        self.matches, self.missing_excel_records, self.missing_pdf_records = reconcile_records(self.excel_records, self.pdf_records, self.run_id, tolerance, max_group)
        conn = connect(self.db_path)
        for match in self.matches:
            save_mapping(conn, match.group_id, self.run_id, match.excel_record_id, match.pdf_record_ids, match.match_type, match.comments)
            audit(conn, self.run_id, "Automatic match selected", match.excel_record_id, match.pdf_record_ids, match.group_id, "Automatic", match.status)
        for excel in self.missing_excel_records:
            audit(conn, self.run_id, "Missing in PDF", excel.excel_record_id, [], "", "Automatic", "")
        for pdf in self.missing_pdf_records:
            audit(conn, self.run_id, "Missing in Excel", "", [pdf.pdf_record_id], "", "Automatic", "")
        conn.close()

    def confirm_manual_match(self, excel_record_id: str, pdf_record_ids: list[str], comments: str = "") -> MatchResult:
        excel_by_id = {record.excel_record_id: record for record in self.excel_records}
        pdf_by_id = {record.pdf_record_id: record for record in self.pdf_records}
        if excel_record_id not in excel_by_id:
            raise ValueError(f"Unknown Excel record: {excel_record_id}")
        if not pdf_record_ids:
            raise ValueError("At least one PDF must be selected")
        missing_pdf_ids = [pdf_id for pdf_id in pdf_record_ids if pdf_id not in pdf_by_id]
        if missing_pdf_ids:
            raise ValueError(f"Unknown PDF records: {', '.join(missing_pdf_ids)}")

        selected_pdf_ids = set(pdf_record_ids)
        removed_matches = [
            match
            for match in self.matches
            if match.excel_record_id == excel_record_id or selected_pdf_ids.intersection(match.pdf_record_ids)
        ]
        self.matches = [
            match
            for match in self.matches
            if match.excel_record_id != excel_record_id and not selected_pdf_ids.intersection(match.pdf_record_ids)
        ]

        excel = excel_by_id[excel_record_id]
        pdfs = [pdf_by_id[pdf_id] for pdf_id in pdf_record_ids]
        tolerance = Decimal(str(self.load_settings().get("amount_tolerance", "0.01")))
        auto_status, diffs = compare_status(excel, pdfs, tolerance)
        all_zero = all(value == Decimal("0.00") for value in diffs.values())
        if len(pdfs) > 1:
            status = "Manual Grouped Match" if all_zero else "Manual Partial Match"
            match_type = "manual_grouped_match"
        else:
            status = "Fully Matched" if all_zero else "Manual Partial Match"
            match_type = "manual_match"
        group_id = f"GROUP-{self.run_id}-MANUAL-{len(self.matches) + 1:04d}"
        match = MatchResult(
            group_id=group_id,
            run_id=self.run_id,
            status=status,
            match_type=match_type,
            match_confidence="Manual",
            match_score=100 if all_zero else 60,
            match_rule="Manual user-confirmed selection",
            excel_record_id=excel.excel_record_id,
            pdf_record_ids=[pdf.pdf_record_id for pdf in pdfs],
            pdf_filenames=[pdf.source_file for pdf in pdfs],
            regular_difference=diffs["regular"],
            one_off_difference=diffs["one_off"],
            usage_difference=diffs["usage"],
            total_difference=diffs["total"],
            comments=comments,
        )
        self.matches.append(match)

        matched_excel_ids = {item.excel_record_id for item in self.matches}
        matched_pdf_ids = {pdf_id for item in self.matches for pdf_id in item.pdf_record_ids}
        self.missing_excel_records = [record for record in self.excel_records if record.excel_record_id not in matched_excel_ids]
        self.missing_pdf_records = [record for record in self.pdf_records if record.pdf_record_id not in matched_pdf_ids]

        conn = connect(self.db_path)
        save_mapping(conn, match.group_id, self.run_id, match.excel_record_id, match.pdf_record_ids, match.match_type, comments)
        audit(
            conn,
            self.run_id,
            "Manual match confirmed",
            match.excel_record_id,
            match.pdf_record_ids,
            match.group_id,
            "Manual",
            comments or f"Replaced {len(removed_matches)} previous match(es)",
        )
        conn.close()
        self.generate_outputs()
        return match

    def load_settings(self) -> dict:
        path = self.project_root / "invoice_reconciliation" / "config" / "application_settings.json"
        if path.exists():
            return json.loads(path.read_text())
        return {"amount_tolerance": "0.01", "max_grouped_pdfs": 4}

    def write_analysis_report(self, inv: dict) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        account_counts = Counter(pdf.account_number_normalized for pdf in self.pdf_records if pdf.account_number_normalized)
        repeated_accounts = {k: v for k, v in account_counts.items() if v > 1}
        charge_counts = Counter(line.charge_name_normalized for line in self.charge_lines if line.charge_name_normalized)
        repeated_charges = {k: v for k, v in charge_counts.items() if v > 1}
        layout_count = len(set(pdf.invoice_template for pdf in self.pdf_records if pdf.invoice_template))
        lines = [
            "# Analysis Findings",
            "",
            f"Run ID: `{self.run_id}`",
            f"Input ZIP processed locally; source archive was not modified.",
            "",
            "## Inventory",
            f"- PDFs discovered: {len(inv['pdfs'])}",
            f"- Excel workbooks discovered: {len(inv['excels'])}",
            f"- Other files discovered: {len(inv['others'])}",
            f"- Duplicate filenames: {len(inv['duplicate_names'])}",
            "",
            "## PDF Structure",
            f"- Text-based PDFs: {sum(1 for p in self.pdf_records if p.extraction_status == 'Extracted')}",
            f"- OCR required: {sum(1 for p in self.pdf_records if p.extraction_status != 'Extracted')}",
            f"- Distinct layouts detected: {layout_count}",
        ]
        for pdf in self.pdf_records:
            masked_account = "***" + pdf.account_number_original[-3:] if pdf.account_number_original else ""
            lines.append(f"- {pdf.source_file}: {pdf.pdf_page_count} pages, text={pdf.extraction_status == 'Extracted'}, account={masked_account}, template={pdf.invoice_template}")
        lines += ["", "## Excel Workbooks"]
        for wb in self.workbook_analysis:
            lines.append(f"- {wb['workbook']}")
            for ws in wb["worksheets"]:
                lines.append(
                    f"  - {ws['name']}: state={ws['state']}, rows={ws['max_row']}, cols={ws['max_column']}, merged={ws['merged_cells']}, formulas={ws['formula_cells']}, candidate_headers={ws['candidate_header_rows']}"
                )
        lines += [
            "",
            "## Verified Relationships",
            f"- PDF account/site identifiers appearing in multiple PDFs: {len(repeated_accounts)}",
            f"- Repeated normalized charge descriptions: {len(repeated_charges)}",
            "- The comparison basis is Excel `Total` versus PDF `Total excluding VAT`; PDF `Total for this bill` includes VAT and is retained separately.",
            "- Category mapping implemented: PDF Regular = Excel Rentals + PriorPeriod; PDF One-off = Excel One-Time + Connection + Cessation; PDF Usage = Excel Usage.",
            f"- Account/site identifiers with multiple PDF candidates: {len(repeated_accounts)}. Review grouped matches for contributing source files.",
            "",
            "## Data Handling",
            "- No full invoice text is copied into this report.",
            "- Account identifiers are masked in this report where practical.",
            "- Generated data is stored under `data/working`, `data/output`, `data/database`, and `data/logs`.",
        ]
        path = self.output_dir / "analysis_findings.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def generate_outputs(self) -> dict[str, Path]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        pdf_rows = _as_rows(self.pdf_records)
        charge_rows = _as_rows(self.charge_lines)
        excel_rows = _as_rows(self.excel_records)
        for name, rows in [("pdf_extraction.json", pdf_rows), ("pdf_charge_lines.json", charge_rows), ("excel_extraction.json", excel_rows), ("matches.json", _as_rows(self.matches))]:
            (self.output_dir / name).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        report_path = self.output_dir / f"reconciliation_report_{self.run_id}.xlsx"
        pdf_report_path = self.output_dir / f"analysis_report_{self.run_id}.pdf"
        generate_report(
            report_path,
            self.run_id,
            self.summary_rows(),
            self.billing_account_reconciliation_rows(),
            self.detailed_rows(),
            self.grouped_rows(),
            self.mapping_rows(),
            self.charge_mapping_rows(),
            self.aggregated_charge_rows(),
            self.field_mismatch_rows(),
            self.unused_pdf_rows(),
            _as_rows(self.missing_excel_records),
            _as_rows(self.missing_pdf_records),
            self.duplicate_rows(),
            self.manual_review_rows(),
            pdf_rows,
            charge_rows,
            excel_rows,
            self.error_rows(),
            self.audit_rows(),
        )
        analysis_path = self.output_dir / "analysis_findings.md"
        generate_pdf_analysis_report(
            pdf_report_path,
            self.run_id,
            self.pdf_records,
            self.excel_records,
            self.workbook_analysis,
            self.matches,
            self.missing_excel_records,
            self.missing_pdf_records,
            self.summary_rows(),
            self.billing_account_reconciliation_rows(),
            self.unused_pdf_rows(),
            {
                "Markdown analysis": analysis_path,
                "PDF analysis report": pdf_report_path,
                "Excel reconciliation report": report_path,
                "PDF extraction JSON": self.output_dir / "pdf_extraction.json",
                "PDF charge lines JSON": self.output_dir / "pdf_charge_lines.json",
                "Excel extraction JSON": self.output_dir / "excel_extraction.json",
                "Matches JSON": self.output_dir / "matches.json",
                "SQLite database": self.db_path,
                "Run log": self.log_path,
            },
        )
        return {"report": report_path, "analysis": analysis_path, "pdf_analysis": pdf_report_path}

    def summary_rows(self) -> list[dict]:
        excel_total = sum((r.total for r in self.excel_records), Decimal("0.00"))
        pdf_total = sum((r.total_excluding_vat for r in self.pdf_records), Decimal("0.00"))
        statuses = Counter(m.status for m in self.matches)
        return [{
            "Run ID": self.run_id,
            "Run date and time": datetime.now().isoformat(timespec="seconds"),
            "Excel files processed": len({r.source_file for r in self.excel_records}),
            "PDFs processed": len(self.pdf_records),
            "PDF records": len(self.pdf_records),
            "PDF charge lines": len(self.charge_lines),
            "Excel records": len(self.excel_records),
            "Fully matched count": statuses.get("Fully Matched", 0),
            "Automatic grouped matches": statuses.get("Automatic Grouped Match", 0),
            "Amount mismatches": sum(1 for m in self.matches if "Mismatch" in m.status),
            "Missing in PDF": len(self.missing_excel_records),
            "Missing in Excel": len(self.missing_pdf_records),
            "Excel total": excel_total,
            "PDF total excluding VAT": pdf_total,
            "Overall difference": pdf_total - excel_total,
        }]

    def detailed_rows(self) -> list[dict]:
        excel_by_id = {r.excel_record_id: r for r in self.excel_records}
        pdf_by_id = {r.pdf_record_id: r for r in self.pdf_records}
        rows = []
        for match in self.matches:
            excel = excel_by_id[match.excel_record_id]
            pdfs = [pdf_by_id[i] for i in match.pdf_record_ids]
            agg = aggregate_invoice_records(pdfs)
            rows.append({
                "Status": match.status,
                "Group ID": match.group_id,
                "Match Type": match.match_type,
                "Match Confidence": match.match_confidence,
                "Match Rule": match.match_rule,
                "Customer": excel.customer_name_original,
                "Billing Account": excel.billing_account_original,
                "Billing Reference": excel.billing_reference_original,
                "Site": excel.site_name_original,
                "Billing Period": excel.billing_period_start,
                "Number of PDFs": len(pdfs),
                "PDF Filenames": match.pdf_filenames,
                "Excel Rentals": excel.rentals,
                "Excel PriorPeriod": excel.prior_period,
                "Excel Expected Regular": excel.expected_regular_charges,
                "PDF Regular": agg["regular_charges"],
                "Regular Difference": match.regular_difference,
                "Excel One-Time": excel.one_time,
                "Excel Connection": excel.connection,
                "Excel Cessation": excel.cessation,
                "Excel Expected One-off": excel.expected_one_off_charges,
                "PDF One-off": agg["one_off_charges"],
                "One-off Difference": match.one_off_difference,
                "Excel Usage": excel.usage,
                "PDF Usage": agg["usage_charges"],
                "Usage Difference": match.usage_difference,
                "Excel Total": excel.total,
                "PDF Total Excluding VAT": agg["total_excluding_vat"],
                "Total Difference": match.total_difference,
                "PDF VAT": agg["vat_amount"],
                "PDF Total Including VAT": agg["total_including_vat"],
                "Excel Workbook": excel.source_file,
                "Excel Worksheet": excel.worksheet_name,
                "Excel Row": excel.source_row,
                "Comments": match.comments,
            })
        return rows

    def billing_account_reconciliation_rows(self) -> list[dict]:
        excel_by_id = {r.excel_record_id: r for r in self.excel_records}
        pdf_by_id = {r.pdf_record_id: r for r in self.pdf_records}
        rows = []
        for match in self.matches:
            excel = excel_by_id[match.excel_record_id]
            pdfs = [pdf_by_id[i] for i in match.pdf_record_ids]
            agg = aggregate_invoice_records(pdfs)
            rows.append({
                "Excel Filename": excel.source_file,
                "Excel Worksheet": excel.worksheet_name,
                "Excel Row": excel.source_row,
                "Summary Name": excel.customer_name_original,
                "BillingAccount": excel.billing_account_original,
                "Excel Regular": excel.expected_regular_charges,
                "Excel One-off": excel.expected_one_off_charges,
                "Excel Usage": excel.usage,
                "Excel Total": excel.total,
                "PDF Filename(s)": match.pdf_filenames,
                "PDF Account Number(s)": [pdf.account_number_original for pdf in pdfs],
                "PDF Regular": agg["regular_charges"],
                "PDF One-off": agg["one_off_charges"],
                "PDF Usage": agg["usage_charges"],
                "PDF Total Excluding VAT": agg["total_excluding_vat"],
                "Regular Difference": match.regular_difference,
                "One-off Difference": match.one_off_difference,
                "Usage Difference": match.usage_difference,
                "Total Difference": match.total_difference,
                "Overall Match Result": self._business_match_result(match.status, len(pdfs), match.total_difference),
                "Match Type": match.match_type,
                "Match Status": match.status,
                "Group ID": match.group_id,
            })
        for excel in self.missing_excel_records:
            rows.append({
                "Excel Filename": excel.source_file,
                "Excel Worksheet": excel.worksheet_name,
                "Excel Row": excel.source_row,
                "Summary Name": excel.customer_name_original,
                "BillingAccount": excel.billing_account_original,
                "Excel Regular": excel.expected_regular_charges,
                "Excel One-off": excel.expected_one_off_charges,
                "Excel Usage": excel.usage,
                "Excel Total": excel.total,
                "PDF Filename(s)": "",
                "PDF Account Number(s)": "",
                "PDF Regular": "",
                "PDF One-off": "",
                "PDF Usage": "",
                "PDF Total Excluding VAT": "",
                "Regular Difference": "",
                "One-off Difference": "",
                "Usage Difference": "",
                "Total Difference": "",
                "Overall Match Result": "Missing PDF",
                "Match Type": "",
                "Match Status": "Missing in PDF",
                "Group ID": "",
            })
        return rows

    def unused_pdf_rows(self) -> list[dict]:
        return [
            {
                "PDF Filename": record.source_file,
                "PDF Record ID": record.pdf_record_id,
                "Account Number": record.account_number_original,
                "Customer": record.customer_name_original,
                "Invoice Reference": record.invoice_reference_original,
                "Regular Charges": record.regular_charges,
                "One-off Charges": record.one_off_charges,
                "Usage Charges": record.usage_charges,
                "VAT": record.vat_amount,
                "Total Excluding VAT": record.total_excluding_vat,
                "Total Including VAT": record.total_including_vat,
                "Extraction Status": record.extraction_status,
                "Reason": "Unused PDF - no Excel BillingAccount match",
            }
            for record in self.missing_pdf_records
        ]

    @staticmethod
    def _business_match_result(status: str, pdf_count: int, total_difference: Decimal) -> str:
        if "Missing" in status:
            return status
        if "Mismatch" in status:
            return "Mismatch"
        if pdf_count > 1:
            return "Grouped Matched" if total_difference == Decimal("0.00") else "Grouped Mismatch"
        return "Matched" if total_difference == Decimal("0.00") else "Mismatch"

    def grouped_rows(self) -> list[dict]:
        return [row for row in self.detailed_rows() if row["Number of PDFs"] > 1]

    def mapping_rows(self) -> list[dict]:
        excel_by_id = {r.excel_record_id: r for r in self.excel_records}
        pdf_by_id = {r.pdf_record_id: r for r in self.pdf_records}
        rows = []
        for match in self.matches:
            excel = excel_by_id[match.excel_record_id]
            for pdf_id in match.pdf_record_ids:
                pdf = pdf_by_id[pdf_id]
                rows.append({
                    "Group ID": match.group_id,
                    "Excel Record ID": excel.excel_record_id,
                    "Excel Row": excel.source_row,
                    "Billing Account": excel.billing_account_original,
                    "PDF Record ID": pdf.pdf_record_id,
                    "PDF Filename": pdf.source_file,
                    "Invoice Reference": pdf.invoice_reference_original,
                    "Site": pdf.site_name_original,
                    "PDF Total Excluding VAT": pdf.total_excluding_vat,
                    "Assignment Type": match.match_type,
                    "Match Status": match.status,
                })
        return rows

    def charge_mapping_rows(self) -> list[dict]:
        group_by_pdf = {pdf_id: match for match in self.matches for pdf_id in match.pdf_record_ids}
        rows = []
        for line in self.charge_lines:
            match = group_by_pdf.get(line.pdf_record_id)
            rows.append({
                "Group ID": match.group_id if match else "",
                "Excel Record ID": match.excel_record_id if match else "",
                "PDF Filename": line.pdf_filename,
                "PDF Page": line.pdf_page,
                "Original Charge Name": line.charge_name_original,
                "Normalized Charge Name": line.charge_name_normalized,
                "Charge Category": line.charge_category,
                "Line Type": line.line_type,
                "Amount": line.amount,
                "Included in Aggregation": line.included_in_aggregation,
                "Exclusion Reason": line.exclusion_reason,
                "Aggregation Key": str(charge_aggregation_key(line)),
                "Match Status": match.status if match else "Missing in Excel",
            })
        return rows

    def aggregated_charge_rows(self) -> list[dict]:
        rows = []
        grouped_lines: dict[tuple, list[PDFChargeLine]] = defaultdict(list)
        for line in self.charge_lines:
            if line.included_in_aggregation and line.line_type in {"Detail Line", "Section Subtotal"}:
                grouped_lines[charge_aggregation_key(line)].append(line)
        for key, lines in grouped_lines.items():
            rows.append({
                "Group ID": "",
                "Customer": key[0],
                "Billing Account": key[1],
                "Billing Period": f"{key[2]} to {key[3]}",
                "Site": key[4],
                "Normalized Charge Name": key[5],
                "Charge Category": key[6],
                "Number of PDFs": len({line.pdf_filename for line in lines}),
                "Number of Charge Lines": len(lines),
                "PDF Filenames": sorted({line.pdf_filename for line in lines}),
                "Aggregated PDF Amount": sum((line.amount for line in lines), Decimal("0.00")),
                "Excel Expected Amount": "",
                "Difference": "",
                "Status": "Repeated Values Aggregated" if len(lines) > 1 else "Single Occurrence",
            })
        return rows

    def field_mismatch_rows(self) -> list[dict]:
        return [row for row in self.detailed_rows() if "Mismatch" in row["Status"]]

    def duplicate_rows(self) -> list[dict]:
        dups = duplicate_accounts(self.pdf_records, self.excel_records)
        return [{"Record Type": key.split(":", 1)[0], "Normalized Key": key.split(":", 1)[1], "Record IDs": ids} for key, ids in dups.items()]

    def manual_review_rows(self) -> list[dict]:
        rows = [{"Status": "Missing in PDF", **r.asdict()} for r in self.missing_excel_records]
        rows += [{"Status": "Missing in Excel", **r.asdict()} for r in self.missing_pdf_records]
        rows += [m.asdict() for m in self.matches if m.match_confidence != "High" or "Mismatch" in m.status]
        return [_serial(row) for row in rows]

    def error_rows(self) -> list[dict]:
        rows = []
        for pdf in self.pdf_records:
            if pdf.extraction_status != "Extracted":
                rows.append({"Source": pdf.source_file, "Status": pdf.extraction_status, "Warnings": pdf.warnings})
        for excel in self.excel_records:
            if excel.extraction_status != "Extracted":
                rows.append({"Source": excel.source_file, "Status": excel.extraction_status, "Warnings": excel.warnings})
        return rows

    def audit_rows(self) -> list[dict]:
        conn = connect(self.db_path)
        rows = list_audit(conn, self.run_id)
        conn.close()
        return rows

    def run_from_zip(self, zip_path: Path) -> dict[str, Path]:
        self.extract_zip(zip_path)
        self.analyse_and_extract()
        self.reconcile()
        return self.generate_outputs()
