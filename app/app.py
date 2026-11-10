from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import mimetypes
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

try:
    import streamlit as st
except Exception:  # pragma: no cover
    st = None

from invoice_reconciliation.persistence.database import export_bytes, import_bytes
from invoice_reconciliation.processing.reconciliation_service import ReconciliationRun


PROJECT_ROOT = Path(__file__).resolve().parent
# Browser build (stlite/Pyodide): everything runs in the tab, files live in an in-memory filesystem.
IN_BROWSER = sys.platform == "emscripten"
DEMO_DATA = not IN_BROWSER and os.environ.get("INVOICE_RECON_DEMO_DATA", "").strip() in {"1", "true", "yes"}
HISTORY_DB_PATH = PROJECT_ROOT / "data" / "database" / "reconciliation.sqlite"


def _maybe_anonymize(run: ReconciliationRun) -> ReconciliationRun:
    """For product demos/videos: replace identity fields with dummy data; keep amounts."""
    if not DEMO_DATA:
        return run
    import sys

    sys.path.insert(0, str(PROJECT_ROOT))
    from video_assets.anonymize_run import anonymize_run

    anonymize_run(run)
    return run


OUTPUT_CATEGORIES = {
    ".pdf": "PDF report",
    ".xlsx": "Excel report",
    ".md": "Markdown analysis",
    ".json": "Data table",
}

MIME_TYPES = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".md": "text/markdown",
    ".json": "application/json",
}


def mime_type_for(path: Path) -> str:
    return MIME_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def human_file_size(size_bytes: int) -> str:
    value = Decimal(size_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < Decimal("1024") or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value.quantize(Decimal('0.1'))} {unit}"
        value = value / Decimal("1024")
    return f"{size_bytes} B"


def collect_generated_files(run: ReconciliationRun) -> list[dict]:
    output_dir = run.output_dir.resolve()
    if not output_dir.exists():
        return []
    files: list[dict] = []
    demo_name = None
    if DEMO_DATA:
        from video_assets.anonymize_run import demo_generated_filename

        demo_name = demo_generated_filename
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file():
            continue
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(output_dir)
        except ValueError:
            continue
        suffix = resolved.suffix.lower()
        if suffix not in OUTPUT_CATEGORIES:
            continue
        stat = resolved.stat()
        display_name = demo_name(resolved.name, run) if demo_name else resolved.name
        display_relative = demo_name(str(relative), run) if demo_name else str(relative)
        files.append(
            {
                "filename": display_name,
                "category": OUTPUT_CATEGORIES[suffix],
                "extension": suffix,
                "size_bytes": stat.st_size,
                "size": human_file_size(stat.st_size),
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "path": resolved,
                "relative_path": display_relative,
                "mime_type": mime_type_for(resolved),
            }
        )
    category_order = {"PDF report": 0, "Excel report": 1, "Markdown analysis": 2, "Data table": 3}
    return sorted(files, key=lambda item: (category_order.get(item["category"], 99), item["filename"]))


def render_download_button(file_info: dict, key_prefix: str) -> None:
    path = Path(file_info["path"])
    st.download_button(
        label=f"Download {file_info['filename']}",
        data=path.read_bytes(),
        file_name=file_info["filename"],
        mime=file_info["mime_type"],
        key=f"{key_prefix}-{file_info['relative_path']}",
    )


def render_generated_files(run: ReconciliationRun, outputs: dict | None = None) -> None:
    st.subheader("Generated files")
    st.caption("Only generated output files are shown here. Uploaded ZIPs and extracted source PDFs/Excel files are not exposed.")
    files = collect_generated_files(run)
    if not files:
        st.warning("No generated files were found for this run.")
        return
    st.dataframe(
        [
            {
                "Filename": item["filename"],
                "Category": item["category"],
                "Type": item["extension"],
                "Size": item["size"],
                "Modified": item["modified"],
            }
            for item in files
        ],
        use_container_width=True,
        hide_index=True,
    )
    for item in files:
        with st.container(border=True):
            cols = st.columns([4, 1, 2])
            cols[0].markdown(f"**{item['filename']}**")
            cols[0].caption(f"{item['category']} · {item['relative_path']}")
            cols[1].write(item["size"])
            with cols[2]:
                render_download_button(item, run.run_id)


