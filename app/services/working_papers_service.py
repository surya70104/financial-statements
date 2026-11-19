"""T-14 Working paper links on statement lines and notes.

A link is a reference (code, title, optional document link or path) attached to one statement line or one note.
Maker-checker: a Checker or Partner marks a link reviewed, never the person who attached it. A reviewed link is
permanent; an unreviewed one may be removed. Nothing can be written once the engagement is locked (HTTP 409).
"""
import re

from fastapi import HTTPException

from app.services import audit_service, locking

LINK_COLUMNS = """w.id, w.target_type, w.target_code, w.wp_ref_code, w.wp_title, w.wp_url_or_path, w.status, w.notes,
                  w.attached_by, au.full_name AS attached_by_name, w.attached_at,
                  w.reviewed_by, ru.full_name AS reviewed_by_name, w.reviewed_at"""
_BAD_SCHEME = re.compile(r"^\s*(javascript|data|vbscript):", re.I)


def _targets(cur, engagement_id: int) -> dict:
    """Every valid target of this engagement's template: detail statement lines and notes."""
    cur.execute("SELECT template_id FROM engagements WHERE id = %s", (engagement_id,))
    eng = cur.fetchone()
    if not eng:
        raise HTTPException(404, "Engagement not found.")
    cur.execute("""SELECT 'statement_line' AS target_type, l.code AS target_code, l.label, l.statement, l.section, NULL::text AS note_no, l.sort_order
                     FROM template_lines l WHERE l.template_id = %(t)s AND l.calc_kind = 'mapped'
                   UNION ALL
                   SELECT 'note', 'NOTE-' || n.note_no, 'Note ' || n.note_no || ' – ' || n.title, l.statement, l.section, n.note_no, 1000 + n.sort_order
                     FROM template_notes n JOIN template_lines l ON l.id = n.line_id WHERE n.template_id = %(t)s
                   ORDER BY 7""", {"t": eng["template_id"]})
    return {(r["target_type"], r["target_code"]): r for r in cur.fetchall()}


def _link(cur, engagement_id: int, wp_id: int, lock: bool = False) -> dict:
    cur.execute(f"SELECT id, status, attached_by, wp_ref_code, target_code FROM working_paper_links WHERE id = %s AND engagement_id = %s "
                f"{'FOR UPDATE' if lock else ''}", (wp_id, engagement_id))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "Working paper link not found in this engagement.")
    return row


def list_links(cur, engagement_id: int) -> dict:
    targets = _targets(cur, engagement_id)
    cur.execute(f"""SELECT {LINK_COLUMNS} FROM working_paper_links w JOIN users au ON au.id = w.attached_by
                     LEFT JOIN users ru ON ru.id = w.reviewed_by WHERE w.engagement_id = %s ORDER BY w.target_type, w.target_code, w.id""",
                (engagement_id,))
    items = cur.fetchall()
    by_target: dict[str, list] = {}
    for it in items:
        by_target.setdefault(f"{it['target_type']}:{it['target_code']}", []).append(it)
    register = []
    for (ttype, code), t in targets.items():
        links = by_target.get(f"{ttype}:{code}", [])
        register.append({"target_type": ttype, "target_code": code, "label": t["label"], "statement": t["statement"], "section": t["section"],
                         "note_no": t["note_no"], "link_count": len(links), "reviewed_count": sum(1 for l in links if l["status"] == "reviewed")})

    def cov(kind):
        rows = [r for r in register if r["target_type"] == kind]
        return {"total": len(rows), "covered": sum(1 for r in rows if r["link_count"]),
                "fully_reviewed": sum(1 for r in rows if r["link_count"] and r["reviewed_count"] == r["link_count"])}
    return {"summary": {"statement_lines": cov("statement_line"), "notes": cov("note"), "total_links": len(items),
                        "reviewed_links": sum(1 for i in items if i["status"] == "reviewed"),
                        "unreviewed_links": sum(1 for i in items if i["status"] != "reviewed")},
            "targets": register, "by_target": by_target, "items": items}


