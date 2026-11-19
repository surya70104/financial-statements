"""Parse and validate a Tally trial balance file (CSV or XLSX). Pure functions: no database access.

Sign convention everywhere: Debit = positive, Credit = negative.
Header names are matched through the import profile's column_map (profile data, not code: open question Q3).
"""
import csv
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

TWO = Decimal("0.01")

# Tally primary and common groups -> accounting nature. Unknown groups are inferred from the closing sign.
GROUP_NATURE = {
    "capital account": "LIABILITY", "reserves & surplus": "LIABILITY", "reserves and surplus": "LIABILITY",
    "secured loans": "LIABILITY", "unsecured loans": "LIABILITY", "loans (liability)": "LIABILITY",
    "bank od a/c": "LIABILITY", "bank occ a/c": "LIABILITY", "sundry creditors": "LIABILITY",
    "duties & taxes": "LIABILITY", "provisions": "LIABILITY", "current liabilities": "LIABILITY",
    "deferred tax": "LIABILITY", "stock reserve": "LIABILITY", "retained earnings": "LIABILITY",
    "fixed assets": "ASSET", "investments": "ASSET", "deposits (asset)": "ASSET",
    "loans & advances (asset)": "ASSET", "current assets": "ASSET", "sundry debtors": "ASSET",
    "bank accounts": "ASSET", "cash-in-hand": "ASSET", "stock-in-hand": "ASSET", "misc. expenses (asset)": "ASSET",
    "sales accounts": "INCOME", "direct incomes": "INCOME", "indirect incomes": "INCOME",
    "purchase accounts": "EXPENSE", "direct expenses": "EXPENSE", "indirect expenses": "EXPENSE",
}

SKIP_ROW_NAMES = {"total", "grand total", "totals"}


class ImportFileError(Exception):
    """File cannot be read at all (bad format, missing columns)."""


@dataclass
class TBRow:
    row_no: int
    name: str
    key: str
    group: str
    nature: str
    opening: Decimal
    debit: Decimal
    credit: Decimal
    closing: Decimal
    closing_per_source: Decimal | None
    raw: dict
    notes: list = field(default_factory=list)


@dataclass
class ParseResult:
    rows: list
    errors: list          # [{"row": int, "ledger": str, "message": str}]
    mismatch_count: int
    total_debit: Decimal
    total_credit: Decimal


def normalise_key(name: str) -> str:
    return " ".join(name.split()).casefold()


def _norm_header(h) -> str:
    return " ".join(str(h or "").replace("﻿", "").split()).casefold()


_AMOUNT_RE = re.compile(r"([-+]?)(\()?([0-9]*\.?[0-9]+)\)?\s*(dr|cr)?", re.I)


def parse_amount(value, type_hint: str | None = None) -> Decimal:
    """Return a signed Decimal (Dr +, Cr -) quantised to 2 places. Blank -> 0."""
    if value is None:
        return Decimal("0.00")
    if isinstance(value, bool):
        raise ValueError("not a number")
    suffix = None
    if isinstance(value, (int, Decimal)):
        d = Decimal(value)
    elif isinstance(value, float):
        d = Decimal(repr(value))
    else:
        s = str(value).strip().replace("₹", "").replace("Rs.", "").replace(",", "").strip()
        if s == "":
            return Decimal("0.00")
        m = _AMOUNT_RE.fullmatch(s)
        if not m:
            raise ValueError(f"'{value}' is not a valid amount")
        d = Decimal(m.group(3))
        if m.group(1) == "-" or m.group(2):
            d = -d
        suffix = (m.group(4) or "").lower() or None
    dtype = suffix or (type_hint or "").strip().lower() or None
    if dtype in ("dr", "debit"):
        pass
    elif dtype in ("cr", "credit"):
        d = -d
    elif dtype:
        raise ValueError(f"'{type_hint}' is not Dr or Cr")
    try:
        q = d.quantize(TWO)
    except InvalidOperation:
        raise ValueError(f"'{value}' is too large")
    if q != d:
        raise ValueError(f"'{value}' has more than 2 decimal places")
    return q


