"""Q4: Word (.docx) exports of the statements, the notes and the audit pack, on A4, with the company header, the current and previous
year side by side in exact Indian currency format (amounts in rupees, paise exact, negatives in brackets) and the signature blocks.

Everything is read from the same services the screens use, so a Word file always matches the screen and the print-out.
"""
import io
from datetime import date
from decimal import Decimal

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from fastapi import HTTPException

from app.services import entity, notes_service, pack_service, statement_service

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ZERO = Decimal("0.00")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def inr(value) -> str:
    """27,26,41,256.00 (Indian digit grouping, exact paise); negatives in brackets: (4,81,634.00)."""
    if value is None or value == "":
        return "–"
    v = Decimal(str(value)).quantize(Decimal("0.01"))
    neg, v = v < 0, abs(v)
    whole, frac = f"{v:.2f}".split(".")
    head, tail, groups = whole[:-3], whole[-3:], []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    text = ",".join(groups + [tail]) + "." + frac
    return f"({text})" if neg else text


def long_date(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


# ---- context ---------------------------------------------------------------------------------------------------
def _context(cur, engagement_id: int) -> dict:
    cur.execute("""SELECT e.id, e.fy_label, e.period_start, e.period_end, e.entity_type, e.data_revision, c.name AS company, c.cin, c.pan
                     FROM engagements e JOIN clients c ON c.id = e.client_id WHERE e.id = %s""", (engagement_id,))
    e = cur.fetchone()
    if not e:
        raise HTTPException(404, "Engagement not found.")
    cur.execute("""SELECT v.version_number, v.signed_off_at, u.full_name AS signer, d.udin_number, d.udin_generated_date
                     FROM report_signoff_versions v JOIN users u ON u.id = v.signed_off_by LEFT JOIN udin_records d ON d.signoff_version_id = v.id AND d.status = 'recorded'
                    WHERE v.engagement_id = %s AND v.status = 'signed_locked'""", (engagement_id,))
    e["signoff"] = cur.fetchone()
    return e


# ---- document building blocks ------------------------------------------------------------------------------------
def _new_document(ctx: dict, title: str) -> Document:
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)                       # A4
    sec.left_margin = sec.right_margin = Cm(2.0)
    sec.top_margin, sec.bottom_margin = Cm(1.8), Cm(1.8)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = "Calibri", Pt(10)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
    p = sec.footer.paragraphs[0]
    p.text = f"{ctx['company']} · {title} · FY {ctx['fy_label']} · generated from CA Workbench, data revision {ctx['data_revision']}"
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for r in p.runs:
        r.font.size, r.font.color.rgb = Pt(8), RGBColor(0x66, 0x66, 0x66)
    return doc


def _para(doc, text="", bold=False, size=None, align=None, space_after=2, italic=False):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold, run.italic = bold, italic
    if size:
        run.font.size = Pt(size)
    if align == "center":
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after, p.paragraph_format.space_before = Pt(space_after), Pt(0)
    return p


def _report_header(doc, ctx, title, as_at: bool = True):
    _para(doc, ctx["company"].upper(), bold=True, size=14, align="center")
    ids = [f"CIN: {ctx['cin']}" if ctx["cin"] else "", f"PAN: {ctx['pan']}" if ctx["pan"] else "", entity.SHORT_LABELS[ctx["entity_type"]]]
    _para(doc, "  ·  ".join(x for x in ids if x), size=9, align="center")
    _para(doc, title, bold=True, size=12, align="center", space_after=0)
    _para(doc, (f"as at {long_date(ctx['period_end'])}" if as_at else f"for the year ended {long_date(ctx['period_end'])}") + f"  ·  Financial Year {ctx['fy_label']}",
          size=9, align="center", space_after=2)
    _para(doc, "(Amount in ₹)", italic=True, size=8, align="center", space_after=6)


def _shade(cell, hex_fill: str):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tcPr.append(shd)


