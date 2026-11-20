"""Prior-year comparatives (T-07 / T-08 comparative column), entered per LEDGER.

The user types, or uploads, the prior-year closing balance of each ledger the way it is normally shown: assets and expenses as
positive debits, liabilities, income and equity as positive credits (a negative number is the opposite side). It is stored as a
trial-balance style Dr+/Cr- amount in ledger_prior_year, and the statements' comparative column (prior_year_amounts, one figure per
template line) is DERIVED from it: each line = sum of its mapped ledgers' prior-year balances, shown with the line's presentation sign.
The prior-year profit (the P&L ledgers) is added to prior-year reserves, exactly as the current-year profit flows into reserves, so the
prior-year Balance Sheet balances when the prior-year trial balance does.

Every change goes through change_coordinator: the statements, notes, ratios, checks and the materiality variances update at once.
"""
from decimal import Decimal

from fastapi import HTTPException

from app.services import audit_service, change_coordinator, locking, register_io as io, statement_service
from app.services.tb_parser import normalise_key

ZERO = Decimal("0.00")
NATURE_SIGN = {"ASSET": 1, "EXPENSE": 1, "LIABILITY": -1, "INCOME": -1}      # +1: a debit balance is the normal side


def _engagement(cur, engagement_id: int, lock: bool = False) -> dict:
    cur.execute(f"SELECT id, template_id FROM engagements WHERE id = %s {'FOR UPDATE' if lock else ''}", (engagement_id,))
    eng = cur.fetchone()
    if not eng:
        raise HTTPException(404, "Engagement not found.")
    return eng


def _ledgers(cur, engagement_id: int) -> list:
    cur.execute("""SELECT l.id AS ledger_id, l.name, g.name AS tally_group, coalesce(g.nature, 'ASSET') AS nature, tl.code AS line_code, tl.label AS line_label,
                          a.adjusted_balance, p.amount AS prior_signed
                     FROM ledgers l LEFT JOIN tally_groups g ON g.id = l.group_id
                     LEFT JOIN v_adjusted_tb a ON a.ledger_id = l.id
                     LEFT JOIN template_lines tl ON tl.id = a.line_id
                     LEFT JOIN ledger_prior_year p ON p.ledger_id = l.id
                    WHERE l.engagement_id = %s AND l.is_active ORDER BY l.id""", (engagement_id,))
    out = []
    for r in cur.fetchall():
        sign = NATURE_SIGN.get(r["nature"], 1)
        out.append({"ledger_id": r["ledger_id"], "name": r["name"], "tally_group": r["tally_group"], "nature": r["nature"], "side": "Dr" if sign == 1 else "Cr",
                    "line_code": r["line_code"], "line_label": r["line_label"],
                    "current_amount": (r["adjusted_balance"] * sign) if r["adjusted_balance"] is not None else None,
                    "prior_year_amount": (r["prior_signed"] * sign) if r["prior_signed"] is not None else None})
    return out


def list_rows(cur, engagement_id: int) -> dict:
    _engagement(cur, engagement_id)
    rows = _ledgers(cur, engagement_id)
    bs = statement_service.balance_sheet(cur, engagement_id)
    return {"ledgers": rows, "entered": sum(1 for r in rows if r["prior_year_amount"] is not None), "total": len(rows),
            "previous_label": bs["previous_label"], "previous_difference": bs["check"]["previous_difference"]}


def recompute_lines(cur, engagement_id: int, template_id: int) -> int:
    """Rebuild prior_year_amounts (per template line) from the ledger-level balances. Only changed figures are written."""
    cur.execute("""SELECT tl.id AS line_id, tl.code, tl.statement, tl.presentation_sign, coalesce(sum(p.amount), 0) AS signed
                     FROM template_lines tl
                     LEFT JOIN ledger_mappings m ON m.line_id = tl.id AND m.engagement_id = %s
                     LEFT JOIN ledgers l ON l.id = m.ledger_id AND l.is_active
                     LEFT JOIN ledger_prior_year p ON p.ledger_id = l.id
                    WHERE tl.template_id = %s AND tl.calc_kind = 'mapped' GROUP BY tl.id""", (engagement_id, template_id))
    lines = cur.fetchall()
    prior_profit = -sum((l["signed"] for l in lines if l["statement"] == "PL"), ZERO)               # Dr+ expenses, Cr- income: profit = -sum
    changed = 0
    for ln in lines:
        amount = (ln["signed"] * ln["presentation_sign"]).quantize(Decimal("0.01"))
        if ln["code"] == statement_service.RESERVES_CODE:
            amount += prior_profit.quantize(Decimal("0.01"))                                       # last year's profit sits in last year's reserves
        cur.execute("SELECT amount FROM prior_year_amounts WHERE engagement_id = %s AND line_id = %s", (engagement_id, ln["line_id"]))
        old = cur.fetchone()
        if old is None:
            cur.execute("INSERT INTO prior_year_amounts (engagement_id, line_id, amount) VALUES (%s,%s,%s)", (engagement_id, ln["line_id"], amount))
            changed += 1
        elif old["amount"] != amount:
            cur.execute("UPDATE prior_year_amounts SET amount = %s WHERE engagement_id = %s AND line_id = %s", (amount, engagement_id, ln["line_id"]))
            changed += 1
    return changed


