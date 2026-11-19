"""T-19 TDS / TCS Entry Pack: a register of tax deducted/collected (payable by us, or receivable by us), posted to the
adjusted trial balance as balanced journal entries.

Each posted row is ONE approved journal entry:
  TDS payable    Dr party (creditor)   Cr TDS payable ledger          (we deducted tax from a payment: current liability)
  TCS payable    Dr party (debtor)     Cr TCS payable ledger          (we collected tax from a customer: current liability)
  TDS receivable Dr TDS receivable     Cr party (debtor)              (a customer deducted tax from our receipt: current asset)
  TCS receivable Dr TCS receivable     Cr party (creditor)            (a supplier collected tax from us: current asset)
Payables map to 'Other current liabilities' and receivables to 'Other current assets'. The entry is created in the name of
whoever entered the row and approved by the Checker/Partner who posts it, so maker-checker holds.
Post only items that are NOT already in the trial balance: posting adds to the TB figures.
"""
import re
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException

from app.services import audit_service, change_coordinator, ledger_support, locking, register_io as io

TYPES = ("TDS_PAYABLE", "TDS_RECEIVABLE", "TCS_PAYABLE", "TCS_RECEIVABLE")
# entry type -> (default ledger, its Tally group, nature, Schedule III line, party kind)
PLAN = {"TDS_PAYABLE": ("TDS Payable", "Duties & Taxes", "LIABILITY", "BS.CL.OTH", "creditor"),
        "TCS_PAYABLE": ("TCS Payable", "Duties & Taxes", "LIABILITY", "BS.CL.OTH", "debtor"),
        "TDS_RECEIVABLE": ("TDS Receivable", "Current Assets", "ASSET", "BS.CA.OTH", "debtor"),
        "TCS_RECEIVABLE": ("TCS Receivable", "Current Assets", "ASSET", "BS.CA.OTH", "creditor")}
PARTY = {"creditor": ("Sundry Creditors", "LIABILITY", "BS.CL.TP"), "debtor": ("Sundry Debtors", "ASSET", "BS.CA.TR")}
SECTION_RE = re.compile(r"^[0-9]{3}[A-Z]{0,4}$")
TOLERANCE = Decimal("1.00")                     # TDS/TCS is usually rounded to the rupee, so a stated amount may differ by up to Rs 1


def _q(v) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _engagement(cur, engagement_id: int, lock: bool = False) -> dict:
    cur.execute(f"SELECT id, period_end FROM engagements WHERE id = %s {'FOR UPDATE' if lock else ''}", (engagement_id,))
    eng = cur.fetchone()
    if not eng:
        raise HTTPException(404, "Engagement not found.")
    return eng


def clean(raw: dict) -> dict:
    """Validate one row (from a form, JSON or a file row) in exact paise. Raises ValueError with a plain-English message."""
    kind = (io.text(raw.get("entry_type"), "Entry type") or "").upper().replace(" ", "_").replace("-", "_")
    if kind not in TYPES:
        raise ValueError(f"Entry type must be one of {', '.join(TYPES)} (got '{raw.get('entry_type')}').")
    section = (io.text(raw.get("section_code"), "Section") or "").upper().replace(" ", "")
    if not SECTION_RE.match(section):
        raise ValueError(f"Section '{raw.get('section_code')}' is not valid (examples: 194C, 194J, 194I, 206C).")
    base = io.money(raw.get("base_amount"), "Base amount")
    pct = io.rate(raw.get("tax_rate"), "Tax rate", "0.01")
    computed = _q(base * pct / 100)
    stated = io.money(raw.get("tax_amount"), "Tax amount", required=False)
    if stated is not None and abs(stated - computed) > TOLERANCE:
        raise ValueError(f"Tax amount {stated} does not match {pct}% of {base} (expected {computed}). Leave it blank to use the calculated amount.")
    tax = stated if stated is not None else computed
    if tax <= 0:
        raise ValueError("The tax amount rounds to zero.")
    return {"entry_type": kind, "section_code": section, "party_name": io.text(raw.get("party_name"), "Party name"),
            "ledger_code": io.text(raw.get("ledger_code"), "Ledger", required=False), "base_amount": base, "tax_rate": pct, "tax_amount": tax}


