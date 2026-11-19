"""Step 11 acceptance tests: T-19 TDS / TCS Entry Pack and T-22 Fixed Deposit Register (WP9), using the two sample workbooks.
Starts from the pilot as Step 10 left it (signed off, locked): the Partner re-opens it, the tests run, and the last test
signs it off again to prove both registers are frozen by the lock."""
import csv
import io
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import psycopg
import pytest
from openpyxl import Workbook

D = Decimal
ROOT = Path(__file__).resolve().parent.parent
TDS_XLSX, FD_XLSX = ROOT / "samples" / "tds_tcs_pack.xlsx", ROOT / "samples" / "fd_register.xlsx"
MAKER, CHECKER, PARTNER = {"X-User-Id": "2"}, {"X-User-Id": "3"}, {"X-User-Id": "4"}
LOCK_MSG = "Engagement is locked after Partner sign-off"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FY_START, FY_END = date(2026, 4, 1), date(2027, 3, 31)


@pytest.fixture(scope="module")
def eid(client):
    return client.get("/api/engagements").json()[0]["id"]


@pytest.fixture(scope="module")
def reopened(client, eid):
    if client.get(f"/api/engagements/{eid}/signoff").json()["is_locked"]:
        assert client.post(f"/api/engagements/{eid}/signoff/reopen", json={"reopen_reason": "Post TDS and FD registers"}, headers=PARTNER).status_code == 200


def upload(client, eid, path, name, who=MAKER, content=None, ctype=XLSX):
    data = content if content is not None else Path(path).read_bytes()
    return client.post(f"/api/engagements/{eid}/{name}/import", files={"file": (Path(path).name if path else "upload.xlsx", data, ctype)}, headers=who)


def xlsx_bytes(rows):
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def lines(client, eid):
    """{line code: current amount} for every Balance Sheet and P&L line, plus the balance check."""
    bs = client.get(f"/api/engagements/{eid}/statements/balance-sheet").json()
    pnl = client.get(f"/api/engagements/{eid}/statements/pnl").json()
    out = {l["code"]: D(l["current"]) for part in bs["parts"] for sec in part["sections"] for l in sec["lines"]}
    out.update({l["code"]: D(l["current"]) for sec in pnl["sections"] for l in sec["lines"]})
    return out, bs["check"]


def adj_net(client, eid, ledger_name):
    rows = client.get(f"/api/engagements/{eid}/adjusted-tb").json()["rows"]
    return next((D(r["adjustment_net"]) for r in rows if r["ledger_name"] == ledger_name), None)


def expected_interest(principal, rate, start, maturity):
    first, last = max(start, FY_START), min(maturity, FY_END + timedelta(days=1))
    return (D(principal) * D(rate) / 100 * max(0, (last - first).days) / 365).quantize(D("0.01"), rounding=ROUND_HALF_UP)


FD_EXPECT = [("HDFC Bank", "50100234567801", "5000000.00", "7.100", date(2026, 4, 15), date(2027, 4, 15), "current", "BS.CA.CASH"),
             ("SBI", "37821904455", "8000000.00", "7.000", date(2025, 12, 10), date(2027, 12, 10), "current", "BS.CA.CASH"),
             ("ICICI Bank", "003405012345", "3000000.00", "7.250", date(2026, 7, 1), date(2029, 7, 1), "non_current", "BS.NCA.OTH"),
             ("HDFC Bank", "50100234567902", "2500000.00", "6.900", date(2026, 1, 20), date(2027, 7, 20), "current", "BS.CA.OTH"),
             ("SBI", "37821907789", "6000000.00", "7.150", date(2026, 10, 5), date(2029, 10, 5), "non_current", "BS.NCA.OTH")]


