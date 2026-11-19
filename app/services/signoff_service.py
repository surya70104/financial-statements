"""T-13 Hard sign-off blocker gate.

Sign-off is possible only when there are ZERO blockers: every enabled check passes and no issue is open.
A pending-definition check (CHK-06 while Q1 is unanswered) fails, so it blocks sign-off by design.
Only a Partner may sign off. Signing off freezes the engagement (see locking.py).
"""
import json
from decimal import Decimal

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.services import audit_service, checks_service, locking, notes_service, statement_service


def _plain(value):
    """Decimal -> str (HTTP error bodies bypass our JSON encoder, which would turn Decimal into float)."""
    return str(value) if isinstance(value, Decimal) else value


def _blockers(cur, engagement_id: int) -> list:
    latest = checks_service.latest(cur, engagement_id)
    blockers = []
    if not latest["summary"]["has_run"]:
        blockers.append({"type": "CHECKS_NOT_RUN", "title": "The checks have not been run yet."})
    for it in latest["items"]:
        if not it["is_enabled"] or it["status"] == "pass":
            continue
        d = it["detail"] or {}
        message = d.get("message") or (f"Difference {_plain(it['variance'])}" if it["variance"] is not None else "Not passing")
        blockers.append({"type": "CHECK", "code": it["code"], "title": f"{it['code']} {it['name']}", "status": it["status"] or "not_run",
                         "message": message, "pending_definition": it["definition_status"] == "pending_definition",
                         "variance": _plain(it["variance"]), "count_based": d.get("unit") == "count"})
    cur.execute("""SELECT id, source, title, detail, amount FROM exceptions WHERE engagement_id = %s AND status = 'open'
                    ORDER BY CASE source WHEN 'check' THEN 0 WHEN 'manual' THEN 1 ELSE 2 END, id""", (engagement_id,))
    for x in cur.fetchall():
        blockers.append({"type": "ISSUE", "issue_id": x["id"], "source": x["source"], "title": x["title"], "message": x["detail"],
                         "amount": _plain(x["amount"])})
    return blockers


def _record(cur, engagement_id: int):
    cur.execute("""SELECT s.id, s.engagement_id, s.signed_off_by, u.full_name AS signed_off_by_name, s.signed_off_at, s.signoff_note,
                          s.snapshot_json, s.is_locked,
                          v.id AS version_id, v.version_number
                     FROM engagement_signoffs s JOIN users u ON u.id = s.signed_off_by
                     LEFT JOIN LATERAL (SELECT id, version_number FROM report_signoff_versions
                                         WHERE engagement_id = s.engagement_id ORDER BY version_number DESC LIMIT 1) v ON TRUE
                    WHERE s.engagement_id = %s""", (engagement_id,))
    return cur.fetchone()


def list_versions(cur, engagement_id: int) -> list:
    """Every signed version (v1, v2, ...), newest first, with headline totals and its UDIN status. The full snapshot stays in the DB."""
    cur.execute("""SELECT v.id, v.version_number, v.status, v.signed_off_by, su.full_name AS signed_off_by_name, v.signed_off_at, v.signoff_note,
                          v.reopened_by, ru.full_name AS reopened_by_name, v.reopened_at, v.reopen_reason,
                          v.snapshot_json -> 'balance_sheet' AS balance_sheet, v.snapshot_json -> 'profit_and_loss' AS profit_and_loss,
                          v.snapshot_json -> 'checks' AS checks, v.snapshot_json -> 'data_revision' AS data_revision,
                          u.id AS udin_id, u.status AS udin_status, u.udin_number
                     FROM report_signoff_versions v
                     JOIN users su ON su.id = v.signed_off_by LEFT JOIN users ru ON ru.id = v.reopened_by
                     LEFT JOIN udin_records u ON u.signoff_version_id = v.id
                    WHERE v.engagement_id = %s ORDER BY v.version_number DESC""", (engagement_id,))
    return cur.fetchall()


