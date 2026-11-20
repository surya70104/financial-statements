"""Step 12 acceptance tests: T-20 Reconciliation Engine and T-21 GSTR-9C Reconciliation (WP9), using the two sample workbooks.
Starts from the pilot as Step 11 left it (signed off, locked): the Partner re-opens it, the tests run, and the last test
signs it off again to prove both registers are frozen by the lock."""
import csv
import io
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from openpyxl import Workbook

D = Decimal
ROOT = Path(__file__).resolve().parent.parent
BANK_XLSX, GSTR_XLSX = ROOT / "samples" / "bank_reconciliation.xlsx", ROOT / "samples" / "gstr9c_reconciliation.xlsx"
MAKER, CHECKER, PARTNER = {"X-User-Id": "2"}, {"X-User-Id": "3"}, {"X-User-Id": "4"}
LOCK_MSG = "Engagement is locked after Partner sign-off"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
BANK = "HDFC Bank Current A/c"


@pytest.fixture(scope="module")
def eid(client):
    return client.get("/api/engagements").json()[0]["id"]


@pytest.fixture(scope="module")
def reopened(client, eid):
    if client.get(f"/api/engagements/{eid}/signoff").json()["is_locked"]:
        assert client.post(f"/api/engagements/{eid}/signoff/reopen", json={"reopen_reason": "Reconcile bank and GSTR-9C"}, headers=PARTNER).status_code == 200


def upload(client, eid, path, name, who=MAKER, content=None, ctype=XLSX, filename=None):
    data = content if content is not None else Path(path).read_bytes()
    return client.post(f"/api/engagements/{eid}/{name}/import", files={"file": (filename or (Path(path).name if path else "upload.xlsx"), data, ctype)}, headers=who)


def xlsx_bytes(rows):
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def csv_bytes(rows):
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return buf.getvalue().encode()


def lines(client, eid):
    bs = client.get(f"/api/engagements/{eid}/statements/balance-sheet").json()
    pnl = client.get(f"/api/engagements/{eid}/statements/pnl").json()
    out = {l["code"]: D(l["current"]) for part in bs["parts"] for sec in part["sections"] for l in sec["lines"]}
    out.update({l["code"]: D(l["current"]) for sec in pnl["sections"] for l in sec["lines"]})
    return out, bs["check"]


def adj_net(client, eid, ledger_name):
    rows = client.get(f"/api/engagements/{eid}/adjusted-tb").json()["rows"]
    return next(D(r["adjustment_net"]) for r in rows if r["ledger_name"] == ledger_name)


def gstr_issues(client, eid, status=None):
    q = "?source=gstr9c" + (f"&status={status}" if status else "")
    return client.get(f"/api/engagements/{eid}/issues{q}").json()


def test_bank_sample_parses_timing_and_adjustment_items(client, eid, reopened):
    assert BANK_XLSX.exists() and GSTR_XLSX.exists()
    r = upload(client, eid, BANK_XLSX, "reconciliations")
    assert r.status_code == 200 and r.json()["imported"] == 6 and r.json()["needing_adjustment"] == 4, r.text
    t = client.get(f"/api/engagements/{eid}/reconciliations").json()
    s = t["summary"]
    assert (s["total_items"], s["timing_differences"], s["adjustment_required"], s["awaiting_proposal"], s["proposed_count"], s["approved_count"]) == (6, 2, 4, 4, 0, 0)
    by_ref = {r["reference_no"]: r for r in t["rows"]}
    diffs = {"CHQ 004521": "185000.00", "DEP 31032027": "-240000.00", "CHG-Q4": "-4720.00", "NEFT-CHG": "-1180.00", "FD-INT-Q4": "38500.00", "POS-MAR": "-7250.40"}
    assert {k: v["difference_amount"] for k, v in by_ref.items()} == diffs                       # statement minus books, exact paise
    for ref in ("CHQ 004521", "DEP 31032027"):
        assert by_ref[ref]["adjustment_action"] == "timing_only" and by_ref[ref]["status"] == "resolved" and by_ref[ref]["proposed_journal_id"] is None
    for ref in ("CHG-Q4", "NEFT-CHG", "FD-INT-Q4", "POS-MAR"):
        assert by_ref[ref]["adjustment_action"] == "propose_adjustment" and by_ref[ref]["status"] == "open"
        assert by_ref[ref]["offset_ledger_code"] == BANK
    assert by_ref["FD-INT-Q4"]["ledger_code"] == "Interest on Fixed Deposits" and by_ref["POS-MAR"]["ledger_code"] == "Bank Charges"
    assert D(s["timing_amount"]) == D("-55000.00") and D(s["adjustment_amount"]) == D("25349.60")
    assert by_ref["CHG-Q4"]["item_date"] == "2027-03-31" and by_ref["CHG-Q4"]["created_by_name"].startswith("Maya")