def test_sample_files_exist_and_both_registers_import_every_row(client, eid, reopened):
    assert TDS_XLSX.exists() and FD_XLSX.exists()
    r = upload(client, eid, TDS_XLSX, "tds-tcs")
    assert r.status_code == 200 and r.json()["imported"] == 8, r.text
    t = client.get(f"/api/engagements/{eid}/tds-tcs").json()
    by = t["summary"]["by_type"]
    assert {k: (v["count"], D(v["tax_amount"])) for k, v in by.items()} == {
        "TDS_PAYABLE": (4, D("345000.00")), "TDS_RECEIVABLE": (3, D("300000.00")), "TCS_PAYABLE": (1, D("6200.00")), "TCS_RECEIVABLE": (0, D("0.00"))}
    assert t["summary"]["draft_rows"] == 8 and t["summary"]["posted_rows"] == 0
    first = t["rows"][0]
    assert (first["entry_type"], first["section_code"], first["party_name"], first["ledger_code"], first["base_amount"], first["tax_rate"], first["tax_amount"]) == (
        "TDS_PAYABLE", "194C", "Cloudnine Hosting", "TDS Payable - Contractors", "1250000.00", "2.00", "25000.00")
    assert {r["section_code"] for r in t["rows"]} == {"194C", "194J", "194I", "194A", "206C"} and all(r["status"] == "draft" and r["created_by_name"].startswith("Maya") for r in t["rows"])

    r = upload(client, eid, FD_XLSX, "fixed-deposits")
    assert r.status_code == 200 and r.json()["imported"] == 5, r.text
    f = client.get(f"/api/engagements/{eid}/fixed-deposits").json()
    assert len(f["rows"]) == 5 and f["period"]["end"] == "2027-03-31"
    for row, (bank, no, principal, rate, start, maturity, cls, line) in zip(f["rows"], FD_EXPECT):
        assert (row["bank_name"], row["fd_number"], row["principal_amount"], D(row["interest_rate"]), row["start_date"], row["maturity_date"],
                row["maturity_classification"], row["schedule_line_code"]) == (bank, no, principal, D(rate), start.isoformat(), maturity.isoformat(), cls, line)
        assert D(row["accrued_interest"]) == expected_interest(principal, rate, start, maturity)      # exact paise, independently calculated
    s = f["summary"]
    assert D(s["total_principal"]) == D("24500000.00") and D(s["current_principal"]) == D("15500000.00") and D(s["non_current_principal"]) == D("9000000.00")
    assert D(s["total_accrued_interest"]) == sum(expected_interest(p, r_, a, b) for _, _, p, r_, a, b, _, _ in FD_EXPECT)
    assert D(f["rows"][1]["accrued_interest"]) == D("560000.00")                                         # a full year: 8,000,000 x 7%


def test_imports_are_all_or_nothing_and_reject_duplicates_and_bad_rows(client, eid, reopened):
    again = upload(client, eid, TDS_XLSX, "tds-tcs")                                                     # the same file twice
    assert again.status_code == 422 and len(again.json()["detail"]["errors"]) == 8
    assert len(client.get(f"/api/engagements/{eid}/tds-tcs").json()["rows"]) == 8
    assert upload(client, eid, FD_XLSX, "fixed-deposits").status_code == 422

    head = ["Entry Type", "Section Code", "Party Name", "Base Amount", "Tax Rate (%)", "Tax Amount"]
    bad = xlsx_bytes([head, ["TDS_PAYABLE", "194C", "Good Party", 100000, 2, 2000],                      # row 2 is fine
                      ["TDS_PAYABLE", "19", "Bad Section", 100000, 2, 2000],                              # row 3
                      ["TDS_PAYABLE", "194C", "Wrong Tax", 100000, 2, 2500],                              # row 4: tax does not match 2%
                      ["TDS_BOGUS", "194C", "Bad Type", 100000, 2, None],                                 # row 5
                      ["TDS_PAYABLE", "194C", "Fractions", 100000.005, 2, None]])                         # row 6: sub-paisa
    r = upload(client, eid, None, "tds-tcs", content=bad)
    assert r.status_code == 422
    assert [e["row"] for e in r.json()["detail"]["errors"]] == [3, 4, 5, 6]
    assert len(client.get(f"/api/engagements/{eid}/tds-tcs").json()["rows"]) == 8                       # row 2 was NOT saved either

    fd_head = ["Bank Name", "FD Number", "Principal Amount", "Interest Rate (%)", "Start Date", "Maturity Date", "Maturity Classification", "Accrued Interest"]
    badfd = xlsx_bytes([fd_head, ["Axis Bank", "A1", 100000, 7, date(2026, 5, 1), date(2027, 5, 1), "non_current", None],      # row 2: matures within 12 months => current
                        ["Axis Bank", "A2", 100000, 7, date(2026, 5, 1), date(2029, 5, 1), "non_current", 5.00],                   # row 3: stated interest is wrong
                        ["Axis Bank", "A3", 100000, 7, date(2027, 5, 1), date(2026, 5, 1), None, None]])                          # row 4: maturity before start
    r = upload(client, eid, None, "fixed-deposits", content=badfd)
    assert r.status_code == 422 and [e["row"] for e in r.json()["detail"]["errors"]] == [2, 3, 4]
    assert len(client.get(f"/api/engagements/{eid}/fixed-deposits").json()["rows"]) == 5
    assert upload(client, eid, None, "tds-tcs", content=b"not an excel file").status_code == 422
    assert client.post(f"/api/engagements/{eid}/tds-tcs/import", files={"file": ("x.txt", b"a,b", "text/plain")}, headers=MAKER).status_code == 422


