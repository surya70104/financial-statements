"""Step 9 acceptance tests: version-locked report sign-off (T-17) and the UDIN dashboard (T-18).
Starts from the pilot engagement as Steps 6 and 8 left it (signed off = v1, locked) and ends with it signed off again (v2).
This module must therefore run after those, and it does (alphabetical order)."""
from datetime import date, timedelta
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import dict_row

D = Decimal
MAKER, CHECKER, PARTNER = {"X-User-Id": "2"}, {"X-User-Id": "3"}, {"X-User-Id": "4"}
LOCK_MSG = "Engagement is locked after Partner sign-off"
GOOD_UDIN, OTHER_UDIN = "26123456ABCDEFGH01", "26123456ABCDEFGH02"
TODAY = date.today().isoformat()


@pytest.fixture(scope="module")
def eid(client):
    return client.get("/api/engagements").json()[0]["id"]


def state(client, eid):
    return client.get(f"/api/engagements/{eid}/signoff").json()


def udin(client, eid):
    return client.get(f"/api/engagements/{eid}/udin").json()


def version_row(test_db_url, eid, n):
    with psycopg.connect(test_db_url, autocommit=True, row_factory=dict_row) as conn:
        return conn.execute("SELECT * FROM report_signoff_versions WHERE engagement_id = %s AND version_number = %s", (eid, n)).fetchone()


def test_signing_off_created_v1_locked_with_a_pending_udin(client, eid, test_db_url):
    s = state(client, eid)
    assert s["is_locked"] is True and s["can_sign_off"] is False
    assert [v["version_number"] for v in s["versions"]] == [1] and s["next_version_number"] == 2
    v1 = s["active_version"]
    assert v1["version_number"] == 1 and v1["status"] == "signed_locked" and v1["signed_off_by_name"] == "Priya Partner"
    assert D(v1["balance_sheet"]["total_assets"]) == D("272641256.00") and D(v1["profit_and_loss"]["profit_for_period"]) == D("-481634.00")
    assert s["signoff"]["version_number"] == 1 and s["signoff"]["version_id"] == v1["id"]
    assert (v1["udin_status"], v1["udin_number"]) == ("pending_udin", None)

    row = version_row(test_db_url, eid, 1)                              # the frozen snapshot carries the whole audit pack
    assert set(row["snapshot_json"]["pack"]) == {"header", "financials", "adjustments", "checks_and_issues", "working_papers"}
    assert D(row["snapshot_json"]["pack"]["financials"]["balance_sheet"]["total_assets"]["current"]) == D("272641256.00")

    u = udin(client, eid)
    assert u["summary"] == {"total_versions": 1, "pending_count": 1, "recorded_count": 0, "revoked_count": 0}
    assert u["items"][0]["version_number"] == 1 and u["items"][0]["status"] == "pending_udin" and u["items"][0]["version_status"] == "signed_locked"
    events = {a["event_name"] for a in client.get(f"/api/engagements/{eid}/audit-logs?scope=events&limit=500", headers=MAKER).json()}
    assert {"ENGAGEMENT_SIGNED_OFF", "REPORT_VERSION_SIGNED_OFF"} <= events


def test_only_a_partner_can_reopen_or_manage_udins(client, eid):
    v1 = state(client, eid)["active_version"]["id"]
    reopen, rec, rev = f"/api/engagements/{eid}/signoff/reopen", f"/api/engagements/{eid}/udin/{v1}", f"/api/engagements/{eid}/udin/{v1}/revoke"
    body = {"udin_number": GOOD_UDIN, "udin_generated_date": TODAY}
    for who in (MAKER, CHECKER):
        assert client.post(reopen, json={"reopen_reason": "fix"}, headers=who).status_code == 403
        assert client.post(rec, json=body, headers=who).status_code == 403
        assert client.post(rev, json={"revoked_reason": "x"}, headers=who).status_code == 403
    for path, b in ((reopen, {"reopen_reason": "x"}), (rec, body), (rev, {"revoked_reason": "x"})):
        assert client.post(path, json=b).status_code == 401
    s = state(client, eid)
    assert s["is_locked"] is True and s["active_version"]["version_number"] == 1                 # nothing changed
    assert udin(client, eid)["summary"]["pending_count"] == 1