def test_import_is_all_or_nothing_and_rejects_duplicates_and_bad_rows(client, eid, reopened):
    assert upload(client, eid, BANK_XLSX, "reconciliations").status_code == 422                    # the same file twice
    head = ["Type", "Account Name", "Date", "Reference No", "Description", "Book Amount", "Statement Amount", "Difference", "Adjustment Action", "Ledger Code", "Offset Ledger Code"]
    bad = xlsx_bytes([head,
                      ["bank", BANK, "2027-03-30", "OK-1", "A fine row", 0, -100, None, "propose_adjustment", "Bank Charges", BANK],       # row 2 fine
                      ["bank", BANK, "2027-03-30", "B-2", "Unknown ledger", 0, -100, None, "propose_adjustment", "No Such Ledger", BANK],  # row 3
                      ["bank", BANK, "2027-03-30", "B-3", "Wrong difference", 0, -100, -90, "timing_only", None, None],                    # row 4
                      ["bank", BANK, "2027-03-30", "B-4", "No offset ledger", 0, -100, None, "propose_adjustment", "Bank Charges", None],  # row 5
                      ["cash", BANK, "2027-03-30", "B-5", "Bad type", 0, -100, None, "timing_only", None, None],                           # row 6
                      ["bank", BANK, "30/13/2027", "B-6", "Bad date", 0, -100, None, "timing_only", None, None],                           # row 7
                      ["bank", BANK, "2027-03-30", "B-7", "Sub-paisa", 0, -100.005, None, "timing_only", None, None],                      # row 8
                      ["bank", BANK, "2027-03-30", "B-8", "Same ledger both sides", 0, -100, None, "propose_adjustment", BANK, BANK]])     # row 9
    r = upload(client, eid, None, "reconciliations", content=bad)
    assert r.status_code == 422 and [e["row"] for e in r.json()["detail"]["errors"]] == [3, 4, 5, 6, 7, 8, 9]
    assert len(client.get(f"/api/engagements/{eid}/reconciliations").json()["rows"]) == 6          # row 2 was NOT saved either
    assert upload(client, eid, None, "reconciliations", content=b"junk").status_code == 422
    assert client.post(f"/api/engagements/{eid}/reconciliations/import", files={"file": ("x.txt", b"a", "text/plain")}, headers=MAKER).status_code == 422

    url = f"/api/engagements/{eid}/reconciliations"
    body = {"recon_type": "party", "account_name": "Zenith Consulting", "item_date": "2027-03-31", "reference_no": "INV-77", "description": "Invoice missing from our books",
            "book_amount": "0", "external_amount": "-11800.00", "adjustment_action": "propose_adjustment", "ledger_code": "Professional & Legal Fees", "offset_ledger_code": "Zenith Consulting"}
    ok = client.post(url, json=body, headers=MAKER)
    assert ok.status_code == 200 and ok.json()["difference_amount"] == "-11800.00" and ok.json()["status"] == "open", ok.text
    assert client.post(url, json=body, headers=MAKER).status_code == 409                          # duplicate
    for broken in ({**body, "book_amount": "abc"}, {**body, "reference_no": "X", "recon_type": "x"}, {**body, "reference_no": "Y", "description": " "},
                   {**body, "reference_no": "Z", "ledger_code": None}):
        assert client.post(url, json=broken, headers=MAKER).status_code == 400, broken
    assert client.post(url, json=body).status_code == 401
    assert client.delete(f"{url}/{ok.json()['id']}", headers=MAKER).status_code == 200            # an open item can be deleted
    assert client.delete(f"{url}/999999", headers=MAKER).status_code == 404
    assert len(client.get(url).json()["rows"]) == 6


