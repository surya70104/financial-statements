"""T-22 Fixed Deposit Register: FD principal and accrued interest, posted to the adjusted trial balance.

Each posted FD is ONE approved journal entry:
  Dr Fixed deposits ledger (on the row's Schedule III line)   principal      } moves the principal out of the bank ledger it sits in today
  Cr Bank ledger                                              principal      }
  Dr Interest accrued on fixed deposits (other current assets) accrued interest  } interest earned in the year, not yet received
  Cr Interest on fixed deposits (Other income, P&L)           accrued interest  }
Accrued interest is simple interest for the days inside the financial year:
  principal x rate / 100 x days / 365, days = from max(start, FY start) up to min(maturity, FY end) inclusive of the FY end date.
A stated accrued interest is VERIFIED against that figure (it may differ by up to Rs 1); a blank one is calculated.
Current = matures within 12 months of the balance sheet date. Post only FDs / interest NOT already in the trial balance.
"""
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException

from app.services import audit_service, change_coordinator, ledger_support, locking, register_io as io

LINES = ("BS.CA.CASH", "BS.CA.OTH", "BS.NCA.OTH")
CONTROL = {"BS.CA.CASH": ("Fixed Deposits - Current", "Bank Accounts", "ASSET"),
           "BS.CA.OTH": ("Fixed Deposits - Other current assets", "Current Assets", "ASSET"),
           "BS.NCA.OTH": ("Fixed Deposits - Non-current", "Deposits (Asset)", "ASSET")}
ACCRUED = ("Interest Accrued on Fixed Deposits", "Current Assets", "ASSET", "BS.CA.OTH")
INCOME = ("Interest on Fixed Deposits", "Indirect Incomes", "INCOME", "PL.OI")
TOLERANCE = Decimal("1.00")


def _q(v) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _engagement(cur, engagement_id: int, lock: bool = False) -> dict:
    cur.execute(f"SELECT id, fy_label, period_start, period_end FROM engagements WHERE id = %s {'FOR UPDATE' if lock else ''}", (engagement_id,))
    eng = cur.fetchone()
    if not eng:
        raise HTTPException(404, "Engagement not found.")
    return eng


def accrued_interest(principal: Decimal, rate: Decimal, start: date, maturity: date, p_start: date, p_end: date) -> Decimal:
    first, last_excl = max(start, p_start), min(maturity, p_end + timedelta(days=1))
    days = max(0, (last_excl - first).days)
    return _q(principal * rate / 100 * days / 365)


def classification(maturity: date, p_end: date) -> str:
    try:
        limit = p_end.replace(year=p_end.year + 1)
    except ValueError:                                              # 29 Feb
        limit = p_end.replace(year=p_end.year + 1, day=28)
    return "current" if maturity <= limit else "non_current"


def clean(raw: dict, eng: dict) -> dict:
    bank = io.text(raw.get("bank_name"), "Bank name", maxlen=100)
    number = io.text(raw.get("fd_number"), "FD number", maxlen=60)
    principal = io.money(raw.get("principal_amount"), "Principal")
    pct = io.rate(raw.get("interest_rate"), "Interest rate", "0.001")
    start, maturity = io.when(raw.get("start_date"), "Start date"), io.when(raw.get("maturity_date"), "Maturity date")
    if maturity <= start:
        raise ValueError("The maturity date must be after the start date.")
    expected_class = classification(maturity, eng["period_end"])
    given = (io.text(raw.get("maturity_classification"), "Classification", required=False) or "").lower().replace("-", "_").replace(" ", "_")
    if given and given not in ("current", "non_current"):
        raise ValueError(f"Classification must be 'current' or 'non_current' (got '{raw.get('maturity_classification')}').")
    if given and given != expected_class:
        raise ValueError(f"The FD matures on {maturity:%d-%m-%Y}, which makes it {expected_class.replace('_', '-')} at {eng['period_end']:%d-%m-%Y} "
                         f"(not {given.replace('_', '-')}). Fix the date or leave the classification blank.")
    cls = expected_class
    line = (io.text(raw.get("schedule_line_code"), "Schedule III line", required=False) or "").upper()
    line = line or ("BS.NCA.OTH" if cls == "non_current" else "BS.CA.CASH")
    if line not in LINES or (cls == "non_current") != (line == "BS.NCA.OTH"):
        raise ValueError(f"Schedule III line '{line}' does not suit a {cls.replace('_', '-')} FD "
                         f"(current: BS.CA.CASH or BS.CA.OTH; non-current: BS.NCA.OTH).")
    calc = accrued_interest(principal, pct, start, maturity, eng["period_start"], eng["period_end"])
    stated = io.money(raw.get("accrued_interest"), "Accrued interest", required=False, positive=False)
    if stated is not None and abs(stated - calc) > TOLERANCE:
        raise ValueError(f"Accrued interest {stated} does not match the calculated {calc} "
                         f"({principal} x {pct}% for the days in FY {eng['fy_label']}). Leave it blank to use the calculated amount.")
    return {"bank_name": bank, "fd_number": number, "ledger_code": io.text(raw.get("ledger_code"), "Bank ledger", required=False),
            "principal_amount": principal, "interest_rate": pct, "start_date": start, "maturity_date": maturity,
            "maturity_classification": cls, "accrued_interest": stated if stated is not None else calc, "schedule_line_code": line}


