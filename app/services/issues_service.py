"""T-12 Unified issue tracker: one list (table `exceptions`) for every kind of issue.

* mapping issues - written only by exceptions_service (one per unmapped ledger).
* check issues   - written only here (sync_check_issues): ONE deduplicated issue per failing active check, re-opened if the
                   check fails again, auto-cleared when it passes (or is switched off). Runs after every checks run.
* materiality issues - written only by materiality_service (one per statement line whose year-on-year change is above materiality).
* gstr9c issues - written only by gstr9c_service (one per GST head and table whose books and GSTR-9C figures differ).
* manual issues  - raised by people, cleared by people with a mandatory resolution note.
System issues cannot be cleared by hand: they clear themselves when the underlying problem is fixed at its source.
"""
import uuid
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException

from app.services import audit_service, locking

_UPSERT_CHECKS = """
INSERT INTO exceptions (engagement_id, source, reference_id, cause_code, title, detail, amount, fingerprint)
SELECT v.engagement_id, 'check', 'CHECK:' || v.check_code, 'CHECK_FAILURE',
       v.check_code || ' failed: ' || d.name,
       coalesce(v.detail ->> 'message', CASE WHEN v.variance IS NOT NULL THEN 'Difference ' || v.variance::text END, 'Check failed'),
       CASE WHEN coalesce(v.detail ->> 'unit', '') = 'count' THEN NULL ELSE v.variance END,
       md5(v.check_code || '|' || coalesce(v.variance::text, '') || '|' || coalesce(v.detail ->> 'message', ''))
  FROM v_latest_check_results v JOIN check_definitions d ON d.code = v.check_code
 WHERE v.engagement_id = %(e)s AND v.status = 'fail' AND d.is_enabled
ON CONFLICT (engagement_id, source, reference_id) DO UPDATE SET
       title = EXCLUDED.title, detail = EXCLUDED.detail, amount = EXCLUDED.amount, fingerprint = EXCLUDED.fingerprint,
       is_condition_active = TRUE, status = 'open',
       cleared_by = NULL, cleared_by_system = FALSE, cleared_at = NULL, clear_reason = NULL,
       approved_by = NULL, approved_at = NULL, approval_reason = NULL,
       last_seen_at = now(), occurrences = exceptions.occurrences + 1
 WHERE NOT exceptions.is_condition_active OR exceptions.fingerprint <> EXCLUDED.fingerprint
"""

_CLOSE_CHECKS = """
UPDATE exceptions e SET
       is_condition_active = FALSE,
       status            = CASE WHEN e.status = 'open' THEN 'cleared' ELSE e.status END,
       cleared_by_system = CASE WHEN e.status = 'open' THEN TRUE ELSE e.cleared_by_system END,
       cleared_at        = CASE WHEN e.status = 'open' THEN now() ELSE e.cleared_at END,
       clear_reason      = CASE WHEN e.status = 'open' THEN 'Auto-cleared: the check now passes (or is switched off)' ELSE e.clear_reason END
 WHERE e.engagement_id = %(e)s AND e.source = 'check' AND e.is_condition_active
   AND NOT EXISTS (SELECT 1 FROM v_latest_check_results v JOIN check_definitions d ON d.code = v.check_code
                    WHERE v.engagement_id = e.engagement_id AND 'CHECK:' || v.check_code = e.reference_id
                      AND v.status = 'fail' AND d.is_enabled)
"""


def sync_check_issues(cur, engagement_id: int) -> None:
    """Reads the latest check run (v_latest_check_results); call right after a run, in the same transaction."""
    cur.execute(_UPSERT_CHECKS, {"e": engagement_id})
    cur.execute(_CLOSE_CHECKS, {"e": engagement_id})


def open_counts(cur, engagement_id: int) -> dict:
    cur.execute("""SELECT count(*) FILTER (WHERE status = 'open') AS open, count(*) FILTER (WHERE status = 'cleared') AS cleared,
                          count(*) FILTER (WHERE status = 'approved') AS approved, count(*) AS total FROM exceptions WHERE engagement_id = %s""", (engagement_id,))
    return cur.fetchone()


