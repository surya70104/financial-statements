"""Everything that follows a statement-affecting change, in the SAME transaction as the change itself.

Refuse if the engagement is locked (backstop), bump the data revision, re-sync mapping exceptions, re-run the checks
engine (T-11), whose run also syncs the failing checks into the unified issue tracker (T-12), then write an audit EVENT.
Returns the exception counts (unchanged contract); the check summary is in the audit event and GET .../checks.
"""
from app.services import audit_service, checks_service, exceptions_service, locking


def after_change(cur, engagement_id: int, event_name: str, detail: dict | None = None, bump_revision: bool = True) -> dict:
    locking.assert_unlocked(cur, engagement_id)
    if bump_revision:
        cur.execute("UPDATE engagements SET data_revision = data_revision + 1 WHERE id = %s", (engagement_id,))
    result = exceptions_service.sync_mapping_exceptions(cur, engagement_id)
    checks = checks_service.run_checks(cur, engagement_id, event_name)
    audit_service.event(cur, engagement_id, event_name, {**(detail or {}), "exceptions": result, "checks": checks})
    return result