def _key(r: dict) -> tuple:
    return (r["entry_type"], r["section_code"], r["party_name"].casefold(), r["base_amount"], r["tax_amount"])


def _existing_keys(cur, engagement_id: int) -> set:
    cur.execute("SELECT entry_type, section_code, party_name, base_amount, tax_amount FROM tds_tcs_entries WHERE engagement_id = %s", (engagement_id,))
    return {(r["entry_type"], r["section_code"], r["party_name"].strip().casefold(), r["base_amount"], r["tax_amount"]) for r in cur.fetchall()}


def _insert(cur, user: dict, engagement_id: int, r: dict) -> int:
    cur.execute("""INSERT INTO tds_tcs_entries (engagement_id, entry_type, section_code, party_name, ledger_code, base_amount, tax_rate, tax_amount, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (engagement_id, r["entry_type"], r["section_code"], r["party_name"], r["ledger_code"], r["base_amount"], r["tax_rate"], r["tax_amount"], user["id"]))
    return cur.fetchone()["id"]


def add_row(cur, user: dict, engagement_id: int, raw: dict) -> dict:
    _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    try:
        r = clean(raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if _key(r) in _existing_keys(cur, engagement_id):
        raise HTTPException(409, "This deduction is already in the register (same type, section, party, base and tax amount).")
    rid = _insert(cur, user, engagement_id, r)
    audit_service.event(cur, engagement_id, "TDS_TCS_ROW_ADDED", {"row_id": rid, **r}, entity_table="tds_tcs_entries", entity_id=str(rid))
    return {"id": rid, "status": "draft", **r}


def import_file(cur, user: dict, engagement_id: int, filename: str, data: bytes) -> dict:
    _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    table = io.read_table(filename, data)
    seen, existing, good, errors = set(), _existing_keys(cur, engagement_id), [], []
    for n, row in table:
        raw = {"entry_type": io.pick(row, "entry type", "type"), "section_code": io.pick(row, "section code", "section"),
               "party_name": io.pick(row, "party name", "party", "deductee", "deductor", "name"),
               "ledger_code": io.pick(row, "ledger code", "ledger"), "base_amount": io.pick(row, "base amount", "base", "amount", "gross amount"),
               "tax_rate": io.pick(row, "tax rate", "rate", "rate %", "tax rate %", "tax rate (%)", "rate (%)"), "tax_amount": io.pick(row, "tax amount", "tds amount", "tax", "tds")}
        try:
            r = clean(raw)
        except ValueError as exc:
            errors.append({"row": n, "message": str(exc)})
            continue
        if _key(r) in seen or _key(r) in existing:
            errors.append({"row": n, "message": "Already in the register (or repeated in this file): same type, section, party, base and tax amount."})
            continue
        seen.add(_key(r))
        good.append(r)
    if errors:
        raise HTTPException(422, {"message": f"{len(errors)} row(s) need attention. Nothing was imported.", "errors": errors})
    for r in good:
        _insert(cur, user, engagement_id, r)
    audit_service.event(cur, engagement_id, "TDS_TCS_IMPORTED", {"file": filename, "rows": len(good), "tax_total": sum((r["tax_amount"] for r in good), Decimal("0.00"))})
    return {"imported": len(good), "file": filename}


def delete_row(cur, user: dict, engagement_id: int, row_id: int) -> dict:
    _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    cur.execute("SELECT id, status, party_name, tax_amount FROM tds_tcs_entries WHERE id = %s AND engagement_id = %s FOR UPDATE", (row_id, engagement_id))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Register row not found in this engagement.")
    if row["status"] != "draft":
        raise HTTPException(409, "A posted row is permanent. Reverse its journal entry on the Adjustments tab instead.")
    cur.execute("DELETE FROM tds_tcs_entries WHERE id = %s", (row_id,))
    audit_service.event(cur, engagement_id, "TDS_TCS_ROW_DELETED", {"row_id": row_id, "party": row["party_name"], "tax_amount": row["tax_amount"]},
                        entity_table="tds_tcs_entries", entity_id=str(row_id))
    return {"deleted": row_id}


def list_rows(cur, engagement_id: int) -> dict:
    _engagement(cur, engagement_id)
    cur.execute("""SELECT t.id, t.entry_type, t.section_code, t.party_name, t.ledger_code, t.base_amount, t.tax_rate, t.tax_amount, t.status,
                          t.posted_journal_id, j.entry_number AS journal_number, t.posted_at, t.created_at,
                          cu.full_name AS created_by_name, pu.full_name AS posted_by_name, t.created_by
                     FROM tds_tcs_entries t JOIN users cu ON cu.id = t.created_by LEFT JOIN users pu ON pu.id = t.posted_by
                     LEFT JOIN journal_entries j ON j.id = t.posted_journal_id
                    WHERE t.engagement_id = %s ORDER BY t.id""", (engagement_id,))
    rows = cur.fetchall()
    zero = Decimal("0.00")
    summary = {k: {"count": 0, "tax_amount": zero, "base_amount": zero, "draft": zero, "posted": zero} for k in TYPES}
    for r in rows:
        s = summary[r["entry_type"]]
        s["count"] += 1
        s["tax_amount"] += r["tax_amount"]
        s["base_amount"] += r["base_amount"]
        s[r["status"]] += r["tax_amount"]
    return {"summary": {"by_type": summary, "draft_rows": sum(1 for r in rows if r["status"] == "draft"),
                        "posted_rows": sum(1 for r in rows if r["status"] == "posted")}, "rows": rows,
            "plan": {k: {"ledger": v[0], "line": v[3]} for k, v in PLAN.items()}}


def post(cur, user: dict, engagement_id: int, ids: list | None) -> dict:
    eng = _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    cur.execute("""SELECT id, entry_type, section_code, party_name, ledger_code, tax_amount, created_by FROM tds_tcs_entries
                    WHERE engagement_id = %s AND status = 'draft' AND (%s::bigint[] IS NULL OR id = ANY(%s)) ORDER BY id FOR UPDATE""",
                (engagement_id, ids or None, ids or None))
    drafts = cur.fetchall()
    if not drafts:
        raise HTTPException(409, "There are no draft rows to post.")
    mine = [d for d in drafts if d["created_by"] == user["id"]]
    todo = [d for d in drafts if d["created_by"] != user["id"]]
    if not todo:
        raise HTTPException(403, "Maker-checker: you entered these rows yourself, so someone else must post them.")
    entries = []
    for d in todo:
        name, group, nature, line, party_kind = PLAN[d["entry_type"]]
        tax_ledger = ledger_support.ensure_ledger(cur, user, engagement_id, d["ledger_code"] or name, group, nature, line)
        pg, pn, pl = PARTY[party_kind]
        party_ledger = ledger_support.ensure_ledger(cur, user, engagement_id, d["party_name"], pg, pn, pl)
        amt, zero = d["tax_amount"], Decimal("0.00")
        payable = d["entry_type"].endswith("PAYABLE")
        note = f"{d['entry_type'].replace('_', ' ').title()} u/s {d['section_code']}"
        lines = ([(party_ledger, amt, zero, note), (tax_ledger, zero, amt, note)] if payable
                 else [(tax_ledger, amt, zero, note), (party_ledger, zero, amt, note)])
        jid = ledger_support.post_entry(cur, d["created_by"], user, engagement_id, eng["period_end"],
                                        f"TDS/TCS pack: {note} - {d['party_name']}", lines)
        cur.execute("UPDATE tds_tcs_entries SET status = 'posted', posted_journal_id = %s, posted_by = %s, posted_at = now() WHERE id = %s",
                    (jid, user["id"], d["id"]))
        entries.append({"row_id": d["id"], "journal_id": jid})
    total = sum((d["tax_amount"] for d in todo), Decimal("0.00"))
    change_coordinator.after_change(cur, engagement_id, "TDS_TCS_PACK_POSTED",
                                    {"rows": len(todo), "tax_total": total, "journals": [e["journal_id"] for e in entries],
                                     "skipped_own_rows": len(mine), "posted_by": user["full_name"]})
    return {"posted": len(todo), "skipped_own": len(mine), "tax_total": total, "entries": entries}