def test_reopen_needs_a_reason_and_the_database_protects_the_signed_version(client, eid, test_db_url):
    url = f"/api/engagements/{eid}/signoff/reopen"
    assert client.post(url, headers=PARTNER).status_code == 400
    assert client.post(url, json={"reopen_reason": "   "}, headers=PARTNER).status_code == 400
    assert client.post("/api/engagements/999999/signoff/reopen", json={"reopen_reason": "x"}, headers=PARTNER).status_code == 404
    assert state(client, eid)["is_locked"] is True

    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.user_id','4',false)")
        for sql in ("UPDATE report_signoff_versions SET snapshot_json = '{}'", "UPDATE report_signoff_versions SET signoff_note = 'tampered'",
                    "UPDATE report_signoff_versions SET status = 'superseded'",               # no reopen fields: the table check refuses it
                    "DELETE FROM report_signoff_versions", "DELETE FROM udin_records",
                    "UPDATE udin_records SET udin_number = 'lowercase00000000x'", "UPDATE udin_records SET status = 'revoked'",
                    "UPDATE engagement_signoffs SET is_locked = FALSE", "DELETE FROM engagement_signoffs"):
            with pytest.raises(psycopg.Error):
                conn.execute(sql)


def test_partner_reopens_v1_edits_then_signs_v2_and_v1_stays_intact(client, eid, test_db_url):
    before = version_row(test_db_url, eid, 1)
    r = client.post(f"/api/engagements/{eid}/signoff/reopen", json={"reopen_reason": "Late audit adjustment on a debtor"}, headers=PARTNER)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["is_locked"] is False and s["active_version"] is None and s["next_version_number"] == 2
    v1 = s["versions"][0]
    assert v1["status"] == "superseded" and v1["reopened_by_name"] == "Priya Partner" and v1["reopen_reason"] == "Late audit adjustment on a debtor" and v1["reopened_at"]
    assert client.get(f"/api/engagements/{eid}").json()["is_locked"] is False
    assert client.post(f"/api/engagements/{eid}/signoff/reopen", json={"reopen_reason": "again"}, headers=PARTNER).status_code == 409   # not locked now

    # edits are allowed again: post and approve an adjustment
    m = client.get(f"/api/engagements/{eid}/mappings").json()["ledgers"]
    a, b = m[0]["ledger_id"], m[1]["ledger_id"]
    made = client.post(f"/api/engagements/{eid}/adjustments", headers=MAKER, json={"entry_date": "2026-11-19", "description": "v2 revision entry",
                       "lines": [{"ledger_id": a, "debit": "1.00"}, {"ledger_id": b, "credit": "1.00"}]})
    assert made.status_code in (200, 201), made.text
    entry = made.json()
    assert client.post(f"/api/engagements/{eid}/adjustments/{entry.get('id') or entry['entry_number']}/approve", headers=CHECKER).status_code == 200

    # the checks must pass for v2: give CHK-06 the Step 6 stand-in rule (Q1 is still open in the product)
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("""UPDATE check_definitions SET rule_type='mapping_complete', rule_config='{}', definition_status='confirmed' WHERE code='CHK-06'""")
    assert client.post(f"/api/engagements/{eid}/checks/run", headers=CHECKER).json()["summary"]["text"] == "6 of 6 active checks passing"

    assert client.post(f"/api/engagements/{eid}/signoff", headers=MAKER).status_code == 403
    done = client.post(f"/api/engagements/{eid}/signoff", json={"note": "Revised and final"}, headers=PARTNER)
    assert done.status_code == 200, done.text
    assert done.json()["version_number"] == 2 and done.json()["is_locked"] is True
    assert client.post(f"/api/engagements/{eid}/signoff", headers=PARTNER).status_code == 409           # already locked again

    s = state(client, eid)
    assert [(v["version_number"], v["status"]) for v in s["versions"]] == [(2, "signed_locked"), (1, "superseded")]
    assert s["active_version"]["version_number"] == 2 and s["active_version"]["signoff_note"] == "Revised and final" and s["next_version_number"] == 3
    assert s["is_locked"] is True and s["signoff"]["version_number"] == 2

    after = version_row(test_db_url, eid, 1)                                                              # v1 is exactly as it was signed
    for col in ("snapshot_json", "signed_off_by", "signed_off_at", "signoff_note", "version_number"):
        assert after[col] == before[col], col
    v2 = version_row(test_db_url, eid, 2)
    assert v2["snapshot_json"]["approved_adjustments"] == before["snapshot_json"]["approved_adjustments"] + 1
    assert v2["snapshot_json"]["data_revision"] > before["snapshot_json"]["data_revision"]
    assert v2["snapshot_json"]["pack"]["adjustments"]["counts"]["approved"] == before["snapshot_json"]["pack"]["adjustments"]["counts"]["approved"] + 1

    # locked again: edits answer 409
    r = client.post(f"/api/engagements/{eid}/adjustments", headers=MAKER, json={"entry_date": "2026-11-19", "description": "late", "lines": [
        {"ledger_id": a, "debit": "1.00"}, {"ledger_id": b, "credit": "1.00"}]})
    assert r.status_code == 409 and r.json()["detail"] == LOCK_MSG

    # each version has its own UDIN slot
    u = udin(client, eid)
    assert u["summary"] == {"total_versions": 2, "pending_count": 2, "recorded_count": 0, "revoked_count": 0}
    events = {a["event_name"] for a in client.get(f"/api/engagements/{eid}/audit-logs?scope=events&limit=500", headers=MAKER).json()}
    assert {"REPORT_VERSION_REOPENED", "REPORT_VERSION_SIGNED_OFF"} <= events


