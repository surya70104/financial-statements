"""Step 13 acceptance tests: T-24 LFAR Assistant (WP9), using samples/loan_schedule.xlsx.
Starts from the pilot as Step 12 left it (signed off, locked): the Partner re-opens it, the tests run, and the last test
signs it off again to prove the loan schedule and the checklist are frozen by the lock."""
import io
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from openpyxl import Workbook

D = Decimal
ROOT = Path(__file__).resolve().parent.parent
LOAN_XLSX = ROOT / "samples" / "loan_schedule.xlsx"
MAKER, CHECKER, PARTNER = {"X-User-Id": "2"}, {"X-User-Id": "3"}, {"X-User-Id": "4"}
LOCK_MSG = "Engagement is locked after Partner sign-off"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CODES = [f"LFAR-0{i}" for i in range(1, 9)]


@pytest.fixture(scope="module")
def eid(client):
    return client.get("/api/engagements").json()[0]["id"]


@pytest.fixture(scope="module")
def reopened(client, eid):
    if client.get(f"/api/engagements/{eid}/signoff").json()["is_locked"]:
        assert client.post(f"/api/engagements/{eid}/signoff/reopen", json={"reopen_reason": "LFAR work"}, headers=PARTNER).status_code == 200


def lfar(client, eid):
    return client.get(f"/api/engagements/{eid}/lfar").json()


def upload(client, eid, path=None, who=MAKER, content=None):
    data = content if content is not None else Path(path).read_bytes()
    return client.post(f"/api/engagements/{eid}/lfar/loans/import", files={"file": (Path(path).name if path else "upload.xlsx", data, XLSX)}, headers=who)


def xlsx_bytes(rows):
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def adj_balance(client, eid, name):
    return next(D(r["adjusted_balance"]) for r in client.get(f"/api/engagements/{eid}/adjusted-tb").json()["rows"] if r["ledger_name"] == name)


def line_current(client, eid, code):
    bs = client.get(f"/api/engagements/{eid}/statements/balance-sheet").json()
    return next(D(l["current"]) for part in bs["parts"] for sec in part["sections"] for l in sec["lines"] if l["code"] == code)