def save(cur, user: dict, engagement_id: int, items: list, source: str = "form", filename: str | None = None) -> dict:
    """items = [{'ledger_id': int, 'amount': '1234.00' | None}]; None or '' removes that ledger's prior-year balance."""
    eng = _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    if not items:
        raise HTTPException(400, "There is nothing to save.")
    by_id = {r["ledger_id"]: r for r in _ledgers(cur, engagement_id)}
    plan, errors = [], []
    for i, it in enumerate(items, 1):
        led = by_id.get(it.get("ledger_id"))
        if not led:
            errors.append(f"Item {i}: ledger {it.get('ledger_id')} is not an active ledger of this engagement.")
            continue
        raw = it.get("amount")
        if raw is None or str(raw).strip() == "":
            plan.append((led, None))
            continue
        try:
            amt = io.signed_money(raw, f"{led['name']}: prior-year amount")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        plan.append((led, amt * NATURE_SIGN.get(led["nature"], 1)))
    if errors:
        raise HTTPException(400, " ".join(errors))
    updated = removed = 0
    for led, signed in plan:
        if signed is None:
            cur.execute("DELETE FROM ledger_prior_year WHERE ledger_id = %s", (led["ledger_id"],))
            removed += cur.rowcount
            continue
        cur.execute("""INSERT INTO ledger_prior_year (engagement_id, ledger_id, amount, updated_by) VALUES (%s,%s,%s,%s)
                       ON CONFLICT (ledger_id) DO UPDATE SET amount = EXCLUDED.amount, updated_by = EXCLUDED.updated_by, updated_at = now()
                        WHERE ledger_prior_year.amount <> EXCLUDED.amount""", (engagement_id, led["ledger_id"], signed, user["id"]))
        updated += cur.rowcount
    lines_changed = recompute_lines(cur, engagement_id, eng["template_id"])
    change_coordinator.after_change(cur, engagement_id, "PRIOR_YEAR_UPDATED",
                                    {"source": source, "file": filename, "ledgers_updated": updated, "ledgers_cleared": removed, "statement_lines_changed": lines_changed,
                                     "updated_by": user["full_name"]})
    return {"updated": updated, "cleared": removed, "lines_changed": lines_changed, **list_rows(cur, engagement_id)}


def import_file(cur, user: dict, engagement_id: int, filename: str, data: bytes) -> dict:
    _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    table = io.read_table(filename, data)
    by_key = {normalise_key(r["name"]): r for r in _ledgers(cur, engagement_id)}
    items, seen, errors = [], set(), []
    for n, row in table:
        name = io.pick(row, "ledger name", "ledger", "particulars", "ledger code")
        amount = io.pick(row, "prior year amount", "prior year", "py amount", "previous year amount", "previous year", "amount")
        try:
            name = io.text(name, "Ledger name")
            led = by_key.get(normalise_key(name))
            if not led:
                raise ValueError(f"Ledger '{name}' is not an active ledger of this engagement (the name must match the trial balance).")
            if led["ledger_id"] in seen:
                raise ValueError(f"Ledger '{name}' appears twice in this file.")
            io.signed_money(amount, "Prior-year amount")
        except ValueError as exc:
            errors.append({"row": n, "message": str(exc)})
            continue
        seen.add(led["ledger_id"])
        items.append({"ledger_id": led["ledger_id"], "amount": amount})
    if errors:
        raise HTTPException(422, {"message": f"{len(errors)} row(s) need attention. Nothing was imported.", "errors": errors})
    result = save(cur, user, engagement_id, items, source="import", filename=filename)
    return {"imported": len(items), "file": filename, **result}