def _existing(cur, engagement_id: int) -> set:
    cur.execute("SELECT bank_name, fd_number FROM fixed_deposit_register WHERE engagement_id = %s", (engagement_id,))
    return {(r["bank_name"], r["fd_number"]) for r in cur.fetchall()}


def _insert(cur, user: dict, engagement_id: int, r: dict) -> int:
    cur.execute("""INSERT INTO fixed_deposit_register (engagement_id, bank_name, fd_number, ledger_code, principal_amount, interest_rate, start_date,
                          maturity_date, maturity_classification, accrued_interest, schedule_line_code, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (engagement_id, r["bank_name"], r["fd_number"], r["ledger_code"], r["principal_amount"], r["interest_rate"], r["start_date"],
                 r["maturity_date"], r["maturity_classification"], r["accrued_interest"], r["schedule_line_code"], user["id"]))
    return cur.fetchone()["id"]


def add_row(cur, user: dict, engagement_id: int, raw: dict) -> dict:
    eng = _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    try:
        r = clean(raw, eng)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if (r["bank_name"], r["fd_number"]) in _existing(cur, engagement_id):
        raise HTTPException(409, f"FD {r['fd_number']} of {r['bank_name']} is already in the register.")
    rid = _insert(cur, user, engagement_id, r)
    audit_service.event(cur, engagement_id, "FD_ROW_ADDED", {"row_id": rid, **r}, entity_table="fixed_deposit_register", entity_id=str(rid))
    return {"id": rid, "status": "draft", **r}


def import_file(cur, user: dict, engagement_id: int, filename: str, data: bytes) -> dict:
    eng = _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    table = io.read_table(filename, data)
    seen, existing, good, errors = set(), _existing(cur, engagement_id), [], []
    for n, row in table:
        raw = {"bank_name": io.pick(row, "bank name", "bank"), "fd_number": io.pick(row, "fd number", "fd no", "fdr number", "fdr no", "fd"),
               "ledger_code": io.pick(row, "ledger code", "ledger", "bank ledger"),
               "principal_amount": io.pick(row, "principal amount", "principal", "amount"),
               "interest_rate": io.pick(row, "interest rate", "rate", "rate %", "interest rate %", "interest rate (%)", "rate (%)"),
               "start_date": io.pick(row, "start date", "date of deposit", "deposit date"), "maturity_date": io.pick(row, "maturity date", "maturity"),
               "maturity_classification": io.pick(row, "maturity classification", "classification", "current non current"),
               "accrued_interest": io.pick(row, "accrued interest", "interest accrued"),
               "schedule_line_code": io.pick(row, "schedule line code", "schedule iii line", "schedule line", "line code")}
        try:
            r = clean(raw, eng)
        except ValueError as exc:
            errors.append({"row": n, "message": str(exc)})
            continue
        k = (r["bank_name"], r["fd_number"])
        if k in seen or k in existing:
            errors.append({"row": n, "message": f"FD {r['fd_number']} of {r['bank_name']} is already in the register (or repeated in this file)."})
            continue
        seen.add(k)
        good.append(r)
    if errors:
        raise HTTPException(422, {"message": f"{len(errors)} row(s) need attention. Nothing was imported.", "errors": errors})
    for r in good:
        _insert(cur, user, engagement_id, r)
    audit_service.event(cur, engagement_id, "FD_REGISTER_IMPORTED", {"file": filename, "rows": len(good),
                        "principal_total": sum((r["principal_amount"] for r in good), Decimal("0.00")),
                        "accrued_total": sum((r["accrued_interest"] for r in good), Decimal("0.00"))})
    return {"imported": len(good), "file": filename}


def delete_row(cur, user: dict, engagement_id: int, row_id: int) -> dict:
    _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    cur.execute("SELECT id, status, bank_name, fd_number FROM fixed_deposit_register WHERE id = %s AND engagement_id = %s FOR UPDATE", (row_id, engagement_id))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "FD not found in this engagement.")
    if row["status"] != "draft":
        raise HTTPException(409, "A posted FD is permanent. Reverse its journal entry on the Adjustments tab instead.")
    cur.execute("DELETE FROM fixed_deposit_register WHERE id = %s", (row_id,))
    audit_service.event(cur, engagement_id, "FD_ROW_DELETED", {"row_id": row_id, "bank": row["bank_name"], "fd_number": row["fd_number"]},
                        entity_table="fixed_deposit_register", entity_id=str(row_id))
    return {"deleted": row_id}


def list_rows(cur, engagement_id: int) -> dict:
    eng = _engagement(cur, engagement_id)
    cur.execute("""SELECT f.id, f.bank_name, f.fd_number, f.ledger_code, f.principal_amount, f.interest_rate, f.start_date, f.maturity_date,
                          f.maturity_classification, f.accrued_interest, f.schedule_line_code, f.status, f.posted_journal_id,
                          j.entry_number AS journal_number, f.posted_at, f.created_at, f.created_by, cu.full_name AS created_by_name,
                          pu.full_name AS posted_by_name
                     FROM fixed_deposit_register f JOIN users cu ON cu.id = f.created_by LEFT JOIN users pu ON pu.id = f.posted_by
                     LEFT JOIN journal_entries j ON j.id = f.posted_journal_id
                    WHERE f.engagement_id = %s ORDER BY f.id""", (engagement_id,))
    rows = cur.fetchall()
    z = Decimal("0.00")
    cls = lambda c: sum((r["principal_amount"] for r in rows if r["maturity_classification"] == c), z)
    return {"summary": {"total_principal": sum((r["principal_amount"] for r in rows), z), "current_principal": cls("current"),
                        "non_current_principal": cls("non_current"), "total_accrued_interest": sum((r["accrued_interest"] for r in rows), z),
                        "count": len(rows), "draft_rows": sum(1 for r in rows if r["status"] == "draft"),
                        "posted_rows": sum(1 for r in rows if r["status"] == "posted")},
            "period": {"fy": eng["fy_label"], "start": eng["period_start"], "end": eng["period_end"]}, "rows": rows}


def _bank_ledger(cur, user: dict, engagement_id: int, fd: dict) -> int:
    if fd["ledger_code"]:
        found = ledger_support.find_ledger(cur, engagement_id, fd["ledger_code"])
        if not found or not found["is_active"]:
            raise HTTPException(409, f"Bank ledger '{fd['ledger_code']}' (FD {fd['fd_number']}) is not an active ledger of this engagement.")
        return found["id"]
    token = fd["bank_name"].split()[0]
    cur.execute("""SELECT l.id FROM ledgers l JOIN tally_groups g ON g.id = l.group_id
                    WHERE l.engagement_id = %s AND l.is_active AND g.name ILIKE '%%bank%%' AND l.name ILIKE %s ORDER BY l.id LIMIT 1""",
                (engagement_id, f"%{token}%"))
    row = cur.fetchone()
    if not row:
        raise HTTPException(409, f"No bank ledger was found for {fd['bank_name']} (FD {fd['fd_number']}). "
                                 f"Put the ledger's name in the 'Ledger code' column and add the FD again.")
    return row["id"]


def post(cur, user: dict, engagement_id: int, ids: list | None) -> dict:
    eng = _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    cur.execute("""SELECT * FROM fixed_deposit_register WHERE engagement_id = %s AND status = 'draft'
                      AND (%s::bigint[] IS NULL OR id = ANY(%s)) ORDER BY id FOR UPDATE""", (engagement_id, ids or None, ids or None))
    drafts = cur.fetchall()
    if not drafts:
        raise HTTPException(409, "There are no draft FDs to post.")
    mine = [d for d in drafts if d["created_by"] == user["id"]]
    todo = [d for d in drafts if d["created_by"] != user["id"]]
    if not todo:
        raise HTTPException(403, "Maker-checker: you entered these FDs yourself, so someone else must post them.")
    z, entries = Decimal("0.00"), []
    for d in todo:
        name, group, nature = CONTROL[d["schedule_line_code"]]
        fd_ledger = ledger_support.ensure_ledger(cur, user, engagement_id, name, group, nature, d["schedule_line_code"])
        bank = _bank_ledger(cur, user, engagement_id, d)
        note = f"FD {d['fd_number']} - {d['bank_name']}"
        lines = [(fd_ledger, d["principal_amount"], z, note), (bank, z, d["principal_amount"], note)]
        if d["accrued_interest"] > 0:
            acc = ledger_support.ensure_ledger(cur, user, engagement_id, *ACCRUED)
            inc = ledger_support.ensure_ledger(cur, user, engagement_id, *INCOME)
            lines += [(acc, d["accrued_interest"], z, note + " interest accrued"), (inc, z, d["accrued_interest"], note + " interest accrued")]
        jid = ledger_support.post_entry(cur, d["created_by"], user, engagement_id, eng["period_end"],
                                        f"FD register: {note} ({d['maturity_classification'].replace('_', '-')})", lines)
        cur.execute("UPDATE fixed_deposit_register SET status = 'posted', posted_journal_id = %s, posted_by = %s, posted_at = now() WHERE id = %s",
                    (jid, user["id"], d["id"]))
        entries.append({"row_id": d["id"], "journal_id": jid})
    principal = sum((d["principal_amount"] for d in todo), z)
    interest = sum((d["accrued_interest"] for d in todo), z)
    change_coordinator.after_change(cur, engagement_id, "FD_REGISTER_POSTED",
                                    {"rows": len(todo), "principal_total": principal, "accrued_interest_total": interest,
                                     "journals": [e["journal_id"] for e in entries], "skipped_own_rows": len(mine), "posted_by": user["full_name"]})
    return {"posted": len(todo), "skipped_own": len(mine), "principal_total": principal, "accrued_interest_total": interest, "entries": entries}
