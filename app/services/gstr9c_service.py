"""T-21 GSTR-9C Reconciliation: the books against the GSTR-9C figure, per GST head and table.

difference = books - GSTR-9C. Every non-zero difference is ONE issue in the tracker (source 'gstr9c', reference
'gstr9c:<HEAD>:<Table>'): re-importing never duplicates it, it re-opens if the figures change, and it clears itself
when the difference becomes 0.00 (or the row is removed). Importing a head/table that already exists UPDATES it.
"""
import re
from decimal import Decimal

from fastapi import HTTPException

from app.services import audit_service, locking, register_io as io

HEADS = ("CGST", "SGST", "IGST", "CESS")
ZERO = Decimal("0.00")

_UPSERT = """
INSERT INTO exceptions (engagement_id, source, reference_id, cause_code, title, detail, amount, fingerprint)
VALUES (%(e)s, 'gstr9c', %(ref)s, 'GSTR9C_DIFFERENCE', %(title)s, %(detail)s, %(amount)s, md5(%(fp)s))
ON CONFLICT (engagement_id, source, reference_id) DO UPDATE SET
       title = EXCLUDED.title, detail = EXCLUDED.detail, amount = EXCLUDED.amount, fingerprint = EXCLUDED.fingerprint,
       is_condition_active = TRUE, status = 'open',
       cleared_by = NULL, cleared_by_system = FALSE, cleared_at = NULL, clear_reason = NULL,
       approved_by = NULL, approved_at = NULL, approval_reason = NULL,
       last_seen_at = now(), occurrences = exceptions.occurrences + 1
 WHERE NOT exceptions.is_condition_active OR exceptions.fingerprint <> EXCLUDED.fingerprint
"""
_CLOSE = """
UPDATE exceptions e SET
       is_condition_active = FALSE,
       status            = CASE WHEN e.status = 'open' THEN 'cleared' ELSE e.status END,
       cleared_by_system = CASE WHEN e.status = 'open' THEN TRUE ELSE e.cleared_by_system END,
       cleared_at        = CASE WHEN e.status = 'open' THEN now() ELSE e.cleared_at END,
       clear_reason      = CASE WHEN e.status = 'open' THEN 'Auto-cleared: the GSTR-9C figure now agrees with the books (or the row was removed)' ELSE e.clear_reason END
 WHERE e.engagement_id = %(e)s AND e.source = 'gstr9c' AND e.is_condition_active AND e.reference_id <> ALL(%(keep)s)
"""


def _inr(v: Decimal) -> str:
    neg, v = v < 0, abs(v)
    whole, frac = f"{v:.2f}".split(".")
    head, tail, groups = whole[:-3], whole[-3:], []
    while len(head) > 2:
        groups.insert(0, head[-2:]); head = head[:-2]
    if head:
        groups.insert(0, head)
    text = ",".join(groups + [tail]) + "." + frac
    return f"₹({text})" if neg else f"₹{text}"


def _engagement(cur, engagement_id: int, lock: bool = False) -> None:
    cur.execute(f"SELECT 1 FROM engagements WHERE id = %s {'FOR UPDATE' if lock else ''}", (engagement_id,))
    if not cur.fetchone():
        raise HTTPException(404, "Engagement not found.")


def _table(value) -> str:
    t = io.text(value, "Table")
    m = re.fullmatch(r"(?i)(?:table\s*)?(\w+)", t)
    return f"Table {m.group(1)}" if m else t


def clean(raw: dict) -> dict:
    head = (io.text(raw.get("gst_head"), "GST head") or "").upper()
    if head not in HEADS:
        raise ValueError(f"GST head must be one of {', '.join(HEADS)} (got '{raw.get('gst_head')}').")
    books, nine_c = io.signed_money(raw.get("books_amount"), "Books amount"), io.signed_money(raw.get("gstr9c_amount"), "GSTR-9C amount")
    diff = books - nine_c
    stated = raw.get("difference_amount")
    if stated not in (None, "") and io.signed_money(stated, "Difference") != diff:
        raise ValueError(f"Difference {stated} is not books minus GSTR-9C ({books} - {nine_c} = {diff}).")
    return {"gst_head": head, "table_ref": _table(raw.get("table_ref")), "description": io.text(raw.get("description"), "Description", required=False, maxlen=300),
            "books_amount": books, "gstr9c_amount": nine_c, "difference_amount": diff}