def test_proposals_are_drafts_that_change_nothing_until_a_checker_approves(client, eid, reopened):
    lines0, check0 = lines(client, eid)
    nets0 = {n: adj_net(client, eid, n) for n in ("Bank Charges", "Interest on Fixed Deposits", BANK)}
    counts0 = client.get(f"/api/engagements/{eid}/adjustments").json()["counts"]
    assert check0["status"] == "BALANCED"

    r = client.post(f"/api/engagements/{eid}/reconciliations/propose-adjustments", headers=MAKER)
    assert r.status_code == 200 and r.json()["proposed"] == 4, r.text
    adj = client.get(f"/api/engagements/{eid}/adjustments").json()
    assert adj["counts"]["draft"] == counts0["draft"] + 4 and adj["counts"]["approved"] == counts0["approved"]
    drafts = [e for e in adj["entries"] if e["description"].startswith("Reconciliation (bank)")]
    assert len(drafts) == 4 and all(e["status"] == "draft" and e["created_by_name"].startswith("Maya") for e in drafts)
    assert all(sum(D(l["debit"]) for l in e["lines"]) == sum(D(l["credit"]) for l in e["lines"]) > 0 for e in drafts)       # balanced
    fd = next(e for e in drafts if "FD interest" in e["description"])
    assert [(l["ledger_name"], D(l["debit"]), D(l["credit"])) for l in fd["lines"]] == [(BANK, D("38500.00"), D("0.00")), ("Interest on Fixed Deposits", D("0.00"), D("38500.00"))]
    chg = next(e for e in drafts if "Bank charges debited" in e["description"])
    assert [(l["ledger_name"], D(l["debit"]), D(l["credit"])) for l in chg["lines"]] == [("Bank Charges", D("4720.00"), D("0.00")), (BANK, D("0.00"), D("4720.00"))]

    lines1, _ = lines(client, eid)
    assert lines1 == lines0                                                                          # drafts never reach the statements
    assert {n: adj_net(client, eid, n) for n in nets0} == nets0                                      # ...or the adjusted trial balance

    t = client.get(f"/api/engagements/{eid}/reconciliations").json()
    assert t["summary"]["proposed_count"] == 4 and t["summary"]["awaiting_proposal"] == 0
    proposed = [x for x in t["rows"] if x["status"] == "proposed"]
    assert len(proposed) == 4 and all(x["proposed_journal_id"] and x["journal_status"] == "draft" for x in proposed)
    assert client.post(f"/api/engagements/{eid}/reconciliations/propose-adjustments", headers=MAKER).status_code == 409        # nothing left to propose
    assert client.delete(f"/api/engagements/{eid}/reconciliations/{proposed[0]['id']}", headers=MAKER).status_code == 409      # it has a journal now
    assert client.post(f"/api/engagements/{eid}/reconciliations/propose-adjustments").status_code == 401
    assert "RECON_ADJUSTMENTS_PROPOSED" in {a["event_name"] for a in client.get(f"/api/engagements/{eid}/audit-logs?scope=events&limit=500", headers=MAKER).json()}

    # maker-checker: the person who proposed cannot approve; a Checker can, and then the items resolve themselves
    for e in drafts:
        assert client.post(f"/api/engagements/{eid}/adjustments/{e['id']}/approve", headers=MAKER).status_code == 403
        assert client.post(f"/api/engagements/{eid}/adjustments/{e['id']}/approve", headers=CHECKER).status_code == 200
    lines2, check2 = lines(client, eid)
    assert check2["status"] == "BALANCED" and D(check2["difference"]) == D("0.00")
    assert adj_net(client, eid, "Bank Charges") - nets0["Bank Charges"] == D("13150.40")           # 4,720 + 1,180 + 7,250.40
    assert adj_net(client, eid, "Interest on Fixed Deposits") - nets0["Interest on Fixed Deposits"] == D("-38500.00")
    assert adj_net(client, eid, BANK) - nets0[BANK] == D("25349.60")                               # +38,500 - 13,150.40
    assert lines2["PL.PAT"] - lines0["PL.PAT"] == D("25349.60") and lines2["PL.OI"] - lines0["PL.OI"] == D("38500.00")
    t = client.get(f"/api/engagements/{eid}/reconciliations").json()
    assert t["summary"]["approved_count"] == 4 and all(x["status"] == "resolved" and x["journal_status"] == "approved" for x in t["rows"] if x["proposed_journal_id"])


