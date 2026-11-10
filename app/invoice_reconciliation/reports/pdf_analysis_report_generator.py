from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
)
from xml.sax.saxutils import escape


def _money(value: object) -> str:
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    return str(value or "")


def _cell(value: object, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(value or "")), style)


def _table(rows: list[list[object]], widths: list[float] | None = None) -> Table:
    header_style = ParagraphStyle(
        name="TableHeader",
        fontName="Helvetica-Bold",
        fontSize=7,
        leading=8,
        wordWrap="CJK",
        splitLongWords=True,
    )
    body_style = ParagraphStyle(
        name="TableBody",
        fontName="Helvetica",
        fontSize=7,
        leading=8,
        wordWrap="CJK",
        splitLongWords=True,
    )
    wrapped_rows = []
    for row_index, row in enumerate(rows):
        style = header_style if row_index == 0 else body_style
        wrapped_rows.append([_cell(cell, style) for cell in row])
    table = Table(wrapped_rows, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9EAF7")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B7B7B7")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F7F7")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _mask_account(value: str) -> str:
    if not value:
        return ""
    return "***" + value[-3:]


def generate_pdf_analysis_report(
    output_path: Path,
    run_id: str,
    pdf_records,
    excel_records,
    workbook_analysis: list[dict],
    matches,
    missing_excel_records,
    missing_pdf_records,
    summary_rows: list[dict],
    billing_account_rows: list[dict],
    unused_pdf_rows: list[dict],
    report_locations: dict[str, Path],
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=8, leading=10, wordWrap="CJK", splitLongWords=True))
    styles.add(ParagraphStyle(name="Muted", parent=styles["BodyText"], fontSize=8, textColor=colors.HexColor("#555555")))
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(A4),
        rightMargin=1.4 * cm,
        leftMargin=1.4 * cm,
        topMargin=1.3 * cm,
        bottomMargin=1.3 * cm,
        title=f"Invoice Reconciliation Analysis {run_id}",
    )
    story = []
    story.append(Paragraph("Invoice Reconciliation Analysis Report", styles["Title"]))
    story.append(Paragraph(f"Run ID: {run_id}", styles["Heading3"]))
    story.append(Paragraph("Confidential local analysis. Full invoice text and unmasked account identifiers are intentionally excluded.", styles["Muted"]))
    story.append(Spacer(1, 0.35 * cm))

    summary = summary_rows[0] if summary_rows else {}
    story.append(Paragraph("Executive Summary", styles["Heading2"]))
    story.append(
        _table(
            [
                ["Metric", "Value"],
                ["PDFs processed", summary.get("PDFs processed", len(pdf_records))],
                ["Excel workbooks processed", summary.get("Excel files processed", len({r.source_file for r in excel_records}))],
                ["Excel records", summary.get("Excel records", len(excel_records))],
                ["PDF charge lines", summary.get("PDF charge lines", "")],
                ["Fully matched", summary.get("Fully matched count", "")],
                ["Automatic grouped matches", summary.get("Automatic grouped matches", "")],
                ["Missing in PDF", summary.get("Missing in PDF", len(missing_excel_records))],
                ["Missing in Excel", summary.get("Missing in Excel", len(missing_pdf_records))],
                ["Excel total", _money(summary.get("Excel total", ""))],
                ["PDF total excluding VAT", _money(summary.get("PDF total excluding VAT", ""))],
                ["Overall difference", _money(summary.get("Overall difference", ""))],
            ],
            [7 * cm, 8 * cm],
        )
    )
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("Billing Account Reconciliation", styles["Heading2"]))
    story.append(Paragraph("Each row starts from an Excel Summary-sheet BillingAccount and shows the matched PDF file(s), preserving original filenames and source row traceability.", styles["Muted"]))
    account_rows = [
        [
            "Excel filename",
            "Worksheet / row",
            "Name",
            "BillingAccount",
            "Excel total",
            "PDF filename(s)",
            "PDF account(s)",
            "PDF total excl VAT",
            "Difference",
            "Result",
        ]
    ]
    for row in billing_account_rows:
        account_rows.append(
            [
                row.get("Excel Filename", ""),
                f"{row.get('Excel Worksheet', '')} row {row.get('Excel Row', '')}",
                row.get("Summary Name", ""),
                row.get("BillingAccount", ""),
                _money(row.get("Excel Total", "")),
                ", ".join(row.get("PDF Filename(s)", [])) if isinstance(row.get("PDF Filename(s)"), list) else row.get("PDF Filename(s)", ""),
                ", ".join(row.get("PDF Account Number(s)", [])) if isinstance(row.get("PDF Account Number(s)"), list) else row.get("PDF Account Number(s)", ""),
                _money(row.get("PDF Total Excluding VAT", "")),
                _money(row.get("Total Difference", "")),
                row.get("Overall Match Result", ""),
            ]
        )
    story.append(_table(account_rows, [3.6 * cm, 2.4 * cm, 2.7 * cm, 2.2 * cm, 1.6 * cm, 4.3 * cm, 2.4 * cm, 1.8 * cm, 1.5 * cm, 2.5 * cm]))
    story.append(Spacer(1, 0.35 * cm))
    story.append(Paragraph("Unused PDFs", styles["Heading2"]))
    if unused_pdf_rows:
        unused_rows = [["PDF filename", "Account", "Invoice reference", "Total excl VAT", "Status", "Reason"]]
        for row in unused_pdf_rows:
            unused_rows.append(
                [
                    row.get("PDF Filename", ""),
                    row.get("Account Number", ""),
                    row.get("Invoice Reference", ""),
                    _money(row.get("Total Excluding VAT", "")),
                    row.get("Extraction Status", ""),
                    row.get("Reason", ""),
                ]
            )
        story.append(_table(unused_rows, [5.0 * cm, 2.5 * cm, 2.5 * cm, 2.2 * cm, 2.2 * cm, 6.5 * cm]))
    else:
        story.append(Paragraph("No unused PDFs. All PDFs are assigned to Excel records.", styles["BodyText"]))
    story.append(PageBreak())

    story.append(Paragraph("Verified Input Structure", styles["Heading2"]))
    story.append(
        _table(
            [
                ["Finding", "Result"],
                ["PDF count", len(pdf_records)],
                ["Text-based PDFs", sum(1 for p in pdf_records if p.extraction_status == "Extracted")],
                ["OCR required", sum(1 for p in pdf_records if p.extraction_status != "Extracted")],
                ["Distinct PDF layouts", len({p.invoice_template for p in pdf_records if p.invoice_template})],
                ["Excel workbooks", len({r.source_file for r in excel_records})],
                ["Comparison basis", "Excel Total compared with PDF Total excluding VAT"],
                ["Regular mapping", "PDF Regular = Excel Rentals + PriorPeriod"],
                ["One-off mapping", "PDF One-off = Excel One-Time + Connection + Cessation"],
                ["Usage mapping", "PDF Usage = Excel Usage"],
            ],
            [6 * cm, 9 * cm],
        )
    )
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("PDF Inventory", styles["Heading2"]))
    pdf_rows = [["Original PDF filename", "Pages", "Text", "Masked account", "Template", "Pre-VAT total"]]
    for pdf in pdf_records:
        pdf_rows.append([pdf.source_file, pdf.pdf_page_count, pdf.extraction_status, _mask_account(pdf.account_number_original), pdf.invoice_template, _money(pdf.total_excluding_vat)])
    story.append(_table(pdf_rows, [5.2 * cm, 1.2 * cm, 2.2 * cm, 2.3 * cm, 3 * cm, 2.2 * cm]))
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("PDF Invoice Value Traceability", styles["Heading2"]))
    pdf_value_rows = [
        [
            "Original PDF filename",
            "PDF record",
            "Regular",
            "One-off",
            "Usage",
            "VAT",
            "Total excl VAT",
            "Total incl VAT",
        ]
    ]
    for pdf in pdf_records:
        pdf_value_rows.append(
            [
                pdf.source_file,
                pdf.pdf_record_id,
                _money(pdf.regular_charges),
                _money(pdf.one_off_charges),
                _money(pdf.usage_charges),
                _money(pdf.vat_amount),
                _money(pdf.total_excluding_vat),
                _money(pdf.total_including_vat),
            ]
        )
    story.append(_table(pdf_value_rows, [4.8 * cm, 1.8 * cm, 1.7 * cm, 1.7 * cm, 1.5 * cm, 1.5 * cm, 1.9 * cm, 1.9 * cm]))
    story.append(PageBreak())

    story.append(Paragraph("Workbook and Worksheet Analysis", styles["Heading2"]))
    wb_rows = [["Workbook", "Worksheet", "Rows", "Cols", "Hidden", "Merged", "Formula cells", "Header candidates"]]
    for wb in workbook_analysis:
        for ws in wb.get("worksheets", []):
            wb_rows.append(
                [
                    wb.get("workbook", ""),
                    ws.get("name", ""),
                    ws.get("max_row", ""),
                    ws.get("max_column", ""),
                    ws.get("state", ""),
                    ws.get("merged_cells", ""),
                    ws.get("formula_cells", ""),
                    ", ".join(str(v) for v in ws.get("candidate_header_rows", [])),
                ]
            )
    story.append(_table(wb_rows, [3.9 * cm, 3.0 * cm, 1.1 * cm, 1.1 * cm, 1.4 * cm, 1.3 * cm, 1.8 * cm, 3.2 * cm]))
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("Detected Excel Summary Records", styles["Heading2"]))
    excel_rows = [["Record", "Original Excel workbook", "Worksheet", "Row", "Masked account", "Expected regular", "Expected one-off", "Usage", "Total"]]
    for rec in excel_records:
        excel_rows.append([rec.excel_record_id, rec.source_file, rec.worksheet_name, rec.source_row, _mask_account(rec.billing_account_original), _money(rec.expected_regular_charges), _money(rec.expected_one_off_charges), _money(rec.usage), _money(rec.total)])
    story.append(_table(excel_rows, [1.8 * cm, 4.0 * cm, 1.8 * cm, 0.9 * cm, 2.0 * cm, 1.8 * cm, 1.8 * cm, 1.4 * cm, 1.5 * cm]))
    story.append(Paragraph("Reconciliation Results", styles["Heading2"]))
    excel_by_id = {rec.excel_record_id: rec for rec in excel_records}
    match_rows = [["Status", "Group ID", "Excel source", "Original PDF filename(s)", "Confidence", "Regular diff", "One-off diff", "Usage diff", "Total diff"]]
    for match in matches:
        excel = excel_by_id.get(match.excel_record_id)
        excel_source = f"{excel.source_file} / {excel.worksheet_name} row {excel.source_row}" if excel else match.excel_record_id
        match_rows.append([match.status, match.group_id, excel_source, ", ".join(match.pdf_filenames), match.match_confidence, _money(match.regular_difference), _money(match.one_off_difference), _money(match.usage_difference), _money(match.total_difference)])
    story.append(_table(match_rows, [2.7 * cm, 3.2 * cm, 3.6 * cm, 4.0 * cm, 1.3 * cm, 1.3 * cm, 1.3 * cm, 1.2 * cm, 1.2 * cm]))
    story.append(Spacer(1, 0.4 * cm))

    grouped = [m for m in matches if len(m.pdf_record_ids) > 1]
    story.append(Paragraph("Grouped Match Detail", styles["Heading2"]))
    if grouped:
        for match in grouped:
            story.append(Paragraph(f"{match.group_id}: {match.status}", styles["Heading3"]))
            story.append(Paragraph(f"Excel record: {match.excel_record_id}. PDFs: {', '.join(match.pdf_filenames)}", styles["Small"]))
            story.append(
                _table(
                    [
                        ["Comparison", "Difference"],
                        ["Regular charges", _money(match.regular_difference)],
                        ["One-off charges", _money(match.one_off_difference)],
                        ["Usage charges", _money(match.usage_difference)],
                        ["Total excluding VAT", _money(match.total_difference)],
                    ],
                    [7 * cm, 4 * cm],
                )
            )
            story.append(Spacer(1, 0.25 * cm))
    else:
        story.append(Paragraph("No grouped matches were required.", styles["BodyText"]))

    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("Exception Summary", styles["Heading2"]))
    story.append(
        _table(
            [
                ["Exception type", "Count"],
                ["Missing in PDF", len(missing_excel_records)],
                ["Unused PDFs", len(unused_pdf_rows)],
                ["Extraction failures", sum(1 for p in pdf_records if p.extraction_status != "Extracted")],
                ["Unresolved mismatches", sum(1 for m in matches if "Mismatch" in m.status)],
            ],
            [7 * cm, 3 * cm],
        )
    )

    story.append(PageBreak())
    story.append(Paragraph("Generated Local Artifacts", styles["Heading2"]))
    location_rows = [["Artifact", "Path"]]
    for name, path in report_locations.items():
        location_rows.append([name, str(path)])
    story.append(_table(location_rows, [4 * cm, 12 * cm]))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("No external services were used to process document contents.", styles["Heading3"]))

    doc.build(story)
    return output_path