def _sync_needed(cur, engagement_id: int) -> bool:
    """True when the latest check run has a failing active check that has no open check issue yet (e.g. right after an upgrade)."""
    cur.execute("""SELECT EXISTS (SELECT 1 FROM v_latest_check_results v JOIN check_definitions d ON d.code = v.check_code
                                   WHERE v.engagement_id = %(e)s AND v.status = 'fail' AND d.is_enabled
                                     AND NOT EXISTS (SELECT 1 FROM exceptions x WHERE x.engagement_id = %(e)s AND x.source = 'check'
                                                        AND x.reference_id = 'CHECK:' || v.check_code AND x.status = 'open')) AS needed""",
                {"e": engagement_id})
    return cur.fetchone()["needed"]


def list_issues(cur, engagement_id: int, source: str | None = None, status: str | None = None) -> dict:
    cur.execute("SELECT 1 FROM engagements WHERE id = %s", (engagement_id,))
    if not cur.fetchone():
        raise HTTPException(404, "Engagement not found.")
    cur.execute("""SELECT source, status, count(*) AS n FROM exceptions WHERE engagement_id = %s GROUP BY source, status""", (engagement_id,))
    by_source: dict = {s: {"open": 0, "cleared": 0} for s in ("check", "mapping", "manual", "materiality", "gstr9c", "reconciliation", "udin")}
    for r in cur.fetchall():
        by_source.setdefault(r["source"], {"open": 0, "cleared": 0})[r["status"]] = r["n"]
    cur.execute("""SELECT x.id, x.source, x.reference_id, x.cause_code, c.label AS cause, x.title, x.detail, x.amount, x.status,
                          x.occurrences, x.first_seen_at, x.last_seen_at, x.cleared_at, x.cleared_by_system,
                          x.clear_reason AS resolution_note, cu.full_name AS cleared_by_name, cr.full_name AS created_by_name,
                          x.owner_id, ou.full_name AS owner_name, x.approved_by, au.full_name AS approved_by_name, x.approved_at,
                          x.approval_reason AS approval_note, x.is_above_materiality
                     FROM exceptions x JOIN exception_causes c ON c.code = x.cause_code
                     LEFT JOIN users cu ON cu.id = x.cleared_by LEFT JOIN users cr ON cr.id = x.created_by
                     LEFT JOIN users ou ON ou.id = x.owner_id LEFT JOIN users au ON au.id = x.approved_by
                    WHERE x.engagement_id = %(e)s AND (%(s)s::text IS NULL OR x.source = %(s)s) AND (%(t)s::text IS NULL OR x.status = %(t)s)
                    ORDER BY (x.status = 'open') DESC, CASE x.source WHEN 'check' THEN 0 WHEN 'manual' THEN 1 WHEN 'materiality' THEN 2 WHEN 'gstr9c' THEN 3 WHEN 'reconciliation' THEN 4 WHEN 'udin' THEN 5 ELSE 6 END, x.id""",
                {"e": engagement_id, "s": source, "t": status})
    items = cur.fetchall()                                   # read before the cursor is reused by the queries below
    totals = open_counts(cur, engagement_id)
    return {"summary": {**totals, "by_source": by_source}, "items": items, "sync_needed": _sync_needed(cur, engagement_id)}


def create_manual(cur, user: dict, engagement_id: int, title: str, detail: str | None, amount) -> dict:
    cur.execute("SELECT 1 FROM engagements WHERE id = %s FOR UPDATE", (engagement_id,))
    if not cur.fetchone():
        raise HTTPException(404, "Engagement not found.")
    title = (title or "").strip()
    if not title:
        raise HTTPException(400, "Enter a title for the issue.")
    if len(title) > 200:
        raise HTTPException(400, "The title is too long (200 characters at most).")
    amt = None
    if amount not in (None, ""):
        try:
            amt = Decimal(str(amount).replace(",", "").strip())
        except InvalidOperation:
            raise HTTPException(400, f"'{amount}' is not a valid amount.")
        if not amt.is_finite() or amt != amt.quantize(Decimal("0.01")):
            raise HTTPException(400, "The amount must be exact to the paisa (2 decimals).")
    ref = "MANUAL:" + uuid.uuid4().hex
    cur.execute("""INSERT INTO exceptions (engagement_id, source, reference_id, cause_code, title, detail, amount, fingerprint, created_by)
                   VALUES (%s,'manual',%s,'MANUAL',%s,%s,%s,md5(%s),%s) RETURNING id""",
                (engagement_id, ref, title, (detail or "").strip() or None, amt, ref, user["id"]))
    issue_id = cur.fetchone()["id"]
    audit_service.event(cur, engagement_id, "ISSUE_RAISED", {"issue_id": issue_id, "title": title, "amount": amt},
                        entity_table="exceptions", entity_id=str(issue_id))
    return {"id": issue_id, "source": "manual", "status": "open", "title": title}