def render_browser_privacy_sidebar() -> None:
    """Browser build only: privacy notice plus history download/restore (nothing is stored in the browser)."""
    st.sidebar.info(
        "Runs entirely in your browser. Uploaded files never leave this device. "
        "Closing or reloading the tab clears everything, so download your history to keep it."
    )
    st.sidebar.subheader("History")
    # Reserve the download slot first, but fill it after any restore so it always has current data.
    download_slot = st.sidebar.container()
    history_file = st.sidebar.file_uploader("Load history", type=["sqlite"], key="history_upload")
    if history_file is not None and st.sidebar.button("Restore history"):
        try:
            import_bytes(HISTORY_DB_PATH, history_file.getvalue())
        except ValueError as exc:
            st.sidebar.error(str(exc))
        else:
            st.sidebar.success("History restored.")
    download_slot.download_button(
        "Download history",
        data=export_bytes(HISTORY_DB_PATH),
        file_name=f"invoicebridge-history-{datetime.now():%Y-%m-%d}.sqlite",
        mime="application/vnd.sqlite3",
        help="Manual matches and audit log from this session (and any history you loaded).",
    )


def decimal_display(value: object) -> str:
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    try:
        return f"{Decimal(str(value)):,.2f}"
    except Exception:
        return str(value or "")


def public_pdf_rows(run: ReconciliationRun) -> list[dict]:
    return [
        {
            "PDF Record ID": record.pdf_record_id,
            "Filename": record.source_file,
            "Customer": record.customer_name_original,
            "Account Number": record.account_number_original,
            "Invoice Reference": record.invoice_reference_original,
            "Regular Charges": record.regular_charges,
            "One-off Charges": record.one_off_charges,
            "Usage Charges": record.usage_charges,
            "VAT": record.vat_amount,
            "Total Excluding VAT": record.total_excluding_vat,
            "Total Including VAT": record.total_including_vat,
            "Status": record.extraction_status,
            "Warnings": record.warnings,
        }
        for record in run.pdf_records
    ]


def public_charge_line_rows(run: ReconciliationRun) -> list[dict]:
    return [
        {
            "Charge Line ID": line.charge_line_id,
            "PDF Record ID": line.pdf_record_id,
            "PDF Filename": line.pdf_filename,
            "Page": line.pdf_page,
            "Billing Account": line.billing_account_original,
            "Original Charge Name": line.charge_name_original,
            "Normalized Charge Name": line.charge_name_normalized,
            "Charge Category": line.charge_category,
            "Line Type": line.line_type,
            "Amount": line.amount,
            "Included": line.included_in_aggregation,
            "Exclusion Reason": line.exclusion_reason,
        }
        for line in run.charge_lines
    ]


def public_excel_rows(run: ReconciliationRun) -> list[dict]:
    return [
        {
            "Excel Record ID": record.excel_record_id,
            "Workbook": record.source_file,
            "Worksheet": record.worksheet_name,
            "Row": record.source_row,
            "Customer": record.customer_name_original,
            "BillingAccount": record.billing_account_original,
            "Rentals": record.rentals,
            "PriorPeriod": record.prior_period,
            "Expected Regular": record.expected_regular_charges,
            "One-Time": record.one_time,
            "Connection": record.connection,
            "Cessation": record.cessation,
            "Expected One-off": record.expected_one_off_charges,
            "Usage": record.usage,
            "Total": record.total,
            "Status": record.extraction_status,
            "Warnings": record.warnings,
        }
        for record in run.excel_records
    ]


