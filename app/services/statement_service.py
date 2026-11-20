"""T-07 Balance Sheet and T-08 Profit & Loss (Schedule III).

Detail-line totals come from the view v_statement_line_totals (v_adjusted_tb + ledger_mappings). Subtotals and
derived lines are evaluated from the template's own formulas, in Decimal (exact paise). Nothing is hardcoded:
labels, note numbers and formulas all come from the template tables.

The profit/(loss) for the period flows into 'Reserves and surplus' (the TB carries P&L ledgers, not a closed
retained-earnings figure), so Total assets = Total equity and liabilities once every ledger is mapped.
"""
from decimal import Decimal

from fastapi import HTTPException

from app.services import entity, exceptions_service

ZERO = Decimal("0.00")
RESERVES_CODE = "BS.EQ.RS"
PAT_CODE = "PL.PAT"
# Which side of the Balance Sheet a line sits on (template line-code prefixes).
_BS_PART = (("BS.EQ", "EQUITY AND LIABILITIES"), ("BS.NCL", "EQUITY AND LIABILITIES"), ("BS.CL", "EQUITY AND LIABILITIES"),
            ("BS.EQL", "EQUITY AND LIABILITIES"), ("BS.NCA", "ASSETS"), ("BS.CA", "ASSETS"), ("BS.ASSETS", "ASSETS"))


def _q(v) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"))


def _prior_label(fy_label: str) -> str:
    try:
        start = int(fy_label.split("-")[0])
        return f"FY {start - 1}-{str(start)[-2:]}"
    except (ValueError, IndexError):
        return "Previous period"


def _load(cur, engagement_id: int, statement: str):
    cur.execute("SELECT id, fy_label, template_id, other_reserves_movement, template_confirmed, entity_type FROM engagements WHERE id = %s",
                (engagement_id,))
    eng = cur.fetchone()
    if not eng:
        raise HTTPException(404, "Engagement not found.")
    cur.execute("""SELECT l.id, l.code, l.statement, l.section, l.label, l.sort_order, l.calc_kind, l.formula,
                          n.note_no, t.amount, t.py_amount, t.ledger_count
                     FROM template_lines l
                     LEFT JOIN template_notes n ON n.line_id = l.id
                     LEFT JOIN v_statement_line_totals t ON t.line_id = l.id AND t.engagement_id = %s
                    WHERE l.template_id = %s AND l.statement = %s ORDER BY l.sort_order""",
                (engagement_id, eng["template_id"], statement))
    lines = cur.fetchall()
    for ln in lines:                                      # a partnership shows Partners' Capital / Current Account instead of Share capital / Reserves
        ln["label"] = entity.line_label(eng["entity_type"], ln["code"], ln["label"])
    return eng, lines


def _evaluate(lines: list, adjust: dict[str, Decimal] | None = None):
    """Return {code: (current, previous)}. `adjust` adds to a detail line's CURRENT amount (profit flow)."""
    vals: dict[str, tuple[Decimal, Decimal]] = {}
    for ln in lines:
        if ln["calc_kind"] == "mapped":
            cur_v = _q(ln["amount"] or 0) + (adjust or {}).get(ln["code"], ZERO)
            vals[ln["code"]] = (_q(cur_v), _q(ln["py_amount"] or 0))
        else:
            f = ln["formula"] or {}
            if "sum" in f:
                parts = [vals[c] for c in f["sum"]]
                vals[ln["code"]] = (_q(sum(p[0] for p in parts)), _q(sum(p[1] for p in parts)))
            elif "sub" in f:
                a, b = vals[f["sub"][0]], vals[f["sub"][1]]
                vals[ln["code"]] = (_q(a[0] - b[0]), _q(a[1] - b[1]))
            else:
                raise HTTPException(500, f"Template line {ln['code']} has no formula.")
    return vals