def get_state(cur, engagement_id: int) -> dict:
    cur.execute("SELECT 1 FROM engagements WHERE id = %s", (engagement_id,))
    if not cur.fetchone():
        raise HTTPException(404, "Engagement not found.")
    record = _record(cur, engagement_id)
    blockers = _blockers(cur, engagement_id)
    summary = checks_service.summary(cur, engagement_id)
    locked = bool(record and record["is_locked"])
    versions = list_versions(cur, engagement_id)
    active = next((v for v in versions if v["status"] == "signed_locked"), None)
    return {"can_sign_off": not locked and not blockers, "is_locked": locked, "blockers": blockers,
            "active_version": active, "versions": versions,
            "next_version_number": (versions[0]["version_number"] + 1) if versions else 1,
            "counts": {"checks": sum(1 for b in blockers if b["type"] in ("CHECK", "CHECKS_NOT_RUN")),
                       "issues": sum(1 for b in blockers if b["type"] == "ISSUE")},
            "checks_summary": summary, "signoff": record}


def _snapshot(cur, engagement_id: int, run: dict) -> dict:
    bs = statement_service.balance_sheet(cur, engagement_id)
    pnl = statement_service.profit_and_loss(cur, engagement_id)
    lines = notes_service.statement_lines(bs, pnl)
    cur.execute("SELECT data_revision, fy_label FROM engagements WHERE id = %s", (engagement_id,))
    eng = cur.fetchone()
    cur.execute("SELECT count(*) AS n FROM v_adjusted_tb WHERE engagement_id = %s", (engagement_id,))
    ledgers = cur.fetchone()["n"]
    cur.execute("SELECT count(*) AS n FROM journal_entries WHERE engagement_id = %s AND status = 'approved'", (engagement_id,))
    approved = cur.fetchone()["n"]
    cur.execute("SELECT count(*) AS n FROM exceptions WHERE engagement_id = %s AND status = 'cleared'", (engagement_id,))
    cleared = cur.fetchone()["n"]
    cur_ = lambda code: lines[code]["current"]
    return {
        "fy": eng["fy_label"], "data_revision": eng["data_revision"], "active_ledgers": ledgers, "approved_adjustments": approved,
        "cleared_issues": cleared, "checks": {"run_id": run["run_id"], "passed": run["passed"], "total": run["total"]},
        "balance_sheet": {"total_assets": cur_("BS.ASSETS.TOTAL"), "total_equity_and_liabilities": cur_("BS.EQL.TOTAL"),
                          "shareholders_funds": cur_("BS.EQ.TOTAL"), "difference": bs["check"]["difference"]},
        "profit_and_loss": {"revenue_from_operations": cur_("PL.REV"), "total_income": cur_("PL.INCOME.TOTAL"),
                            "total_expenses": cur_("PL.EXP.TOTAL"), "profit_for_period": pnl["profit_for_period"]["current"]},
    }


