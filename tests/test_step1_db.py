"""Step 1 acceptance tests (T-01, T-02, T-03) against a real PostgreSQL. Run: python -m pytest tests -q"""
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent
PILOT = (ROOT / "sample_data" / "9and9_pilot_tb_76_ledgers.csv").read_bytes()
MAKER, CHECKER = {"X-User-Id": "2"}, {"X-User-Id": "3"}

GROUP_TO_LINE = {
    "Capital Account": "BS.EQ.SC", "Reserves & Surplus": "BS.EQ.RS", "Secured Loans": "BS.NCL.BORR",
    "Unsecured Loans": "BS.CL.BORR", "Sundry Creditors": "BS.CL.TP", "Duties & Taxes": "BS.CL.OTH",
    "Provisions": "BS.CL.PROV", "Current Liabilities": "BS.CL.OTH", "Deferred Tax": "BS.NCL.DTL",
    "Fixed Assets": "BS.NCA.PPE", "Investments": "BS.NCA.INV", "Deposits (Asset)": "BS.NCA.OTH",
    "Loans & Advances (Asset)": "BS.CA.STLA", "Current Assets": "BS.CA.OTH", "Sundry Debtors": "BS.CA.TR",
    "Bank Accounts": "BS.CA.CASH", "Cash-in-Hand": "BS.CA.CASH", "Sales Accounts": "PL.REV",
    "Indirect Incomes": "PL.OI", "Direct Expenses": "PL.COGS", "Indirect Expenses": "PL.OTHEXP",
}


def upload(client, eid, data=PILOT, headers=MAKER, name="tb.csv"):
    return client.post(f"/api/engagements/{eid}/tb/import", files={"file": (name, data, "text/csv")}, headers=headers)


@pytest.fixture(scope="module")
def eid(client):
    return client.get("/api/engagements").json()[0]["id"]


def test_permissions(client, eid):
    assert client.post(f"/api/engagements/{eid}/tb/import", files={"file": ("a.csv", b"x", "text/csv")}).status_code == 401
    assert upload(client, eid, headers=CHECKER).status_code == 403


