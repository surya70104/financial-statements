"""T-01: idempotent trial balance import.

Identity of a ledger is (engagement, ledger_key). Re-importing UPSERTs on that key, so ledgers can never be
duplicated; ledgers missing from the new file are marked inactive (never deleted) so their saved mappings survive.
A re-import that changes nothing writes no ledger rows at all.
"""
import hashlib
from decimal import Decimal

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.db import tx
from app.services import audit_service, change_coordinator, locking
from app.services.tb_parser import ImportFileError, parse_trial_balance


def _log_failure(user_id, engagement_id, profile_id, filename, sha, message, errors, rows_total=0, mismatches=0) -> int:
    """Failed imports are logged in their own transaction (the main one is rolled back / never started)."""
    with tx(user_id) as cur:
        cur.execute(
            """INSERT INTO upload_history (engagement_id, profile_id, source_file_name, file_sha256, uploaded_by, status,
                                           rows_total, closing_mismatch_count, message, error_detail)
               VALUES (%s,%s,%s,%s,%s,'failed',%s,%s,%s,%s) RETURNING id""",
            (engagement_id, profile_id, filename, sha, user_id, rows_total, mismatches, message, Jsonb(errors[:200])))
        upload_id = cur.fetchone()["id"]
        audit_service.event(cur, engagement_id, "TB_IMPORT_FAILED", {"file": filename, "upload_id": upload_id, "message": message},
                            entity_table="upload_history", entity_id=str(upload_id))
        return upload_id


def import_trial_balance(engagement_id: int, user: dict, filename: str, data: bytes) -> dict:
    sha = hashlib.sha256(data).hexdigest()
    ext = "xlsx" if filename.lower().endswith((".xlsx", ".xlsm")) else "csv"
    with tx() as cur:
        cur.execute("SELECT id FROM engagements WHERE id = %s", (engagement_id,))
        if not cur.fetchone():
            raise HTTPException(404, "Engagement not found.")
        locking.assert_unlocked(cur, engagement_id)       # 409 before anything is parsed or logged
        cur.execute("SELECT id, column_map FROM import_profiles WHERE format = %s AND is_default ORDER BY id LIMIT 1", (ext,))
        profile = cur.fetchone()
    profile_id = profile["id"] if profile else None
    if not profile:
        raise HTTPException(500, f"No default import profile for {ext} files.")

    try:
        parsed = parse_trial_balance(filename, data, profile["column_map"])
    except ImportFileError as exc:
        upload_id = _log_failure(user["id"], engagement_id, profile_id, filename, sha, str(exc), [{"row": 0, "ledger": "", "message": str(exc)}])
        raise HTTPException(422, {"message": str(exc), "upload_id": upload_id, "errors": []})
    if parsed.errors:
        msg = f"Import rejected: {len(parsed.errors)} validation error(s). Nothing was changed."
        upload_id = _log_failure(user["id"], engagement_id, profile_id, filename, sha, msg, parsed.errors,
                                 len(parsed.rows), parsed.mismatch_count)
        raise HTTPException(422, {"message": msg, "upload_id": upload_id, "errors": parsed.errors[:200]})

    try:
        return _commit(engagement_id, user, profile_id, filename, sha, parsed)
    except HTTPException:
        raise
    except Exception as exc:
        upload_id = _log_failure(user["id"], engagement_id, profile_id, filename, sha, f"Database error: {exc}", [])
        raise HTTPException(500, {"message": f"Import failed and was rolled back: {exc}", "upload_id": upload_id, "errors": []})