def _table(doc, headers: list, rows: list, widths_cm: list, num_cols=(), bold_rows=(), band_rows=()):
    """rows: list of lists of strings. Row indexes in bold_rows are bold; in band_rows are section headings (shaded)."""
    t = doc.add_table(rows=1, cols=len(headers))
    t.style, t.alignment = "Table Grid", WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False

    def fill(cells, values, bold=False, fill_hex=None):
        for i, (c, v) in enumerate(zip(cells, values)):
            c.width = Cm(widths_cm[i])
            p = c.paragraphs[0]
            p.paragraph_format.space_after = Pt(1)
            run = p.add_run("" if v is None else str(v))
            run.bold = bold
            run.font.size = Pt(9)
            if i in num_cols:
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            if fill_hex:
                _shade(c, fill_hex)

    fill(t.rows[0].cells, headers, bold=True, fill_hex="D9E2F3")
    for idx, row in enumerate(rows):
        fill(t.add_row().cells, row, bold=idx in bold_rows or idx in band_rows, fill_hex="EEF2F8" if idx in band_rows else None)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    return t


def _signature_block(doc, ctx):
    who = entity.SIGNATORIES[ctx["entity_type"]]
    _para(doc, "", space_after=8)
    left = f"For and on behalf of the {'Board of Directors' if ctx['entity_type'] == 'private_limited' else 'Partners'}"
    t = doc.add_table(rows=1, cols=2)
    t.autofit = False
    a, b = t.rows[0].cells
    a.width = b.width = Cm(8.5)
    for cell, lines in ((a, [left, ctx["company"], "", "", f"{who[:-1]}", f"DIN / Membership No.: ________", f"Place: ________    Date: ________"]),
                        (b, ["As per our report of even date attached", "For ____________________", "Chartered Accountants", "Firm Registration No.: ________", "", "Partner",
                             f"Membership No.: ________"])):
        for i, line in enumerate(lines):
            p = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
            r = p.add_run(line)
            r.font.size, r.bold = Pt(9), i == 0
            p.paragraph_format.space_after = Pt(1)
    s = ctx["signoff"]
    u = f"UDIN: {s['udin_number']}" if s and s["udin_number"] else "UDIN: ____________________"
    _para(doc, "", space_after=2)
    _para(doc, u + (f"   ·   Report version v{s['version_number']}, signed off by {s['signer']} on {long_date(s['signed_off_at'].date())}" if s else "   ·   Not yet signed off"),
          size=8, italic=True)
    _para(doc, f"The {who.lower()} named above sign to authenticate these statements; the second director/partner signs in the same way.", size=8, italic=True)


def _to_bytes(doc) -> bytes:
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---- the three documents -------------------------------------------------------------------------------------------
def statements_docx(cur, engagement_id: int) -> bytes:
    ctx = _context(cur, engagement_id)
    bs, pnl = statement_service.balance_sheet(cur, engagement_id), statement_service.profit_and_loss(cur, engagement_id)
    doc = _new_document(ctx, "Financial Statements")
    cur_l, prev_l = bs["current_label"], bs["previous_label"]
    head = ["Particulars", "Note", cur_l, prev_l]
    widths = [9.0, 1.4, 3.3, 3.3]

    _report_header(doc, ctx, "Balance Sheet")
    rows, bold, band = [], [], []
    for part in bs["parts"]:
        band.append(len(rows))
        rows.append([part["name"], "", "", ""])
        for sec in part["sections"]:
            band.append(len(rows))
            rows.append([sec["name"], "", "", ""])
            for ln in sec["lines"]:
                if ln["kind"] != "mapped":
                    bold.append(len(rows))
                rows.append([("   " if ln["kind"] == "mapped" else "") + ln["label"], ln["note_no"] or "", inr(ln["current"]), inr(ln["previous"])])
    chk = bs["check"]
    rows.append(["Total assets less total equity and liabilities (must be nil)", "", inr(chk["difference"]), inr(chk["previous_difference"])])
    _table(doc, head, rows, widths, num_cols=(2, 3), bold_rows=bold, band_rows=band)
    _signature_block(doc, ctx)

    doc.add_page_break()
    _report_header(doc, ctx, "Statement of Profit and Loss", as_at=False)
    rows, bold, band = [], [], []
    for sec in pnl["sections"]:
        band.append(len(rows))
        rows.append([sec["name"], "", "", ""])
        for ln in sec["lines"]:
            if ln["kind"] != "mapped":
                bold.append(len(rows))
            rows.append([("   " if ln["kind"] == "mapped" else "") + ln["label"], ln["note_no"] or "", inr(ln["current"]), inr(ln["previous"])])
    _table(doc, head, rows, widths, num_cols=(2, 3), bold_rows=bold, band_rows=band)
    _para(doc, "See the accompanying notes forming part of the financial statements.", italic=True, size=8)
    _signature_block(doc, ctx)
    return _to_bytes(doc)


