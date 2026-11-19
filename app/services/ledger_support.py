"""Find or create the ledgers that the WP9 registers post to, and post a balanced, approved journal entry.

A ledger created here has origin 'adjustment' (it is not in the Tally trial balance, so a TB re-import never touches it).
It is mapped to its Schedule III line at once, so it is never an unmapped-ledger exception.
"""
from fastapi import HTTPException

from app.services import adjustments_service
from app.services.tb_parser import normalise_key


def _group(cur, engagement_id: int, name: str, nature: str) -> int:
    cur.execute("SELECT id FROM tally_groups WHERE engagement_id = %s AND name = %s", (engagement_id, name))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur.execute("INSERT INTO tally_groups (engagement_id, name, nature) VALUES (%s,%s,%s) RETURNING id", (engagement_id, name, nature))
    return cur.fetchone()["id"]


def find_ledger(cur, engagement_id: int, name: str):
    cur.execute("SELECT id, name, is_active FROM ledgers WHERE engagement_id = %s AND ledger_key = %s", (engagement_id, normalise_key(name)))
    return cur.fetchone()


def ensure_ledger(cur, user: dict, engagement_id: int, name: str, group: str, nature: str, line_code: str) -> int:
    """The ledger called `name` (any group), created and mapped to `line_code` when it does not exist yet."""
    existing = find_ledger(cur, engagement_id, name)
    if existing:
        if not existing["is_active"]:
            raise HTTPException(409, f"Ledger '{existing['name']}' is no longer in the trial balance, so it cannot be posted to.")
        return existing["id"]
    gid = _group(cur, engagement_id, group, nature)
    cur.execute("""INSERT INTO ledgers (engagement_id, ledger_key, name, group_id, origin) VALUES (%s,%s,%s,%s,'adjustment') RETURNING id""",
                (engagement_id, normalise_key(name), name, gid))
    ledger_id = cur.fetchone()["id"]
    cur.execute("""SELECT l.id AS line_id, (SELECT n.id FROM template_notes n WHERE n.line_id = l.id ORDER BY n.sort_order LIMIT 1) AS note_id
                     FROM template_lines l WHERE l.code = %s AND l.template_id = (SELECT template_id FROM engagements WHERE id = %s)""",
                (line_code, engagement_id))
    line = cur.fetchone()
    cur.execute("INSERT INTO ledger_mappings (engagement_id, ledger_id, line_id, note_id, mapped_by) VALUES (%s,%s,%s,%s,%s)",
                (engagement_id, ledger_id, line["line_id"], line["note_id"], user["id"]))
    return ledger_id


def post_entry(cur, creator_id: int, approver: dict, engagement_id: int, entry_date, description: str, lines: list) -> int:
    """One balanced journal entry: created in the name of the person who entered the register row, approved by the poster.
    `lines` = [(ledger_id, debit, credit, note)]; zero amounts are dropped. Maker-checker holds (the database enforces it too)."""
    lines = [l for l in lines if l[1] > 0 or l[2] > 0]
    entry = adjustments_service._insert(cur, {"id": creator_id}, engagement_id, entry_date, description, lines, None)
    cur.execute("UPDATE journal_entries SET status = 'approved', approved_by = %s, approved_at = now() WHERE id = %s", (approver["id"], entry["id"]))
    return entry["id"]
