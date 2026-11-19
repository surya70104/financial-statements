"""T-11 Checks engine. Every enabled check is evaluated in exact Decimal and stored as one run (history is append-only).

A check's definition (code, rule type, config, on/off) is data in check_definitions; the code here is a registry of
rule types. Counting rule (Default #2): only ENABLED checks are counted, and a check pending definition (CHK-06) counts
as not passing, so it blocks sign-off until Q1 is answered.
"""
from decimal import Decimal

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.services import issues_service, notes_service, statement_service

ZERO = Decimal("0.00")


def _q(v) -> Decimal:
    return Decimal(v).quantize(Decimal("0.01"))


def _find_line(bs: dict, code: str) -> dict:
    for part in bs["parts"]:
        for sec in part["sections"]:
            for ln in sec["lines"]:
                if ln["code"] == code:
                    return ln
    raise HTTPException(500, f"Template line {code} not found.")


# --- rule registry: each returns (status, lhs, rhs, detail) ----------------------------------------------------------
def _bs_balance(cur, eid, cfg, ctx):
    chk = ctx["bs"]["check"]
    lhs, rhs = chk["total_assets"], chk["total_equity_and_liabilities"]
    return ("pass" if lhs == rhs else "fail"), lhs, rhs, {"labels": ["Total assets", "Total equity and liabilities"]}


def _profit_ties_reserves(cur, eid, cfg, ctx):
    code = cfg.get("reserves_line", "BS.EQ.RS")
    bs = ctx["bs"]
    closing = _q(_find_line(bs, code)["current"])
    cur.execute("""SELECT coalesce(sum(lb.opening_balance), 0) AS opening
                     FROM v_adjusted_tb a JOIN ledger_balances lb ON lb.ledger_id = a.ledger_id
                     JOIN template_lines tl ON tl.id = a.line_id
                    WHERE a.engagement_id = %s AND tl.code = %s""", (eid, code))
    opening = _q(-cur.fetchone()["opening"])                       # Cr-natured: present as positive
    other = _q(ctx["other_reserves_movement"])
    profit = _q(bs["profit_for_period"])
    lhs = _q(closing - opening - other)
    return ("pass" if lhs == profit else "fail"), lhs, profit, {
        "reserves_closing": closing, "reserves_opening": opening, "other_reserves_movement": other,
        "labels": ["Reserves closing - opening - other movements", "Profit / (loss) for the period"]}


def _notes_tie(cur, eid, cfg, ctx):
    t = notes_service.build(cur, eid, ctx["bs"], ctx["pnl"])["tie_summary"]
    total = t["total_absolute_difference"]
    return ("pass" if not t["failing"] else "fail"), total, ZERO, {
        "labels": ["Sum of note vs statement-line differences", "Expected"], "notes_checked": t["notes"],
        "notes_tying": t["tying"], "failing_notes": t["failing"]}


def _mapping_complete(cur, eid, cfg, ctx):
    cur.execute("""SELECT count(*) AS n, coalesce(sum(lb.closing_balance), 0) AS net
                     FROM v_unmapped_ledgers u LEFT JOIN ledger_balances lb ON lb.ledger_id = u.ledger_id
                    WHERE u.engagement_id = %s""", (eid,))
    r = cur.fetchone()
    return ("pass" if r["n"] == 0 else "fail"), Decimal(r["n"]), ZERO, {
        "unit": "count", "unmapped_ledgers": r["n"], "unmapped_net_balance": _q(r["net"]),
        "labels": ["Unmapped active ledgers", "Expected"]}


def _comparatives_present(cur, eid, cfg, ctx):
    cur.execute("""SELECT count(*) AS expected, count(py.id) AS present
                     FROM engagements e JOIN template_lines tl ON tl.template_id = e.template_id AND tl.calc_kind = 'mapped'
                     LEFT JOIN prior_year_amounts py ON py.engagement_id = e.id AND py.line_id = tl.id
                    WHERE e.id = %s""", (eid,))
    r = cur.fetchone()
    return ("pass" if r["expected"] == r["present"] else "fail"), Decimal(r["expected"]), Decimal(r["present"]), {
        "unit": "count", "labels": ["Statement lines", "Lines with a comparative"], "missing_lines": r["expected"] - r["present"]}


def _pending_definition(cur, eid, cfg, ctx):
    return "fail", None, None, {"message": cfg.get("message", "Definition pending."), "pending_definition": True}