def notes_docx(cur, engagement_id: int) -> bytes:
    ctx = _context(cur, engagement_id)
    data = notes_service.build(cur, engagement_id)
    doc = _new_document(ctx, "Notes to the Financial Statements")
    _report_header(doc, ctx, "Notes to the Financial Statements", as_at=False)
    cur_l, prev_l = data["current_label"], data["previous_label"]
    for n in data["notes"]:
        _para(doc, f"Note {n['note_no']}: {n['title']}", bold=True, size=10, space_after=2)
        rows = [[l["ledger_name"], inr(l["closing"])] for l in n["ledgers"]] + [[e["label"], inr(e["closing"])] for e in n["extras"]]
        if not rows:
            rows = [["No ledgers are mapped to this note", inr(ZERO)]]
        rows.append([f"Total (agrees to: {n['line_label']})", inr(n["note_total"])])
        rows.append([f"{prev_l}", inr(n["previous_total"])])
        _table(doc, ["Particulars", cur_l], rows, [13.0, 4.0], num_cols=(1,), bold_rows=[len(rows) - 2])
    fd = data["fd_note"]
    _para(doc, f"Supporting schedule: {fd['title']}", bold=True, size=10, space_after=2)
    if fd["rows"]:
        rows = [[r["bank_name"], r["fd_number"], "Current" if r["maturity_classification"] == "current" else "Non-current", inr(r["principal_amount"]),
                 f"{r['interest_rate']} %", r["maturity_date"].strftime("%d-%m-%Y"), inr(r["accrued_interest"]), "Posted" if r["status"] == "posted" else "Draft"] for r in fd["rows"]]
        t = fd["totals"]
        rows.append(["Total (posted FDs)", "", "", inr(t["total_principal"]), "", "", inr(t["accrued_interest"]), ""])
        rows.append([f"of which current {inr(t['current_principal'])}; non-current {inr(t['non_current_principal'])}", "", "", "", "", "", "", ""])
        _table(doc, ["Bank", "FD number", "Class", "Principal", "Rate", "Maturity", "Accrued interest", "Status"], rows, [2.4, 3.0, 1.8, 2.6, 1.3, 2.0, 2.4, 1.3],
               num_cols=(3, 6), bold_rows=[len(rows) - 2])
    else:
        _para(doc, "No fixed deposits are registered.", italic=True)
    _signature_block(doc, ctx)
    return _to_bytes(doc)