def test_csv_import_and_single_row_forms_in_exact_paise(client, eid, reopened):
    buf = io.StringIO()
    csv.writer(buf).writerows([["entry type", "section", "party", "base", "rate", "tax"], ["TCS receivable", "206c", "Pinnacle Office Supplies", "340,000.00", "1", ""]])
    r = upload(client, eid, "x.csv", "tds-tcs", content=buf.getvalue().encode(), ctype="text/csv")
    assert r.status_code == 200 and r.json()["imported"] == 1, r.text
    row = client.get(f"/api/engagements/{eid}/tds-tcs").json()["rows"][-1]
    assert (row["entry_type"], row["section_code"], row["tax_amount"]) == ("TCS_RECEIVABLE", "206C", "3400.00")       # tax calculated, section upper-cased

    url = f"/api/engagements/{eid}/tds-tcs"
    ok = client.post(url, json={"entry_type": "TDS_PAYABLE", "section_code": "194C", "party_name": "Pinnacle Office Supplies", "base_amount": "333333.33", "tax_rate": "2"}, headers=MAKER)
    assert ok.status_code == 200 and ok.json()["tax_amount"] == "6666.67"                                         # 6,666.6666 half-up
    assert client.post(url, json={"entry_type": "TDS_PAYABLE", "section_code": "194C", "party_name": "Pinnacle Office Supplies", "base_amount": "333333.33", "tax_rate": "2"}, headers=MAKER).status_code == 409
    for bad in ({"entry_type": "X"}, {"entry_type": "TDS_PAYABLE", "section_code": "194C", "party_name": "P", "base_amount": "10.005", "tax_rate": "2"},
                {"entry_type": "TDS_PAYABLE", "section_code": "194C", "party_name": "P", "base_amount": "100", "tax_rate": "0"},
                {"entry_type": "TDS_PAYABLE", "section_code": "194C", "party_name": "P", "base_amount": "100", "tax_rate": "2", "tax_amount": "5"},
                {"entry_type": "TDS_PAYABLE", "section_code": "194C", "party_name": " ", "base_amount": "100", "tax_rate": "2"}):
        assert client.post(url, json=bad, headers=MAKER).status_code == 400, bad
    assert client.post(url, json={"entry_type": "TDS_PAYABLE"}).status_code == 401

    furl = f"/api/engagements/{eid}/fixed-deposits"
    base = {"bank_name": "Axis Bank", "fd_number": "AX-1", "principal_amount": "1000000", "interest_rate": "7.5", "start_date": "2026-04-01", "maturity_date": "2027-04-01"}
    fd = client.post(furl, json=base, headers=MAKER)
    assert fd.status_code == 200, fd.text
    assert fd.json()["accrued_interest"] == "75000.00" and fd.json()["maturity_classification"] == "current" and fd.json()["schedule_line_code"] == "BS.CA.CASH"   # 364 days to 31-Mar... full FY
    assert client.post(furl, json=base, headers=MAKER).status_code == 409
    assert client.post(furl, json={**base, "fd_number": "AX-2", "maturity_classification": "non_current"}, headers=MAKER).status_code == 400
    assert client.post(furl, json={**base, "fd_number": "AX-3", "accrued_interest": "1.00"}, headers=MAKER).status_code == 400
    assert client.post(furl, json={**base, "fd_number": "AX-4", "schedule_line_code": "BS.NCA.OTH"}, headers=MAKER).status_code == 400
    assert client.post(furl, json={**base, "fd_number": "AX-5", "interest_rate": "0.0001"}, headers=MAKER).status_code == 400

    # drafts can be deleted; the two scratch rows above are removed so the totals in the next tests stay simple
    for path, rows in ((url, client.get(url).json()["rows"]), (furl, client.get(furl).json()["rows"])):
        for r in rows:
            if r["party_name" if "tds" in path else "fd_number"] in ("Pinnacle Office Supplies", "AX-1"):
                assert client.delete(f"{path}/{r['id']}", headers=MAKER).status_code == 200
    assert len(client.get(url).json()["rows"]) == 8 and len(client.get(furl).json()["rows"]) == 5
    assert client.delete(f"{url}/999999", headers=MAKER).status_code == 404