def read_table(filename: str, data: bytes) -> tuple[list, list]:
    """Return (headers, [(row_no, [cells...]), ...]) for the first sheet / the CSV."""
    name = filename.lower()
    if name.endswith(".csv"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252")
        raw_rows = list(csv.reader(io.StringIO(text)))
    elif name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:
            raise ImportFileError(f"Cannot open the Excel file: {exc}")
        raw_rows = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    else:
        raise ImportFileError("Unsupported file type. Upload a .csv or .xlsx file (re-save .xls files as .xlsx).")
    numbered = [(i + 1, r) for i, r in enumerate(raw_rows)]
    for idx, (_, r) in enumerate(numbered):
        if any(c not in (None, "") for c in r):
            return r, numbered[idx + 1:]
    raise ImportFileError("The file is empty.")


def _resolve_columns(headers: list, column_map: dict) -> dict:
    index = {}
    for i, h in enumerate(headers):
        index.setdefault(_norm_header(h), i)
    cols = {}
    for fld, aliases in column_map.items():
        for a in aliases:
            if a in index:
                cols[fld] = index[a]
                break
    missing = [f for f in ("ledger", "group") if f not in cols]
    if missing or not ({"closing"} <= set(cols) or {"opening", "debit", "credit"} & set(cols)):
        raise ImportFileError(
            "Required columns not found. Need a ledger name, a group and a closing balance (or opening/debit/credit). "
            f"Headers seen: {[str(h) for h in headers if h not in (None, '')]}")
    return cols


def parse_trial_balance(filename: str, data: bytes, column_map: dict) -> ParseResult:
    headers, body = read_table(filename, data)
    cols = _resolve_columns(headers, column_map)
    header_names = [str(h) if h is not None else "" for h in headers]
    has_movement = bool({"opening", "debit", "credit"} & set(cols))
    errors, rows, seen = [], [], {}
    mismatches = 0

    def cell(r, fld):
        i = cols.get(fld)
        return r[i] if i is not None and i < len(r) else None

    for row_no, r in body:
        if not any(c not in (None, "") for c in r):
            continue
        name = str(cell(r, "ledger") or "").strip()
        if name.casefold() in SKIP_ROW_NAMES:
            continue
        raw = {header_names[i]: ("" if c is None else str(c)) for i, c in enumerate(r) if i < len(header_names)}
        if not name:
            errors.append({"row": row_no, "ledger": "", "message": "Ledger name is blank."})
            continue
        key = normalise_key(name)
        if key in seen:
            errors.append({"row": row_no, "ledger": name, "message": f"Duplicate ledger (first seen on row {seen[key]})."})
            continue
        seen[key] = row_no
        group = str(cell(r, "group") or "").strip()
        if not group:
            errors.append({"row": row_no, "ledger": name, "message": "Group is blank."})
            continue
        try:
            opening = parse_amount(cell(r, "opening"), str(cell(r, "opening_type") or "") or None)
            debit = parse_amount(cell(r, "debit"))
            credit = parse_amount(cell(r, "credit"))
            closing_cell = cell(r, "closing") if "closing" in cols else None
            has_closing = "closing" in cols
            stated = parse_amount(closing_cell, str(cell(r, "closing_type") or "") or None) if has_closing else None
        except ValueError as exc:
            errors.append({"row": row_no, "ledger": name, "message": str(exc)})
            continue
        if debit < 0 or credit < 0:
            errors.append({"row": row_no, "ledger": name, "message": "Debit and Credit movements must not be negative."})
            continue
        if not has_movement:
            opening = Decimal("0.00")
            debit = max(stated, Decimal("0.00"))
            credit = max(-stated, Decimal("0.00"))
        computed = opening + debit - credit
        if stated is not None and stated != computed:
            mismatches += 1
            errors.append({"row": row_no, "ledger": name, "message":
                           f"Closing balance {stated} does not equal opening + debit - credit ({computed})."})
            continue
        closing = computed
        notes = []
        nature = GROUP_NATURE.get(group.casefold())
        if nature is None:
            nature = "ASSET" if closing >= 0 else "LIABILITY"
            notes.append(f"Group '{group}' is not a known Tally group; nature inferred as {nature} from the balance sign.")
        rows.append(TBRow(row_no, name, key, group, nature, opening, debit, credit, closing, stated, raw, notes))

    if not rows and not errors:
        errors.append({"row": 0, "ledger": "", "message": "No ledger rows found."})
    total_dr = sum((r.closing for r in rows if r.closing > 0), Decimal("0.00"))
    total_cr = sum((-r.closing for r in rows if r.closing < 0), Decimal("0.00"))
    if not errors and total_dr != total_cr:
        errors.append({"row": 0, "ledger": "", "message":
                       f"Trial balance does not foot: total debits {total_dr} vs total credits {total_cr} "
                       f"(difference {total_dr - total_cr})."})
    return ParseResult(rows, errors, mismatches, total_dr, total_cr)