def test_first_visit_seeds_the_eight_checkpoints_and_stores_no_ledger_balance(client, eid, reopened, test_db_url):
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM lfar_checklist_items WHERE engagement_id = %s", (eid,)).fetchone()[0] == 0     # nothing before the first visit
        cols = {r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name IN ('lfar_loan_facilities', 'lfar_checklist_items')")}
    assert not [c for c in cols if "balance" in c] and "ledger_code" in cols                                                     # no second copy of any ledger balance

    first = lfar(client, eid)
    assert [i["section_code"] for i in first["items"]] == CODES
    assert [i["category"] for i in first["items"]][:2] == ["Borrowings & Sanctioned Limits", "Overdue Loans & Defaults"]
    assert all(i["status"] == "pending" and i["auditor_remarks"] is None and i["reviewed_by"] is None and i["question_text"] and i["auto_metric"] for i in first["items"])
    assert first["source"] == "v_adjusted_tb" and first["loans"] == []
    assert first["summary"]["checkpoints_total"] == 8 and first["summary"]["checkpoints_reviewed"] == 0 and first["summary"]["facilities"] == 0
    second = lfar(client, eid)
    assert [i["id"] for i in second["items"]] == [i["id"] for i in first["items"]]                                               # seeding happens once

    metrics = {i["section_code"]: i["auto_metric"] for i in second["items"]}                                                    # live figures from the single source of truth
    assert D(metrics["LFAR-04"]["value"]) == line_current(client, eid, "BS.CA.TR") and D(metrics["LFAR-05"]["value"]) == line_current(client, eid, "BS.NCA.PPE")
    assert D(metrics["LFAR-08"]["value"]) == line_current(client, eid, "BS.CA.CASH")
    assert D(metrics["LFAR-03"]["value"]) == D(client.get(f"/api/engagements/{eid}/fixed-deposits").json()["summary"]["total_principal"])
    gst_open = client.get(f"/api/engagements/{eid}/gstr9c").json()["summary"]["open_issues"]
    assert f"{gst_open} open in Issues" in metrics["LFAR-07"]["detail"]
    assert metrics["LFAR-01"]["flag"] == "warn" and "No loan facilities" in metrics["LFAR-01"]["detail"]
    assert client.get(f"/api/engagements/{eid}/lfar").status_code == 200 and client.get("/api/engagements/999999/lfar").status_code == 404


def test_loan_schedule_import_reads_live_balances_and_flags_overrun_and_overdue(client, eid, reopened):
    r = upload(client, eid, LOAN_XLSX)
    assert r.status_code == 200 and (r.json()["imported"], r.json()["added"], r.json()["updated"]) == (4, 4, 0), r.text
    d = lfar(client, eid)
    loans = {l["ledger_code"]: l for l in d["loans"]}
    assert set(loans) == {"Term Loan - HDFC Bank", "Vehicle Loan", "Unsecured Loan - Directors", "Unsecured Loan - Others"}
    term = loans["Term Loan - HDFC Bank"]
    assert (term["lender_name"], term["facility_type"], term["account_number"], term["sanctioned_limit"], D(term["interest_rate"]), term["emi_status"]) == (
        "HDFC Bank Ltd", "Term Loan", "50300012345678", "45000000.00", D("9.250"), "regular")
    assert "mortgage of office premises" in term["security_details"] and term["created_by_name"].startswith("Maya")
    for name, l in loans.items():                                                                                                # the balance IS the adjusted TB's (credit shown positive)
        assert D(l["book_balance"]) == -adj_balance(client, eid, name)
    assert D(term["tb_balance"]) == D("40000000.00") and D(term["utilization_pct"]) == D("88.89") and D(term["headroom"]) == D("5000000.00") and term["flags"] == []
    veh = loans["Vehicle Loan"]
    assert D(veh["book_balance"]) == D("2400000.00") and D(veh["utilization_pct"]) == D("96.00") and veh["flags"] == []
    assert loans["Unsecured Loan - Directors"]["flags"] == []
    others = loans["Unsecured Loan - Others"]
    assert D(others["book_balance"]) == D("5000000.00") > D(others["sanctioned_limit"]) and sorted(others["flags"]) == ["over_limit", "overdue"]
    assert others["emi_status"] == "overdue" and others["overdue_amount"] == "350000.00" and D(others["utilization_pct"]) == D("125.00")

    s = d["summary"]
    assert s["facilities"] == 4 and s["over_limit"] == 1 and s["overdue"] == 1 and D(s["overdue_amount"]) == D("350000.00")
    assert D(s["total_sanctioned_limits"]) == D("71500000.00") and D(s["live_borrowings"]) == D("62400000.00")                      # 40,000,000 + 2,400,000 + 15,000,000 + 5,000,000
    m = {i["section_code"]: i["auto_metric"] for i in d["items"]}
    assert D(m["LFAR-01"]["value"]) == D("62400000.00") and m["LFAR-01"]["flag"] == "warn" and "1 over the limit" in m["LFAR-01"]["detail"]
    assert D(m["LFAR-02"]["value"]) == D("350000.00") and m["LFAR-02"]["flag"] == "warn"

    again = upload(client, eid, LOAN_XLSX)                                                                                       # re-import updates, never duplicates
    assert again.status_code == 200 and (again.json()["added"], again.json()["updated"]) == (0, 4) and len(lfar(client, eid)["loans"]) == 4
    assert "LFAR_LOANS_UPDATED" in {a["event_name"] for a in client.get(f"/api/engagements/{eid}/audit-logs?scope=events&limit=500", headers=MAKER).json()}


def test_import_is_all_or_nothing_and_names_the_bad_rows(client, eid, reopened):
    head = ["Lender Name", "Facility Type", "Account Number", "Ledger Code", "Sanctioned Limit", "Interest Rate (%)", "Security Details", "EMI Status", "Overdue Amount"]
    bad = xlsx_bytes([head,
                      ["Axis Bank", "Term Loan", "A-1", "Vehicle Loan", 1000000, 9, "ok", "regular", 0],                    # row 2 is fine
                      ["Axis Bank", "Term Loan", "A-2", "No Such Loan Ledger", 1000000, 9, "x", "regular", 0],            # row 3: unknown ledger
                      ["Axis Bank", "Mortgage", "A-3", "Vehicle Loan", 1000000, 9, "x", "regular", 0],                     # row 4: unknown type
                      ["Axis Bank", "Term Loan", "A-4", "Vehicle Loan", -5, 9, "x", "regular", 0],                         # row 5: negative limit
                      ["Axis Bank", "Term Loan", "A-5", "Vehicle Loan", 1000000, 9, "x", "regular", 5000],                 # row 6: overdue amount but status regular
                      ["Axis Bank", "Term Loan", "A-6", "Vehicle Loan", 1000000, 150, "x", "regular", 0],                  # row 7: rate above 100
                      ["Axis Bank", "Term Loan", "A-7", "Vehicle Loan", 1000000.005, 9, "x", "regular", 0],                # row 8: sub-paisa
                      ["Axis Bank", "Term Loan", "A-1", "Vehicle Loan", 1000000, 9, "x", "regular", 0]])                   # row 9: repeats row 2
    r = upload(client, eid, content=bad)
    assert r.status_code == 422 and [e["row"] for e in r.json()["detail"]["errors"]] == [3, 4, 5, 6, 7, 8, 9]
    assert "No Such Loan Ledger" in r.json()["detail"]["errors"][0]["message"]
    assert len(lfar(client, eid)["loans"]) == 4                                                                                 # row 2 was NOT saved either
    assert upload(client, eid, content=b"junk").status_code == 422
    assert client.post(f"/api/engagements/{eid}/lfar/loans/import", files={"file": ("x.txt", b"a", "text/plain")}, headers=MAKER).status_code == 422

    url = f"/api/engagements/{eid}/lfar/loans"
    body = {"lender_name": "Axis Bank", "facility_type": "cc", "account_number": 912010055, "ledger_code": "Unsecured Loan - Others", "sanctioned_limit": "7500000", "interest_rate": "10.5"}
    ok = client.post(url, json=body, headers=MAKER)
    assert ok.status_code == 200 and ok.json()["result"] == "added" and ok.json()["facility_type"] == "Working Capital / CC" and ok.json()["account_number"] == "912010055", ok.text
    assert client.post(url, json={**body, "sanctioned_limit": "9000000"}, headers=MAKER).json()["result"] == "updated"        # same lender and account: updated
    for broken in ({**body, "account_number": "B", "ledger_code": "Nope"}, {**body, "account_number": "C", "facility_type": "x"}, {**body, "account_number": "D", "sanctioned_limit": "abc"},
                   {**body, "account_number": "E", "emi_status": "late"}, {**body, "account_number": "F", "lender_name": " "}):
        assert client.post(url, json=broken, headers=MAKER).status_code == 400, broken
    assert client.post(url, json=body).status_code == 401
    mine = next(l for l in lfar(client, eid)["loans"] if l["lender_name"] == "Axis Bank")
    assert mine["sanctioned_limit"] == "9000000.00"
    assert client.delete(f"{url}/{mine['id']}", headers=MAKER).status_code == 200 and client.delete(f"{url}/999999", headers=MAKER).status_code == 404
    assert len(lfar(client, eid)["loans"]) == 4


def test_a_posted_adjustment_shows_in_lfar_at_once(client, eid, reopened):
    ledgers = {l["name"]: l["id"] for l in client.get(f"/api/engagements/{eid}/adjustments").json()["ledgers"]}
    before = lfar(client, eid)
    veh0 = next(l for l in before["loans"] if l["ledger_code"] == "Vehicle Loan")
    assert D(veh0["book_balance"]) == D("2400000.00") and "over_limit" not in veh0["flags"]

    entry = client.post(f"/api/engagements/{eid}/adjustments", headers=MAKER, json={"entry_date": "2027-03-31", "description": "Vehicle loan: instalment capitalised", "lines": [
        {"ledger_id": ledgers["Bank Charges"], "debit": "150000.00"}, {"ledger_id": ledgers["Vehicle Loan"], "credit": "150000.00"}]})
    assert entry.status_code in (200, 201), entry.text
    unchanged = lfar(client, eid)                                                                                                # a DRAFT is not in the adjusted TB, so not in LFAR
    assert next(l for l in unchanged["loans"] if l["ledger_code"] == "Vehicle Loan")["book_balance"] == veh0["book_balance"]
    assert client.post(f"/api/engagements/{eid}/adjustments/{entry.json()['id']}/approve", headers=CHECKER).status_code == 200

    after = lfar(client, eid)                                                                                                    # no re-import, no refresh: LFAR just reads the adjusted TB
    veh1 = next(l for l in after["loans"] if l["ledger_code"] == "Vehicle Loan")
    assert D(veh1["book_balance"]) == D("2550000.00") == -adj_balance(client, eid, "Vehicle Loan")
    assert D(veh1["adjustment_net"]) == D("150000.00") and D(veh1["tb_balance"]) == D("2400000.00") and D(veh1["utilization_pct"]) == D("102.00")
    assert veh1["flags"] == ["over_limit"] and D(veh1["headroom"]) == D("-50000.00")
    assert after["summary"]["over_limit"] == before["summary"]["over_limit"] + 1
    assert D(after["summary"]["live_borrowings"]) == D(before["summary"]["live_borrowings"]) + D("150000.00")
    m = {i["section_code"]: i["auto_metric"] for i in after["items"]}
    assert D(m["LFAR-01"]["value"]) == D(after["summary"]["live_borrowings"]) and "2 over the limit" in m["LFAR-01"]["detail"]
    assert D(m["LFAR-08"]["value"]) == line_current(client, eid, "BS.CA.CASH")


def test_checklist_updates_are_logged_and_validated(client, eid, reopened):
    items = {i["section_code"]: i for i in lfar(client, eid)["items"]}
    one, two, three = items["LFAR-01"]["id"], items["LFAR-02"]["id"], items["LFAR-03"]["id"]
    url = lambda i: f"/api/engagements/{eid}/lfar/items/{i}"
    r = client.patch(url(one), json={"status": "complied", "auditor_remarks": "Sanction letters seen for all four facilities."}, headers=MAKER)
    assert r.status_code == 200 and r.json()["status"] == "complied", r.text
    assert client.patch(url(two), json={"status": "exception_noted", "auditor_remarks": "Sunrise Finance: Rs 3,50,000 overdue and limit exceeded."}, headers=CHECKER).status_code == 200
    assert client.patch(url(three), json={"status": "not_applicable"}, headers=PARTNER).status_code == 200

    d = lfar(client, eid)
    got = {i["section_code"]: i for i in d["items"]}
    assert (got["LFAR-01"]["status"], got["LFAR-01"]["reviewed_by_name"], got["LFAR-01"]["auditor_remarks"]) == ("complied", "Maya Maker (Preparer)", "Sanction letters seen for all four facilities.")
    assert got["LFAR-02"]["reviewed_by_name"].startswith("Chetan") and got["LFAR-03"]["reviewed_by_name"] == "Priya Partner" and got["LFAR-03"]["reviewed_at"]
    assert (d["summary"]["checkpoints_reviewed"], d["summary"]["exceptions_noted"]) == (3, 1)
    ev = [a for a in client.get(f"/api/engagements/{eid}/audit-logs?scope=events&limit=500", headers=MAKER).json() if a["event_name"] == "LFAR_ITEM_UPDATED"]
    assert len(ev) >= 3 and {a["after_data"]["section_code"] for a in ev} >= {"LFAR-01", "LFAR-02", "LFAR-03"}
    assert next(a for a in ev if a["after_data"]["section_code"] == "LFAR-02")["after_data"]["status"] == "exception_noted"
    assert next(a for a in ev if a["after_data"]["section_code"] == "LFAR-01")["reason"] == "Sanction letters seen for all four facilities."

    assert client.patch(url(one), json={"status": "exception_noted"}, headers=MAKER).status_code == 400                           # an exception needs remarks
    assert client.patch(url(one), json={"status": "exception_noted", "auditor_remarks": "  "}, headers=MAKER).status_code == 400
    assert client.patch(url(one), json={"status": "done"}, headers=MAKER).status_code == 400
    assert client.patch(url(one), json={"status": "complied"}).status_code == 401
    assert client.patch(url(999999), json={"status": "complied"}, headers=MAKER).status_code == 404
    assert lfar(client, eid)["items"][0]["status"] == "complied"                                                                  # refused requests changed nothing
    back = client.patch(url(three), json={"status": "pending"}, headers=MAKER)                                                    # back to pending clears the reviewer
    assert back.status_code == 200
    assert next(i for i in lfar(client, eid)["items"] if i["section_code"] == "LFAR-03")["reviewed_by"] is None


def test_loans_and_checklist_are_frozen_once_the_engagement_is_signed_off(client, eid, reopened, test_db_url):
    assert client.post(f"/api/engagements/{eid}/materiality", json={"input_mode": "manual", "overall_materiality": "999999999999.00"}, headers=CHECKER).status_code == 200
    assert client.post(f"/api/engagements/{eid}/checks/run", headers=CHECKER).json()["summary"]["text"] == "6 of 6 active checks passing"
    done = client.post(f"/api/engagements/{eid}/signoff", json={"note": "With LFAR"}, headers=PARTNER)
    assert done.status_code == 200, done.text

    items = lfar(client, eid)["items"]                                                                                            # reading still works while locked
    loan = lfar(client, eid)["loans"][0]
    r = upload(client, eid, LOAN_XLSX)
    assert r.status_code == 409 and r.json()["detail"] == LOCK_MSG
    for resp in (client.post(f"/api/engagements/{eid}/lfar/loans", headers=MAKER, json={"lender_name": "X", "facility_type": "Term Loan", "account_number": "1",
                                                                                         "ledger_code": "Vehicle Loan", "sanctioned_limit": "1"}),
                 client.delete(f"/api/engagements/{eid}/lfar/loans/{loan['id']}", headers=MAKER),
                 client.patch(f"/api/engagements/{eid}/lfar/items/{items[0]['id']}", headers=MAKER, json={"status": "complied", "auditor_remarks": "late"}),
                 client.patch(f"/api/engagements/{eid}/lfar/items/{items[0]['id']}", headers=PARTNER, json={"status": "pending"})):
        assert resp.status_code == 409 and resp.json()["detail"] == LOCK_MSG
    assert lfar(client, eid)["items"][0]["status"] == "complied" and len(lfar(client, eid)["loans"]) == 4                          # nothing changed

    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.user_id','2',false)")
        with pytest.raises(psycopg.errors.RaiseException, match="locked"):
            conn.execute("UPDATE lfar_checklist_items SET auditor_remarks = 'x' WHERE id = %s", (items[0]["id"],))
        with pytest.raises(psycopg.errors.RaiseException, match="locked"):
            conn.execute("DELETE FROM lfar_loan_facilities WHERE id = %s", (loan["id"],))
    page = client.get("/lfar")
    assert page.status_code == 200 and 'href="/lfar"' in page.text and "data-print" in page.text