def test_maker_cannot_post_and_the_poster_cannot_be_the_enterer(client, eid, reopened):
    for name in ("tds-tcs", "fixed-deposits"):
        assert client.post(f"/api/engagements/{eid}/{name}/post", headers=MAKER).status_code == 403
        assert client.post(f"/api/engagements/{eid}/{name}/post").status_code == 401
    own = client.post(f"/api/engagements/{eid}/tds-tcs", headers=CHECKER, json={"entry_type": "TDS_PAYABLE", "section_code": "194H", "party_name": "Zenith Consulting",
                                                                                  "base_amount": "100000", "tax_rate": "5"})
    assert own.status_code == 200
    r = client.post(f"/api/engagements/{eid}/tds-tcs/post", json={"ids": [own.json()["id"]]}, headers=CHECKER)       # the Checker entered it: someone else must post
    assert r.status_code == 403
    assert client.delete(f"/api/engagements/{eid}/tds-tcs/{own.json()['id']}", headers=CHECKER).status_code == 200
    assert client.get(f"/api/engagements/{eid}/adjustments").json()["counts"]["approved"] >= 1                         # nothing new was posted
    assert all(r["status"] == "draft" for r in client.get(f"/api/engagements/{eid}/tds-tcs").json()["rows"])


def test_checker_posts_tds_tcs_to_the_adjusted_tb_and_the_balance_sheet_stays_balanced(client, eid, reopened, test_db_url):
    before, check0 = lines(client, eid)
    assert check0["status"] == "BALANCED" and D(check0["difference"]) == 0
    approved0 = client.get(f"/api/engagements/{eid}/adjustments").json()["counts"]["approved"]
    tb0 = {n: adj_net(client, eid, n) for n in ("TDS Payable - Contractors", "TDS Payable - Professionals", "TDS Receivable", "Cloudnine Hosting", "Acme Retail Pvt Ltd")}
    assert "TDS Payable" not in {r["ledger_name"] for r in client.get(f"/api/engagements/{eid}/adjusted-tb").json()["rows"]}

    r = client.post(f"/api/engagements/{eid}/tds-tcs/post", headers=CHECKER)
    assert r.status_code == 200, r.text
    assert r.json()["posted"] == 8 and r.json()["skipped_own"] == 0 and D(r.json()["tax_total"]) == D("651200.00")

    after, check1 = lines(client, eid)
    assert check1["status"] == "BALANCED" and D(check1["difference"]) == D("0.00")                                   # still balanced to the paisa
    delta = lambda code: after[code] - before[code]
    assert delta("BS.CL.OTH") == D("351200.00")                                                                      # payables: TDS 345,000 + TCS 6,200
    assert delta("BS.CA.OTH") == D("300000.00")                                                                      # receivables: 240,000 + 20,000 + 40,000
    assert delta("BS.CL.TP") == D("-345000.00") and delta("BS.CA.TR") == D("-253800.00") and delta("BS.CA.STLA") == D("-40000.00")
    assert delta("BS.ASSETS.TOTAL") == D("6200.00") == delta("BS.EQL.TOTAL")                                         # only the TCS collected from a customer adds to both sides

    tb1 = {n: adj_net(client, eid, n) for n in tb0}
    assert tb1["TDS Payable - Contractors"] - tb0["TDS Payable - Contractors"] == D("-25000.00")                     # credit to the existing ledger
    assert tb1["TDS Payable - Professionals"] - tb0["TDS Payable - Professionals"] == D("-140000.00")
    assert tb1["TDS Receivable"] - tb0["TDS Receivable"] == D("300000.00")
    assert tb1["Cloudnine Hosting"] - tb0["Cloudnine Hosting"] == D("25000.00") and tb1["Acme Retail Pvt Ltd"] - tb0["Acme Retail Pvt Ltd"] == D("-240000.00")
    assert adj_net(client, eid, "TDS Payable") == D("-180000.00") and adj_net(client, eid, "TCS Payable") == D("-6200.00")          # new default ledgers, mapped at once

    adj = client.get(f"/api/engagements/{eid}/adjustments").json()
    assert adj["counts"]["approved"] == approved0 + 8
    pack = [e for e in adj["entries"] if e["description"].startswith("TDS/TCS pack")]
    assert len(pack) == 8 and all(e["status"] == "approved" and e["approved_by_name"].startswith("Chetan") and e["created_by_name"].startswith("Maya") for e in pack)
    assert all(sum(D(l["debit"]) for l in e["lines"]) == sum(D(l["credit"]) for l in e["lines"]) for e in pack)

    t = client.get(f"/api/engagements/{eid}/tds-tcs").json()
    assert t["summary"]["posted_rows"] == 8 and t["summary"]["draft_rows"] == 0 and all(r["status"] == "posted" and r["journal_number"] for r in t["rows"])
    assert client.post(f"/api/engagements/{eid}/tds-tcs/post", headers=CHECKER).status_code == 409                   # nothing left to post
    assert client.delete(f"/api/engagements/{eid}/tds-tcs/{t['rows'][0]['id']}", headers=MAKER).status_code == 409   # posted rows are permanent
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.user_id','3',false)")
        with pytest.raises(psycopg.errors.RaiseException, match="permanent"):
            conn.execute("UPDATE tds_tcs_entries SET party_name = 'x'")
    assert "TDS_TCS_PACK_POSTED" in {a["event_name"] for a in client.get(f"/api/engagements/{eid}/audit-logs?scope=events&limit=500", headers=MAKER).json()}
    checks = client.get(f"/api/engagements/{eid}/checks").json()["summary"]
    assert checks["passed"] == checks["total"]                                                                        # every check still passes