REGISTRY = {"bs_balance": _bs_balance, "profit_ties_reserves": _profit_ties_reserves, "notes_tie": _notes_tie,
            "mapping_complete": _mapping_complete, "comparatives_present": _comparatives_present,
            "pending_definition": _pending_definition}


def run_checks(cur, engagement_id: int, trigger_event: str) -> dict:
    """Evaluate all checks, store the run, return the summary. Runs inside the caller's transaction."""
    cur.execute("SELECT data_revision, other_reserves_movement FROM engagements WHERE id = %s", (engagement_id,))
    eng = cur.fetchone()
    if not eng:
        raise HTTPException(404, "Engagement not found.")
    ctx = {"bs": statement_service.balance_sheet(cur, engagement_id), "pnl": statement_service.profit_and_loss(cur, engagement_id),
           "other_reserves_movement": eng["other_reserves_movement"]}
    cur.execute("SELECT code, rule_type, rule_config, is_enabled FROM check_definitions ORDER BY sort_order")
    results = []
    for d in cur.fetchall():
        if not d["is_enabled"]:
            results.append((d["code"], "not_run", None, None, {"message": "Switched off."}))
            continue
        status, lhs, rhs, detail = REGISTRY[d["rule_type"]](cur, engagement_id, d["rule_config"], ctx)
        results.append((d["code"], status, lhs, rhs, detail))
    active = [r for r in results if r[1] != "not_run"]
    passed = sum(1 for r in active if r[1] == "pass")
    cur.execute("""INSERT INTO check_runs (engagement_id, data_revision, trigger_event, triggered_by, passed_count, total_count)
                   VALUES (%s,%s,%s,current_setting('app.user_id')::bigint,%s,%s) RETURNING id""",
                (engagement_id, eng["data_revision"], trigger_event, passed, len(active)))
    run_id = cur.fetchone()["id"]
    for code, status, lhs, rhs, detail in results:
        cur.execute("INSERT INTO check_results (run_id, check_code, status, lhs_amount, rhs_amount, detail) VALUES (%s,%s,%s,%s,%s,%s)",
                    (run_id, code, status, lhs, rhs, Jsonb(detail, dumps=_dumps)))
    issues_service.sync_check_issues(cur, engagement_id)       # T-12: one issue per failing check, auto-cleared when it passes
    return {"run_id": run_id, "passed": passed, "total": len(active),
            "failing": [r[0] for r in active if r[1] != "pass"]}


def _dumps(obj):
    import json
    return json.dumps(obj, default=str)


def summary(cur, engagement_id: int) -> dict:
    cur.execute("""SELECT r.id, r.passed_count, r.total_count, r.run_at, r.data_revision, e.data_revision AS current_revision
                     FROM engagements e LEFT JOIN check_runs r ON r.engagement_id = e.id
                    WHERE e.id = %s ORDER BY r.id DESC NULLS LAST LIMIT 1""", (engagement_id,))
    r = cur.fetchone()
    if not r or r["id"] is None:
        return {"has_run": False, "passed": 0, "total": 0, "stale": True}
    cur.execute("SELECT count(*) AS n FROM check_definitions WHERE is_enabled")
    # stale = the data changed since the run, OR the set of enabled checks changed (e.g. CHK-03 switched on by a migration)
    stale = r["data_revision"] != r["current_revision"] or r["total_count"] != cur.fetchone()["n"]
    return {"has_run": True, "run_id": r["id"], "passed": r["passed_count"], "total": r["total_count"],
            "run_at": r["run_at"], "stale": stale}


def latest(cur, engagement_id: int) -> dict:
    summ = summary(cur, engagement_id)
    cur.execute("""SELECT d.code, d.name, d.description, d.rule_type, d.definition_status, d.is_enabled, d.enabled_from_step,
                          x.status, x.lhs_amount, x.rhs_amount, x.variance, x.detail
                     FROM check_definitions d
                     LEFT JOIN check_results x ON x.check_code = d.code AND x.run_id = %s
                    ORDER BY d.sort_order""", (summ.get("run_id"),))
    items = cur.fetchall()
    for it in items:
        if not it["is_enabled"]:
            it["status"] = "not_run"
    notes = [f"{i['code']} switches on in Step {i['enabled_from_step']}" for i in items if not i["is_enabled"]]
    notes += [f"{i['code']} awaits Q1" for i in items if i["definition_status"] == "pending_definition"]
    return {"summary": {**summ, "notes": notes,
                        "text": f"{summ['passed']} of {summ['total']} active checks passing" if summ["has_run"] else "Checks not run yet"},
            "items": items}
