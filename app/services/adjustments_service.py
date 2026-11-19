"""T-04 draft adjustments, T-05 approval (maker-checker) and reversal, T-06 adjusted trial balance.

A draft never touches v_adjusted_tb. Approval flips status in the same transaction that runs change_coordinator, so
statements, notes, ratios and checks all reflect the entry the moment it is approved. Approved entries are immutable
(database trigger); a mistake is fixed by a reversal: a NEW draft with debit and credit swapped, approved as usual.
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException

from app.services import audit_service, change_coordinator, locking

ZERO = Decimal("0.00")
PAISA = Decimal("0.01")


def _lock_engagement(cur, engagement_id: int) -> dict:
    cur.execute("SELECT id FROM engagements WHERE id = %s FOR UPDATE", (engagement_id,))
    if not cur.fetchone():
        raise HTTPException(404, "Engagement not found.")
    locking.assert_unlocked(cur, engagement_id)          # 409 once the Partner has signed off
    return {"id": engagement_id}


def _amount(value, what: str) -> Decimal:
    if value is None or value == "":
        return ZERO
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        raise HTTPException(400, f"{what}: '{value}' is not a number.")
    if not d.is_finite() or d != d.quantize(PAISA):
        raise HTTPException(400, f"{what}: amounts must be exact to the paisa (2 decimals).")
    if d < 0:
        raise HTTPException(400, f"{what}: amounts cannot be negative; use the other side instead.")
    return d.quantize(PAISA)


def _next_number(cur, engagement_id: int) -> int:
    cur.execute("SELECT coalesce(max(entry_number), 0) + 1 AS n FROM journal_entries WHERE engagement_id = %s", (engagement_id,))
    return cur.fetchone()["n"]


def create_draft(cur, user: dict, engagement_id: int, entry_date: date, description: str, raw_lines: list) -> dict:
    _lock_engagement(cur, engagement_id)
    description = (description or "").strip()
    if not description:
        raise HTTPException(400, "Enter a description for the adjustment.")
    if len(raw_lines) < 2:
        raise HTTPException(400, "An adjustment needs at least two lines.")
    cur.execute("SELECT id, name FROM ledgers WHERE engagement_id = %s AND is_active", (engagement_id,))
    active = {r["id"]: r["name"] for r in cur.fetchall()}
    lines, total_dr, total_cr = [], ZERO, ZERO
    for i, ln in enumerate(raw_lines, 1):
        if ln["ledger_id"] not in active:
            raise HTTPException(400, f"Line {i}: choose an active ledger of this engagement.")
        dr, cr = _amount(ln.get("debit"), f"Line {i} debit"), _amount(ln.get("credit"), f"Line {i} credit")
        if (dr > 0) == (cr > 0):
            raise HTTPException(400, f"Line {i}: enter either a debit or a credit amount, not both and not neither.")
        lines.append((ln["ledger_id"], dr, cr, (ln.get("note") or "").strip() or None))
        total_dr += dr
        total_cr += cr
    if total_dr != total_cr:
        raise HTTPException(400, f"Entry is not balanced: total debit {total_dr} vs total credit {total_cr} "
                                 f"(difference {total_dr - total_cr}). Nothing was saved.")
    return _insert(cur, user, engagement_id, entry_date, description, lines, None)


def _insert(cur, user, engagement_id, entry_date, description, lines, reverses_id) -> dict:
    number = _next_number(cur, engagement_id)
    cur.execute("""INSERT INTO journal_entries (engagement_id, entry_number, entry_date, description, created_by, reverses_entry_id)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""", (engagement_id, number, entry_date, description, user["id"], reverses_id))
    entry_id = cur.fetchone()["id"]
    for ledger_id, dr, cr, note in lines:
        cur.execute("""INSERT INTO journal_lines (journal_entry_id, engagement_id, ledger_id, debit_amount, credit_amount, line_note)
                       VALUES (%s,%s,%s,%s,%s,%s)""", (entry_id, engagement_id, ledger_id, dr, cr, note))
    audit_service.event(cur, engagement_id, "ADJUSTMENT_CREATED",
                        {"entry_number": number, "description": description, "lines": len(lines),
                         "total": sum((l[1] for l in lines), ZERO), "reverses_entry_id": reverses_id},
                        entity_table="journal_entries", entity_id=str(entry_id))
    return {"id": entry_id, "entry_number": number, "status": "draft"}


def _load_entry(cur, engagement_id: int, entry_id: int, lock: bool = False) -> dict:
    cur.execute(f"""SELECT id, entry_number, entry_date, description, status, created_by, approved_by, reverses_entry_id
                      FROM journal_entries WHERE id = %s AND engagement_id = %s {'FOR UPDATE' if lock else ''}""", (entry_id, engagement_id))
    entry = cur.fetchone()
    if not entry:
        raise HTTPException(404, "Adjustment not found in this engagement.")
    return entry


def approve(cur, user: dict, engagement_id: int, entry_id: int) -> dict:
    _lock_engagement(cur, engagement_id)
    entry = _load_entry(cur, engagement_id, entry_id, lock=True)
    if entry["status"] != "draft":
        raise HTTPException(409, f"Adjustment {entry['entry_number']} is already approved.")
    if entry["created_by"] == user["id"]:
        raise HTTPException(403, "Maker-checker: you created this adjustment, so someone else must approve it.")
    cur.execute("SELECT count(*) AS n FROM journal_lines jl JOIN ledgers l ON l.id = jl.ledger_id WHERE jl.journal_entry_id = %s AND NOT l.is_active", (entry_id,))
    if cur.fetchone()["n"]:
        raise HTTPException(409, "A ledger in this adjustment is no longer in the trial balance; it cannot be approved.")
    cur.execute("UPDATE journal_entries SET status = 'approved', approved_by = %s, approved_at = now() WHERE id = %s",
                (user["id"], entry_id))
    result = change_coordinator.after_change(cur, engagement_id, "ADJUSTMENT_APPROVED",
                                             {"entry_id": entry_id, "entry_number": entry["entry_number"], "approved_by": user["id"]})
    return {"id": entry_id, "entry_number": entry["entry_number"], "status": "approved", "counts": result}


def reverse(cur, user: dict, engagement_id: int, entry_id: int) -> dict:
    _lock_engagement(cur, engagement_id)
    entry = _load_entry(cur, engagement_id, entry_id, lock=True)
    if entry["status"] != "approved":
        raise HTTPException(409, "Only an approved adjustment can be reversed.")
    cur.execute("SELECT entry_number FROM journal_entries WHERE reverses_entry_id = %s", (entry_id,))
    existing = cur.fetchone()
    if existing:
        raise HTTPException(409, f"Adjustment {entry['entry_number']} is already reversed by adjustment {existing['entry_number']}.")
    cur.execute("SELECT ledger_id, debit_amount, credit_amount, line_note FROM journal_lines WHERE journal_entry_id = %s ORDER BY id", (entry_id,))
    swapped = [(r["ledger_id"], r["credit_amount"], r["debit_amount"], r["line_note"]) for r in cur.fetchall()]
    return _insert(cur, user, engagement_id, date.today(), f"Reversal of adjustment {entry['entry_number']}: {entry['description']}",
                   swapped, entry_id)


def list_entries(cur, engagement_id: int) -> dict:
    cur.execute("SELECT id FROM engagements WHERE id = %s", (engagement_id,))
    if not cur.fetchone():
        raise HTTPException(404, "Engagement not found.")
    cur.execute("""SELECT e.id, e.entry_number, e.entry_date, e.description, e.status, e.created_at, e.approved_at,
                          e.created_by, e.approved_by, cu.full_name AS created_by_name, au.full_name AS approved_by_name,
                          e.reverses_entry_id, rv.entry_number AS reverses_entry_number, rb.entry_number AS reversed_by_entry_number,
                          coalesce((SELECT json_agg(json_build_object('ledger_id', jl.ledger_id, 'ledger_name', l.name,
                                      'debit', jl.debit_amount, 'credit', jl.credit_amount, 'note', jl.line_note) ORDER BY jl.id)
                                      FROM journal_lines jl JOIN ledgers l ON l.id = jl.ledger_id WHERE jl.journal_entry_id = e.id), '[]') AS lines,
                          (SELECT coalesce(sum(debit_amount), 0) FROM journal_lines WHERE journal_entry_id = e.id) AS total
                     FROM journal_entries e
                     JOIN users cu ON cu.id = e.created_by
                     LEFT JOIN users au ON au.id = e.approved_by
                     LEFT JOIN journal_entries rv ON rv.id = e.reverses_entry_id
                     LEFT JOIN journal_entries rb ON rb.reverses_entry_id = e.id
                    WHERE e.engagement_id = %s ORDER BY e.entry_number DESC""", (engagement_id,))
    entries = cur.fetchall()
    cur.execute("""SELECT l.id, l.name, g.name AS tally_group FROM ledgers l LEFT JOIN tally_groups g ON g.id = l.group_id
                    WHERE l.engagement_id = %s AND l.is_active ORDER BY l.name""", (engagement_id,))
    return {"entries": entries, "ledgers": cur.fetchall(),
            "counts": {"draft": sum(1 for e in entries if e["status"] == "draft"), "approved": sum(1 for e in entries if e["status"] == "approved")}}


def adjusted_tb(cur, engagement_id: int) -> dict:
    cur.execute("SELECT id FROM engagements WHERE id = %s", (engagement_id,))
    if not cur.fetchone():
        raise HTTPException(404, "Engagement not found.")
    cur.execute("""SELECT ledger_id, ledger_name, tally_group, tb_balance, adjustment_net, adjusted_balance, (line_id IS NOT NULL) AS is_mapped
                     FROM v_adjusted_tb WHERE engagement_id = %s ORDER BY ledger_id""", (engagement_id,))
    rows = cur.fetchall()
    tot = lambda key, pos: sum((r[key] for r in rows if (r[key] > 0) == pos and r[key] != 0), ZERO)
    return {"rows": rows,
            "totals": {"tb_debit": tot("tb_balance", True), "tb_credit": -tot("tb_balance", False),
                       "adjustment_net": sum((r["adjustment_net"] for r in rows), ZERO),
                       "adjusted_debit": tot("adjusted_balance", True), "adjusted_credit": -tot("adjusted_balance", False),
                       "adjusted_difference": sum((r["adjusted_balance"] for r in rows), ZERO)}}
