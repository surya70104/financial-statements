"""T-24 LFAR Assistant (Long Form Audit Report): loan scrutiny and a checklist of standard checkpoints.

No ledger is copied here. A loan facility stores only its schedule details (lender, limit, rate, security, EMI status) and the
NAME of its ledger; the book balance is read live from the adjusted trial balance (v_adjusted_tb), exactly like the
statements. Every checklist line also shows a live figure ('auto metric') pulled from the same source and from the Step 10-12
registers, so a change in the main trial balance shows in LFAR without re-importing anything.
"""
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException

from app.services import (audit_service, fd_service, gstr9c_service, locking, materiality_service, reconciliation_service, register_io as io,
                          tds_service, ledger_support)
from app.services.gstr9c_service import _inr
from app.services.tb_parser import normalise_key

ZERO = Decimal("0.00")
FACILITY_TYPES = ("Term Loan", "Working Capital / CC", "Vehicle / Equipment Loan", "Unsecured Loan")
_TYPE_ALIASES = {"term loan": "Term Loan", "term": "Term Loan", "working capital / cc": "Working Capital / CC", "working capital": "Working Capital / CC",
                 "cc": "Working Capital / CC", "cash credit": "Working Capital / CC", "overdraft": "Working Capital / CC", "od": "Working Capital / CC",
                 "vehicle / equipment loan": "Vehicle / Equipment Loan", "vehicle loan": "Vehicle / Equipment Loan", "equipment loan": "Vehicle / Equipment Loan",
                 "vehicle": "Vehicle / Equipment Loan", "equipment": "Vehicle / Equipment Loan", "unsecured loan": "Unsecured Loan", "unsecured": "Unsecured Loan"}
STATUSES = ("pending", "complied", "exception_noted", "not_applicable")
EMI = ("regular", "overdue", "restructured")

CHECKLIST = [
    ("LFAR-01", "Borrowings & Sanctioned Limits", "Are all borrowings supported by sanction letters, are balances within the sanctioned limits, and are charges and security created and registered?"),
    ("LFAR-02", "Overdue Loans & Defaults", "Has the company defaulted in repaying any loan, interest or EMI to a bank, financial institution or other lender, and for how long?"),
    ("LFAR-03", "Fixed Deposits & Accrued Interest", "Are fixed deposits agreed to bank confirmations, correctly classified as current or non-current, and is interest accrued up to the balance sheet date?"),
    ("LFAR-04", "Trade Receivables & Recoverability", "Are trade receivables confirmed or verified, is ageing reviewed, and is any provision for doubtful debts adequate? Are there debtors with credit balances?"),
    ("LFAR-05", "Fixed Assets & Capital WIP", "Are fixed assets and capital work-in-progress properly recorded, physically verified, depreciated and free from unrecorded disposals?"),
    ("LFAR-06", "Inventory Valuation", "Is inventory physically verified at reasonable intervals and valued at the lower of cost and net realisable value, with discrepancies dealt with in the books?"),
    ("LFAR-07", "Statutory Dues - TDS/TCS & GST", "Is the company regular in depositing undisputed statutory dues (TDS, TCS, GST and others), and do the GST figures agree with GSTR-9C?"),
    ("LFAR-08", "Bank Reconciliations & Cash Balances", "Are bank balances reconciled to bank statements, and are unreconciled items and unrecorded entries explained and adjusted?"),
]


def _engagement(cur, engagement_id: int, lock: bool = False) -> None:
    cur.execute(f"SELECT 1 FROM engagements WHERE id = %s {'FOR UPDATE' if lock else ''}", (engagement_id,))
    if not cur.fetchone():
        raise HTTPException(404, "Engagement not found.")