def _commit(engagement_id, user, profile_id, filename, sha, parsed) -> dict:
    with tx(user["id"]) as cur:
        cur.execute("SELECT id FROM engagements WHERE id = %s FOR UPDATE", (engagement_id,))   # serialise imports/mappings

        # What exists now?
        cur.execute("""SELECT l.id, l.ledger_key, l.name, l.is_active, g.name AS group_name,
                              lb.opening_balance, lb.period_debit, lb.period_credit, lb.closing_balance
                         FROM ledgers l
                         LEFT JOIN tally_groups g ON g.id = l.group_id
                         LEFT JOIN ledger_balances lb ON lb.ledger_id = l.id
                        WHERE l.engagement_id = %s AND l.origin = 'tally'""", (engagement_id,))
        existing = {r["ledger_key"]: r for r in cur.fetchall()}

        inserted, updated, unchanged = [], [], []
        for row in parsed.rows:
            cur_row = existing.get(row.key)
            if cur_row is None:
                inserted.append(row)
            elif (cur_row["name"], cur_row["group_name"], cur_row["is_active"],
                  cur_row["opening_balance"], cur_row["period_debit"], cur_row["period_credit"], cur_row["closing_balance"]) == \
                 (row.name, row.group, True, row.opening, row.debit, row.credit, row.closing):
                unchanged.append(row)
            else:
                updated.append(row)
        in_file = {r.key for r in parsed.rows}
        dropped = [r for k, r in existing.items() if k not in in_file and r["is_active"]]

        cur.execute(
            """INSERT INTO upload_history (engagement_id, profile_id, source_file_name, file_sha256, uploaded_by, status, rows_total,
                      ledgers_inserted, ledgers_updated, ledgers_unchanged, ledgers_deactivated, total_debit, total_credit,
                      closing_mismatch_count, message)
               VALUES (%s,%s,%s,%s,%s,'committed',%s,%s,%s,%s,%s,%s,%s,0,%s) RETURNING id""",
            (engagement_id, profile_id, filename, sha, user["id"], len(parsed.rows), len(inserted), len(updated),
             len(unchanged), len(dropped), parsed.total_debit, parsed.total_credit,
             f"{len(inserted)} new, {len(updated)} updated, {len(unchanged)} unchanged, {len(dropped)} deactivated"))
        upload_id = cur.fetchone()["id"]

        cur.executemany("INSERT INTO tb_import_rows (upload_id, row_no, ledger_key, raw, parse_note) VALUES (%s,%s,%s,%s,%s)",
                        [(upload_id, r.row_no, r.key, Jsonb(r.raw), "; ".join(r.notes) or None) for r in parsed.rows])

        # Groups (upsert by name; nature only changes when the file says something different)
        group_ids = {}
        for name, nature in {(r.group, r.nature) for r in parsed.rows}:
            cur.execute("""INSERT INTO tally_groups (engagement_id, name, nature) VALUES (%s,%s,%s)
                           ON CONFLICT (engagement_id, name) DO UPDATE SET nature = EXCLUDED.nature
                             WHERE tally_groups.nature <> EXCLUDED.nature
                           RETURNING id""", (engagement_id, name, nature))
            got = cur.fetchone()
            if got is None:
                cur.execute("SELECT id FROM tally_groups WHERE engagement_id = %s AND name = %s", (engagement_id, name))
                got = cur.fetchone()
            group_ids[name] = got["id"]

        for row in inserted + updated:
            cur.execute(
                """INSERT INTO ledgers (engagement_id, ledger_key, name, group_id, is_active, first_upload_id, last_upload_id)
                   VALUES (%s,%s,%s,%s,TRUE,%s,%s)
                   ON CONFLICT (engagement_id, ledger_key) DO UPDATE SET
                        name = EXCLUDED.name, group_id = EXCLUDED.group_id, is_active = TRUE, last_upload_id = EXCLUDED.last_upload_id
                   RETURNING id""",
                (engagement_id, row.key, row.name, group_ids[row.group], upload_id, upload_id))
            ledger_id = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO ledger_balances (ledger_id, engagement_id, opening_balance, period_debit, period_credit,
                                                closing_balance, closing_per_source, last_upload_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (ledger_id) DO UPDATE SET
                        opening_balance = EXCLUDED.opening_balance, period_debit = EXCLUDED.period_debit,
                        period_credit = EXCLUDED.period_credit, closing_balance = EXCLUDED.closing_balance,
                        closing_per_source = EXCLUDED.closing_per_source, last_upload_id = EXCLUDED.last_upload_id""",
                (ledger_id, engagement_id, row.opening, row.debit, row.credit, row.closing, row.closing_per_source, upload_id))

        if dropped:
            cur.execute("UPDATE ledgers SET is_active = FALSE, last_upload_id = %s WHERE id = ANY(%s)",
                        (upload_id, [r["id"] for r in dropped]))

        changed = bool(inserted or updated or dropped)
        summary = {"upload_id": upload_id, "file": filename, "rows": len(parsed.rows), "inserted": len(inserted),
                   "updated": len(updated), "unchanged": len(unchanged), "deactivated": len(dropped),
                   "deactivated_ledgers": [r["name"] for r in dropped], "changed": changed,
                   "total_debit": parsed.total_debit, "total_credit": parsed.total_credit}
        exc = change_coordinator.after_change(
            cur, engagement_id, "TB_IMPORTED",
            {k: summary[k] for k in ("upload_id", "file", "rows", "inserted", "updated", "unchanged", "deactivated")},
            bump_revision=changed)
        summary["exceptions"] = exc
        return summary
