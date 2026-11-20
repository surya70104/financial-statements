"""T-20 Reconciliation Engine: differences between our books and an outside statement, turned into PROPOSED adjustments.

difference = external amount - book amount, measured on the offset (bank / control) ledger as a debit balance:
  difference > 0  (the statement shows more than the books)  ->  Dr offset ledger, Cr the other ledger
  difference < 0  (the statement shows less than the books)  ->  Dr the other ledger, Cr offset ledger
e.g. a bank charge not yet in the books: book 0, statement -590  ->  Dr Bank Charges 590, Cr Bank 590.
A row marked 'timing_only' (cheque not yet presented, deposit in transit) needs no entry: it is resolved at once.
'Propose adjustments' creates DRAFT journal entries only. They reach the adjusted trial balance when a DIFFERENT user approves
them on the Adjustments tab (Step 5); the item then turns 'resolved' by itself.
"""
from datetime import date
from decimal import Decimal

from fastapi import HTTPException

from app.services import adjustments_service, audit_service, ledger_support, locking, materiality_service, register_io as io

TYPES = ("bank", "party", "other")
ACTIONS = {"timing_only": "timing_only", "timing": "timing_only", "propose_adjustment": "propose_adjustment", "propose": "propose_adjustment",
           "adjustment": "propose_adjustment", "adjust": "propose_adjustment"}
ZERO = Decimal("0.00")


def _engagement(cur, engagement_id: int, lock: bool = False) -> dict:
    cur.execute(f"SELECT id, period_end FROM engagements WHERE id = %s {'FOR UPDATE' if lock else ''}", (engagement_id,))
    eng = cur.fetchone()
    if not eng:
        raise HTTPException(404, "Engagement not found.")
    return eng


def _active_ledger(cur, engagement_id: int, name: str) -> int | None:
    found = ledger_support.find_ledger(cur, engagement_id, name)
    return found["id"] if found and found["is_active"] else None


def clean(cur, engagement_id: int, raw: dict) -> dict:
    kind = (io.text(raw.get("recon_type"), "Type") or "").lower()
    if kind not in TYPES:
        raise ValueError(f"Type must be bank, party or other (got '{raw.get('recon_type')}').")
    book, ext = io.signed_money(raw.get("book_amount"), "Book amount"), io.signed_money(raw.get("external_amount"), "Statement amount")
    diff = ext - book
    stated = raw.get("difference_amount")
    if stated not in (None, "") and io.signed_money(stated, "Difference") != diff:
        raise ValueError(f"Difference {stated} is not statement minus books ({ext} - {book} = {diff}).")
    action = ACTIONS.get((io.text(raw.get("adjustment_action"), "Action") or "").lower().replace(" ", "_").replace("-", "_"))
    if not action:
        raise ValueError("Action must be 'timing_only' or 'propose_adjustment'.")
    ledger, offset = io.text(raw.get("ledger_code"), "Ledger", required=False), io.text(raw.get("offset_ledger_code"), "Offset ledger", required=False)
    if action == "propose_adjustment" and diff != 0:
        for label, name in (("Ledger", ledger), ("Offset ledger", offset)):
            if not name:
                raise ValueError(f"{label} is required to propose an adjustment.")
            if not _active_ledger(cur, engagement_id, name):
                raise ValueError(f"{label} '{name}' is not an active ledger of this engagement.")
        if ledger.casefold() == offset.casefold():
            raise ValueError("The ledger and the offset ledger must be different.")
    return {"recon_type": kind, "account_name": io.text(raw.get("account_name"), "Account", maxlen=150), "item_date": io.when(raw.get("item_date"), "Date"),
            "reference_no": io.text(raw.get("reference_no"), "Reference", required=False, maxlen=80),
            "description": io.text(raw.get("description"), "Description", maxlen=300), "book_amount": book, "external_amount": ext,
            "difference_amount": diff, "adjustment_action": action, "ledger_code": ledger, "offset_ledger_code": offset,
            "status": "open" if action == "propose_adjustment" and diff != 0 else "resolved"}


def _key(r: dict) -> tuple:
    return (r["recon_type"], r["account_name"].casefold(), r["item_date"], (r["reference_no"] or "").casefold(), r["book_amount"], r["external_amount"])


