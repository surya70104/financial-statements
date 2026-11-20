"""Step 14 acceptance tests: the gaps found when the build was audited against the brief.
  (a) T-12 issue owner, approval, grouping by cause       (b) T-18 a signed report without a UDIN is an exception
  (c) T-16 stale-pack detection                           (d) T-23 materiality applied to GSTR-9C and reconciliation differences
  (e) T-22 the dedicated fixed-deposit note               (f) prior-year comparatives (ledger by ledger) feeding the statements
Starts from the pilot as Step 13 left it (signed off, locked): the Partner re-opens it. The last test signs it off again."""
import io
from collections import Counter
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from openpyxl import Workbook

D = Decimal
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
        assert client.post(f"/api/engagements/{eid}/signoff/reopen", json={"reopen_reason": "Step 14 checks"}, headers=PARTNER).status_code == 200


def issues(client, eid, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"/api/engagements/{eid}/issues" + (f"?{qs}" if qs else "")).json()


def set_materiality(client, eid, amount):
    r = client.post(f"/api/engagements/{eid}/materiality", json={"input_mode": "manual", "overall_materiality": str(amount)}, headers=CHECKER)
    assert r.status_code == 200, r.text


def xlsx_bytes(rows):
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def line(client, eid, code, statement="statements/balance-sheet"):
    d = client.get(f"/api/engagements/{eid}/{statement}").json()
    secs = [s for p in d["parts"] for s in p["sections"]] if "parts" in d else d["sections"]
    return next(l for s in secs for l in s["lines"] if l["code"] == code)


def test_owner_approval_and_grouping_by_cause(client, eid, reopened):
    url = f"/api/engagements/{eid}/issues"
    a = client.post(url, json={"title": "Confirm related-party disclosure", "detail": "Note 24"}, headers=MAKER).json()["id"]
    b = client.post(url, json={"title": "Obtain Board resolution for the loan"}, headers=MAKER).json()["id"]

    # owners: any team member can assign, the owner must be a real team member
    r = client.patch(f"{url}/{a}/assign", json={"owner_id": 3}, headers=MAKER)
    assert r.status_code == 200 and r.json()["owner_name"].startswith("Chetan")
    got = next(i for i in issues(client, eid, status="open")["items"] if i["id"] == a)
    assert got["owner_id"] == 3 and got["owner_name"].startswith("Chetan")
    assert client.patch(f"{url}/{a}/assign", json={"owner_id": 1}, headers=MAKER).status_code == 400            # the SYSTEM user is not a person
    assert client.patch(f"{url}/{a}/assign", json={"owner_id": 999}, headers=MAKER).status_code == 400
    assert client.patch(f"{url}/999999/assign", json={"owner_id": 3}, headers=MAKER).status_code == 404
    assert client.patch(f"{url}/{a}/assign", json={"owner_id": 3}).status_code == 401
    assert client.patch(f"{url}/{a}/assign", json={"owner_id": None}, headers=PARTNER).json()["owner_name"] is None   # unassign
    assert client.patch(f"{url}/{a}/assign", json={"owner_id": 4}, headers=CHECKER).status_code == 200
    assert "ISSUE_ASSIGNED" in {e["event_name"] for e in client.get(f"/api/engagements/{eid}/audit-logs?scope=events&limit=500", headers=MAKER).json()}

    # approval: Checker or Partner, with a note; the Maker cannot
    assert any(x.get("issue_id") == a for x in client.get(f"/api/engagements/{eid}/signoff").json()["blockers"])      # open: it blocks sign-off
    assert client.post(f"{url}/{a}/approve", json={"approval_note": "Accepted"}, headers=MAKER).status_code == 403
    assert client.post(f"{url}/{a}/approve", json={"approval_note": "Accepted"}).status_code == 401
    assert client.post(f"{url}/{a}/approve", json={}, headers=CHECKER).status_code == 400
    assert client.post(f"{url}/{a}/approve", json={"approval_note": "   "}, headers=CHECKER).status_code == 400
    ok = client.post(f"{url}/{a}/approve", json={"approval_note": "Disclosure reviewed; accepted by the reviewer"}, headers=CHECKER)
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    done = next(i for i in issues(client, eid, status="approved")["items"] if i["id"] == a)
    assert done["approved_by_name"].startswith("Chetan") and done["approval_note"] == "Disclosure reviewed; accepted by the reviewer" and done["approved_at"]
    assert client.post(f"{url}/{a}/approve", json={"approval_note": "again"}, headers=CHECKER).status_code == 409
    assert not any(x.get("issue_id") == a for x in client.get(f"/api/engagements/{eid}/signoff").json()["blockers"])    # approved: no longer a blocker
    assert any(x.get("issue_id") == b for x in client.get(f"/api/engagements/{eid}/signoff").json()["blockers"])        # the other one still is
    assert client.post(f"{url}/{b}/approve", json={"approval_note": "Partner accepts"}, headers=PARTNER).status_code == 200
    assert issues(client, eid)["summary"]["open"] == 0 and issues(client, eid)["summary"]["approved"] == 2
    assert "ISSUE_APPROVED" in {e["event_name"] for e in client.get(f"/api/engagements/{eid}/audit-logs?scope=events&limit=500", headers=MAKER).json()}

    # grouping by cause: every issue has a cause, and the groups add up to the whole list
    allv = issues(client, eid)["items"]
    groups = Counter(i["cause_code"] for i in allv)
    assert all(i["cause"] for i in allv) and sum(groups.values()) == len(allv) and "MANUAL" in groups and groups["MANUAL"] >= 2
    page = client.get("/issues").text
    assert 'id="f-group"' in page and "Group by cause" in page and 'value="approved"' in page and 'value="udin"' in page and "<th>Owner</th>" in page


