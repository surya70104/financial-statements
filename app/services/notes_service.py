"""T-09 Notes to the financial statements.

Each note lists the active ledgers mapped to it (v_adjusted_tb grouped by note_id) with opening, movement and closing,
and a note total. The note total is computed from the LEDGERS; the parent statement line is computed by the statement
service from the line mapping, so comparing the two is a genuine tie-out (it breaks if a ledger is mapped to a line
without a note, or if a statement figure is changed without its note).
Reserves and surplus (Note 2) also carries the profit/(loss) for the period and other reserves movement, exactly as the
Balance Sheet line does.
"""
from decimal import Decimal

from fastapi import HTTPException

from app.services import entity, statement_service

ZERO = Decimal("0.00")


def _q(v) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"))


def statement_lines(bs: dict, pnl: dict) -> dict:
    """{line code: line dict} for every Balance Sheet and P&L line."""
    out = {}
    for part in bs["parts"]:
        for sec in part["sections"]:
            for ln in sec["lines"]:
                out[ln["code"]] = ln
    for sec in pnl["sections"]:
        for ln in sec["lines"]:
            out[ln["code"]] = ln
    return out


def build(cur, engagement_id: int, bs: dict | None = None, pnl: dict | None = None) -> dict:
    bs = bs or statement_service.balance_sheet(cur, engagement_id)
    pnl = pnl or statement_service.profit_and_loss(cur, engagement_id)
    lines = statement_lines(bs, pnl)

    cur.execute("SELECT template_id, entity_type FROM engagements WHERE id = %s", (engagement_id,))
    eng = cur.fetchone()
    if not eng:
        raise HTTPException(404, "Engagement not found.")
    cur.execute("""SELECT n.id, n.note_no, n.title, tl.code AS line_code, tl.label AS line_label, tl.statement
                     FROM template_notes n JOIN template_lines tl ON tl.id = n.line_id
                    WHERE n.template_id = %s ORDER BY n.sort_order""", (eng["template_id"],))
    notes = cur.fetchall()
    cur.execute("""SELECT a.note_id, a.ledger_id, a.ledger_name, a.tally_group,
                          (coalesce(lb.opening_balance, 0) * tl.presentation_sign)                        AS opening,
                          ((a.adjusted_balance - coalesce(lb.opening_balance, 0)) * tl.presentation_sign) AS movement,
                          (a.adjusted_balance * tl.presentation_sign)                                     AS closing
                     FROM v_adjusted_tb a
                     JOIN template_lines tl ON tl.id = a.line_id
                     LEFT JOIN ledger_balances lb ON lb.ledger_id = a.ledger_id
                    WHERE a.engagement_id = %s AND a.note_id IS NOT NULL
                    ORDER BY a.ledger_name""", (engagement_id,))
    by_note: dict[int, list] = {}
    for r in cur.fetchall():
        by_note.setdefault(r["note_id"], []).append({k: r[k] for k in ("ledger_id", "ledger_name", "tally_group", "opening", "movement", "closing")})

    out = []
    for n in notes:
        ledgers = by_note.get(n["id"], [])
        ledger_total = _q(sum((_q(l["closing"]) for l in ledgers), ZERO))
        extras = []
        if n["line_code"] == statement_service.RESERVES_CODE:
            b = lines[n["line_code"]]["breakdown"]
            extras = [{"label": "Profit / (loss) for the period (from Statement of Profit and Loss)", "closing": b["profit_for_period"]},
                      {"label": "Other movements in reserves", "closing": b["other_movements"]}]
        note_total = _q(ledger_total + sum((_q(e["closing"]) for e in extras), ZERO))
        line = lines[n["line_code"]]
        diff = _q(note_total - _q(line["current"]))
        out.append({"note_no": n["note_no"], "title": entity.note_title(eng["entity_type"], n["line_code"], n["title"]), "statement": n["statement"],
                    "line_code": n["line_code"], "line_label": line["label"], "ledgers": ledgers, "extras": extras,
                    "ledger_total": ledger_total, "note_total": note_total, "line_total": _q(line["current"]),
                    "previous_total": _q(line["previous"]), "difference": diff, "ties": diff == 0})
    failing = [n for n in out if not n["ties"]]
    return {"current_label": bs["current_label"], "previous_label": bs["previous_label"],
            "notes": out, "fd_note": fd_note(cur, engagement_id),
            "tie_summary": {"notes": len(out), "tying": len(out) - len(failing),
                            "total_absolute_difference": _q(sum((abs(n["difference"]) for n in failing), ZERO)),
                            "failing": [{"note_no": n["note_no"], "title": n["title"], "difference": n["difference"]} for n in failing]},
            "warnings": bs["warnings"]}


def fd_note(cur, engagement_id: int) -> dict:
    """The dedicated Fixed Deposits & Accrued Interest schedule, read straight from the FD register. Totals count POSTED FDs only (those are in
    the statements); drafts are listed but marked as not yet posted."""
    cur.execute("""SELECT f.id, f.bank_name, f.fd_number, f.principal_amount, f.interest_rate, f.start_date, f.maturity_date, f.maturity_classification,
                          f.accrued_interest, f.schedule_line_code, f.status, tl.label AS line_label
                     FROM fixed_deposit_register f LEFT JOIN template_lines tl ON tl.code = f.schedule_line_code
                          AND tl.template_id = (SELECT template_id FROM engagements WHERE id = f.engagement_id)
                    WHERE f.engagement_id = %s ORDER BY f.maturity_classification DESC, f.maturity_date, f.id""", (engagement_id,))
    rows = cur.fetchall()
    posted = [r for r in rows if r["status"] == "posted"]
    total = lambda sel, key: _q(sum((r[key] for r in sel), ZERO))
    return {"title": "Fixed deposits and accrued interest", "rows": rows,
            "totals": {"current_principal": total([r for r in posted if r["maturity_classification"] == "current"], "principal_amount"),
                       "non_current_principal": total([r for r in posted if r["maturity_classification"] == "non_current"], "principal_amount"),
                       "total_principal": total(posted, "principal_amount"), "accrued_interest": total(posted, "accrued_interest")},
            "posted_count": len(posted), "draft_count": len(rows) - len(posted)}