def _warnings(cur, engagement_id: int) -> dict:
    c = exceptions_service.counts(cur, engagement_id)
    cur.execute("""SELECT count(*) AS n, coalesce(sum(adjusted_balance), 0) AS net
                     FROM v_adjusted_tb WHERE engagement_id = %s AND line_id IS NULL""", (engagement_id,))
    u = cur.fetchone()
    return {"unmapped_ledgers": c["unmapped_ledgers"], "ledgers_without_line": u["n"], "unmapped_net_balance": _q(u["net"])}


def _profit(cur, engagement_id: int):
    eng, lines = _load(cur, engagement_id, "PL")
    return eng, lines, _evaluate(lines)


def _rows(lines: list, vals: dict, extra_detail: dict | None = None) -> list:
    rows = []
    for ln in lines:
        c, p = vals[ln["code"]]
        rows.append({"code": ln["code"], "section": ln["section"], "label": ln["label"], "note_no": ln["note_no"],
                     "kind": ln["calc_kind"], "current": c, "previous": p, "ledger_count": ln["ledger_count"] or 0,
                     **(extra_detail or {}).get(ln["code"], {})})
    return rows


def _group_sections(rows: list) -> list:
    sections: list[dict] = []
    for r in rows:
        if not sections or sections[-1]["name"] != r["section"]:
            sections.append({"name": r["section"], "lines": []})
        sections[-1]["lines"].append({k: v for k, v in r.items() if k != "section"})
    return sections


def profit_and_loss(cur, engagement_id: int) -> dict:
    eng, lines, vals = _profit(cur, engagement_id)
    pat = vals[PAT_CODE]
    return {
        "statement": "PL", "title": "Statement of Profit and Loss",
        "current_label": f"FY {eng['fy_label']}", "previous_label": _prior_label(eng["fy_label"]),
        "template_confirmed": eng["template_confirmed"], "entity_type": eng["entity_type"],
        "sections": _group_sections(_rows(lines, vals)),
        "profit_for_period": {"current": pat[0], "previous": pat[1]},
        "warnings": _warnings(cur, engagement_id),
    }


def balance_sheet(cur, engagement_id: int) -> dict:
    _, _, pl_vals = _profit(cur, engagement_id)
    profit = pl_vals[PAT_CODE][0]
    eng, lines = _load(cur, engagement_id, "BS")
    other = _q(eng["other_reserves_movement"])
    vals = _evaluate(lines, {RESERVES_CODE: _q(profit + other)})
    tb_reserves = next(_q(l["amount"] or 0) for l in lines if l["code"] == RESERVES_CODE)
    detail = {RESERVES_CODE: {"breakdown": {"per_trial_balance": tb_reserves, "profit_for_period": profit, "other_movements": other}}}

    parts = {"EQUITY AND LIABILITIES": [], "ASSETS": []}
    for r in _rows(lines, vals, detail):
        part = next((p for pre, p in _BS_PART if r["code"].startswith(pre)), "ASSETS")
        parts[part].append(r)
    total_el, total_assets = vals["BS.EQL.TOTAL"], vals["BS.ASSETS.TOTAL"]
    diff = _q(total_assets[0] - total_el[0])
    warn = _warnings(cur, engagement_id)
    if diff == 0 and warn["unmapped_ledgers"] == 0:
        status = "BALANCED"
    elif warn["unmapped_ledgers"]:
        status = "INCOMPLETE"       # unmapped ledgers are excluded; the difference is expected to remain until mapped
    else:
        status = "NOT_BALANCED"
    return {
        "statement": "BS", "title": "Balance Sheet",
        "current_label": f"FY {eng['fy_label']}", "previous_label": _prior_label(eng["fy_label"]),
        "template_confirmed": eng["template_confirmed"], "entity_type": eng["entity_type"],
        "parts": [{"name": name, "sections": _group_sections(rows)} for name, rows in parts.items()],
        "check": {"status": status, "total_assets": total_assets[0], "total_equity_and_liabilities": total_el[0],
                  "difference": diff, "previous_difference": _q(total_assets[1] - total_el[1])},
        "profit_for_period": profit,
        "warnings": warn,
    }