def clear_manual(cur, user: dict, engagement_id: int, issue_id: int, note: str | None) -> dict:
    cur.execute("SELECT id, source, status, title FROM exceptions WHERE id = %s AND engagement_id = %s FOR UPDATE", (issue_id, engagement_id))
    issue = cur.fetchone()
    if not issue:
        raise HTTPException(404, "Issue not found in this engagement.")
    if issue["source"] != "manual":
        raise HTTPException(400, f"A {issue['source']} issue cannot be cleared by hand: it clears automatically once the underlying "
                                 f"problem is fixed at its source.")
    if issue["status"] != "open":
        raise HTTPException(409, "This issue is already cleared.")
    note = (note or "").strip()
    if not note:
        raise HTTPException(400, "A resolution note is required to clear an issue.")
    cur.execute("""UPDATE exceptions SET status = 'cleared', is_condition_active = FALSE, cleared_by = %s, cleared_by_system = FALSE,
                          cleared_at = now(), clear_reason = %s WHERE id = %s""", (user["id"], note, issue_id))
    audit_service.event(cur, engagement_id, "ISSUE_CLEARED", {"issue_id": issue_id, "title": issue["title"]},
                        entity_table="exceptions", entity_id=str(issue_id), reason=note)
    return {"id": issue_id, "status": "cleared", "resolution_note": note}


def _load(cur, engagement_id: int, issue_id: int) -> dict:
    cur.execute("SELECT id, source, status, title, owner_id FROM exceptions WHERE id = %s AND engagement_id = %s FOR UPDATE", (issue_id, engagement_id))
    issue = cur.fetchone()
    if not issue:
        raise HTTPException(404, "Issue not found in this engagement.")
    return issue


def assign(cur, user: dict, engagement_id: int, issue_id: int, owner_id: int | None) -> dict:
    """Give an issue an owner (or none). Any working user can do it; frozen once the engagement is signed off."""
    locking.assert_unlocked(cur, engagement_id)
    issue = _load(cur, engagement_id, issue_id)
    name = None
    if owner_id is not None:
        cur.execute("""SELECT u.full_name FROM users u WHERE u.id = %s AND u.is_active AND u.id <> 1
                          AND EXISTS (SELECT 1 FROM user_roles r WHERE r.user_id = u.id AND r.role_code IN ('MAKER', 'CHECKER', 'PARTNER'))""", (owner_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(400, "Choose one of the team members as the owner.")
        name = row["full_name"]
    cur.execute("UPDATE exceptions SET owner_id = %s WHERE id = %s", (owner_id, issue_id))
    audit_service.event(cur, engagement_id, "ISSUE_ASSIGNED", {"issue_id": issue_id, "title": issue["title"], "owner": name or "Unassigned",
                                                                "assigned_by": user["full_name"]}, entity_table="exceptions", entity_id=str(issue_id))
    return {"id": issue_id, "owner_id": owner_id, "owner_name": name}


def approve(cur, user: dict, engagement_id: int, issue_id: int, note: str | None) -> dict:
    """A Checker or Partner accepts an open issue with a reason. An approved issue no longer blocks sign-off; it re-opens by itself if the
    underlying facts change (the system upserts re-open it when the fingerprint differs)."""
    locking.assert_unlocked(cur, engagement_id)
    issue = _load(cur, engagement_id, issue_id)
    note = (note or "").strip()
    if not note:
        raise HTTPException(400, "An approval note is required.")
    if issue["status"] != "open":
        raise HTTPException(409, f"Only an open issue can be approved (this one is {issue['status']}).")
    cur.execute("""UPDATE exceptions SET status = 'approved', approved_by = %s, approved_at = now(), approval_reason = %s,
                          cleared_by = NULL, cleared_by_system = FALSE, cleared_at = NULL, clear_reason = NULL WHERE id = %s""", (user["id"], note, issue_id))
    audit_service.event(cur, engagement_id, "ISSUE_APPROVED", {"issue_id": issue_id, "title": issue["title"], "source": issue["source"], "approved_by": user["full_name"]},
                        entity_table="exceptions", entity_id=str(issue_id), reason=note)
    return {"id": issue_id, "status": "approved", "approval_note": note}