def attach(cur, user: dict, engagement_id: int, target_type: str, target_code: str, ref_code: str, title: str,
           url_or_path: str | None, notes: str | None) -> dict:
    cur.execute("SELECT id FROM engagements WHERE id = %s FOR UPDATE", (engagement_id,))
    if not cur.fetchone():
        raise HTTPException(404, "Engagement not found.")
    locking.assert_unlocked(cur, engagement_id)
    targets = _targets(cur, engagement_id)
    target_code = (target_code or "").strip()
    if (target_type, target_code) not in targets:
        raise HTTPException(400, "That statement line or note does not exist in this engagement's template.")
    ref_code, title = (ref_code or "").strip(), (title or "").strip()
    if not ref_code:
        raise HTTPException(400, "Enter the working paper code (for example WP-B1).")
    if not title:
        raise HTTPException(400, "Enter a title for the working paper.")
    if len(ref_code) > 40 or len(title) > 200 or len(url_or_path or "") > 1000 or len(notes or "") > 1000:
        raise HTTPException(400, "One of the fields is too long.")
    url_or_path = (url_or_path or "").strip() or None
    if url_or_path and _BAD_SCHEME.match(url_or_path):
        raise HTTPException(400, "The document link must be a web address (http/https) or a file path.")
    cur.execute("""SELECT 1 FROM working_paper_links WHERE engagement_id = %s AND target_type = %s AND target_code = %s
                      AND lower(wp_ref_code) = lower(%s)""", (engagement_id, target_type, target_code, ref_code))
    if cur.fetchone():
        raise HTTPException(409, f"{ref_code} is already attached to this {'note' if target_type == 'note' else 'line'}.")
    cur.execute("""INSERT INTO working_paper_links (engagement_id, target_type, target_code, wp_ref_code, wp_title, wp_url_or_path, attached_by, notes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (engagement_id, target_type, target_code, ref_code, title, url_or_path, user["id"], (notes or "").strip() or None))
    wp_id = cur.fetchone()["id"]
    audit_service.event(cur, engagement_id, "WORKING_PAPER_ATTACHED",
                        {"wp_id": wp_id, "target_type": target_type, "target_code": target_code, "wp_ref_code": ref_code, "title": title},
                        entity_table="working_paper_links", entity_id=str(wp_id))
    return {"id": wp_id, "status": "attached", "target_type": target_type, "target_code": target_code, "wp_ref_code": ref_code}


def review(cur, user: dict, engagement_id: int, wp_id: int) -> dict:
    cur.execute("SELECT id FROM engagements WHERE id = %s FOR UPDATE", (engagement_id,))
    if not cur.fetchone():
        raise HTTPException(404, "Engagement not found.")
    locking.assert_unlocked(cur, engagement_id)
    link = _link(cur, engagement_id, wp_id, lock=True)
    if link["status"] == "reviewed":
        raise HTTPException(409, "This working paper is already reviewed.")
    if link["attached_by"] == user["id"]:
        raise HTTPException(403, "Maker-checker: you attached this working paper, so someone else must review it.")
    cur.execute("UPDATE working_paper_links SET status = 'reviewed', reviewed_by = %s, reviewed_at = now() WHERE id = %s", (user["id"], wp_id))
    audit_service.event(cur, engagement_id, "WORKING_PAPER_REVIEWED",
                        {"wp_id": wp_id, "target_code": link["target_code"], "wp_ref_code": link["wp_ref_code"]},
                        entity_table="working_paper_links", entity_id=str(wp_id))
    return {"id": wp_id, "status": "reviewed", "reviewed_by": user["id"]}


def remove(cur, user: dict, engagement_id: int, wp_id: int) -> dict:
    cur.execute("SELECT id FROM engagements WHERE id = %s FOR UPDATE", (engagement_id,))
    if not cur.fetchone():
        raise HTTPException(404, "Engagement not found.")
    locking.assert_unlocked(cur, engagement_id)
    link = _link(cur, engagement_id, wp_id, lock=True)
    if link["status"] == "reviewed":
        raise HTTPException(409, "A reviewed working paper cannot be removed.")
    cur.execute("DELETE FROM working_paper_links WHERE id = %s", (wp_id,))
    audit_service.event(cur, engagement_id, "WORKING_PAPER_REMOVED",
                        {"wp_id": wp_id, "target_code": link["target_code"], "wp_ref_code": link["wp_ref_code"]},
                        entity_table="working_paper_links", entity_id=str(wp_id))
    return {"id": wp_id, "removed": True}