# ---- loan facilities ---------------------------------------------------------------------------------------------
def clean(cur, engagement_id: int, raw: dict) -> dict:
    kind = _TYPE_ALIASES.get((io.text(raw.get("facility_type"), "Facility type") or "").lower())
    if not kind:
        raise ValueError(f"Facility type must be one of {', '.join(FACILITY_TYPES)} (got '{raw.get('facility_type')}').")
    ledger = io.text(raw.get("ledger_code"), "Ledger code")
    found = ledger_support.find_ledger(cur, engagement_id, ledger)
    if not found or not found["is_active"]:
        raise ValueError(f"Ledger '{ledger}' is not an active ledger of this engagement (the name must match the trial balance).")
    limit = io.money(raw.get("sanctioned_limit"), "Sanctioned limit", positive=False)
    rate = raw.get("interest_rate")
    rate = None if rate is None or str(rate).strip() == "" else io.rate(rate, "Interest rate", "0.001")
    emi = (io.text(raw.get("emi_status"), "EMI status", required=False) or "regular").lower()
    if emi not in EMI:
        raise ValueError(f"EMI status must be regular, overdue or restructured (got '{raw.get('emi_status')}').")
    overdue = io.money(raw.get("overdue_amount"), "Overdue amount", required=False, positive=False) or ZERO
    if overdue > 0 and emi == "regular":
        raise ValueError("An overdue amount is given but the EMI status is 'regular'. Set the status to overdue or restructured.")
    return {"lender_name": io.text(raw.get("lender_name"), "Lender name", maxlen=120), "facility_type": kind,
            "account_number": io.text(raw.get("account_number"), "Account number", maxlen=60), "ledger_code": found["name"],
            "sanctioned_limit": limit, "interest_rate": rate, "security_details": io.text(raw.get("security_details"), "Security", required=False, maxlen=400),
            "emi_status": emi, "overdue_amount": overdue}


def _upsert(cur, user: dict, engagement_id: int, r: dict) -> str:
    cur.execute("""INSERT INTO lfar_loan_facilities (engagement_id, lender_name, facility_type, account_number, ledger_code, sanctioned_limit, interest_rate,
                          security_details, emi_status, overdue_amount, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (engagement_id, lender_name, account_number) DO UPDATE SET facility_type = EXCLUDED.facility_type,
                          ledger_code = EXCLUDED.ledger_code, sanctioned_limit = EXCLUDED.sanctioned_limit, interest_rate = EXCLUDED.interest_rate,
                          security_details = EXCLUDED.security_details, emi_status = EXCLUDED.emi_status, overdue_amount = EXCLUDED.overdue_amount
                   RETURNING (xmax = 0) AS inserted""",
                (engagement_id, r["lender_name"], r["facility_type"], r["account_number"], r["ledger_code"], r["sanctioned_limit"], r["interest_rate"],
                 r["security_details"], r["emi_status"], r["overdue_amount"], user["id"]))
    return "added" if cur.fetchone()["inserted"] else "updated"