def test_differences_above_materiality_are_flagged_in_the_tracker(client, eid, reopened):
    set_materiality(client, eid, "10000.00")
    g, r = f"/api/engagements/{eid}/gstr9c", f"/api/engagements/{eid}/reconciliations"
    assert client.post(g, headers=MAKER, json={"gst_head": "CGST", "table_ref": "Table 9", "books_amount": "100000.00", "gstr9c_amount": "60000.00"}).status_code == 200
    assert client.post(g, headers=MAKER, json={"gst_head": "SGST", "table_ref": "Table 9", "books_amount": "5000.00", "gstr9c_amount": "2000.00"}).status_code == 200
    big = {"recon_type": "bank", "account_name": BANK, "item_date": "2027-03-31", "reference_no": "BIG-1", "description": "Unbooked bank debit", "book_amount": "0",
           "external_amount": "-25000.00", "adjustment_action": "propose_adjustment", "ledger_code": "Bank Charges", "offset_ledger_code": BANK}
    big_id = client.post(r, json=big, headers=MAKER).json()["id"]
    small = client.post(r, json={**big, "reference_no": "SMALL-1", "external_amount": "-3000.00"}, headers=MAKER)
    assert small.status_code == 200

    gst = {i["reference_id"]: i for i in issues(client, eid, source="gstr9c", status="open")["items"]}
    assert gst["gstr9c:CGST:Table 9"]["is_above_materiality"] is True and gst["gstr9c:SGST:Table 9"]["is_above_materiality"] is False
    rec = issues(client, eid, source="reconciliation", status="open")["items"]
    assert [i["reference_id"] for i in rec] == [f"recon:{big_id}"] and rec[0]["is_above_materiality"] is True and D(rec[0]["amount"]) == D("-25000.00")
    assert rec[0]["cause_code"] == "RECON_DIFFERENCE" and "Reconciliation difference above materiality" in rec[0]["title"]

    m = client.get(f"/api/engagements/{eid}/materiality").json()                                   # the Materiality screen lists them with the statement variances
    refs = {d["reference"]: d for d in m["differences_exceeding"]}
    assert {"gstr9c:CGST:Table 9", f"recon:{big_id}"} <= set(refs) and "gstr9c:SGST:Table 9" not in refs
    assert D(refs["gstr9c:CGST:Table 9"]["variance"]) == D("40000.00") and any(d["reference"] == "gstr9c:SGST:Table 9" for d in m["differences"])

    # raising materiality clears the flag and the reconciliation issue; lowering it brings back the SAME issue
    set_materiality(client, eid, "1000000.00")
    assert issues(client, eid, source="reconciliation", status="open")["items"] == []
    assert all(i["is_above_materiality"] is False for i in issues(client, eid, source="gstr9c", status="open")["items"])
    cleared = [i for i in issues(client, eid, source="reconciliation", status="cleared")["items"] if i["reference_id"] == f"recon:{big_id}"]
    assert len(cleared) == 1 and cleared[0]["cleared_by_system"] is True
    set_materiality(client, eid, "10000.00")
    again = [i for i in issues(client, eid, source="reconciliation")["items"] if i["reference_id"] == f"recon:{big_id}"]
    assert len(again) == 1 and again[0]["id"] == cleared[0]["id"] and again[0]["status"] == "open"

    # approving the proposed adjustment resolves the item, and its issue clears
    assert client.post(f"{r}/propose-adjustments", headers=MAKER).status_code == 200
    drafts = [e for e in client.get(f"/api/engagements/{eid}/adjustments").json()["entries"] if e["status"] == "draft" and e["description"].startswith("Reconciliation (bank)")]
    for e in drafts:
        assert client.post(f"/api/engagements/{eid}/adjustments/{e['id']}/approve", headers=CHECKER).status_code == 200
    assert issues(client, eid, source="reconciliation", status="open")["items"] == []

    for x in client.get(g).json()["rows"]:                                                         # tidy: nothing may block the later sign-off
        if x["difference_amount"] != "0.00":
            assert client.delete(f"{g}/{x['id']}", headers=MAKER).status_code == 200
    assert issues(client, eid, source="gstr9c", status="open")["items"] == []
    set_materiality(client, eid, "999999999999.00")