def pack_docx(cur, engagement_id: int) -> bytes:
    ctx = _context(cur, engagement_id)
    pack = pack_service.build(cur, engagement_id)
    doc = _new_document(ctx, "Audit Committee / IA Pack")
    _report_header(doc, ctx, "Audit Committee / Internal Audit Pack", as_at=True)
    hd, fin, adj, ci, wp = pack["header"], pack["financials"], pack["adjustments"], pack["checks_and_issues"], pack["working_papers"]
    cur_l, prev_l = fin["current_label"], fin["previous_label"]

    _para(doc, "1. Engagement & governance", bold=True, size=11, space_after=3)
    g = hd["governance"]
    _table(doc, ["Item", "Detail"], [
        ["Entity", f"{hd['entity']['name']} ({entity.SHORT_LABELS[ctx['entity_type']]})"], ["Financial year", f"FY {hd['financial_year']} ({long_date(hd['period_start'])} to {long_date(hd['period_end'])})"],
        ["Engagement", f"{hd['engagement_type'].replace('_', ' ').title()} · {hd['engagement_status']}"],
        ["Template", f"{hd['template']['name']} · {'confirmed' if hd['template']['confirmed'] else 'not confirmed'}"],
        ["Sign-off status", g["status"] + (f" ({g['signed_off_by']})" if g["signed_off_by"] else "")], ["Data revision", str(hd["data_revision"])]], [4.5, 12.5])

    _para(doc, "2. Financial highlights", bold=True, size=11, space_after=3)
    b, p = fin["balance_sheet"], fin["profit_and_loss"]
    rows = [[v["label"], inr(v["current"]), inr(v["previous"])] for v in (b["shareholders_funds"], b["non_current_liabilities"], b["current_liabilities"], b["total_equity_and_liabilities"],
                                                                         b["non_current_assets"], b["current_assets"], b["total_assets"])]
    rows += [[v["label"], inr(v["current"]), inr(v["previous"])] for v in (p["revenue_from_operations"], p["other_income"], p["total_income"], p["total_expenses"],
                                                                         p["profit_before_tax"], p["tax_expense"], p["profit_for_period"])]
    _table(doc, ["Particulars", cur_l, prev_l], rows, [9.0, 4.0, 4.0], num_cols=(1, 2), bold_rows=[3, 6, 9, 13])
    _para(doc, f"Balance check: {b['verification']['status']} (assets less equity and liabilities = {inr(b['verification']['difference'])}).", size=9, italic=True)

    from app.services import ratios_service
    ratios = ratios_service.build(cur, engagement_id)
    _para(doc, ratios["basis"], bold=True, size=10, space_after=2)
    rows = [[r["name"], r["formula"], "N/A" if r["value"] is None else f"{r['value']}", "N/A" if r["prior_value"] is None else f"{r['prior_value']}",
             "–" if r["variance_pct"] is None else (f"({str(r['variance_pct']).lstrip('-')}) %" if r["variance_pct"] < 0 else f"{r['variance_pct']} %")] for r in ratios["items"]]
    _table(doc, ["Ratio", "Formula", cur_l, prev_l, "% variance"], rows, [3.6, 6.4, 2.2, 2.2, 2.4], num_cols=(2, 3, 4))

    _para(doc, "3. Audit adjustments", bold=True, size=11, space_after=3)
    c = adj["counts"]
    _para(doc, f"{c['approved']} approved, {c['draft']} draft, {c['reversed']} reversed; {adj['ledgers_with_net_adjustment']} ledger(s) carry a net adjustment.", size=9)
    rows = [[f"#{e['entry_number']}", str(e["entry_date"]), e["description"][:90], e["status"].upper(), inr(e["total"])] for e in adj["entries"]] or [["", "", "None", "", ""]]
    _table(doc, ["#", "Date", "Description", "Status", "Amount"], rows, [1.2, 2.2, 8.2, 2.2, 3.2], num_cols=(4,))

    _para(doc, "4. Checks & open exceptions", bold=True, size=11, space_after=3)
    _para(doc, ci["checks_summary"]["text"] or "Checks not run", size=9)
    rows = [[k["code"], k["name"], {"pass": "PASS", "fail": "FAIL", "not_run": "OFF"}.get(k["status"], k["status"]),
             "–" if k["variance"] is None or k["status"] == "not_run" else (str(int(Decimal(str(k["variance"])))) if k["count_based"] else inr(k["variance"]))] for k in ci["checks"]]
    _table(doc, ["Check", "Name", "Status", "Difference"], rows, [2.0, 9.0, 2.2, 3.8], num_cols=(3,))
    _para(doc, f"Open issues: {ci['issues']['open']}", bold=True, size=9)
    if ci["issues"]["open_items"]:
        _table(doc, ["#", "Source", "Issue"], [[f"#{x['id']}", x["source"].upper(), x["title"]] for x in ci["issues"]["open_items"]], [1.4, 3.0, 12.6])

    _para(doc, "5. Working paper coverage", bold=True, size=11, space_after=3)
    l, n = wp["statement_lines"], wp["notes"]
    _para(doc, f"Statement lines covered {l['covered']} of {l['total']}; notes covered {n['covered']} of {n['total']}; {wp['reviewed_links']} of {wp['total_links']} working papers reviewed.", size=9)
    _signature_block(doc, ctx)
    return _to_bytes(doc)