def ui_billing_account_rows(run: ReconciliationRun) -> list[dict]:
    rows = []
    for row in run.billing_account_reconciliation_rows():
        rows.append(
            {
                "Result": row.get("Overall Match Result", ""),
                "BillingAccount": row.get("BillingAccount", ""),
                "Summary Name": row.get("Summary Name", ""),
                "Excel Filename": row.get("Excel Filename", ""),
                "Worksheet / Row": f"{row.get('Excel Worksheet', '')} row {row.get('Excel Row', '')}",
                "Excel Regular": row.get("Excel Regular", ""),
                "PDF Regular": row.get("PDF Regular", ""),
                "Regular Diff": row.get("Regular Difference", ""),
                "Excel One-off": row.get("Excel One-off", ""),
                "PDF One-off": row.get("PDF One-off", ""),
                "One-off Diff": row.get("One-off Difference", ""),
                "Excel Usage": row.get("Excel Usage", ""),
                "PDF Usage": row.get("PDF Usage", ""),
                "Usage Diff": row.get("Usage Difference", ""),
                "Excel Total": row.get("Excel Total", ""),
                "PDF Total Excl VAT": row.get("PDF Total Excluding VAT", ""),
                "Total Diff": row.get("Total Difference", ""),
                "PDF Filename(s)": ", ".join(row.get("PDF Filename(s)", [])) if isinstance(row.get("PDF Filename(s)"), list) else row.get("PDF Filename(s)", ""),
                "PDF Account(s)": ", ".join(row.get("PDF Account Number(s)", [])) if isinstance(row.get("PDF Account Number(s)"), list) else row.get("PDF Account Number(s)", ""),
                "Match Status": row.get("Match Status", ""),
                "Group ID": row.get("Group ID", ""),
            }
        )
    return rows


def unused_pdf_rows(run: ReconciliationRun) -> list[dict]:
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
        for record in run.missing_pdf_records
    ]


def excel_option_label(record) -> str:
    return (
        f"{record.billing_account_original} | {record.customer_name_original} | "
        f"{record.source_file} | {record.worksheet_name} row {record.source_row} | "
        f"Excel total {decimal_display(record.total)}"
    )


def pdf_option_label(record) -> str:
    return (
        f"{record.account_number_original} | {record.source_file} | "
        f"PDF total excl VAT {decimal_display(record.total_excluding_vat)}"
    )


def render_billing_account_reconciliation(run: ReconciliationRun) -> None:
    st.subheader("Billing Account Reconciliation")
    st.caption("One row per Excel Summary BillingAccount, matched against PDF account numbers and pre-VAT invoice totals.")
    rows = ui_billing_account_rows(run)
    if not rows:
        st.warning("No reconciliation rows are available for this run.")
        return
    result_options = sorted({row["Result"] for row in rows if row["Result"]})
    cols = st.columns([1, 1, 1, 1])
    selected_result = cols[0].selectbox("Result", ["All", *result_options])
    account_filter = cols[1].text_input("BillingAccount")
    excel_filter = cols[2].text_input("Excel filename")
    pdf_filter = cols[3].text_input("PDF filename")
    grouped_only = st.checkbox("Grouped matches only")
    filtered = rows
    if selected_result != "All":
        filtered = [row for row in filtered if row["Result"] == selected_result]
    if account_filter:
        filtered = [row for row in filtered if account_filter.lower() in str(row["BillingAccount"]).lower()]
    if excel_filter:
        filtered = [row for row in filtered if excel_filter.lower() in str(row["Excel Filename"]).lower()]
    if pdf_filter:
        filtered = [row for row in filtered if pdf_filter.lower() in str(row["PDF Filename(s)"]).lower()]
    if grouped_only:
        filtered = [row for row in filtered if "Grouped" in str(row["Result"])]
    st.dataframe(filtered, use_container_width=True, hide_index=True)
    st.subheader("Unused PDFs")
    unused = unused_pdf_rows(run)
    if unused:
        st.warning(f"Unused PDFs: {len(unused)}")
        st.dataframe(unused, use_container_width=True, hide_index=True)
    else:
        st.success("No unused PDFs. All PDFs are assigned to Excel records.")