def test_partner_posts_the_fd_register_and_the_statements_follow(client, eid, reopened):
    before, _ = lines(client, eid)
    expected = [expected_interest(p, r, a, b) for _, _, p, r, a, b, _, _ in FD_EXPECT]
    r = client.post(f"/api/engagements/{eid}/fixed-deposits/post", headers=PARTNER)
    assert r.status_code == 200, r.text
    assert r.json()["posted"] == 5 and D(r.json()["principal_total"]) == D("24500000.00") and D(r.json()["accrued_interest_total"]) == sum(expected)

    after, check = lines(client, eid)
    assert check["status"] == "BALANCED" and D(check["difference"]) == D("0.00")
    delta = lambda code: after[code] - before[code]
    accrued = sum(expected)
    assert delta("BS.CA.CASH") == D("13000000.00") - D("24500000.00")                                                 # current FDs stay here; every principal leaves the bank ledgers
    assert delta("BS.NCA.OTH") == D("9000000.00")                                                                     # non-current FDs: ICICI 3,000,000 + SBI 6,000,000
    assert delta("BS.CA.OTH") == D("2500000.00") + accrued                                                            # the FD on 'other current assets' + accrued interest receivable
    assert delta("PL.OI") == accrued and delta("PL.PAT") == accrued                                                   # interest is income in the P&L and lifts profit
    assert delta("BS.ASSETS.TOTAL") == accrued == delta("BS.EQL.TOTAL")                                               # profit flows to reserves, so the sheet balances

    f = client.get(f"/api/engagements/{eid}/fixed-deposits").json()
    assert f["summary"]["posted_rows"] == 5 and all(x["status"] == "posted" and x["journal_number"] for x in f["rows"])
    assert adj_net(client, eid, "Fixed Deposits - Non-current") == D("9000000.00") and adj_net(client, eid, "Fixed Deposits - Current") == D("13000000.00")
    assert adj_net(client, eid, "SBI Fixed Deposit") == D("-14000000.00")                                             # SBI's two FDs left that ledger
    assert adj_net(client, eid, "HDFC Bank Current A/c") == D("-7500000.00") and adj_net(client, eid, "ICICI Bank Current A/c") == D("-3000000.00")
    fd_entries = [e for e in client.get(f"/api/engagements/{eid}/adjustments").json()["entries"] if e["description"].startswith("FD register")]
    assert len(fd_entries) == 5 and all(e["status"] == "approved" and e["approved_by_name"] == "Priya Partner" for e in fd_entries)
    assert "FD_REGISTER_POSTED" in {a["event_name"] for a in client.get(f"/api/engagements/{eid}/audit-logs?scope=events&limit=500", headers=MAKER).json()}
    assert client.post(f"/api/engagements/{eid}/fixed-deposits/post", headers=PARTNER).status_code == 409