def sign_off(cur, user: dict, engagement_id: int, note: str | None) -> dict:
    cur.execute("SELECT 1 FROM engagements WHERE id = %s FOR UPDATE", (engagement_id,))
    if not cur.fetchone():
        raise HTTPException(404, "Engagement not found.")
    if locking.is_locked(cur, engagement_id):
        raise HTTPException(409, "The engagement is already signed off and locked.")
    run = checks_service.run_checks(cur, engagement_id, "SIGNOFF_ATTEMPT")        # the gate always uses fresh results
    blockers = _blockers(cur, engagement_id)
    if blockers:
        raise HTTPException(409, {"message": f"Sign-off blocked: {len(blockers)} blocker(s) must be resolved first.", "blockers": blockers})
    snap = _snapshot(cur, engagement_id, run)
    from app.services import pack_service                 # imported here: pack_service itself reads the sign-off state
    full = {**snap, "pack": pack_service.build(cur, engagement_id)}          # the frozen financial + pack snapshot for this version
    note = (note or "").strip() or None
    dumps = lambda o: json.dumps(o, default=str)
    cur.execute("SELECT coalesce(max(version_number), 0) + 1 AS n FROM report_signoff_versions WHERE engagement_id = %s", (engagement_id,))
    version_number = cur.fetchone()["n"]
    cur.execute("SELECT id FROM engagement_signoffs WHERE engagement_id = %s", (engagement_id,))
    existing = cur.fetchone()
    if existing:                                          # a re-sign-off after a formal re-opening: the lock row is re-armed
        cur.execute("SELECT set_config('app.signoff_transition', 'on', true)")
        cur.execute("""UPDATE engagement_signoffs SET signed_off_by = %s, signed_off_at = now(), signoff_note = %s, snapshot_json = %s, is_locked = TRUE
                        WHERE id = %s RETURNING id""", (user["id"], note, Jsonb(snap, dumps=dumps), existing["id"]))
    else:
        cur.execute("""INSERT INTO engagement_signoffs (engagement_id, signed_off_by, signoff_note, snapshot_json, is_locked)
                       VALUES (%s,%s,%s,%s,TRUE) RETURNING id""", (engagement_id, user["id"], note, Jsonb(snap, dumps=dumps)))
    sid = cur.fetchone()["id"]
    cur.execute("""INSERT INTO report_signoff_versions (engagement_id, version_number, status, signed_off_by, signoff_note, snapshot_json)
                   VALUES (%s,%s,'signed_locked',%s,%s,%s) RETURNING id""", (engagement_id, version_number, user["id"], note, Jsonb(full, dumps=dumps)))
    vid = cur.fetchone()["id"]
    cur.execute("INSERT INTO udin_records (engagement_id, signoff_version_id) VALUES (%s,%s) RETURNING id", (engagement_id, vid))
    uid = cur.fetchone()["id"]
    audit_service.event(cur, engagement_id, "ENGAGEMENT_SIGNED_OFF", {"signoff_id": sid, "signed_off_by": user["full_name"], "snapshot": snap},
                        entity_table="engagement_signoffs", entity_id=str(sid), reason=note)
    audit_service.event(cur, engagement_id, "REPORT_VERSION_SIGNED_OFF",
                        {"version_id": vid, "version_number": version_number, "signed_off_by": user["full_name"], "udin_record_id": uid,
                         "data_revision": snap["data_revision"], "total_assets": snap["balance_sheet"]["total_assets"],
                         "profit_for_period": snap["profit_and_loss"]["profit_for_period"]},
                        entity_table="report_signoff_versions", entity_id=str(vid), reason=note)
    return _record(cur, engagement_id)


def reopen(cur, user: dict, engagement_id: int, reason: str | None) -> dict:
    """Formal Partner re-opening: the active version becomes 'superseded' and the engagement unlocks until the next version is signed."""
    reason = (reason or "").strip()
    if not reason:
        raise HTTPException(400, "A reason is required to re-open a signed-off engagement.")
    cur.execute("SELECT 1 FROM engagements WHERE id = %s FOR UPDATE", (engagement_id,))
    if not cur.fetchone():
        raise HTTPException(404, "Engagement not found.")
    cur.execute("SELECT id, version_number FROM report_signoff_versions WHERE engagement_id = %s AND status = 'signed_locked'", (engagement_id,))
    active = cur.fetchone()
    if not locking.is_locked(cur, engagement_id) or not active:
        raise HTTPException(409, "The engagement is not signed off, so there is nothing to re-open.")
    cur.execute("""UPDATE report_signoff_versions SET status = 'superseded', reopened_by = %s, reopened_at = now(), reopen_reason = %s
                    WHERE id = %s""", (user["id"], reason, active["id"]))
    cur.execute("SELECT set_config('app.signoff_transition', 'on', true)")
    cur.execute("UPDATE engagement_signoffs SET is_locked = FALSE WHERE engagement_id = %s", (engagement_id,))
    audit_service.event(cur, engagement_id, "REPORT_VERSION_REOPENED",
                        {"version_id": active["id"], "version_number": active["version_number"], "reopened_by": user["full_name"]},
                        entity_table="report_signoff_versions", entity_id=str(active["id"]), reason=reason)
    return get_state(cur, engagement_id)
