from __future__ import annotations

from pathlib import Path
import logging
import re


# References to source files, which are usually named after the customer:
# quoted names ('Customer Ltd_May.xlsx') and absolute paths (/tmp/x/Customer Ltd.pdf).
_EXT = r"\.(?:pdf|xlsx|xlsm|xls|zip)\b"
QUOTED_FILE = re.compile(r"""(['"])[^'"\n]*""" + _EXT + r"\1", re.IGNORECASE)
PATH_FILE = re.compile(r"""(?:(?<![\w.])/|\b[A-Za-z]:\\)[^'"\n]*?""" + _EXT, re.IGNORECASE)
# Internal record ids such as PDF-0001 / EXCEL-0002 / RUN-2026-07-14-001 carry no customer data.
INTERNAL_ID = re.compile(r"\b(?:PDF|EXCEL|RUN|GROUP|TEST)-[A-Z0-9-]+\b")


def mask_text(text: str) -> str:
    text = QUOTED_FILE.sub(r"\1<file>\1", text)
    text = PATH_FILE.sub("<file>", text)
    kept: list[str] = []

    def _keep(match: re.Match) -> str:
        kept.append(match.group(0))
        return f"\x00{len(kept) - 1}\x00"

    text = INTERNAL_ID.sub(_keep, text)
    text = re.sub(r"\b[A-Z]{2,}[_-][A-Z0-9]{1,}\b", "***", text)
    text = re.sub(r"\b\d{6,}\b", "***", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], text)


class MaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_text(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = mask_text(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def configure_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("invoice_reconciliation")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handler.addFilter(MaskingFilter())
    logger.addHandler(handler)
    return logger

