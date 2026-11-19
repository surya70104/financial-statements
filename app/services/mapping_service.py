"""T-02: one ledger -> one Schedule III line + one note, per engagement. Keyed on the persistent ledger id,
so mappings survive every re-import."""
from fastapi import HTTPException

from app.services import change_coordinator, exceptions_service, locking


def _lock_engagement(cur, engagement_id: int) -> dict:
    cur.execute("SELECT id, template_id FROM engagements WHERE id = %s FOR UPDATE", (engagement_id,))
    eng = cur.fetchone()
    if not eng:
        raise HTTPException(404, "Engagement not found.")
    locking.assert_unlocked(cur, engagement_id)          # 409 once the Partner has signed off
    return eng


def list_lines(cur, template_id: int) -> list:
    cur.execute("""SELECT l.id, l.code, l.statement, l.section, l.label, l.sort_order,
                          coalesce(json_agg(json_build_object('id', n.id, 'note_no', n.note_no, 'title', n.title)
                                            ORDER BY n.sort_order) FILTER (WHERE n.id IS NOT NULL), '[]') AS notes
                     FROM template_lines l LEFT JOIN template_notes n ON n.line_id = l.id
                    WHERE l.template_id = %s AND l.calc_kind = 'mapped'
                    GROUP BY l.id ORDER BY l.sort_order""", (template_id,))
    return cur.fetchall()


def get_mappings(cur, engagement_id: int) -> dict:
    cur.execute("SELECT template_id FROM engagements WHERE id = %s", (engagement_id,))
    eng = cur.fetchone()
    if not eng:
        raise HTTPException(404, "Engagement not found.")
    cur.execute("""SELECT l.id AS ledger_id, l.name, g.name AS tally_group, g.nature AS tally_nature,
                          lb.closing_balance, m.line_id, m.note_id, tl.requires_note,
                          (u.ledger_id IS NULL) AS is_mapped
                     FROM ledgers l
                     LEFT JOIN tally_groups g ON g.id = l.group_id
                     LEFT JOIN ledger_balances lb ON lb.ledger_id = l.id
                     LEFT JOIN ledger_mappings m ON m.ledger_id = l.id
                     LEFT JOIN template_lines tl ON tl.id = m.line_id
                     LEFT JOIN v_unmapped_ledgers u ON u.ledger_id = l.id
                    WHERE l.engagement_id = %s AND l.is_active
                    ORDER BY l.id""", (engagement_id,))
    ledgers = cur.fetchall()
    return {"ledgers": ledgers, "lines": list_lines(cur, eng["template_id"]),
            "counts": {**exceptions_service.counts(cur, engagement_id), "total_ledgers": len(ledgers)}}


def _load_ledger(cur, engagement_id: int, ledger_id: int) -> dict:
    cur.execute("SELECT id, name, is_active FROM ledgers WHERE id = %s AND engagement_id = %s", (ledger_id, engagement_id))
    ledger = cur.fetchone()
    if not ledger:
        raise HTTPException(404, "Ledger not found in this engagement.")
    if not ledger["is_active"]:
        raise HTTPException(409, "Ledger is inactive (not in the latest trial balance); it cannot be mapped.")
    return ledger


