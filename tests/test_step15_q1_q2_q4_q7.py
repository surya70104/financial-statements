"""Step 15 acceptance tests: the Product Owner's answers to Q1, Q2, Q4 and Q7.
  Q1  CHK-06 is a real check (notes reconcile to the statement face)      Q2  template confirmed; Private Limited vs Partnership labels
  Q4  Word (.docx) exports of the statements, the notes and the pack        Q7  confirmed ratios with prior year and % variance
Starts from the pilot as Step 14 left it (signed off, locked): the Partner re-opens it. The last test signs it off with the REAL CHK-06."""
import io
from decimal import ROUND_HALF_UP, Decimal

import psycopg
import pytest
from docx import Document

D = Decimal
MAKER, CHECKER, PARTNER = {"X-User-Id": "2"}, {"X-User-Id": "3"}, {"X-User-Id": "4"}
LOCK_MSG = "Engagement is locked after Partner sign-off"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
BASIS = "Standard Schedule III / ICAI Analytical Ratios (Confirmed)"


@pytest.fixture(scope="module")
def eid(client):
    return client.get("/api/engagements").json()[0]["id"]


@pytest.fixture(scope="module")
def reopened(client, eid):
    if client.get(f"/api/engagements/{eid}/signoff").json()["is_locked"]:
        assert client.post(f"/api/engagements/{eid}/signoff/reopen", json={"reopen_reason": "Step 15 checks"}, headers=PARTNER).status_code == 200


def inr(v) -> str:
    """Independent Indian-format helper (so the tests do not trust the code under test)."""
    v = D(str(v)).quantize(D("0.01"))
    neg, v = v < 0, abs(v)
    whole, frac = f"{v:.2f}".split(".")
    out, head = whole[-3:], whole[:-3]
    while head:
        out, head = head[-2:] + "," + out, head[:-2]
    return f"({out}.{frac})" if neg else f"{out}.{frac}"


def docx_text(content: bytes) -> str:
    d = Document(io.BytesIO(content))
    parts = [p.text for p in d.paragraphs] + [p.text for p in d.sections[0].footer.paragraphs]
    for t in d.tables:
        for row in t.rows:
            parts += [c.text for c in row.cells]
    return "\n".join(parts)


def line(client, eid, code, statement="statements/balance-sheet"):
    d = client.get(f"/api/engagements/{eid}/{statement}").json()
    secs = [s for p in d["parts"] for s in p["sections"]] if "parts" in d else d["sections"]
    return next(l for s in secs for l in s["lines"] if l["code"] == code)


def checks(client, eid):
    r = client.post(f"/api/engagements/{eid}/checks/run", headers=CHECKER).json()
    return r["summary"], {i["code"]: i for i in r["items"]}


def test_chk06_is_a_real_check_that_passes_and_catches_a_note_mismatch(client, eid, reopened, test_db_url):
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        row = conn.execute("SELECT name, rule_type, definition_status FROM check_definitions WHERE code = 'CHK-06'").fetchone()
    assert row == ("Notes reconcile to statement face", "notes_reconcile_face", "confirmed")                   # no stand-in, no placeholder

    summary, c = checks(client, eid)
    assert summary["text"] == "6 of 6 active checks passing" and summary["notes"] == []
    c6 = c["CHK-06"]
    assert c6["status"] == "pass" and D(c6["variance"]) == D("0.00") and c6["definition_status"] == "confirmed"
    assert c6["detail"]["notes_checked"] == c6["detail"]["notes_tying"] == 30 and c6["detail"]["failing_notes"] == []
    assert client.get(f"/api/engagements/{eid}/signoff").json()["counts"]["checks"] == 0                          # no failing check blocks sign-off

    # a ledger mapped to its line WITHOUT a note: the face includes it, the note cannot
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.user_id','2',false)")
        m = conn.execute("SELECT m.id, m.note_id, a.adjusted_balance FROM ledger_mappings m JOIN v_adjusted_tb a ON a.ledger_id = m.ledger_id WHERE a.ledger_name = 'Cash in Hand'").fetchone()
        conn.execute("UPDATE ledger_mappings SET note_id = NULL WHERE id = %s", (m[0],))
        try:
            summary, c = checks(client, eid)
            gate = client.get(f"/api/engagements/{eid}/signoff").json()
        finally:
            conn.execute("UPDATE ledger_mappings SET note_id = %s WHERE id = %s", (m[1], m[0]))
    c6 = c["CHK-06"]
    assert c6["status"] == "fail" and D(c6["variance"]) == abs(D(m[2])) and summary["passed"] < summary["total"]
    failing = c6["detail"]["failing_notes"]
    assert [f["note_no"] for f in failing] == ["20"] and D(failing[0]["difference"]) == -D(m[2]) and D(failing[0]["line_total"]) - D(failing[0]["note_total"]) == D(m[2])
    assert any(b["type"] == "CHECK" and b["code"] == "CHK-06" and b["pending_definition"] is False for b in gate["blockers"])    # a genuine failure blocks sign-off
    summary, c = checks(client, eid)
    assert summary["text"] == "6 of 6 active checks passing" and c["CHK-06"]["status"] == "pass"                      # fixed: it passes again, by itself


