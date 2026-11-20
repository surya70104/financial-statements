"""Shared reading and validation helpers for the WP9 registers (TDS/TCS pack, FD register): .xlsx and .csv files,
header aliases, exact-paise amounts, dates. Nothing here touches the database."""
import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException

PAISA = Decimal("0.01")


def _norm(h) -> str:
    return " ".join(str(h or "").replace("﻿", "").replace("_", " ").split()).casefold()


def fail(message: str, errors: list | None = None):
    raise HTTPException(422, {"message": message, "errors": errors or []})


def read_table(filename: str, data: bytes, max_rows: int = 2000) -> list:
    """Return [(excel_row_number, {normalised header: value})] from the first sheet / the CSV. Blank rows are skipped."""
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook
        try:
            ws = load_workbook(io.BytesIO(data), data_only=True).worksheets[0]
        except Exception:
            fail("The file could not be read as an Excel workbook (.xlsx).")
        rows = []
        for r in ws.iter_rows():
            vals = []
            for c in r:
                v = c.value
                if isinstance(v, (int, float)) and not isinstance(v, bool) and "%" in (c.number_format or ""):
                    v = str(Decimal(str(v)) * 100)                     # a cell formatted as 12.5% holds 0.125
                vals.append(v)
            rows.append((r[0].row if r else 0, vals))
    elif name.endswith(".csv"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        rows = [(i, row) for i, row in enumerate(csv.reader(io.StringIO(text)), 1)]
    else:
        fail("Upload an Excel (.xlsx) or CSV (.csv) file.")
    rows = [(n, v) for n, v in rows if any(str(x).strip() for x in v if x is not None)]
    if len(rows) < 2:
        fail("The file has no data rows.")
    if len(rows) - 1 > max_rows:
        fail(f"The file has more than {max_rows} rows.")
    headers = [_norm(h) for h in rows[0][1]]
    return [(n, {headers[i]: (v[i] if i < len(v) else None) for i in range(len(headers)) if headers[i]}) for n, v in rows[1:]]


def pick(row: dict, *aliases):
    """First non-blank value among the header aliases (already normalised, e.g. 'party name')."""
    for a in aliases:
        v = row.get(_norm(a))
        if v is not None and str(v).strip() != "":
            return v
    return None


def money(value, label: str, required: bool = True, positive: bool = True) -> Decimal | None:
    if value is None or str(value).strip() == "":
        if required:
            raise ValueError(f"{label} is missing.")
        return None
    try:
        d = Decimal(str(value).replace(",", "").replace("₹", "").strip())
    except InvalidOperation:
        raise ValueError(f"{label}: '{value}' is not a number.")
    if not d.is_finite() or d != d.quantize(PAISA):
        raise ValueError(f"{label}: '{value}' must be exact to the paisa (2 decimals).")
    if (d <= 0) if positive else (d < 0):
        raise ValueError(f"{label} must be {'greater than' if positive else 'at least'} zero.")
    return d.quantize(PAISA)


def rate(value, label: str, places: str) -> Decimal:
    """A percentage as a plain number (12.5 means 12.5%), at most `places` decimals, 0 < rate <= 100."""
    if value is None or str(value).strip() == "":
        raise ValueError(f"{label} is missing.")
    try:
        d = Decimal(str(value).replace("%", "").strip())
    except InvalidOperation:
        raise ValueError(f"{label}: '{value}' is not a number.")
    q = Decimal(places)
    if not d.is_finite() or d != d.quantize(q):
        raise ValueError(f"{label}: '{value}' has too many decimals.")
    if d <= 0 or d > 100:
        raise ValueError(f"{label} must be above 0 and at most 100.")
    return d.quantize(q)


def when(value, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is missing.")
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"{label}: '{text}' is not a date (use YYYY-MM-DD or DD-MM-YYYY).")


def text(value, label: str, required: bool = True, maxlen: int = 200) -> str | None:
    if isinstance(value, float) and value.is_integer():
        value = int(value)                                     # an FD number typed as a number in Excel
    t = " ".join(str(value if value is not None else "").split())
    if not t:
        if required:
            raise ValueError(f"{label} is missing.")
        return None
    if len(t) > maxlen:
        raise ValueError(f"{label} is too long ({maxlen} characters at most).")
    return t


def signed_money(value, label: str) -> Decimal:
    """An amount that may be negative or zero (differences, GST figures), exact to the paisa."""
    if value is None or str(value).strip() == "":
        raise ValueError(f"{label} is missing.")
    s = str(value).replace(",", "").replace("₹", "").strip()
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]                                          # accounting style (1,234.00)
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"{label}: '{value}' is not a number.")
    if not d.is_finite() or d != d.quantize(PAISA):
        raise ValueError(f"{label}: '{value}' must be exact to the paisa (2 decimals).")
    return d.quantize(PAISA)