def test_gstr9c_sample_logs_one_deduplicated_issue_per_difference_and_clears_it(client, eid, reopened):
    r = upload(client, eid, GSTR_XLSX, "gstr9c")
    assert r.status_code == 200 and (r.json()["imported"], r.json()["added"], r.json()["differences"]) == (4, 4, 2), r.text
    g = client.get(f"/api/engagements/{eid}/gstr9c").json()
    assert {(x["gst_head"], x["table_ref"]): (x["status"], x["difference_amount"]) for x in g["rows"]} == {
        ("CGST", "Table 9"): ("matched", "0.00"), ("SGST", "Table 9"): ("matched", "0.00"),
        ("IGST", "Table 12"): ("difference_logged", "47500.00"), ("CESS", "Table 12"): ("difference_logged", "6250.00")}
    s = g["summary"]
    assert (D(s["books_total"]), D(s["gstr9c_total"]), D(s["net_difference"]), s["open_issues"]) == (D("5823400.00"), D("5769650.00"), D("53750.00"), 2)

    issues = gstr_issues(client, eid, "open")
    assert {i["reference_id"] for i in issues["items"]} == {"gstr9c:IGST:Table 12", "gstr9c:CESS:Table 12"} and len(issues["items"]) == 2
    by = {i["reference_id"]: i for i in issues["items"]}
    assert D(by["gstr9c:IGST:Table 12"]["amount"]) == D("47500.00") and D(by["gstr9c:CESS:Table 12"]["amount"]) == D("6250.00")
    assert by["gstr9c:IGST:Table 12"]["cause_code"] == "GSTR9C_DIFFERENCE" and by["gstr9c:IGST:Table 12"]["title"] == "GSTR-9C difference: IGST (Table 12)"
    assert "₹38,45,000.00" in by["gstr9c:IGST:Table 12"]["detail"] and "₹37,97,500.00" in by["gstr9c:IGST:Table 12"]["detail"]
    assert client.get(f"/api/engagements/{eid}/issues").json()["summary"]["by_source"]["gstr9c"]["open"] == 2
    assert any(b["type"] == "ISSUE" and b["source"] == "gstr9c" for b in client.get(f"/api/engagements/{eid}/signoff").json()["blockers"])   # they gate sign-off
    assert client.post(f"/api/engagements/{eid}/issues/{by['gstr9c:IGST:Table 12']['id']}/clear", json={"resolution_note": "x"}, headers=PARTNER).status_code == 400

    again = upload(client, eid, GSTR_XLSX, "gstr9c")                                                   # re-importing the same file: updates, no duplicates
    assert again.status_code == 200 and (again.json()["added"], again.json()["updated"]) == (0, 4)
    after = gstr_issues(client, eid)["items"]
    assert len(after) == 2 and {i["reference_id"]: (i["id"], i["occurrences"]) for i in after} == {k: (v["id"], v["occurrences"]) for k, v in by.items()}

    # IGST now agrees (0.00 difference): its issue clears itself, CESS stays open
    fixed = upload(client, eid, None, "gstr9c", content=csv_bytes([["GST Head", "Table Ref", "Books Amount", "GSTR-9C Amount"], ["igst", "12", "3845000.00", "3845000.00"]]),
                   ctype="text/csv", filename="fix.csv")
    assert fixed.status_code == 200 and fixed.json()["differences"] == 1
    rows = {(x["gst_head"], x["table_ref"]): x for x in client.get(f"/api/engagements/{eid}/gstr9c").json()["rows"]}
    assert rows[("IGST", "Table 12")]["status"] == "resolved" and rows[("IGST", "Table 12")]["difference_amount"] == "0.00"
    cleared = [i for i in gstr_issues(client, eid, "cleared")["items"] if i["reference_id"] == "gstr9c:IGST:Table 12"]
    assert len(cleared) == 1 and cleared[0]["cleared_by_system"] is True and "agrees with the books" in cleared[0]["resolution_note"]
    assert {i["reference_id"] for i in gstr_issues(client, eid, "open")["items"]} == {"gstr9c:CESS:Table 12"}

    # a difference that comes back re-opens the SAME issue; a single-row form works too
    back = client.post(f"/api/engagements/{eid}/gstr9c", headers=MAKER, json={"gst_head": "IGST", "table_ref": "Table 12", "books_amount": "3845000.00", "gstr9c_amount": "3800000.00"})
    assert back.status_code == 200 and back.json()["result"] == "updated"
    assert {i["reference_id"] for i in gstr_issues(client, eid, "open")["items"]} == {"gstr9c:CESS:Table 12", "gstr9c:IGST:Table 12"}
    assert next(i for i in gstr_issues(client, eid, "open")["items"] if i["reference_id"] == "gstr9c:IGST:Table 12")["id"] == by["gstr9c:IGST:Table 12"]["id"]
    assert len([i for i in gstr_issues(client, eid)["items"] if i["reference_id"] == "gstr9c:IGST:Table 12"]) == 1

    # bad rows are all-or-nothing; deleting a row closes its issue
    bad = xlsx_bytes([["GST Head", "Table Ref", "Books Amount", "GSTR-9C Amount"], ["CGST", "Table 9", 1, 1], ["VAT", "Table 9", 1, 1], ["IGST", "Table 9", 1.005, 1], ["CGST", "Table 9", 2, 2]])
    r = upload(client, eid, None, "gstr9c", content=bad)
    assert r.status_code == 422 and [e["row"] for e in r.json()["detail"]["errors"]] == [3, 4, 5]
    assert client.post(f"/api/engagements/{eid}/gstr9c", headers=MAKER, json={"gst_head": "IGST", "table_ref": "T", "books_amount": "x", "gstr9c_amount": "1"}).status_code == 400
    for x in client.get(f"/api/engagements/{eid}/gstr9c").json()["rows"]:
        if x["difference_amount"] != "0.00":
            assert client.delete(f"/api/engagements/{eid}/gstr9c/{x['id']}", headers=MAKER).status_code == 200
    assert gstr_issues(client, eid, "open")["items"] == []                                           # nothing open: the sign-off gate is clear again
    assert "GSTR9C_RECONCILED" in {a["event_name"] for a in client.get(f"/api/engagements/{eid}/audit-logs?scope=events&limit=500", headers=MAKER).json()}


