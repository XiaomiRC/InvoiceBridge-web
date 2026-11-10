from __future__ import annotations

from pathlib import Path
import sqlite3


SCHEMA = """
create table if not exists mappings (
  mapping_id text primary key,
  run_id text not null,
  excel_record_id text not null,
  pdf_record_ids text not null,
  mapping_type text not null,
  confirmed_by text,
  confirmed_at text,
  comments text
);
create table if not exists audit_log (
  id integer primary key autoincrement,
  run_id text not null,
  timestamp text not null,
  action text not null,
  excel_record_id text,
  pdf_record_ids text,
  group_id text,
  automatic_or_manual text,
  user_comment text
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn



REQUIRED_TABLES = {"mappings", "audit_log"}
SQLITE_HEADER = b"SQLite format 3\x00"


def export_bytes(db_path: Path) -> bytes:
    """Return the history database as bytes, creating an empty one if needed."""
    connect(db_path).close()
    return db_path.read_bytes()


def import_bytes(db_path: Path, data: bytes) -> None:
    """Replace the history database with ``data`` after checking it is a valid history file."""
    if not data.startswith(SQLITE_HEADER):
        raise ValueError("Not a SQLite history file.")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    candidate = db_path.with_suffix(".import")
    candidate.write_bytes(data)
    try:
        conn = sqlite3.connect(candidate)
        try:
            tables = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
            if not REQUIRED_TABLES <= tables:
                raise ValueError("History file is missing the mappings/audit_log tables.")
            conn.execute("select count(*) from mappings").fetchone()
            conn.execute("select count(*) from audit_log").fetchone()
        except sqlite3.DatabaseError as exc:
            raise ValueError("History file is corrupt or not a SQLite database.") from exc
        finally:
            conn.close()
        candidate.replace(db_path)
    finally:
        if candidate.exists():
            candidate.unlink()