def save_mapping(cur, user: dict, engagement_id: int, ledger_id: int, line_id: int, note_id: int | None) -> dict:
    eng = _lock_engagement(cur, engagement_id)
    ledger = _load_ledger(cur, engagement_id, ledger_id)
    cur.execute("SELECT id, code, calc_kind, requires_note FROM template_lines WHERE id = %s AND template_id = %s",
                (line_id, eng["template_id"]))
    line = cur.fetchone()
    if not line:
        raise HTTPException(422, "That Schedule III line does not belong to this engagement's template.")
    if line["calc_kind"] != "mapped":
        raise HTTPException(422, "Ledgers can only be mapped to detail lines, not to totals.")
    cur.execute("SELECT id, note_no FROM template_notes WHERE line_id = %s ORDER BY sort_order", (line_id,))
    notes = cur.fetchall()
    if note_id is None:
        if len(notes) == 1:
            note_id = notes[0]["id"]
        elif line["requires_note"]:
            raise HTTPException(422, "Choose a note for this line.")
    elif note_id not in {n["id"] for n in notes}:
        raise HTTPException(422, "That note does not belong to the chosen line.")

    cur.execute("""INSERT INTO ledger_mappings (engagement_id, ledger_id, line_id, note_id, mapped_by)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (ledger_id) DO UPDATE SET line_id = EXCLUDED.line_id, note_id = EXCLUDED.note_id,
                        mapped_by = EXCLUDED.mapped_by, mapped_at = now()
                     WHERE (ledger_mappings.line_id, ledger_mappings.note_id) IS DISTINCT FROM (EXCLUDED.line_id, EXCLUDED.note_id)
                   RETURNING id""", (engagement_id, ledger_id, line_id, note_id, user["id"]))
    changed = cur.fetchone() is not None
    if changed:
        counts = change_coordinator.after_change(cur, engagement_id, "MAPPING_CHANGED",
                                                 {"ledger_id": ledger_id, "ledger": ledger["name"], "line": line["code"], "note_id": note_id})
    else:
        counts = exceptions_service.counts(cur, engagement_id)
    return {"ledger_id": ledger_id, "line_id": line_id, "note_id": note_id, "changed": changed, "counts": counts}


def auto_map(cur, user: dict, engagement_id: int) -> dict:
    """Map every currently-unmapped ledger whose Tally group has a default line (group_line_defaults).
    Never overwrites a complete mapping a person already made. Ledgers without a rule are reported, not guessed."""
    eng = _lock_engagement(cur, engagement_id)
    cur.execute("""SELECT u.ledger_id, u.name, g.name AS tally_group, d.line_id, tl.code,
                          (SELECT CASE WHEN count(*) = 1 THEN min(n.id) END FROM template_notes n WHERE n.line_id = d.line_id) AS note_id
                     FROM v_unmapped_ledgers u
                     JOIN ledgers l ON l.id = u.ledger_id
                     LEFT JOIN tally_groups g ON g.id = l.group_id
                     LEFT JOIN group_line_defaults d ON d.template_id = %s AND d.tally_group = g.name
                     LEFT JOIN template_lines tl ON tl.id = d.line_id
                    WHERE u.engagement_id = %s ORDER BY u.ledger_id""", (eng["template_id"], engagement_id))
    todo = cur.fetchall()
    mapped, skipped = 0, []
    for r in todo:
        if r["line_id"] is None or r["note_id"] is None:
            skipped.append({"ledger": r["name"], "tally_group": r["tally_group"],
                            "reason": "no default line for this Tally group" if r["line_id"] is None else "line needs a manual note choice"})
            continue
        cur.execute("""INSERT INTO ledger_mappings (engagement_id, ledger_id, line_id, note_id, mapped_by) VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT (ledger_id) DO UPDATE SET line_id = EXCLUDED.line_id, note_id = EXCLUDED.note_id,
                            mapped_by = EXCLUDED.mapped_by, mapped_at = now()""",
                    (engagement_id, r["ledger_id"], r["line_id"], r["note_id"], user["id"]))
        mapped += 1
    if mapped:
        counts = change_coordinator.after_change(cur, engagement_id, "AUTO_MAP",
                                                 {"mapped": mapped, "skipped": len(skipped), "rule": "Tally group default line"})
    else:
        counts = exceptions_service.counts(cur, engagement_id)
    return {"mapped": mapped, "skipped": skipped, "changed": mapped > 0, "counts": counts}


def delete_mapping(cur, user: dict, engagement_id: int, ledger_id: int) -> dict:
    _lock_engagement(cur, engagement_id)
    ledger = _load_ledger(cur, engagement_id, ledger_id)
    cur.execute("DELETE FROM ledger_mappings WHERE ledger_id = %s AND engagement_id = %s RETURNING id", (ledger_id, engagement_id))
    changed = cur.fetchone() is not None
    if changed:
        counts = change_coordinator.after_change(cur, engagement_id, "MAPPING_REMOVED", {"ledger_id": ledger_id, "ledger": ledger["name"]})
    else:
        counts = exceptions_service.counts(cur, engagement_id)
    return {"ledger_id": ledger_id, "changed": changed, "counts": counts}
