"""T-16 IA Report / Audit Committee Pack: one structured payload assembled live from the database.

Nothing is stored or hard-coded: every figure comes from the same services the screens use, so the pack can never disagree
with the Balance Sheet, P&L, adjustments register, checks, issue tracker or working paper register.
Reading the pack changes nothing; exporting it only writes an audit event (allowed even when the engagement is locked).
"""
from datetime import datetime, timezone

from fastapi import HTTPException

from app.services import (adjustments_service, audit_service, checks_service, issues_service, notes_service, ratios_service,
                          signoff_service, statement_service, working_papers_service)


def _both(line: dict) -> dict:
    return {"label": line["label"], "current": line["current"], "previous": line["previous"]}


def build(cur, engagement_id: int) -> dict:
    cur.execute("""SELECT e.id, e.fy_label, e.period_start, e.period_end, e.engagement_type, e.status, e.data_revision, e.template_confirmed_at,
                          c.name AS client_name, c.cin, c.pan, c.entity_type, t.code AS template_code, t.name AS template_name
                     FROM engagements e JOIN clients c ON c.id = e.client_id JOIN statement_templates t ON t.id = e.template_id
                    WHERE e.id = %s""", (engagement_id,))
    eng = cur.fetchone()
    if not eng:
        raise HTTPException(404, "Engagement not found.")

    gate = signoff_service.get_state(cur, engagement_id)
    signoff = gate["signoff"]
    header = {
        "entity": {"name": eng["client_name"], "cin": eng["cin"], "pan": eng["pan"], "entity_type": eng["entity_type"]},
        "financial_year": eng["fy_label"], "period_start": eng["period_start"], "period_end": eng["period_end"],
        "engagement_type": eng["engagement_type"], "engagement_status": eng["status"], "data_revision": eng["data_revision"],
        "template": {"code": eng["template_code"], "name": eng["template_name"], "confirmed": eng["template_confirmed_at"] is not None,
                     "open_question": "Q2",
                     "note": None if eng["template_confirmed_at"] else "The statement template is not yet confirmed for this entity type (open question Q2)."},
        "governance": {"is_locked": gate["is_locked"], "can_sign_off": gate["can_sign_off"], "blockers": len(gate["blockers"]),
                       "signed_off_by": signoff["signed_off_by_name"] if signoff else None,
                       "signed_off_at": signoff["signed_off_at"] if signoff else None,
                       "status": "SIGNED OFF AND LOCKED" if gate["is_locked"] else ("READY FOR SIGN-OFF" if gate["can_sign_off"] else "NOT READY: BLOCKERS OPEN")},
        "generated_at": datetime.now(timezone.utc),
    }

    bs = statement_service.balance_sheet(cur, engagement_id)
    pnl = statement_service.profit_and_loss(cur, engagement_id)
    lines = notes_service.statement_lines(bs, pnl)
    ratios = ratios_service.build(cur, engagement_id)
    financials = {
        "current_label": bs["current_label"], "previous_label": bs["previous_label"],
        "balance_sheet": {**{k: _both(lines[c]) for k, c in (
            ("shareholders_funds", "BS.EQ.TOTAL"), ("non_current_liabilities", "BS.NCL.TOTAL"), ("current_liabilities", "BS.CL.TOTAL"),
            ("total_equity_and_liabilities", "BS.EQL.TOTAL"), ("non_current_assets", "BS.NCA.TOTAL"), ("current_assets", "BS.CA.TOTAL"),
            ("total_assets", "BS.ASSETS.TOTAL"))},
            "verification": bs["check"]},
        "profit_and_loss": {k: _both(lines[c]) for k, c in (
            ("revenue_from_operations", "PL.REV"), ("other_income", "PL.OI"), ("total_income", "PL.INCOME.TOTAL"),
            ("total_expenses", "PL.EXP.TOTAL"), ("profit_before_tax", "PL.PBT"), ("tax_expense", "PL.TAX"), ("profit_for_period", "PL.PAT"))},
        "ratios": {"provisional": ratios["provisional"], "open_question": "Q7", "note": ratios["provisional_message"],
                   "items": [{k: r[k] for k in ("code", "name", "category", "formula", "unit", "value", "status", "explanation")} for r in ratios["items"]]},
        "unmapped_ledgers_excluded": bs["warnings"]["unmapped_ledgers"],
    }

    adj = adjustments_service.list_entries(cur, engagement_id)["entries"]
    atb = adjustments_service.adjusted_tb(cur, engagement_id)
    adjustments = {
        "counts": {"approved": sum(1 for e in adj if e["status"] == "approved"), "draft": sum(1 for e in adj if e["status"] == "draft"),
                   "reversed": sum(1 for e in adj if e["reversed_by_entry_number"]), "reversal_entries": sum(1 for e in adj if e["reverses_entry_number"]),
                   "total": len(adj)},
        "ledgers_with_net_adjustment": sum(1 for r in atb["rows"] if r["adjustment_net"] != 0),
        "entries": [{"entry_number": e["entry_number"], "entry_date": e["entry_date"], "description": e["description"], "status": e["status"],
                     "created_by": e["created_by_name"], "approved_by": e["approved_by_name"], "total": e["total"],
                     "reverses_entry_number": e["reverses_entry_number"], "reversed_by_entry_number": e["reversed_by_entry_number"],
                     "lines": [{"ledger": l["ledger_name"], "debit": l["debit"], "credit": l["credit"]} for l in e["lines"]]} for e in adj],
    }

    chk = checks_service.latest(cur, engagement_id)
    open_issues = issues_service.list_issues(cur, engagement_id, status="open")
    checks_and_issues = {
        "checks_summary": {k: chk["summary"].get(k) for k in ("has_run", "passed", "total", "text", "stale", "run_at", "notes")},
        "checks": [{"code": i["code"], "name": i["name"], "status": i["status"] or "not_run", "enabled": i["is_enabled"],
                    "lhs": i["lhs_amount"], "rhs": i["rhs_amount"], "variance": i["variance"],
                    "count_based": (i["detail"] or {}).get("unit") == "count",
                    "message": (i["detail"] or {}).get("message"), "pending_definition": i["definition_status"] == "pending_definition"} for i in chk["items"]],
        "issues": {"open": open_issues["summary"]["open"], "cleared": open_issues["summary"]["cleared"], "by_source": open_issues["summary"]["by_source"],
                   "open_items": [{k: x[k] for k in ("id", "source", "title", "detail", "amount")} for x in open_issues["items"]]},
    }

    wp = working_papers_service.list_links(cur, engagement_id)
    working_papers = {
        **wp["summary"],
        "uncovered": [{"target_type": t["target_type"], "target_code": t["target_code"], "label": t["label"]} for t in wp["targets"] if not t["link_count"]],
    }
    return {"header": header, "financials": financials, "adjustments": adjustments, "checks_and_issues": checks_and_issues,
            "working_papers": working_papers}


def log_export(cur, user: dict, engagement_id: int, fmt: str) -> dict:
    if fmt not in ("print", "json"):
        raise HTTPException(400, "format must be 'print' or 'json'.")
    cur.execute("SELECT data_revision FROM engagements WHERE id = %s", (engagement_id,))
    eng = cur.fetchone()
    if not eng:
        raise HTTPException(404, "Engagement not found.")
    summ = checks_service.summary(cur, engagement_id)
    audit_service.event(cur, engagement_id, "AUDIT_PACK_EXPORTED",
                        {"format": fmt, "data_revision": eng["data_revision"], "checks_passing": f"{summ['passed']} of {summ['total']}"})
    return {"logged": True, "event": "AUDIT_PACK_EXPORTED", "format": fmt, "data_revision": eng["data_revision"]}
