"""T-18 UDIN dashboard. Each signed report version has exactly one UDIN slot: pending_udin -> recorded -> revoked.

The UDIN is the 18-character ICAI Unique Document Identification Number. It is generated on the ICAI portal; this module only
records it against the signed version and keeps the history. Format: exactly 18 characters, uppercase letters and digits.
"""
import re
from datetime import date

from fastapi import HTTPException

from app.services import audit_service

UDIN_RE = re.compile(r"^[A-Z0-9]{18}$")


def _engagement(cur, engagement_id: int) -> None:
    cur.execute("SELECT 1 FROM engagements WHERE id = %s", (engagement_id,))
    if not cur.fetchone():
        raise HTTPException(404, "Engagement not found.")


def _rows(cur, engagement_id: int) -> list:
    cur.execute("""SELECT u.id AS udin_id, u.signoff_version_id AS version_id, v.version_number, v.status AS version_status,
                          v.signed_off_at, su.full_name AS signed_off_by_name, v.reopen_reason,
                          v.snapshot_json -> 'balance_sheet' ->> 'total_assets' AS total_assets,
                          v.snapshot_json -> 'profit_and_loss' ->> 'profit_for_period' AS profit_for_period,
                          u.status, u.udin_number, u.document_type, u.udin_generated_date,
                          u.recorded_at, ru.full_name AS recorded_by_name, u.revoked_at, vu.full_name AS revoked_by_name, u.revoked_reason
                     FROM udin_records u
                     JOIN report_signoff_versions v ON v.id = u.signoff_version_id
                     JOIN users su ON su.id = v.signed_off_by
                     LEFT JOIN users ru ON ru.id = u.recorded_by LEFT JOIN users vu ON vu.id = u.revoked_by
                    WHERE u.engagement_id = %s ORDER BY v.version_number DESC""", (engagement_id,))
    return cur.fetchall()


def dashboard(cur, engagement_id: int) -> dict:
    _engagement(cur, engagement_id)
    rows = _rows(cur, engagement_id)
    count = lambda st: sum(1 for r in rows if r["status"] == st)
    return {"summary": {"total_versions": len(rows), "pending_count": count("pending_udin"), "recorded_count": count("recorded"),
                        "revoked_count": count("revoked")},
            "items": rows}


def _slot(cur, engagement_id: int, version_id: int) -> dict:
    cur.execute("SELECT id, status FROM udin_records WHERE engagement_id = %s AND signoff_version_id = %s FOR UPDATE", (engagement_id, version_id))
    rec = cur.fetchone()
    if not rec:
        raise HTTPException(404, "Signed version not found for this engagement.")
    return rec


def _item(cur, engagement_id: int, version_id: int) -> dict:
    return next(r for r in _rows(cur, engagement_id) if r["version_id"] == version_id)


def record(cur, user: dict, engagement_id: int, version_id: int, udin_number: str | None, generated_date: str | None,
           document_type: str | None) -> dict:
    _engagement(cur, engagement_id)
    udin = (udin_number or "").strip()
    if not UDIN_RE.match(udin):
        raise HTTPException(400, "A UDIN is exactly 18 characters: uppercase letters A-Z and digits 0-9 only.")
    try:
        gen = date.fromisoformat((generated_date or "").strip())
    except ValueError:
        raise HTTPException(400, "Enter the UDIN generation date as YYYY-MM-DD.")
    if gen > date.today():
        raise HTTPException(400, "The UDIN generation date cannot be in the future.")
    rec = _slot(cur, engagement_id, version_id)
    if rec["status"] != "pending_udin":
        raise HTTPException(409, f"This version's UDIN is already {rec['status'].replace('_udin', '')}; it cannot be recorded again.")
    cur.execute("SELECT 1 FROM udin_records WHERE udin_number = %s", (udin,))
    if cur.fetchone():
        raise HTTPException(409, "This UDIN is already recorded against another signed version.")
    doc = (document_type or "").strip() or "Statutory Audit Report"
    cur.execute("""UPDATE udin_records SET status = 'recorded', udin_number = %s, udin_generated_date = %s, document_type = %s,
                          recorded_by = %s, recorded_at = now() WHERE id = %s""", (udin, gen, doc, user["id"], rec["id"]))
    sync_issues(cur, engagement_id)
    item = _item(cur, engagement_id, version_id)
    audit_service.event(cur, engagement_id, "UDIN_RECORDED",
                        {"version_number": item["version_number"], "udin_number": udin, "udin_generated_date": gen, "document_type": doc,
                         "recorded_by": user["full_name"]}, entity_table="udin_records", entity_id=str(rec["id"]))
    return item


