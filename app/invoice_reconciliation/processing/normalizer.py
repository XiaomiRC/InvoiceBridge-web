from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re


ZERO = Decimal("0.00")


def collapse_spaces(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_customer(value: object) -> str:
    text = collapse_spaces(value).upper()
    text = re.sub(r"[.,]", "", text)
    text = re.sub(r"\bPUBLIC LIMITED COMPANY\b", "PLC", text)
    text = re.sub(r"\bLIMITED\b", "LTD", text)
    return collapse_spaces(text)


def normalize_account(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", collapse_spaces(value).upper())


def normalize_reference(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", collapse_spaces(value).upper())


def normalize_site(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", collapse_spaces(value).upper())


def display_site(value: object) -> str:
    text = collapse_spaces(value).upper().replace("-", "_").replace(" ", "_")
    text = re.sub(r"_+", "_", text)
    return text


def normalize_charge_name(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", collapse_spaces(value).upper())


def parse_amount(value: object) -> Decimal:
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(Decimal("0.01"))
    text = collapse_spaces(value)
    if not text:
        return ZERO
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    if re.search(r"-\s*£|£\s*-|^-", text):
        negative = True
    text = text.replace("GBP", "").replace("£", "")
    text = text.replace("-", "")
    if re.match(r"^\d{1,3}(?:\s\d{3})+,\d{2}$", text):
        text = text.replace(" ", "").replace(",", ".")
    else:
        text = text.replace(" ", "")
    if re.match(r"^\d+\.\d{3},\d{2}$", text):
        text = text.replace(".", "").replace(",", ".")
    elif re.match(r"^\d+,\d{2}$", text):
        text = text.replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        amount = Decimal(text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return ZERO
    return -amount if negative and amount > 0 else amount


def parse_date(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = collapse_spaces(value)
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%d-%b-%y", "%d %b %y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    month_match = re.match(r"^([A-Za-z]+)\s+(\d{4})$", text)
    if month_match:
        try:
            return datetime.strptime("1 " + text, "%d %B %Y").date().isoformat()
        except ValueError:
            pass
    return text


def amount_close(left: Decimal, right: Decimal, tolerance: Decimal = Decimal("0.01")) -> bool:
    return abs(left - right) <= tolerance
