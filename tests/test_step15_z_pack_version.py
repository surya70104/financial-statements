"""Follow-ups to Step 15: the Audit Pack header shows the report version (T-16), and the Issues owner cell is always rendered.
Runs after test_step15_q1_q2_q4_q7.py, which leaves the pilot signed off and locked."""
import io

from docx import Document

MAKER, CHECKER, PARTNER = {"X-User-Id": "2"}, {"X-User-Id": "3"}, {"X-User-Id": "4"}


def eid_of(client):
    return client.get("/api/engagements").json()[0]["id"]


def docx_text(content: bytes) -> str:
    d = Document(io.BytesIO(content))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            parts += [c.text for c in row.cells]
    return "\n".join(parts)


def test_the_pack_header_shows_the_report_version_signed_then_draft(client):
    eid = eid_of(client)
    sign = client.get(f"/api/engagements/{eid}/signoff").json()
    assert sign["is_locked"] is True                                                                       # left signed off by the previous test file
    n = sign["active_version"]["version_number"]

    pack = client.get(f"/api/engagements/{eid}/pack").json()
    v = pack["header"]["report_version"]
    assert v["status"] == "signed" and v["number"] == n and v["label"] == f"Version v{n} (Signed)" and v["signed_at"]
    assert pack["header"]["period_end"] == "2027-03-31" and pack["header"]["data_revision"] >= 1                 # statement date and data revision sit beside it
    text = docx_text(client.get(f"/api/engagements/{eid}/export/pack.docx", headers=MAKER).content)
    assert f"Version v{n} (Signed)" in text and "31 March 2027" in text and "Statement date" in text and "Report version" in text

    # re-opening withdraws the signed version: the next one is a draft
    assert client.post(f"/api/engagements/{eid}/signoff/reopen", json={"reopen_reason": "Pack version check"}, headers=PARTNER).status_code == 200
    v = client.get(f"/api/engagements/{eid}/pack").json()["header"]["report_version"]
    assert v == {"number": n + 1, "status": "draft", "label": f"Draft v{n + 1}", "signed_at": None}
    assert f"Draft v{n + 1}" in docx_text(client.get(f"/api/engagements/{eid}/export/pack.docx", headers=CHECKER).content)
    js = client.get("/static/js/pack.js").text
    assert "Report version" in js and "Statement date" in js and "report_version" in js


def test_the_issues_owner_cell_is_always_rendered(client):
    js = client.get("/static/js/issues.js").text
    ow = js.split("function ownerCell(it) {")[1].split("function actionCell")[0]
    assert 'it.status !== "open" || locked()' in ow and '"Unassigned"' in ow and "return sel;" in ow                       # name or "Unassigned" when closed or locked, else the dropdown
    assert "disabled: locked()" not in ow                                                                         # no half-empty disabled dropdown any more
    head = client.get("/issues").text.split('id="issues-table"')[1].split("</thead>")[0]
    assert len(__import__("re").findall(r"<th[ >]", head)) == 9 and "<th>Owner</th>" in head                                                    # nine headers, and the body renders nine cells per row
    assert js.count("h(\"td\"") >= 9 and 'colspan: 9' in js
