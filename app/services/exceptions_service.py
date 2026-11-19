"""The ONLY writer of automated exceptions (T-03).

Each unmapped ledger has exactly one exception row, identified by (engagement, 'mapping', 'LEDGER:<ledger_key>').
Running the sync any number of times can never create a second row: the unique constraint plus ON CONFLICT
make it an upsert. The set of unmapped ledgers comes from the SAME view the Mapping screen counts.
"""

_UPSERT = """
INSERT INTO exceptions (engagement_id, source, reference_id, cause_code, title, detail, amount, fingerprint)
SELECT u.engagement_id, 'mapping', 'LEDGER:' || u.ledger_key, 'UNMAPPED_LEDGER',
       'Unmapped ledger: ' || u.name,
       'Ledger is not mapped to a Schedule III line and note.',
       lb.closing_balance,
       md5(u.ledger_key || '|' || coalesce(lb.closing_balance::text, ''))
  FROM v_unmapped_ledgers u
  LEFT JOIN ledger_balances lb ON lb.ledger_id = u.ledger_id
 WHERE u.engagement_id = %(e)s
ON CONFLICT (engagement_id, source, reference_id) DO UPDATE SET
       title = EXCLUDED.title, amount = EXCLUDED.amount, fingerprint = EXCLUDED.fingerprint,
       is_condition_active = TRUE, status = 'open',
       cleared_by = NULL, cleared_by_system = FALSE, cleared_at = NULL, clear_reason = NULL,
       approved_by = NULL, approved_at = NULL, approval_reason = NULL,
       last_seen_at = now(), occurrences = exceptions.occurrences + 1
 WHERE NOT exceptions.is_condition_active OR exceptions.fingerprint <> EXCLUDED.fingerprint
"""
# (The WHERE keeps an unchanged exception untouched, so re-running the sync writes nothing and adds no audit noise.
#  A cleared/approved exception re-opens only if the facts changed or the condition had gone away and came back.)

_CLOSE_RESOLVED = """
UPDATE exceptions e SET
       is_condition_active = FALSE,
       status            = CASE WHEN e.status = 'open' THEN 'cleared' ELSE e.status END,
       cleared_by_system = CASE WHEN e.status = 'open' THEN TRUE ELSE e.cleared_by_system END,
       cleared_at        = CASE WHEN e.status = 'open' THEN now() ELSE e.cleared_at END,
       clear_reason      = CASE WHEN e.status = 'open'
                                THEN 'Auto-cleared: ledger is now mapped (or is no longer in the trial balance)'
                                ELSE e.clear_reason END
 WHERE e.engagement_id = %(e)s AND e.source = 'mapping' AND e.is_condition_active
   AND NOT EXISTS (SELECT 1 FROM v_unmapped_ledgers u
                    WHERE u.engagement_id = e.engagement_id AND 'LEDGER:' || u.ledger_key = e.reference_id)
"""


def sync_mapping_exceptions(cur, engagement_id: int) -> dict:
    cur.execute(_UPSERT, {"e": engagement_id})
    cur.execute(_CLOSE_RESOLVED, {"e": engagement_id})
    return counts(cur, engagement_id)


def counts(cur, engagement_id: int) -> dict:
    cur.execute("SELECT count(*) AS n FROM v_unmapped_ledgers WHERE engagement_id = %s", (engagement_id,))
    unmapped = cur.fetchone()["n"]
    cur.execute("""SELECT count(*) FILTER (WHERE status = 'open')     AS open,
                          count(*) FILTER (WHERE status = 'cleared')  AS cleared,
                          count(*) FILTER (WHERE status = 'approved') AS approved,
                          count(*)                                    AS total
                     FROM exceptions WHERE engagement_id = %s AND source = 'mapping'""", (engagement_id,))
    c = cur.fetchone()
    return {"unmapped_ledgers": unmapped, "open_mapping_exceptions": c["open"], "cleared": c["cleared"],
            "approved": c["approved"], "total_rows": c["total"], "matches": unmapped == c["open"]}