def add_loan(cur, user: dict, engagement_id: int, raw: dict) -> dict:
    _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    try:
        r = clean(cur, engagement_id, raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    outcome = _upsert(cur, user, engagement_id, r)
    audit_service.event(cur, engagement_id, "LFAR_LOANS_UPDATED", {"source": "form", outcome: 1, "lender": r["lender_name"], "account": r["account_number"],
                                                                    "sanctioned_limit": r["sanctioned_limit"]})
    return {"result": outcome, **r}


def import_loans(cur, user: dict, engagement_id: int, filename: str, data: bytes) -> dict:
    _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    table = io.read_table(filename, data)
    seen, good, errors = set(), [], []
    for n, row in table:
        raw = {"lender_name": io.pick(row, "lender name", "lender", "bank"), "facility_type": io.pick(row, "facility type", "type"),
               "account_number": io.pick(row, "account number", "account no", "loan account number", "a/c no"),
               "ledger_code": io.pick(row, "ledger code", "ledger"), "sanctioned_limit": io.pick(row, "sanctioned limit", "limit", "sanctioned amount"),
               "interest_rate": io.pick(row, "interest rate", "interest rate (%)", "interest rate %", "rate"),
               "security_details": io.pick(row, "security details", "security"), "emi_status": io.pick(row, "emi status", "status"),
               "overdue_amount": io.pick(row, "overdue amount", "overdue")}
        try:
            r = clean(cur, engagement_id, raw)
        except ValueError as exc:
            errors.append({"row": n, "message": str(exc)})
            continue
        k = (r["lender_name"].casefold(), r["account_number"].casefold())
        if k in seen:
            errors.append({"row": n, "message": f"{r['lender_name']} account {r['account_number']} appears twice in this file."})
            continue
        seen.add(k)
        good.append(r)
    if errors:
        raise HTTPException(422, {"message": f"{len(errors)} row(s) need attention. Nothing was imported.", "errors": errors})
    outcomes = [_upsert(cur, user, engagement_id, r) for r in good]
    result = {"imported": len(good), "added": outcomes.count("added"), "updated": outcomes.count("updated"), "file": filename}
    audit_service.event(cur, engagement_id, "LFAR_LOANS_UPDATED", {"source": "import", **result, "sanctioned_total": sum((r["sanctioned_limit"] for r in good), ZERO)})
    return result


def delete_loan(cur, user: dict, engagement_id: int, loan_id: int) -> dict:
    _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    cur.execute("SELECT id, lender_name, account_number FROM lfar_loan_facilities WHERE id = %s AND engagement_id = %s FOR UPDATE", (loan_id, engagement_id))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Loan facility not found in this engagement.")
    cur.execute("DELETE FROM lfar_loan_facilities WHERE id = %s", (loan_id,))
    audit_service.event(cur, engagement_id, "LFAR_LOANS_UPDATED", {"source": "delete", "deleted": f"{row['lender_name']} / {row['account_number']}"})
    return {"deleted": loan_id}


# ---- checklist ---------------------------------------------------------------------------------------------------
def _seed(cur, engagement_id: int) -> None:
    cur.executemany("INSERT INTO lfar_checklist_items (engagement_id, section_code, category, question_text) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    [(engagement_id, c, cat, q) for c, cat, q in CHECKLIST])


def update_item(cur, user: dict, engagement_id: int, item_id: int, status: str | None, remarks: str | None) -> dict:
    _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    if status not in STATUSES:
        raise HTTPException(400, f"Status must be one of {', '.join(STATUSES)}.")
    remarks = (remarks or "").strip() or None
    if remarks and len(remarks) > 2000:
        raise HTTPException(400, "The remarks are too long (2,000 characters at most).")
    if status == "exception_noted" and not remarks:
        raise HTTPException(400, "Remarks are required when an exception is noted.")
    cur.execute("SELECT id, section_code, category, status, auditor_remarks FROM lfar_checklist_items WHERE id = %s AND engagement_id = %s FOR UPDATE", (item_id, engagement_id))
    item = cur.fetchone()
    if not item:
        raise HTTPException(404, "Checklist item not found in this engagement.")
    if status == "pending":
        cur.execute("UPDATE lfar_checklist_items SET status = 'pending', auditor_remarks = %s, reviewed_by = NULL, reviewed_at = NULL WHERE id = %s", (remarks, item_id))
    else:
        cur.execute("UPDATE lfar_checklist_items SET status = %s, auditor_remarks = %s, reviewed_by = %s, reviewed_at = now() WHERE id = %s", (status, remarks, user["id"], item_id))
    audit_service.event(cur, engagement_id, "LFAR_ITEM_UPDATED",
                        {"section_code": item["section_code"], "category": item["category"], "from_status": item["status"], "status": status, "reviewed_by": user["full_name"]},
                        entity_table="lfar_checklist_items", entity_id=str(item_id), reason=remarks)
    return {"id": item_id, "section_code": item["section_code"], "status": status, "auditor_remarks": remarks}


# ---- the live view -----------------------------------------------------------------------------------------------
def _line_totals(cur, engagement_id: int) -> dict:
    """{line code: {'total': Dr-positive sum, 'ledgers': n, 'credit_balance_ledgers': n}} from the adjusted trial balance."""
    cur.execute("""SELECT tl.code, count(*) AS ledgers, coalesce(sum(a.adjusted_balance), 0) AS total, count(*) FILTER (WHERE a.adjusted_balance < 0) AS credit_ledgers
                     FROM v_adjusted_tb a JOIN template_lines tl ON tl.id = a.line_id WHERE a.engagement_id = %s GROUP BY tl.code""", (engagement_id,))
    return {r["code"]: r for r in cur.fetchall()}


def _facilities(cur, engagement_id: int, tb: dict) -> list:
    cur.execute("""SELECT f.id, f.lender_name, f.facility_type, f.account_number, f.ledger_code, f.sanctioned_limit, f.interest_rate, f.security_details,
                          f.emi_status, f.overdue_amount, f.created_at, u.full_name AS created_by_name
                     FROM lfar_loan_facilities f JOIN users u ON u.id = f.created_by WHERE f.engagement_id = %s ORDER BY f.id""", (engagement_id,))
    out = []
    for f in cur.fetchall():
        row = tb.get(normalise_key(f["ledger_code"]))
        book = (-row["adjusted_balance"]).quantize(Decimal("0.01")) if row else None              # borrowings are credit balances
        limit = f["sanctioned_limit"]
        flags = []
        if row is None:
            flags.append("ledger_missing")
        if book is not None and book > limit:
            flags.append("over_limit")
        if f["overdue_amount"] > 0:
            flags.append("overdue")
        out.append({**f, "book_balance": book, "tb_balance": row["tb_balance"] * -1 if row else None,
                    "adjustment_net": row["adjustment_net"] * -1 if row else None,
                    "utilization_pct": (book / limit * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if book is not None and limit > 0 else None,
                    "headroom": (limit - book) if book is not None else None, "flags": flags})
    return out


def _metrics(cur, engagement_id: int, loans: list, lines: dict, mat) -> dict:
    fd, tds = fd_service.list_rows(cur, engagement_id)["summary"], tds_service.list_rows(cur, engagement_id)["summary"]
    rec, gst = reconciliation_service.list_rows(cur, engagement_id)["summary"], gstr9c_service.list_rows(cur, engagement_id)["summary"]
    total = lambda code: lines.get(code, {}).get("total", ZERO)
    count = lambda code: lines.get(code, {}).get("ledgers", 0)
    mtxt = f" Overall materiality {_inr(mat['overall_materiality'])}." if mat else " Materiality is not set."
    books = [l["book_balance"] for l in loans if l["book_balance"] is not None]
    limits = sum((l["sanctioned_limit"] for l in loans), ZERO)
    over, overdue = [l for l in loans if "over_limit" in l["flags"]], [l for l in loans if "overdue" in l["flags"]]
    by = tds["by_type"]
    credit_debtors = lines.get("BS.CA.TR", {}).get("credit_ledgers", 0)
    m = {}
    m["LFAR-01"] = {"label": "Borrowings per adjusted TB", "value": sum(books, ZERO),
                    "detail": (f"{len(loans)} facilities, {_inr(sum(books, ZERO))} against {_inr(limits)} sanctioned; {len(over)} over the limit." if loans
                               else "No loan facilities listed yet: upload the loan schedule below."), "flag": "warn" if over or not loans else "ok"}
    m["LFAR-02"] = {"label": "Overdue amount", "value": sum((l["overdue_amount"] for l in loans), ZERO),
                    "detail": f"{len(overdue)} facility(ies) with an overdue amount; {sum(1 for l in loans if l['emi_status'] == 'restructured')} restructured.", "flag": "warn" if overdue else "ok"}
    m["LFAR-03"] = {"label": "FD principal", "value": fd["total_principal"],
                    "detail": f"{fd['count']} FD(s): current {_inr(fd['current_principal'])}, non-current {_inr(fd['non_current_principal'])}; accrued interest {_inr(fd['total_accrued_interest'])}; {fd['draft_rows']} not yet posted.",
                    "flag": "warn" if fd["draft_rows"] else "ok"}
    m["LFAR-04"] = {"label": "Trade receivables per adjusted TB", "value": total("BS.CA.TR"),
                    "detail": f"{count('BS.CA.TR')} debtor ledger(s), {credit_debtors} with a credit balance.{mtxt}", "flag": "warn" if credit_debtors else "ok"}
    m["LFAR-05"] = {"label": "Property, plant & equipment per adjusted TB", "value": total("BS.NCA.PPE"),
                    "detail": f"{count('BS.NCA.PPE')} ledger(s) mapped (net of accumulated depreciation).{mtxt}", "flag": None}
    m["LFAR-06"] = {"label": "Inventories per adjusted TB", "value": total("BS.CA.INVT"),
                    "detail": (f"{count('BS.CA.INVT')} inventory ledger(s) mapped." if count("BS.CA.INVT") else "No inventory ledgers are mapped (a services company may hold none).") + mtxt, "flag": None}
    pending_tax = tds["draft_rows"]
    m["LFAR-07"] = {"label": "TDS/TCS payable per register", "value": by["TDS_PAYABLE"]["tax_amount"] + by["TCS_PAYABLE"]["tax_amount"],
                    "detail": (f"TDS payable {_inr(by['TDS_PAYABLE']['tax_amount'])}, TCS payable {_inr(by['TCS_PAYABLE']['tax_amount'])}, receivable "
                               f"{_inr(by['TDS_RECEIVABLE']['tax_amount'] + by['TCS_RECEIVABLE']['tax_amount'])}; {pending_tax} register row(s) not posted. "
                               f"GSTR-9C: {gst['with_difference']} difference(s), net {_inr(gst['net_difference'])}, {gst['open_issues']} open in Issues."),
                    "flag": "warn" if pending_tax or gst["open_issues"] else "ok"}
    m["LFAR-08"] = {"label": "Cash and bank balances per adjusted TB", "value": total("BS.CA.CASH"),
                    "detail": f"{rec['total_items']} reconciliation item(s): {rec['timing_differences']} timing, {rec['adjustment_required']} needing an entry ({rec['awaiting_proposal']} awaiting a proposal, {rec['approved_count']} approved).",
                    "flag": "warn" if rec["awaiting_proposal"] else "ok"}
    return m


def get(cur, engagement_id: int) -> dict:
    _engagement(cur, engagement_id)
    cur.execute("SELECT count(*) AS n FROM lfar_checklist_items WHERE engagement_id = %s", (engagement_id,))
    seeded = cur.fetchone()["n"] > 0                              # read before the cursor is reused by locking.is_locked
    if not seeded and not locking.is_locked(cur, engagement_id):
        _seed(cur, engagement_id)                                  # first access: the standard checkpoints appear (attributed to SYSTEM by the route)
    cur.execute("""SELECT i.id, i.section_code, i.category, i.question_text, i.status, i.auditor_remarks, i.reviewed_by, u.full_name AS reviewed_by_name, i.reviewed_at
                     FROM lfar_checklist_items i LEFT JOIN users u ON u.id = i.reviewed_by WHERE i.engagement_id = %s ORDER BY i.section_code""", (engagement_id,))
    items = cur.fetchall()
    if not items:                                                  # locked before anyone opened LFAR: show the standard list, read-only
        items = [{"id": None, "section_code": c, "category": cat, "question_text": q, "status": "pending", "auditor_remarks": None,
                  "reviewed_by": None, "reviewed_by_name": None, "reviewed_at": None} for c, cat, q in CHECKLIST]
    cur.execute("SELECT ledger_name, tb_balance, adjustment_net, adjusted_balance FROM v_adjusted_tb WHERE engagement_id = %s", (engagement_id,))
    tb = {normalise_key(r["ledger_name"]): r for r in cur.fetchall()}
    loans = _facilities(cur, engagement_id, tb)
    lines, mat = _line_totals(cur, engagement_id), materiality_service.get_config(cur, engagement_id)
    metrics = _metrics(cur, engagement_id, loans, lines, mat)
    for it in items:
        it["auto_metric"] = metrics.get(it["section_code"])
    books = [l["book_balance"] for l in loans if l["book_balance"] is not None]
    return {"summary": {"checkpoints_total": len(items), "checkpoints_reviewed": sum(1 for i in items if i["status"] != "pending"),
                        "exceptions_noted": sum(1 for i in items if i["status"] == "exception_noted"),
                        "total_sanctioned_limits": sum((l["sanctioned_limit"] for l in loans), ZERO), "live_borrowings": sum(books, ZERO),
                        "facilities": len(loans), "over_limit": sum(1 for l in loans if "over_limit" in l["flags"]),
                        "overdue": sum(1 for l in loans if "overdue" in l["flags"]), "overdue_amount": sum((l["overdue_amount"] for l in loans), ZERO)},
            "items": items, "loans": loans, "materiality": mat, "source": "v_adjusted_tb"}