def test_template_is_confirmed_and_the_entity_type_relabels_the_equity_lines(client, eid, reopened):
    head = client.get(f"/api/engagements/{eid}").json()
    assert head["template_confirmed"] is True and head["entity_type"] == "private_limited" and head["entity_label"] == "Private Limited Company (Schedule III)"
    page = client.get("/").text
    assert "open question Q2" not in page and 'id="entity-type"' in page and "entity_type.js" in page
    assert line(client, eid, "BS.EQ.SC")["label"] == "Share capital" and line(client, eid, "BS.EQ.RS")["label"] == "Reserves and surplus"
    assets = line(client, eid, "BS.ASSETS.TOTAL")["current"]
    rev0 = client.get(f"/api/engagements/{eid}").json()["data_revision"]

    url = f"/api/engagements/{eid}/entity-type"
    assert client.patch(url, json={"entity_type": "partnership"}).status_code == 401
    assert client.patch(url, json={"entity_type": "partnership"}, headers=MAKER).status_code == 403
    assert client.patch(url, json={"entity_type": "trust"}, headers=CHECKER).status_code == 400
    assert client.patch(url, json={}, headers=CHECKER).status_code == 400
    assert client.patch("/api/engagements/999999/entity-type", json={"entity_type": "partnership"}, headers=CHECKER).status_code == 404
    assert line(client, eid, "BS.EQ.SC")["label"] == "Share capital"                                                  # refused requests changed nothing

    r = client.patch(url, json={"entity_type": "partnership"}, headers=CHECKER)
    assert r.status_code == 200 and r.json() == {"entity_type": "partnership", "label": "Partnership Firm", "changed": True}
    assert line(client, eid, "BS.EQ.SC")["label"] == "Partners' Capital Account"
    assert line(client, eid, "BS.EQ.RS")["label"] == "Partners' Current Account"
    assert line(client, eid, "BS.EQ.TOTAL")["label"] == "Total partners' funds"
    assert line(client, eid, "BS.ASSETS.TOTAL")["current"] == assets                                                  # only labels move, never a figure
    titles = {n["line_code"]: n["title"] for n in client.get(f"/api/engagements/{eid}/notes").json()["notes"]}
    assert titles["BS.EQ.SC"] == "Partners' capital account" and titles["BS.EQ.RS"] == "Partners' current account"
    assert client.get(f"/api/engagements/{eid}/pack").json()["header"]["entity"]["entity_type"] == "partnership"
    assert client.get(f"/api/engagements/{eid}").json()["data_revision"] > rev0                                         # the pack is now stale, as for any change
    assert client.get(f"/api/engagements/{eid}/pack").json()["is_stale"] is True
    summary = client.get(f"/api/engagements/{eid}/checks").json()["summary"]
    assert summary["passed"] == summary["total"] == 6
    assert "ENTITY_TYPE_CHANGED" in {e["event_name"] for e in client.get(f"/api/engagements/{eid}/audit-logs?scope=events&limit=500", headers=MAKER).json()}
    assert client.patch(url, json={"entity_type": "partnership"}, headers=PARTNER).json()["changed"] is False          # saving the same value is a no-op