def revoke(cur, user: dict, engagement_id: int, version_id: int, reason: str | None) -> dict:
    _engagement(cur, engagement_id)
    reason = (reason or "").strip()
    if not reason:
        raise HTTPException(400, "A reason is required to revoke a UDIN.")
    rec = _slot(cur, engagement_id, version_id)
    if rec["status"] != "recorded":
        raise HTTPException(409, "Only a recorded UDIN can be revoked.")
    cur.execute("""UPDATE udin_records SET status = 'revoked', revoked_by = %s, revoked_at = now(), revoked_reason = %s WHERE id = %s""",
                (user["id"], reason, rec["id"]))
    sync_issues(cur, engagement_id)
    item = _item(cur, engagement_id, version_id)
    audit_service.event(cur, engagement_id, "UDIN_REVOKED",
                        {"version_number": item["version_number"], "udin_number": item["udin_number"], "revoked_by": user["full_name"]},
                        entity_table="udin_records", entity_id=str(rec["id"]), reason=reason)
    return item


_UPSERT_UDIN = """
INSERT INTO exceptions (engagement_id, source, reference_id, cause_code, title, detail, fingerprint)
VALUES (%(e)s, 'udin', %(ref)s, 'MISSING_UDIN', %(title)s, %(detail)s, md5(%(fp)s))
ON CONFLICT (engagement_id, source, reference_id) DO UPDATE SET
       title = EXCLUDED.title, detail = EXCLUDED.detail, fingerprint = EXCLUDED.fingerprint,
       is_condition_active = TRUE, status = 'open',
       cleared_by = NULL, cleared_by_system = FALSE, cleared_at = NULL, clear_reason = NULL,
       approved_by = NULL, approved_at = NULL, approval_reason = NULL,
       last_seen_at = now(), occurrences = exceptions.occurrences + 1
 WHERE NOT exceptions.is_condition_active OR exceptions.fingerprint <> EXCLUDED.fingerprint
"""
_CLOSE_UDIN = """
UPDATE exceptions e SET
       is_condition_active = FALSE,
       status            = CASE WHEN e.status = 'open' THEN 'cleared' ELSE e.status END,
       cleared_by_system = CASE WHEN e.status = 'open' THEN TRUE ELSE e.cleared_by_system END,
       cleared_at        = CASE WHEN e.status = 'open' THEN now() ELSE e.cleared_at END,
       clear_reason      = CASE WHEN e.status = 'open' THEN 'Auto-cleared: the UDIN is recorded (or the report version was withdrawn by re-opening)' ELSE e.clear_reason END
 WHERE e.engagement_id = %(e)s AND e.source = 'udin' AND e.is_condition_active AND e.reference_id <> ALL(%(keep)s)
"""


def sync_issues(cur, engagement_id: int) -> None:
    """One deduplicated issue ('udin:v<n>') for every ACTIVE signed version whose UDIN is still pending or was revoked. It clears when
    the UDIN is recorded, and when the version is withdrawn by a re-opening (a withdrawn report needs no UDIN). Idempotent."""
    cur.execute("""SELECT v.version_number, u.status AS udin_status FROM udin_records u JOIN report_signoff_versions v ON v.id = u.signoff_version_id
                    WHERE u.engagement_id = %s AND v.status = 'signed_locked' AND u.status IN ('pending_udin', 'revoked')""", (engagement_id,))
    keep = []
    for r in cur.fetchall():
        ref = f"udin:v{r['version_number']}"
        keep.append(ref)
        revoked = r["udin_status"] == "revoked"
        cur.execute(_UPSERT_UDIN, {"e": engagement_id, "ref": ref, "fp": f"{ref}|{r['udin_status']}",
                                   "title": f"Signed report v{r['version_number']} has no UDIN" if not revoked else f"UDIN of signed report v{r['version_number']} was revoked",
                                   "detail": "Record the 18-character ICAI UDIN for this signed version on the UDIN tab."})
    cur.execute(_CLOSE_UDIN, {"e": engagement_id, "keep": keep})
