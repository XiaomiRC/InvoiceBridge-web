from __future__ import annotations

from datetime import datetime
import json
import sqlite3


def save_mapping(conn: sqlite3.Connection, mapping_id: str, run_id: str, excel_record_id: str, pdf_record_ids: list[str], mapping_type: str, comments: str = "") -> None:
    conn.execute(
        """
        insert or replace into mappings
        (mapping_id, run_id, excel_record_id, pdf_record_ids, mapping_type, confirmed_by, confirmed_at, comments)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (mapping_id, run_id, excel_record_id, json.dumps(pdf_record_ids), mapping_type, "local_user", datetime.now().isoformat(timespec="seconds"), comments),
    )
    conn.commit()


def list_mappings(conn: sqlite3.Connection, run_id: str | None = None) -> list[dict]:
    cur = conn.execute("select * from mappings where (? is null or run_id = ?)", (run_id, run_id))
    cols = [d[0] for d in cur.description]
    rows = []
    for row in cur.fetchall():
        item = dict(zip(cols, row))
        item["pdf_record_ids"] = json.loads(item["pdf_record_ids"])
        rows.append(item)
    return rows