def test_word_exports_are_valid_documents_with_the_company_and_the_figures(client, eid, reopened):
    bs, pnl = f"/api/engagements/{eid}/statements/balance-sheet", f"/api/engagements/{eid}/statements/pnl"
    for kind in ("statements", "notes", "pack"):
        url = f"/api/engagements/{eid}/export/{kind}.docx"
        assert client.get(url).status_code == 401
    assert client.get("/api/engagements/999999/export/statements.docx", headers=MAKER).status_code == 404

    # Partnership (still set from the previous test): the equity labels and the signatories follow
    r = client.get(f"/api/engagements/{eid}/export/statements.docx", headers=MAKER)
    assert r.status_code == 200 and r.headers["content-type"] == DOCX
    assert 'filename="Financial-Statements-FY2026-27.docx"' in r.headers["content-disposition"] and r.content[:2] == b"PK"      # a real .docx is a zip
    text = docx_text(r.content)
    doc = Document(io.BytesIO(r.content))
    assert abs(doc.sections[0].page_width.cm - 21.0) < 0.05 and abs(doc.sections[0].page_height.cm - 29.7) < 0.05           # A4
    assert "9AND9 DIGISOFT INSIGHTS PRIVATE LIMITED" in text and "Partnership Firm" in text
    assert "Balance Sheet" in text and "Statement of Profit and Loss" in text and "FY 2026-27" in text and "FY 2025-26" in text
    assert "Partners' Capital Account" in text and "Partners' Current Account" in text and "Total partners' funds" in text and "Share capital" not in text
    assets, loss = line(client, eid, "BS.ASSETS.TOTAL"), line(client, eid, "PL.PAT", "statements/pnl")
    assert inr(assets["current"]) in text and inr(assets["previous"]) in text and inr(loss["current"]) in text
    assert "Chartered Accountants" in text and "UDIN" in text and "For and on behalf of the Partners" in text

    # back to a company: Directors sign, Share capital / Reserves are back
    assert client.patch(f"/api/engagements/{eid}/entity-type", json={"entity_type": "private_limited"}, headers=PARTNER).status_code == 200
    text = docx_text(client.get(f"/api/engagements/{eid}/export/statements.docx", headers=CHECKER).content)
    assert "Share capital" in text and "Reserves and surplus" in text and "Partners' Capital Account" not in text
    assert "For and on behalf of the Board of Directors" in text and "Private Limited Company" in text
    assert inr(line(client, eid, "BS.ASSETS.TOTAL")["current"]) in text
    assert inr(line(client, eid, "PL.REV", "statements/pnl")["current"]) in text                                     # Indian grouping, exact paise, e.g. 11,35,50,000.00

    notes = client.get(f"/api/engagements/{eid}/export/notes.docx", headers=MAKER)
    assert notes.status_code == 200 and notes.headers["content-type"] == DOCX and "Notes-to-Accounts-FY2026-27.docx" in notes.headers["content-disposition"]
    ntext = docx_text(notes.content)
    assert "Notes to the Financial Statements" in ntext and "Note 1:" in ntext and "Note 30:" in ntext and "Cash in Hand" in ntext
    assert "Fixed deposits and accrued interest" in ntext and "37821904455" in ntext and "HDFC Bank" in ntext                  # the dedicated FD schedule
    fd = client.get(f"/api/engagements/{eid}/notes").json()["fd_note"]["totals"]
    assert inr(fd["total_principal"]) in ntext and inr(fd["accrued_interest"]) in ntext

    pack_url = f"/api/engagements/{eid}/export/pack.docx"
    assert client.get(f"/api/engagements/{eid}/pack").json()["is_stale"] is True
    p = client.get(pack_url, headers=PARTNER)
    assert p.status_code == 200 and p.headers["content-type"] == DOCX and "Audit-Pack-FY2026-27.docx" in p.headers["content-disposition"]
    ptext = docx_text(p.content)
    for needle in ("Audit Committee / Internal Audit Pack", "1. Engagement & governance", "2. Financial highlights", "3. Audit adjustments", "4. Checks & open exceptions",
                   "5. Working paper coverage", BASIS, "CHK-06", "Notes reconcile to statement face", "Current Ratio", "9AND9 DIGISOFT INSIGHTS PRIVATE LIMITED"):
        assert needle in ptext, needle
    assert inr(line(client, eid, "BS.ASSETS.TOTAL")["current"]) in ptext
    assert client.get(f"/api/engagements/{eid}/pack").json()["is_stale"] is False                                      # a Word export of the pack is also a build
    events = {e["event_name"] for e in client.get(f"/api/engagements/{eid}/audit-logs?scope=events&limit=500", headers=MAKER).json()}
    assert {"DOCX_EXPORTED", "AUDIT_PACK_EXPORTED"} <= events
    assert 'data-docx="statements"' in client.get("/balance-sheet").text and 'data-docx="statements"' in client.get("/pnl").text
    assert 'data-docx="notes"' in client.get("/notes").text and 'data-docx="pack"' in client.get("/pack").text and "Download Word (.docx)" in client.get("/pack").text