def test_audit_pack_stale_flag_follows_the_data_revision(client, eid, reopened):
    url = f"/api/engagements/{eid}/pack"
    first = client.get(url).json()
    assert first["is_stale"] is True and first["current_revision"] > (first["last_built_revision"] or 0)                    # changed since the earlier exports (or never built)

    assert client.post(f"{url}/build").status_code == 401
    built = client.post(f"{url}/build", headers=MAKER)
    assert built.status_code == 200 and built.json()["is_stale"] is False
    fresh = client.get(url).json()
    assert fresh["is_stale"] is False and fresh["last_built_revision"] == fresh["current_revision"] and fresh["last_built_by"].startswith("Maya")
    assert "AUDIT_PACK_BUILT" in {e["event_name"] for e in client.get(f"/api/engagements/{eid}/audit-logs?scope=events&limit=500", headers=MAKER).json()}

    ledgers = {l["name"]: l["id"] for l in client.get(f"/api/engagements/{eid}/adjustments").json()["ledgers"]}
    entry = client.post(f"/api/engagements/{eid}/adjustments", headers=MAKER, json={"entry_date": "2027-03-31", "description": "stale-pack test", "lines": [
        {"ledger_id": ledgers["Bank Charges"], "debit": "100.00"}, {"ledger_id": ledgers[BANK], "credit": "100.00"}]})
    assert client.get(url).json()["is_stale"] is False                                              # a DRAFT changes no statement, so the pack is still current
    assert client.post(f"/api/engagements/{eid}/adjustments/{entry.json()['id']}/approve", headers=CHECKER).status_code == 200
    stale = client.get(url).json()
    assert stale["is_stale"] is True and stale["current_revision"] > stale["last_built_revision"]                          # an approved change makes it stale
    assert client.get("/pack").text.count('id="pack-build"') == 1 and "pack-stale" in client.get("/pack").text

    assert client.post(f"{url}/export", json={"format": "print"}, headers=MAKER).status_code == 200                       # an export is also a build
    assert client.get(url).json()["is_stale"] is False
    assert client.post(f"{url}/build", headers=PARTNER).status_code == 200