def test_step1_end_to_end(client, eid):
    # T-01 first import
    r = upload(client, eid)
    assert r.status_code == 200, r.text
    s = r.json()
    assert (s["rows"], s["inserted"], s["unchanged"]) == (76, 76, 0)
    # T-03 every unmapped ledger has exactly one open exception
    assert s["exceptions"]["unmapped_ledgers"] == 76 and s["exceptions"]["open_mapping_exceptions"] == 76
    tb = client.get(f"/api/engagements/{eid}/tb").json()
    assert tb["summary"]["ledger_count"] == 76
    assert tb["summary"]["net_assets_by_tally_group"] == "272641256.00"
    assert tb["summary"]["net_result_by_tally_group"] == "-481634.00"

    # T-01 idempotent re-import: no duplicates, nothing changes, still logged
    r2 = upload(client, eid).json()
    assert (r2["inserted"], r2["updated"], r2["unchanged"], r2["changed"]) == (0, 0, 76, False)
    tb = client.get(f"/api/engagements/{eid}/tb").json()
    assert len(tb["ledgers"]) == 76 and len(tb["uploads"]) == 2
    ex = client.get(f"/api/engagements/{eid}/exceptions").json()
    assert ex["summary"]["total_rows"] == 76 and len(ex["items"]) == 76

    # T-02 / T-03 map one ledger: exception clears; unmap: the SAME exception row re-opens
    m = client.get(f"/api/engagements/{eid}/mappings").json()
    lines = {l["code"]: l for l in m["lines"]}
    first = m["ledgers"][0]
    assert client.post(f"/api/engagements/{eid}/mappings/{first['ledger_id']}", json={"line_id": lines["BS.NCA.PPE"]["id"]},
                       headers=CHECKER).status_code == 403
    res = client.post(f"/api/engagements/{eid}/mappings/{first['ledger_id']}", json={"line_id": lines["BS.NCA.PPE"]["id"]}, headers=MAKER).json()
    assert res["counts"]["unmapped_ledgers"] == 75 and res["counts"]["open_mapping_exceptions"] == 75 and res["counts"]["matches"]
    assert res["note_id"] is not None                                  # note auto-selected (one note per line)
    assert client.post(f"/api/engagements/{eid}/mappings/{first['ledger_id']}", json={"line_id": lines["BS.NCA.PPE"]["id"]},
                       headers=MAKER).json()["changed"] is False       # same mapping again: no-op
    res = client.delete(f"/api/engagements/{eid}/mappings/{first['ledger_id']}", headers=MAKER).json()
    assert res["counts"]["unmapped_ledgers"] == 76 and res["counts"]["total_rows"] == 76 and res["counts"]["matches"]

    # a total line cannot be a mapping target
    bad = client.post(f"/api/engagements/{eid}/mappings/{first['ledger_id']}", json={"line_id": 999999}, headers=MAKER)
    assert bad.status_code == 422

    # map everything
    for l in m["ledgers"]:
        code = GROUP_TO_LINE[l["tally_group"]]
        assert client.post(f"/api/engagements/{eid}/mappings/{l['ledger_id']}", json={"line_id": lines[code]["id"]}, headers=MAKER).status_code == 200
    c = client.get(f"/api/engagements/{eid}/exceptions").json()["summary"]
    assert c["unmapped_ledgers"] == 0 and c["open_mapping_exceptions"] == 0 and c["matches"]

    # mappings survive a re-import that drops one ledger and changes one balance
    # (drop Cash in Hand, shift its 85,000 into HDFC so the file still foots)
    text = PILOT.decode("utf-8-sig")
    hdfc_old = "HDFC Bank Current A/c,Bank Accounts,51706485.00,Dr,0.00,30361485.00,21345000.00,Dr"
    hdfc_new = "HDFC Bank Current A/c,Bank Accounts,51706485.00,Dr,0.00,30276485.00,21430000.00,Dr"
    assert hdfc_old in text
    smaller = "\n".join(ln for ln in text.replace(hdfc_old, hdfc_new).splitlines() if not ln.startswith("Cash in Hand,")).encode()
    r3 = upload(client, eid, smaller)
    assert r3.status_code == 200, r3.text
    r3 = r3.json()
    assert (r3["updated"], r3["deactivated"], r3["unchanged"]) == (1, 1, 74) and r3["deactivated_ledgers"] == ["Cash in Hand"]
    assert r3["exceptions"]["unmapped_ledgers"] == 0                    # saved mappings untouched
    tb = client.get(f"/api/engagements/{eid}/tb").json()
    assert tb["summary"]["ledger_count"] == 75 and len(tb["ledgers"]) == 76   # inactive ledger kept, not deleted
    # the ledger returns: it is reactivated with its ORIGINAL mapping (no new exception)
    r4 = upload(client, eid).json()
    assert (r4["updated"], r4["inserted"]) == (2, 0)
    assert r4["exceptions"]["unmapped_ledgers"] == 0 and r4["exceptions"]["open_mapping_exceptions"] == 0
    assert len(client.get(f"/api/engagements/{eid}/tb").json()["ledgers"]) == 76


def test_failed_import_is_logged_and_changes_nothing(client, eid):
    before = client.get(f"/api/engagements/{eid}/tb").json()
    broken = PILOT.replace(b"Cash in Hand,Cash-in-Hand,68000.00", b"Cash in Hand,Cash-in-Hand,68001.00")
    assert broken != PILOT
    r = upload(client, eid, broken)
    assert r.status_code == 422
    assert r.json()["detail"]["errors"]
    after = client.get(f"/api/engagements/{eid}/tb").json()
    assert after["ledgers"] == before["ledgers"]
    assert after["uploads"][0]["status"] == "failed" and len(after["uploads"]) == len(before["uploads"]) + 1


def test_audit_trail_is_append_only(client, eid, test_db_url):
    logs = client.get(f"/api/engagements/{eid}/audit-logs", headers=MAKER).json()
    assert any(a["event_name"] == "MAPPING_CHANGED" for a in logs)
    assert any(a["event_name"] == "TB_IMPORTED" for a in logs)
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("UPDATE audit_logs SET reason = 'x'")
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM audit_logs")
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("TRUNCATE audit_logs")
        with pytest.raises(psycopg.errors.RaiseException):               # un-attributed writes are refused
            conn.execute("UPDATE engagements SET status = 'fieldwork'")


def test_duplicate_exception_is_impossible(test_db_url):
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.user_id', '1', false)")
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("""INSERT INTO exceptions (engagement_id, source, reference_id, cause_code, title, fingerprint)
                            SELECT engagement_id, source, reference_id, cause_code, title, fingerprint FROM exceptions LIMIT 1""")