def render_manual_review(run: ReconciliationRun) -> None:
    st.subheader("Manual Review")
    st.caption("Use business labels to compare one Excel Summary BillingAccount with one or more PDF invoices.")
    if not run.excel_records or not run.pdf_records:
        st.info("No Excel or PDF records are available.")
        return
    excel_by_label = {excel_option_label(record): record for record in run.excel_records}
    pdf_by_label = {pdf_option_label(record): record for record in run.pdf_records}
    selected_excel_label = st.selectbox("Excel Summary record", list(excel_by_label))
    selected_pdf_labels = st.multiselect("PDF candidate invoice(s)", list(pdf_by_label))
    excel = excel_by_label[selected_excel_label]
    selected_pdfs = [pdf_by_label[label] for label in selected_pdf_labels]
    left, right = st.columns(2)
    with left:
        st.markdown("**Selected Excel Summary row**")
        st.dataframe(
            [
                {
                    "Workbook": excel.source_file,
                    "Worksheet": excel.worksheet_name,
                    "Row": excel.source_row,
                    "BillingAccount": excel.billing_account_original,
                    "Customer": excel.customer_name_original,
                    "Expected Regular": excel.expected_regular_charges,
                    "Expected One-off": excel.expected_one_off_charges,
                    "Usage": excel.usage,
                    "Total": excel.total,
                }
            ],
            hide_index=True,
            use_container_width=True,
        )
    with right:
        st.markdown("**Selected PDF invoice(s)**")
        st.dataframe(
            [
                {
                    "Filename": pdf.source_file,
                    "Account": pdf.account_number_original,
                    "Regular": pdf.regular_charges,
                    "One-off": pdf.one_off_charges,
                    "Usage": pdf.usage_charges,
                    "Total Excl VAT": pdf.total_excluding_vat,
                }
                for pdf in selected_pdfs
            ],
            hide_index=True,
            use_container_width=True,
        )
    if selected_pdfs:
        from invoice_reconciliation.processing.charge_aggregator import aggregate_invoice_records

        agg = aggregate_invoice_records(selected_pdfs)
        comparison = [
            {
                "Category": "Regular",
                "Excel": excel.expected_regular_charges,
                "Selected PDF": agg["regular_charges"],
                "Difference": agg["regular_charges"] - excel.expected_regular_charges,
            },
            {
                "Category": "One-off",
                "Excel": excel.expected_one_off_charges,
                "Selected PDF": agg["one_off_charges"],
                "Difference": agg["one_off_charges"] - excel.expected_one_off_charges,
            },
            {
                "Category": "Usage",
                "Excel": excel.usage,
                "Selected PDF": agg["usage_charges"],
                "Difference": agg["usage_charges"] - excel.usage,
            },
            {
                "Category": "Total excluding VAT",
                "Excel": excel.total,
                "Selected PDF": agg["total_excluding_vat"],
                "Difference": agg["total_excluding_vat"] - excel.total,
            },
        ]
        st.markdown("**Live selected total comparison**")
        st.dataframe(comparison, hide_index=True, use_container_width=True)
        st.caption(f"Audit IDs: Excel {excel.excel_record_id}; PDFs {', '.join(pdf.pdf_record_id for pdf in selected_pdfs)}")
        comments = st.text_area("Review comment", placeholder="Optional reason for this manual match")
        button_label = "Confirm Grouped Match" if len(selected_pdfs) > 1 else "Confirm Match"
        if st.button(button_label, type="primary"):
            try:
                match = run.confirm_manual_match(
                    excel.excel_record_id,
                    [pdf.pdf_record_id for pdf in selected_pdfs],
                    comments=comments,
                )
                st.success(f"Saved {match.status}. The Billing Account Reconciliation table and generated reports were updated.")
                st.dataframe(ui_billing_account_rows(run), use_container_width=True, hide_index=True)
            except Exception as exc:
                st.error(f"Could not save manual match: {exc}")