def test_fixed_deposit_note_reads_the_register(client, eid, reopened):
    note = client.get(f"/api/engagements/{eid}/notes").json()["fd_note"]
    reg = client.get(f"/api/engagements/{eid}/fixed-deposits").json()
    posted = [r for r in reg["rows"] if r["status"] == "posted"]
    assert note["posted_count"] == len(posted) == 5 and note["draft_count"] == len(reg["rows"]) - 5
    t = note["totals"]
    assert D(t["current_principal"]) == D("15500000.00") and D(t["non_current_principal"]) == D("9000000.00") and D(t["total_principal"]) == D("24500000.00")
    assert D(t["accrued_interest"]) == sum(D(r["accrued_interest"]) for r in posted)
    row = next(r for r in note["rows"] if r["fd_number"] == "37821904455")
    assert (row["bank_name"], row["maturity_classification"], row["principal_amount"], row["maturity_date"], row["accrued_interest"]) == ("SBI", "current", "8000000.00", "2027-12-10", "560000.00")
    assert any(r["status"] == "draft" for r in note["rows"]) is (note["draft_count"] > 0)                                  # drafts are listed but kept out of the totals
    assert 'id="fd-note"' in client.get("/notes").text


def test_prior_year_comparatives_feed_the_statements_and_materiality(client, eid, reopened, test_db_url):
    url = f"/api/engagements/{eid}/prior-year"
    py = client.get(url).json()
    assert py["total"] > 70 and py["entered"] == 0 and py["previous_label"] == "FY 2025-26"

    # 1. upload last year's balances (here: the same as this year's, ledger by ledger, in the way they are normally shown)
    sheet = [["Ledger Name", "Prior Year Amount"]] + [[r["name"], r["current_amount"]] for r in py["ledgers"] if r["current_amount"] is not None]
    bad = xlsx_bytes([["Ledger Name", "Prior Year Amount"], [BANK, "100"], ["No Such Ledger", "5"], [BANK, "6"], ["Bank Charges", "1.005"]])
    r = client.post(f"{url}/import", files={"file": ("bad.xlsx", bad, XLSX)}, headers=MAKER)
    assert r.status_code == 422 and [e["row"] for e in r.json()["detail"]["errors"]] == [3, 4, 5]                          # all-or-nothing, with row numbers
    assert client.get(url).json()["entered"] == 0
    assert client.post(f"{url}/import", files={"file": ("x.txt", b"a", "text/plain")}, headers=MAKER).status_code == 422
    assert client.post(f"{url}/import", files={"file": ("p.xlsx", xlsx_bytes(sheet), XLSX)}).status_code == 401
    ok = client.post(f"{url}/import", files={"file": ("p.xlsx", xlsx_bytes(sheet), XLSX)}, headers=MAKER)
    assert ok.status_code == 200 and ok.json()["imported"] == len(sheet) - 1 and ok.json()["lines_changed"] > 0, ok.text
    assert D(ok.json()["previous_difference"]) == D("0.00")                                                           # a balanced prior-year TB gives a balanced prior-year sheet

    cur_assets = line(client, eid, "BS.ASSETS.TOTAL")
    assert D(cur_assets["previous"]) == D(cur_assets["current"]) > 0
    assert D(line(client, eid, "BS.EQ.RS")["previous"]) == D(line(client, eid, "BS.EQ.RS")["current"])                 # last year's profit is in last year's reserves
    assert D(line(client, eid, "PL.REV", "statements/pnl")["previous"]) == D(line(client, eid, "PL.REV", "statements/pnl")["current"]) > 0
    assert D(client.get(f"/api/engagements/{eid}/statements/pnl").json()["profit_for_period"]["previous"]) == D(client.get(f"/api/engagements/{eid}/statements/pnl").json()["profit_for_period"]["current"])
    assert client.get(f"/api/engagements/{eid}/materiality").json()["variances"] == []                                # nothing moved yet
    assert "PRIOR_YEAR_UPDATED" in {e["event_name"] for e in client.get(f"/api/engagements/{eid}/audit-logs?scope=events&limit=500", headers=MAKER).json()}

    # 2. edit two ledgers inline: revenue was 50 lakh lower last year and so was the bank balance
    by = {l["name"]: l for l in client.get(url).json()["ledgers"]}
    rev, bank = by["Software Services - Domestic"], by[BANK]
    rev0 = client.get(f"/api/engagements/{eid}/statements/pnl").json()
    saved = client.post(url, json={"items": [{"ledger_id": rev["ledger_id"], "amount": str(D(rev["current_amount"]) - D("5000000.00"))},
                                             {"ledger_id": bank["ledger_id"], "amount": str(D(bank["current_amount"]) - D("5000000.00"))}]}, headers=MAKER)
    assert saved.status_code == 200 and saved.json()["updated"] == 2 and D(saved.json()["previous_difference"]) == D("0.00"), saved.text
    r_rev = line(client, eid, "PL.REV", "statements/pnl")
    assert D(r_rev["current"]) - D(r_rev["previous"]) == D("5000000.00")
    assert D(line(client, eid, "BS.CA.CASH")["current"]) - D(line(client, eid, "BS.CA.CASH")["previous"]) == D("5000000.00")
    assert client.get(f"/api/engagements/{eid}/statements/balance-sheet").json()["check"]["previous_difference"] == "0.00"
    variances = {v["code"]: D(v["variance"]) for v in client.get(f"/api/engagements/{eid}/materiality").json()["variances"]}
    assert variances == {"PL.REV": D("5000000.00"), "BS.CA.CASH": D("5000000.00"), "BS.EQ.RS": D("5000000.00")}          # the year-on-year variances update at once
    set_materiality(client, eid, "1000000.00")
    assert {i["reference_id"] for i in issues(client, eid, source="materiality", status="open")["items"]} == {"MATERIALITY:PL.REV", "MATERIALITY:BS.CA.CASH", "MATERIALITY:BS.EQ.RS"}
    summary = client.get(f"/api/engagements/{eid}/checks").json()["summary"]
    assert summary["passed"] == summary["total"]                                                                          # the checks were re-run and still pass

    # 3. clearing a ledger's balance, and bad input
    assert client.post(url, json={"items": [{"ledger_id": rev["ledger_id"], "amount": None}]}, headers=MAKER).json()["cleared"] == 1
    assert client.post(url, json={"items": [{"ledger_id": 999999, "amount": "1"}]}, headers=MAKER).status_code == 400
    assert client.post(url, json={"items": [{"ledger_id": bank["ledger_id"], "amount": "12.345"}]}, headers=MAKER).status_code == 400
    assert client.post(url, json={"items": []}, headers=MAKER).status_code == 400
    assert client.post(url, json={"items": [{"ledger_id": bank["ledger_id"], "amount": "1"}]}).status_code == 401
    # put it back so the statements are tidy, and let nothing block the sign-off in the next test
    client.post(url, json={"items": [{"ledger_id": rev["ledger_id"], "amount": str(D(rev["current_amount"]))}]}, headers=MAKER)
    set_materiality(client, eid, "999999999999.00")
    assert issues(client, eid, source="materiality", status="open")["items"] == []
    assert client.get("/").text.count("py-card") >= 1 and "comparatives.js" in client.get("/").text