def _upsert_row(cur, user: dict, engagement_id: int, r: dict) -> str:
    cur.execute("SELECT id, status FROM gstr9c_reconciliations WHERE engagement_id = %s AND gst_head = %s AND table_ref = %s FOR UPDATE",
                (engagement_id, r["gst_head"], r["table_ref"]))
    old = cur.fetchone()
    if r["difference_amount"] != 0:
        status = "difference_logged"
    else:
        status = "resolved" if old and old["status"] == "difference_logged" else "matched"
    if old:
        cur.execute("""UPDATE gstr9c_reconciliations SET description = %s, books_amount = %s, gstr9c_amount = %s, difference_amount = %s, status = %s WHERE id = %s""",
                    (r["description"], r["books_amount"], r["gstr9c_amount"], r["difference_amount"], status, old["id"]))
        return "updated"
    cur.execute("""INSERT INTO gstr9c_reconciliations (engagement_id, gst_head, table_ref, description, books_amount, gstr9c_amount, difference_amount, status, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (engagement_id, r["gst_head"], r["table_ref"], r["description"], r["books_amount"], r["gstr9c_amount"], r["difference_amount"], status, user["id"]))
    return "added"


def sync_issues(cur, engagement_id: int) -> dict:
    """One deduplicated issue per non-zero difference; closes the ones whose difference is gone. Idempotent."""
    cur.execute("SELECT gst_head, table_ref, description, books_amount, gstr9c_amount, difference_amount FROM gstr9c_reconciliations "
                "WHERE engagement_id = %s AND difference_amount <> 0 ORDER BY id", (engagement_id,))
    keep = []
    for r in cur.fetchall():
        ref = f"gstr9c:{r['gst_head']}:{r['table_ref']}"
        keep.append(ref)
        detail = (f"Books {_inr(r['books_amount'])} against GSTR-9C {_inr(r['gstr9c_amount'])}: a difference of {_inr(r['difference_amount'])}"
                  + (f" ({r['description']})" if r["description"] else "") + ".")
        cur.execute(_UPSERT, {"e": engagement_id, "ref": ref, "title": f"GSTR-9C difference: {r['gst_head']} ({r['table_ref']})", "detail": detail,
                              "amount": r["difference_amount"], "fp": f"{ref}|{r['books_amount']}|{r['gstr9c_amount']}"})
    cur.execute(_CLOSE, {"e": engagement_id, "keep": keep})
    return {"differences": len(keep)}


def _finish(cur, engagement_id: int, event: str, extra: dict) -> None:
    s = sync_issues(cur, engagement_id)
    cur.execute("SELECT count(*) FILTER (WHERE difference_amount = 0) AS matched, count(*) FILTER (WHERE difference_amount <> 0) AS differing, "
                "coalesce(sum(difference_amount), 0) AS net FROM gstr9c_reconciliations WHERE engagement_id = %s", (engagement_id,))
    t = cur.fetchone()
    audit_service.event(cur, engagement_id, event, {**extra, "rows_matched": t["matched"], "rows_with_difference": t["differing"], "net_difference": t["net"],
                                                     "issues_open": s["differences"]})


def add_row(cur, user: dict, engagement_id: int, raw: dict) -> dict:
    _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    try:
        r = clean(raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    outcome = _upsert_row(cur, user, engagement_id, r)
    _finish(cur, engagement_id, "GSTR9C_RECONCILED", {"source": "form", "rows": 1, outcome: 1})
    return {"result": outcome, **r}


def import_file(cur, user: dict, engagement_id: int, filename: str, data: bytes) -> dict:
    _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    table = io.read_table(filename, data)
    seen, good, errors = set(), [], []
    for n, row in table:
        raw = {"gst_head": io.pick(row, "gst head", "head", "tax head"), "table_ref": io.pick(row, "table ref", "table", "gstr 9c table"),
               "description": io.pick(row, "description", "particulars"), "books_amount": io.pick(row, "books amount", "per books", "books"),
               "gstr9c_amount": io.pick(row, "gstr9c amount", "gstr 9c amount", "gstr-9c amount", "per gstr 9c", "gstr9c", "gstr 9c"),
               "difference_amount": io.pick(row, "difference amount", "difference")}
        try:
            r = clean(raw)
        except ValueError as exc:
            errors.append({"row": n, "message": str(exc)})
            continue
        k = (r["gst_head"], r["table_ref"])
        if k in seen:
            errors.append({"row": n, "message": f"{r['gst_head']} / {r['table_ref']} appears twice in this file."})
            continue
        seen.add(k)
        good.append(r)
    if errors:
        raise HTTPException(422, {"message": f"{len(errors)} row(s) need attention. Nothing was imported.", "errors": errors})
    added = updated = 0
    for r in good:
        if _upsert_row(cur, user, engagement_id, r) == "added":
            added += 1
        else:
            updated += 1
    _finish(cur, engagement_id, "GSTR9C_RECONCILED", {"source": "import", "file": filename, "rows": len(good), "added": added, "updated": updated})
    cur.execute("SELECT count(*) AS n FROM gstr9c_reconciliations WHERE engagement_id = %s AND difference_amount <> 0", (engagement_id,))
    return {"imported": len(good), "added": added, "updated": updated, "differences": cur.fetchone()["n"], "file": filename}


def delete_row(cur, user: dict, engagement_id: int, row_id: int) -> dict:
    _engagement(cur, engagement_id, lock=True)
    locking.assert_unlocked(cur, engagement_id)
    cur.execute("SELECT id, gst_head, table_ref FROM gstr9c_reconciliations WHERE id = %s AND engagement_id = %s FOR UPDATE", (row_id, engagement_id))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "GSTR-9C row not found in this engagement.")
    cur.execute("DELETE FROM gstr9c_reconciliations WHERE id = %s", (row_id,))
    _finish(cur, engagement_id, "GSTR9C_RECONCILED", {"source": "delete", "deleted": f"{row['gst_head']} / {row['table_ref']}"})
    return {"deleted": row_id}


def list_rows(cur, engagement_id: int) -> dict:
    _engagement(cur, engagement_id)
    cur.execute("""SELECT g.id, g.gst_head, g.table_ref, g.description, g.books_amount, g.gstr9c_amount, g.difference_amount, g.status, g.created_at,
                          u.full_name AS created_by_name, x.id AS issue_id, x.status AS issue_status
                     FROM gstr9c_reconciliations g JOIN users u ON u.id = g.created_by
                     LEFT JOIN exceptions x ON x.engagement_id = g.engagement_id AND x.source = 'gstr9c'
                                           AND x.reference_id = 'gstr9c:' || g.gst_head || ':' || g.table_ref
                    WHERE g.engagement_id = %s ORDER BY g.id""", (engagement_id,))
    rows = cur.fetchall()
    cur.execute("SELECT count(*) AS n FROM exceptions WHERE engagement_id = %s AND source = 'gstr9c' AND status = 'open'", (engagement_id,))
    return {"summary": {"books_total": sum((r["books_amount"] for r in rows), ZERO), "gstr9c_total": sum((r["gstr9c_amount"] for r in rows), ZERO),
                        "net_difference": sum((r["difference_amount"] for r in rows), ZERO), "open_issues": cur.fetchone()["n"],
                        "rows": len(rows), "matched": sum(1 for r in rows if r["difference_amount"] == 0),
                        "with_difference": sum(1 for r in rows if r["difference_amount"] != 0)}, "rows": rows}