def test_ratios_are_confirmed_with_both_years_and_the_percentage_variance(client, eid, reopened):
    d = client.get(f"/api/engagements/{eid}/ratios").json()
    assert d["provisional"] is False and d["basis"] == BASIS and d["provisional_message"] is None and len(d["items"]) == 9
    assert d["prior_available"] is True and d["previous_label"] == "FY 2025-26"
    for r in d["items"]:
        assert r["is_provisional"] is False
        assert {"numerator", "denominator", "numerator_label", "denominator_label", "value", "prior_value", "variance_pct", "prior_numerator", "prior_denominator"} <= set(r)
        if r["value"] is not None and r["prior_value"] is not None and D(r["prior_value"]) != 0:
            expect = ((D(r["value"]) - D(r["prior_value"])) / abs(D(r["prior_value"])) * 100).quantize(D("0.01"), rounding=ROUND_HALF_UP)
            assert D(r["variance_pct"]) == expect, r["code"]
    by = {r["code"]: r for r in d["items"]}
    pat, rev = line(client, eid, "PL.PAT", "statements/pnl"), line(client, eid, "PL.REV", "statements/pnl")
    r8 = by["R-08"]                                                                                                    # net profit ratio, both years from the statements
    assert D(r8["numerator"]) == D(pat["current"]) and D(r8["denominator"]) == D(rev["current"])
    assert D(r8["value"]) == (D(pat["current"]) / D(rev["current"]) * 100).quantize(D("0.01"), rounding=ROUND_HALF_UP)
    assert D(r8["prior_numerator"]) == D(pat["previous"]) and D(r8["prior_denominator"]) == D(rev["previous"])
    assert D(r8["prior_value"]) == (D(pat["previous"]) / D(rev["previous"]) * 100).quantize(D("0.01"), rounding=ROUND_HALF_UP)
    page = client.get("/ratios").text
    assert "q7-banner" not in page and "provisional" not in page.lower() and BASIS in page and "% variance" in page and 'id="th-prev"' in page
    pack = client.get(f"/api/engagements/{eid}/pack").json()["financials"]["ratios"]
    assert pack["provisional"] is False and pack["open_question"] is None and BASIS in pack["note"]


def test_signing_off_works_with_the_real_chk06_and_the_new_settings_freeze(client, eid, reopened, test_db_url):
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        assert conn.execute("SELECT rule_type FROM check_definitions WHERE code = 'CHK-06'").fetchone()[0] == "notes_reconcile_face"      # no workaround in place
    assert client.post(f"/api/engagements/{eid}/checks/run", headers=CHECKER).json()["summary"]["text"] == "6 of 6 active checks passing"
    state = client.get(f"/api/engagements/{eid}/signoff").json()
    assert state["can_sign_off"] is True and state["blockers"] == []
    done = client.post(f"/api/engagements/{eid}/signoff", json={"note": "Step 15: all six checks pass"}, headers=PARTNER)
    assert done.status_code == 200, done.text
    assert done.json()["snapshot_json"]["checks"]["passed"] == done.json()["snapshot_json"]["checks"]["total"] == 6

    r = client.patch(f"/api/engagements/{eid}/entity-type", json={"entity_type": "partnership"}, headers=CHECKER)
    assert r.status_code == 409 and r.json()["detail"] == LOCK_MSG
    assert client.get(f"/api/engagements/{eid}").json()["entity_type"] == "private_limited"
    assert client.get(f"/api/engagements/{eid}/export/statements.docx", headers=MAKER).status_code == 200              # exporting never changes data: allowed when locked
    assert client.get(f"/api/engagements/{eid}/export/pack.docx", headers=MAKER).status_code == 200