def test_udin_validation_recording_revoking_and_counts(client, eid):
    ids = {v["version_number"]: v["id"] for v in state(client, eid)["versions"]}
    v1, v2 = ids[1], ids[2]
    url = lambda v, tail="": f"/api/engagements/{eid}/udin/{v}{tail}"

    for bad in ("", "SHORT123", "26123456ABCDEFGH0", "26123456ABCDEFGH012", "26123456abcdefgh01", "26123456ABCDEFGH-1", "26123456ABCDEFGH 1"):
        r = client.post(url(v1), json={"udin_number": bad, "udin_generated_date": TODAY}, headers=PARTNER)
        assert r.status_code == 400, bad
    assert client.post(url(v1), json={"udin_generated_date": TODAY}, headers=PARTNER).status_code == 400
    for bad_date in (None, "", "19-11-2026", "2026-13-40", (date.today() + timedelta(days=2)).isoformat()):
        assert client.post(url(v1), json={"udin_number": GOOD_UDIN, "udin_generated_date": bad_date}, headers=PARTNER).status_code == 400, bad_date
    assert client.post(url(999999), json={"udin_number": GOOD_UDIN, "udin_generated_date": TODAY}, headers=PARTNER).status_code == 404
    assert udin(client, eid)["summary"]["recorded_count"] == 0

    ok = client.post(url(v2), json={"udin_number": GOOD_UDIN, "udin_generated_date": TODAY, "document_type": "Statutory Audit Report"}, headers=PARTNER)
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "recorded" and ok.json()["udin_number"] == GOOD_UDIN and ok.json()["recorded_by_name"] == "Priya Partner"
    u = udin(client, eid)
    assert u["summary"] == {"total_versions": 2, "pending_count": 1, "recorded_count": 1, "revoked_count": 0}
    row = next(i for i in u["items"] if i["version_number"] == 2)
    assert row["udin_number"] == GOOD_UDIN and row["udin_generated_date"] == TODAY and row["version_status"] == "signed_locked"
    assert D(row["total_assets"]) == D(state(client, eid)["active_version"]["balance_sheet"]["total_assets"])
    assert state(client, eid)["active_version"]["udin_status"] == "recorded"

    assert client.post(url(v2), json={"udin_number": OTHER_UDIN, "udin_generated_date": TODAY}, headers=PARTNER).status_code == 409   # already recorded
    assert client.post(url(v1), json={"udin_number": GOOD_UDIN, "udin_generated_date": TODAY}, headers=PARTNER).status_code == 409    # same UDIN twice
    assert client.post(url(v1, "/revoke"), json={"revoked_reason": "x"}, headers=PARTNER).status_code == 409                          # nothing to revoke

    assert client.post(url(v2, "/revoke"), json={}, headers=PARTNER).status_code == 400
    assert client.post(url(v2, "/revoke"), json={"revoked_reason": "  "}, headers=PARTNER).status_code == 400
    rv = client.post(url(v2, "/revoke"), json={"revoked_reason": "Generated against the wrong document"}, headers=PARTNER)
    assert rv.status_code == 200 and rv.json()["status"] == "revoked" and rv.json()["revoked_reason"] == "Generated against the wrong document"
    assert udin(client, eid)["summary"] == {"total_versions": 2, "pending_count": 1, "recorded_count": 0, "revoked_count": 1}
    assert client.post(url(v2, "/revoke"), json={"revoked_reason": "again"}, headers=PARTNER).status_code == 409

    # a UDIN can be recorded against the superseded v1 as well, and the dashboard reads straight from the register
    assert client.post(url(v1), json={"udin_number": OTHER_UDIN, "udin_generated_date": TODAY}, headers=PARTNER).status_code == 200
    assert udin(client, eid)["summary"] == {"total_versions": 2, "pending_count": 0, "recorded_count": 1, "revoked_count": 1}
    events = {a["event_name"] for a in client.get(f"/api/engagements/{eid}/audit-logs?scope=events&limit=500", headers=MAKER).json()}
    assert {"UDIN_RECORDED", "UDIN_REVOKED"} <= events
    assert client.get("/udin").status_code == 200 and 'href="/udin"' in client.get("/udin").text
    assert client.get("/api/engagements/999999/udin").status_code == 404