def run_cli(zip_path: str) -> None:
    run = ReconciliationRun(PROJECT_ROOT)
    outputs = run.run_from_zip(PROJECT_ROOT / zip_path)
    print(f"Run ID: {run.run_id}")
    print(f"Analysis: {outputs['analysis']}")
    print(f"PDF analysis: {outputs['pdf_analysis']}")
    print(f"Report: {outputs['report']}")


def streamlit_app() -> None:
    if st is None:
        raise SystemExit("Streamlit is not installed. Install requirements or run `python app.py` for CLI processing.")
    st.set_page_config(page_title="InvoiceBridge", layout="wide")
    st.title("InvoiceBridge")
    pages = [
        "Upload",
        "Billing Account Reconciliation",
        "Manual Matching",
        "Generated Files",
        "Audit / Advanced",
    ]
    page = st.sidebar.radio("Page", pages)
    if "run" not in st.session_state:
        st.session_state.run = None
        st.session_state.outputs = None
    if page == "Upload":
        uploaded = st.file_uploader("Upload ZIP", type=["zip"])
        if st.button("Start analysis and reconciliation"):
            if uploaded is None:
                st.error("Upload a ZIP file to start reconciliation.")
            else:
                filename = Path(uploaded.name.replace("\\", "/")).name
                source = PROJECT_ROOT / "data" / "source" / uuid4().hex / filename
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(uploaded.getbuffer())
                run = ReconciliationRun(PROJECT_ROOT)
                outputs = run.run_from_zip(source)
                if IN_BROWSER:
                    # Free tab memory: source files are not needed once records are extracted.
                    shutil.rmtree(source.parent, ignore_errors=True)
                    shutil.rmtree(run.extracted_dir, ignore_errors=True)
                run = _maybe_anonymize(run)
                st.session_state.run = run
                st.session_state.outputs = outputs
                st.success(f"Completed {run.run_id}")
                render_billing_account_reconciliation(run)
    if IN_BROWSER:
        # Rendered after any run on this rerun, so "Download history" includes it.
        render_browser_privacy_sidebar()
    run = st.session_state.run
    if not run:
        st.info("Start a run from the Upload page.")
        return
    if page == "Billing Account Reconciliation":
        render_billing_account_reconciliation(run)
    elif page == "Manual Matching":
        render_manual_review(run)
    elif page == "Generated Files":
        render_generated_files(run, st.session_state.outputs)
    elif page == "Audit / Advanced":
        tab_summary, tab_pdf, tab_charges, tab_excel, tab_matches, tab_analysis, tab_config = st.tabs(
            ["Summary", "PDF Records", "Charge Lines", "Excel Records", "Matches", "Analysis", "Configuration"]
        )
        with tab_summary:
            st.dataframe(run.summary_rows(), use_container_width=True)
        with tab_pdf:
            st.dataframe(public_pdf_rows(run), use_container_width=True, hide_index=True)
        with tab_charges:
            st.dataframe(public_charge_line_rows(run), use_container_width=True, hide_index=True)
        with tab_excel:
            st.dataframe(public_excel_rows(run), use_container_width=True, hide_index=True)
        with tab_matches:
            st.dataframe(run.detailed_rows(), use_container_width=True, hide_index=True)
            st.subheader("Exceptions")
            st.dataframe(run.manual_review_rows(), use_container_width=True, hide_index=True)
            st.subheader("Repeated Charge Review")
            st.dataframe(run.aggregated_charge_rows(), use_container_width=True, hide_index=True)
        with tab_analysis:
            demo_text = getattr(run, "_demo_analysis_text", None)
            if demo_text is not None:
                st.markdown(demo_text)
            else:
                st.markdown((run.output_dir / "analysis_findings.md").read_text(encoding="utf-8"))
        with tab_config:
            for cfg in sorted((PROJECT_ROOT / "invoice_reconciliation" / "config").glob("*.json")):
                st.subheader(cfg.name)
                st.json(cfg.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import sys
    if st is None or len(sys.argv) > 1:
        if len(sys.argv) < 2:
            raise SystemExit("Usage: python app.py /path/to/invoices.zip")
        run_cli(sys.argv[1])
    else:
        streamlit_app()
