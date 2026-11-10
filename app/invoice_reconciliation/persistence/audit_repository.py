from __future__ import annotations

from datetime import datetime
import json
import sqlite3


def audit(conn: sqlite3.Connection, run_id: str, action: str, excel_record_id: str = "", pdf_record_ids: list[str] | None = None, group_id: str = "", mode: str = "Automatic", comment: str = "") -> None:
    conn.execute(
        """
        insert into audit_log
        (run_id, timestamp, action, excel_record_id, pdf_record_ids, group_id, automatic_or_manual, user_comment)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, datetime.now().isoformat(timespec="seconds"), action, excel_record_id, json.dumps(pdf_record_ids or []), group_id, mode, comment),
    )
    conn.commit()


def list_audit(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    cur = conn.execute("select run_id,timestamp,action,excel_record_id,pdf_record_ids,group_id,automatic_or_manual,user_comment from audit_log where run_id = ?", (run_id,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]