def test_both_registers_are_frozen_once_the_engagement_is_signed_off(client, eid, reopened, test_db_url):
    top = D("999999999999.00")                                                                                        # nothing is 'material' for this test, so no variance issue blocks sign-off
    assert client.post(f"/api/engagements/{eid}/materiality", json={"input_mode": "manual", "overall_materiality": str(top)}, headers=CHECKER).status_code == 200
    queued = client.post(f"/api/engagements/{eid}/tds-tcs", headers=MAKER, json={"entry_type": "TDS_PAYABLE", "section_code": "194H", "party_name": "Orbit Recruitment",
                                                                                    "base_amount": "50000", "tax_rate": "5"})
    assert queued.status_code == 200                                                                                  # a draft left in the register when the Partner signs off
    queued_fd = client.post(f"/api/engagements/{eid}/fixed-deposits", headers=MAKER, json={"bank_name": "HDFC Bank", "fd_number": "LATE-1", "principal_amount": "100000",
                                                                                            "interest_rate": "7", "start_date": "2026-05-01", "maturity_date": "2027-05-01"})
    assert queued_fd.status_code == 200
    assert client.post(f"/api/engagements/{eid}/checks/run", headers=CHECKER).json()["summary"]["text"] == "6 of 6 active checks passing"
    done = client.post(f"/api/engagements/{eid}/signoff", json={"note": "With TDS and FD"}, headers=PARTNER)
    assert done.status_code == 200, done.text

    for name in ("tds-tcs", "fixed-deposits"):
        r = upload(client, eid, TDS_XLSX if name == "tds-tcs" else FD_XLSX, name)
        assert r.status_code == 409 and r.json()["detail"] == LOCK_MSG, name
        for who in (CHECKER, PARTNER):
            p = client.post(f"/api/engagements/{eid}/{name}/post", headers=who)
            assert p.status_code == 409 and p.json()["detail"] == LOCK_MSG, name
    for resp in (client.post(f"/api/engagements/{eid}/tds-tcs", headers=MAKER, json={"entry_type": "TDS_PAYABLE", "section_code": "194C", "party_name": "X", "base_amount": "100", "tax_rate": "2"}),
                 client.post(f"/api/engagements/{eid}/fixed-deposits", headers=MAKER, json={"bank_name": "B", "fd_number": "Z", "principal_amount": "1", "interest_rate": "1",
                                                                                              "start_date": "2026-05-01", "maturity_date": "2027-05-01"}),
                 client.delete(f"/api/engagements/{eid}/tds-tcs/{queued.json()['id']}", headers=MAKER),
                 client.delete(f"/api/engagements/{eid}/fixed-deposits/{queued_fd.json()['id']}", headers=MAKER)):
        assert resp.status_code == 409 and resp.json()["detail"] == LOCK_MSG
    assert client.get(f"/api/engagements/{eid}/tds-tcs").status_code == 200 and client.get(f"/api/engagements/{eid}/fixed-deposits").status_code == 200   # reading still works

    with psycopg.connect(test_db_url, autocommit=True) as conn:                                                        # the database says the same
        conn.execute("SELECT set_config('app.user_id','2',false)")
        with pytest.raises(psycopg.errors.RaiseException, match="locked"):
            conn.execute("DELETE FROM tds_tcs_entries WHERE id = %s", (queued.json()["id"],))
        with pytest.raises(psycopg.errors.RaiseException, match="locked"):
            conn.execute("INSERT INTO fixed_deposit_register (engagement_id, bank_name, fd_number, principal_amount, interest_rate, start_date, maturity_date, "
                         "maturity_classification, schedule_line_code, created_by) VALUES (%s,'B','Q',1,1,'2026-05-01','2027-05-01','current','BS.CA.CASH',2)", (eid,))
    for page in ("/tds-tcs", "/fixed-deposits"):
        assert client.get(page).status_code == 200 and f'href="{page}"' in client.get(page).text