def test_signed_report_without_a_udin_is_an_exception_until_the_udin_is_recorded(client, eid, reopened, test_db_url):
    assert issues(client, eid)["summary"]["open"] == 0
    assert client.post(f"/api/engagements/{eid}/checks/run", headers=CHECKER).json()["summary"]["text"] == "6 of 6 active checks passing"
    done = client.post(f"/api/engagements/{eid}/signoff", json={"note": "Step 14"}, headers=PARTNER)
    assert done.status_code == 200, done.text
    n = done.json()["version_number"]
    ver = next(v for v in client.get(f"/api/engagements/{eid}/signoff").json()["versions"] if v["version_number"] == n)

    ud = issues(client, eid, source="udin", status="open")                                                                # (earlier sign-offs left cleared ones behind)
    assert len(ud["items"]) == 1 and ud["items"][0]["reference_id"] == f"udin:v{n}" and ud["items"][0]["status"] == "open"
    assert ud["items"][0]["cause_code"] == "MISSING_UDIN" and ud["items"][0]["title"] == f"Signed report v{n} has no UDIN"
    issue_id = ud["items"][0]["id"]
    assert client.get(f"/api/engagements/{eid}/pack").json()["checks_and_issues"]["issues"]["by_source"]["udin"]["open"] == 1
    again = client.post(f"/api/engagements/{eid}/checks/run", headers=CHECKER)                                            # re-running anything never duplicates it
    assert again.status_code == 200 and len([i for i in issues(client, eid, source="udin")["items"] if i["reference_id"] == f"udin:v{n}"]) == 1

    udin = "26STEP14ABCDEFGH01"
    rec = client.post(f"/api/engagements/{eid}/udin/{ver['id']}", json={"udin_number": udin, "udin_generated_date": "2026-11-19"}, headers=PARTNER)
    assert rec.status_code == 200, rec.text
    cleared = next(i for i in issues(client, eid, source="udin")["items"] if i["id"] == issue_id)
    assert cleared["status"] == "cleared" and cleared["cleared_by_system"] is True and "UDIN is recorded" in cleared["resolution_note"]
    assert issues(client, eid, source="udin", status="open")["items"] == []

    rv = client.post(f"/api/engagements/{eid}/udin/{ver['id']}/revoke", json={"revoked_reason": "Wrong document"}, headers=PARTNER)
    assert rv.status_code == 200
    back = next(i for i in issues(client, eid, source="udin")["items"] if i["id"] == issue_id)
    assert back["status"] == "open" and "revoked" in back["title"]                                                          # a revoked UDIN is missing again: same issue, re-opened

    # a re-opening withdraws the version, so it needs no UDIN: the issue clears, and the next sign-off is not blocked by it
    assert client.post(f"/api/engagements/{eid}/signoff/reopen", json={"reopen_reason": "Step 14 withdraw"}, headers=PARTNER).status_code == 200
    assert next(i for i in issues(client, eid, source="udin")["items"] if i["id"] == issue_id)["status"] == "cleared"
    assert client.post(f"/api/engagements/{eid}/signoff", headers=PARTNER).status_code == 200
    now = issues(client, eid, source="udin", status="open")["items"]
    assert len(now) == 1 and now[0]["reference_id"] == f"udin:v{n + 1}"

    # and the comparatives are frozen with everything else once signed off
    url = f"/api/engagements/{eid}/prior-year"
    ledger = client.get(url).json()["ledgers"][0]["ledger_id"]
    for resp in (client.post(url, json={"items": [{"ledger_id": ledger, "amount": "1"}]}, headers=MAKER),
                 client.post(f"{url}/import", files={"file": ("p.xlsx", xlsx_bytes([["Ledger Name", "Prior Year Amount"], [BANK, "1"]]), XLSX)}, headers=MAKER),
                 client.patch(f"/api/engagements/{eid}/issues/{issue_id}/assign", json={"owner_id": 3}, headers=MAKER),
                 client.post(f"/api/engagements/{eid}/issues/{now[0]['id']}/approve", json={"approval_note": "x"}, headers=CHECKER)):
        assert resp.status_code == 409 and resp.json()["detail"] == LOCK_MSG
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.user_id','2',false)")
        with pytest.raises(psycopg.errors.RaiseException, match="locked"):
            conn.execute("UPDATE ledger_prior_year SET amount = 1")
    assert client.get(f"/api/engagements/{eid}/pack").status_code == 200
    assert client.post(f"/api/engagements/{eid}/pack/build", headers=MAKER).status_code == 200                              # building the pack is allowed when locked