def test_both_modules_are_frozen_once_the_engagement_is_signed_off(client, eid, reopened, test_db_url):
    url, gurl = f"/api/engagements/{eid}/reconciliations", f"/api/engagements/{eid}/gstr9c"
    spare = client.post(url, headers=MAKER, json={"recon_type": "bank", "account_name": BANK, "item_date": "2027-03-31", "reference_no": "LATE", "description": "Late bank charge",
                                                 "book_amount": "0", "external_amount": "-50.00", "adjustment_action": "propose_adjustment", "ledger_code": "Bank Charges", "offset_ledger_code": BANK})
    assert spare.status_code == 200                                                                  # an open item waiting when the Partner signs off
    row = client.post(gurl, headers=MAKER, json={"gst_head": "CGST", "table_ref": "Table 9", "books_amount": "5", "gstr9c_amount": "5"}).json()
    assert client.post(f"/api/engagements/{eid}/materiality", json={"input_mode": "manual", "overall_materiality": "999999999999.00"}, headers=CHECKER).status_code == 200
    assert client.post(f"/api/engagements/{eid}/checks/run", headers=CHECKER).json()["summary"]["text"] == "6 of 6 active checks passing"
    done = client.post(f"/api/engagements/{eid}/signoff", json={"note": "With reconciliations"}, headers=PARTNER)
    assert done.status_code == 200, done.text

    for name, path in (("reconciliations", BANK_XLSX), ("gstr9c", GSTR_XLSX)):
        r = upload(client, eid, path, name)
        assert r.status_code == 409 and r.json()["detail"] == LOCK_MSG, name
    p = client.post(f"{url}/propose-adjustments", headers=MAKER)
    assert p.status_code == 409 and p.json()["detail"] == LOCK_MSG
    for resp in (client.post(url, headers=MAKER, json={"recon_type": "bank", "account_name": "x", "item_date": "2027-03-31", "description": "d", "book_amount": "0", "external_amount": "1",
                                                      "adjustment_action": "timing_only"}),
                 client.post(gurl, headers=MAKER, json={"gst_head": "SGST", "table_ref": "Table 9", "books_amount": "1", "gstr9c_amount": "1"}),
                 client.delete(f"{url}/{spare.json()['id']}", headers=MAKER), client.delete(f"{gurl}/{row['id'] if 'id' in row else 0}", headers=MAKER)):
        assert resp.status_code == 409 and resp.json()["detail"] == LOCK_MSG
    assert client.get(url).status_code == 200 and client.get(gurl).status_code == 200                 # reading still works

    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.user_id','2',false)")
        with pytest.raises(psycopg.errors.RaiseException, match="locked"):
            conn.execute("DELETE FROM reconciliation_items WHERE id = %s", (spare.json()["id"],))
        with pytest.raises(psycopg.errors.RaiseException, match="locked"):
            conn.execute("INSERT INTO gstr9c_reconciliations (engagement_id, gst_head, table_ref, books_amount, gstr9c_amount, difference_amount, status, created_by) "
                         "VALUES (%s,'SGST','Table 12',1,1,0,'matched',2)", (eid,))
    for page in ("/reconciliation", "/gstr9c"):
        assert client.get(page).status_code == 200 and f'href="{page}"' in client.get(page).text
    assert 'value="gstr9c"' in client.get("/issues").text