def _existing(cur, engagement_id: int) -> set:
    cur.execute("SELECT recon_type, account_name, item_date, reference_no, book_amount, external_amount FROM reconciliation_items WHERE engagement_id = %s", (engagement_id,))
    return {(r["recon_type"], r["account_name"].strip().casefold(), r["item_date"], (r["reference_no"] or "").strip().casefold(), r["book_amount"], r["external_amount"])
            for r in cur.fetchall()}


def _insert(cur, user: dict, engagement_id: int, r: dict) -> int:
    cur.execute("""INSERT INTO reconciliation_items (engagement_id, recon_type, account_name, item_date, reference_no, description, book_amount, external_amount,
                          difference_amount, adjustment_action, ledger_code, offset_ledger_code, status, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (engagement_id, r["recon_type"], r["account_name"], r["item_date"], r["reference_no"], r["description"], r["book_amount"], r["external_amount"],
                 r["difference_amount"], r["adjustment_action"], r["ledger_code"], r["offset_ledger_code"], r["status"], user["id"]))
    return cur.fetchone()["id"]


def add_row(cur, user: dict, engagement_id: int, raw: dict) -> dict:
    _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    try:
        r = clean(cur, engagement_id, raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if _key(r) in _existing(cur, engagement_id):
        raise HTTPException(409, "This item is already in the reconciliation (same account, date, reference and amounts).")
    rid = _insert(cur, user, engagement_id, r)
    materiality_service.sync_difference_issues(cur, engagement_id)
    audit_service.event(cur, engagement_id, "RECON_ITEM_ADDED", {"item_id": rid, **r}, entity_table="reconciliation_items", entity_id=str(rid))
    return {"id": rid, **r}


def import_file(cur, user: dict, engagement_id: int, filename: str, data: bytes) -> dict:
    _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    table = io.read_table(filename, data)
    seen, existing, good, errors = set(), _existing(cur, engagement_id), [], []
    for n, row in table:
        raw = {"recon_type": io.pick(row, "recon type", "type", "reconciliation type"), "account_name": io.pick(row, "account name", "account", "bank account"),
               "item_date": io.pick(row, "item date", "date"), "reference_no": io.pick(row, "reference no", "reference", "ref no", "cheque no", "utr"),
               "description": io.pick(row, "description", "particulars", "narration"), "book_amount": io.pick(row, "book amount", "per books", "books"),
               "external_amount": io.pick(row, "external amount", "statement amount", "per statement", "bank amount", "per bank"),
               "difference_amount": io.pick(row, "difference amount", "difference"), "adjustment_action": io.pick(row, "adjustment action", "action"),
               "ledger_code": io.pick(row, "ledger code", "ledger", "account ledger"), "offset_ledger_code": io.pick(row, "offset ledger code", "offset ledger", "bank ledger")}
        try:
            r = clean(cur, engagement_id, raw)
        except ValueError as exc:
            errors.append({"row": n, "message": str(exc)})
            continue
        if _key(r) in seen or _key(r) in existing:
            errors.append({"row": n, "message": "Already in the reconciliation (or repeated in this file): same account, date, reference and amounts."})
            continue
        seen.add(_key(r))
        good.append(r)
    if errors:
        raise HTTPException(422, {"message": f"{len(errors)} row(s) need attention. Nothing was imported.", "errors": errors})
    for r in good:
        _insert(cur, user, engagement_id, r)
    materiality_service.sync_difference_issues(cur, engagement_id)
    audit_service.event(cur, engagement_id, "RECON_IMPORTED", {"file": filename, "rows": len(good),
                        "needing_adjustment": sum(1 for r in good if r["status"] == "open"), "net_difference": sum((r["difference_amount"] for r in good), ZERO)})
    return {"imported": len(good), "needing_adjustment": sum(1 for r in good if r["status"] == "open"), "file": filename}


def delete_row(cur, user: dict, engagement_id: int, item_id: int) -> dict:
    _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    cur.execute("SELECT id, proposed_journal_id, account_name, difference_amount FROM reconciliation_items WHERE id = %s AND engagement_id = %s FOR UPDATE", (item_id, engagement_id))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Reconciliation item not found in this engagement.")
    if row["proposed_journal_id"]:
        raise HTTPException(409, "This item already has a journal entry. Reverse that entry on the Adjustments tab instead.")
    cur.execute("DELETE FROM reconciliation_items WHERE id = %s", (item_id,))
    materiality_service.sync_difference_issues(cur, engagement_id)
    audit_service.event(cur, engagement_id, "RECON_ITEM_DELETED", {"item_id": item_id, "account": row["account_name"], "difference": row["difference_amount"]},
                        entity_table="reconciliation_items", entity_id=str(item_id))
    return {"deleted": item_id}


def list_rows(cur, engagement_id: int) -> dict:
    _engagement(cur, engagement_id)
    cur.execute("""SELECT r.id, r.recon_type, r.account_name, r.item_date, r.reference_no, r.description, r.book_amount, r.external_amount, r.difference_amount,
                          r.adjustment_action, r.ledger_code, r.offset_ledger_code, r.status, r.proposed_journal_id, j.entry_number AS journal_number,
                          j.status AS journal_status, r.created_at, r.created_by, u.full_name AS created_by_name
                     FROM reconciliation_items r JOIN users u ON u.id = r.created_by LEFT JOIN journal_entries j ON j.id = r.proposed_journal_id
                    WHERE r.engagement_id = %s ORDER BY r.id""", (engagement_id,))
    rows = cur.fetchall()
    timing = [r for r in rows if r["adjustment_action"] == "timing_only" and r["difference_amount"] != 0]
    needing = [r for r in rows if r["adjustment_action"] == "propose_adjustment" and r["difference_amount"] != 0]
    return {"summary": {"total_items": len(rows), "timing_differences": len(timing), "adjustment_required": len(needing),
                        "awaiting_proposal": sum(1 for r in needing if r["status"] == "open"),
                        "proposed_count": sum(1 for r in needing if r["proposed_journal_id"]),
                        "approved_count": sum(1 for r in needing if r["status"] == "resolved" and r["proposed_journal_id"]),
                        "timing_amount": sum((r["difference_amount"] for r in timing), ZERO),
                        "adjustment_amount": sum((r["difference_amount"] for r in needing), ZERO)}, "rows": rows}


def propose(cur, user: dict, engagement_id: int) -> dict:
    eng = _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    cur.execute("""SELECT * FROM reconciliation_items WHERE engagement_id = %s AND status = 'open' AND adjustment_action = 'propose_adjustment'
                      AND difference_amount <> 0 ORDER BY id FOR UPDATE""", (engagement_id,))
    items = cur.fetchall()
    if not items:
        raise HTTPException(409, "There are no open items marked 'propose_adjustment' with a difference.")
    created = []
    for it in items:
        target, offset = _active_ledger(cur, engagement_id, it["ledger_code"]), _active_ledger(cur, engagement_id, it["offset_ledger_code"])
        if not target or not offset:
            missing = it["ledger_code"] if not target else it["offset_ledger_code"]
            raise HTTPException(409, f"Ledger '{missing}' (item {it['id']}, {it['account_name']}) is no longer an active ledger of this engagement. Nothing was proposed.")
        d = it["difference_amount"]
        note = f"{it['account_name']}" + (f" / {it['reference_no']}" if it["reference_no"] else "")
        lines = [(offset, d, ZERO, note), (target, ZERO, d, note)] if d > 0 else [(target, -d, ZERO, note), (offset, ZERO, -d, note)]
        entry = adjustments_service._insert(cur, user, engagement_id, it["item_date"] or eng["period_end"],
                                            f"Reconciliation ({it['recon_type']}): {it['description']} - {note}", lines, None)
        cur.execute("UPDATE reconciliation_items SET status = 'proposed', proposed_journal_id = %s WHERE id = %s", (entry["id"], it["id"]))
        created.append({"item_id": it["id"], "journal_id": entry["id"], "entry_number": entry["entry_number"]})
    materiality_service.sync_difference_issues(cur, engagement_id)
    audit_service.event(cur, engagement_id, "RECON_ADJUSTMENTS_PROPOSED",
                        {"items": len(created), "total_difference": sum((i["difference_amount"] for i in items), ZERO),
                         "entry_numbers": [c["entry_number"] for c in created], "proposed_by": user["full_name"]})
    return {"proposed": len(created), "entries": created}
