"""Step 6 acceptance tests: unified issue tracker (T-12) and hard sign-off gate (T-13).
This module signs the pilot engagement off at the end (that locks it), so it must run last; it is the last file alphabetically."""
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

D = Decimal
ROOT = Path(__file__).resolve().parent.parent
PILOT = (ROOT / "sample_data" / "9and9_pilot_tb_76_ledgers.csv").read_bytes()
MAKER, CHECKER, PARTNER = {"X-User-Id": "2"}, {"X-User-Id": "3"}, {"X-User-Id": "4"}
LOCK_MSG = "Engagement is locked after Partner sign-off"


@pytest.fixture(scope="module")
def eid(client):
    return client.get("/api/engagements").json()[0]["id"]


@pytest.fixture(scope="module")
def mapped(client, eid):
    assert client.post(f"/api/engagements/{eid}/tb/import", files={"file": ("tb.csv", PILOT, "text/csv")}, headers=MAKER).status_code == 200
    assert client.post(f"/api/engagements/{eid}/mappings/auto-map", headers=MAKER).status_code == 200
    assert client.post(f"/api/engagements/{eid}/checks/run", headers=MAKER).status_code == 200


def issues(client, eid, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"/api/engagements/{eid}/issues" + (f"?{q}" if q else "")).json()


def open_codes(client, eid):
    return {i["reference_id"].split(":")[1] for i in issues(client, eid, source="check", status="open")["items"]}


def test_chk06_creates_one_deduplicated_check_issue_and_blocks_signoff(client, eid, mapped, test_db_url):
    for _ in range(3):                                                       # repeated runs must never duplicate the issue
        assert client.post(f"/api/engagements/{eid}/checks/run", headers=CHECKER).status_code == 200
    got = issues(client, eid, source="check", status="open")           # (earlier tests left some already-cleared check issues behind)
    assert [i["reference_id"] for i in got["items"]] == ["CHECK:CHK-06"] and got["items"][0]["status"] == "open"
    assert "CHK-06" in got["items"][0]["title"] and "Q1" in got["items"][0]["detail"] and got["items"][0]["cause"]
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM exceptions WHERE source='check' AND reference_id='CHECK:CHK-06'").fetchone()[0] == 1

    state = client.get(f"/api/engagements/{eid}/signoff").json()
    assert state["can_sign_off"] is False and state["is_locked"] is False and state["signoff"] is None
    assert any(b["type"] == "CHECK" and b["code"] == "CHK-06" and b["pending_definition"] for b in state["blockers"])
    assert any(b["type"] == "ISSUE" and b["source"] == "check" for b in state["blockers"])
    r = client.post(f"/api/engagements/{eid}/signoff", headers=PARTNER)
    assert r.status_code == 409
    assert {"CHK-06"} <= {b.get("code") for b in r.json()["detail"]["blockers"]}
    assert client.get(f"/api/engagements/{eid}/signoff").json()["is_locked"] is False       # a blocked attempt locks nothing


def test_unmap_adds_mapping_issue_and_checks_issues_then_remap_clears_them(client, eid, mapped):
    m = client.get(f"/api/engagements/{eid}/mappings").json()
    victim = next(l for l in m["ledgers"] if l["tally_group"] == "Sales Accounts")
    line_id = victim["line_id"]
    base_open = issues(client, eid)["summary"]["open"]

    assert client.delete(f"/api/engagements/{eid}/mappings/{victim['ledger_id']}", headers=MAKER).status_code == 200
    mapping = issues(client, eid, source="mapping", status="open")["items"]
    assert len(mapping) == 1 and victim["name"] in mapping[0]["title"]
    assert open_codes(client, eid) == {"CHK-01", "CHK-04", "CHK-06"}                          # one issue per failing check
    unified = issues(client, eid)
    assert unified["summary"]["open"] == base_open + 3 and unified["summary"]["by_source"]["mapping"]["open"] == 1
    chk04 = next(i for i in unified["items"] if i["reference_id"] == "CHECK:CHK-04")

    # system issues cannot be cleared by hand
    for sys_issue in (mapping[0], chk04):
        r = client.post(f"/api/engagements/{eid}/issues/{sys_issue['id']}/clear", json={"resolution_note": "done"}, headers=MAKER)
        assert r.status_code == 400 and "automatically" in r.json()["detail"]

    assert client.post(f"/api/engagements/{eid}/mappings/{victim['ledger_id']}", json={"line_id": line_id}, headers=MAKER).status_code == 200
    assert issues(client, eid, source="mapping", status="open")["items"] == []
    cleared = [i for i in issues(client, eid, source="mapping", status="cleared")["items"] if i["id"] == mapping[0]["id"]]
    assert len(cleared) == 1 and cleared[0]["cleared_by_system"] is True and "Auto-cleared" in cleared[0]["resolution_note"]
    assert open_codes(client, eid) == {"CHK-06"}                                              # CHK-01 and CHK-04 issues auto-cleared
    cleared_checks = {i["reference_id"]: i for i in issues(client, eid, source="check", status="cleared")["items"]}
    assert cleared_checks["CHECK:CHK-04"]["cleared_by_system"] is True

    # the same failure again re-opens the SAME row (no duplicates)
    client.delete(f"/api/engagements/{eid}/mappings/{victim['ledger_id']}", headers=MAKER)
    again = {i["reference_id"]: i for i in issues(client, eid, source="check", status="open")["items"]}
    assert again["CHECK:CHK-04"]["id"] == chk04["id"] and again["CHECK:CHK-04"]["occurrences"] >= 2
    client.post(f"/api/engagements/{eid}/mappings/{victim['ledger_id']}", json={"line_id": line_id}, headers=MAKER)
    assert open_codes(client, eid) == {"CHK-06"}


def test_manual_issues_raise_and_clear(client, eid, mapped):
    url = f"/api/engagements/{eid}/issues"
    assert client.post(url, json={"title": "x"}).status_code == 401
    assert client.post(url, json={"title": "   "}, headers=MAKER).status_code == 400
    assert client.post(url, json={"title": "t", "amount": "1.234"}, headers=MAKER).status_code == 400
    for who in (MAKER, CHECKER, PARTNER):                                                      # all three roles may raise
        assert client.post(url, json={"title": "Role test"}, headers=who).status_code == 200
    r = client.post(url, json={"title": "Confirm related-party disclosure", "detail": "Ask management", "amount": "1234.50"}, headers=CHECKER)
    assert r.status_code == 200
    mid = r.json()["id"]
    item = next(i for i in issues(client, eid, source="manual", status="open")["items"] if i["id"] == mid)
    assert item["source"] == "manual" and D(item["amount"]) == D("1234.50") and item["created_by_name"].startswith("Chetan")
    assert any(b["type"] == "ISSUE" and b["issue_id"] == mid for b in client.get(f"/api/engagements/{eid}/signoff").json()["blockers"])

    clear = f"{url}/{mid}/clear"
    assert client.post(clear, json={}, headers=MAKER).status_code == 400                       # note required
    assert client.post(clear, json={"resolution_note": "   "}, headers=MAKER).status_code == 400
    ok = client.post(clear, json={"resolution_note": "Disclosure added as Note 24(b)"}, headers=MAKER)
    assert ok.status_code == 200 and ok.json()["status"] == "cleared"
    done = next(i for i in issues(client, eid, source="manual", status="cleared")["items"] if i["id"] == mid)
    assert done["resolution_note"] == "Disclosure added as Note 24(b)" and done["cleared_by_name"].startswith("Maya") and done["cleared_by_system"] is False
    assert client.post(clear, json={"resolution_note": "again"}, headers=MAKER).status_code == 409
    assert client.post(f"{url}/999999/clear", json={"resolution_note": "x"}, headers=MAKER).status_code == 404

    for i in issues(client, eid, source="manual", status="open")["items"]:                     # tidy: clear the role-test issues
        assert client.post(f"{url}/{i['id']}/clear", json={"resolution_note": "test cleanup"}, headers=PARTNER).status_code == 200
    names = {a["event_name"] for a in client.get(f"/api/engagements/{eid}/audit-logs", headers=MAKER).json()}
    assert {"ISSUE_RAISED", "ISSUE_CLEARED"} <= names
    assert issues(client, eid)["summary"]["by_source"]["manual"]["open"] == 0


def test_only_a_partner_can_sign_off(client, eid, mapped):
    url = f"/api/engagements/{eid}/signoff"
    assert client.post(url, headers=MAKER).status_code == 403
    assert client.post(url, headers=CHECKER).status_code == 403
    assert client.post(url).status_code == 401
    assert client.get(url).status_code == 200                                                  # anyone may look at the gate


def test_partner_signs_off_and_the_engagement_locks(client, eid, mapped, test_db_url):
    # Answer Q1: give CHK-06 a real rule (a test-only stand-in), then every check can pass.
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("""UPDATE check_definitions SET rule_type='mapping_complete', rule_config='{}',
                               definition_status='confirmed' WHERE code='CHK-06'""")
    r = client.post(f"/api/engagements/{eid}/checks/run", headers=CHECKER).json()
    assert r["summary"]["text"] == "6 of 6 active checks passing"
    assert issues(client, eid)["summary"]["open"] == 0                                         # CHK-06's issue auto-cleared; nothing else open
    state = client.get(f"/api/engagements/{eid}/signoff").json()
    assert state["can_sign_off"] is True and state["blockers"] == [] and state["counts"] == {"checks": 0, "issues": 0}
    assert client.post(f"/api/engagements/{eid}/signoff", headers=MAKER).status_code == 403    # still not theirs to do

    done = client.post(f"/api/engagements/{eid}/signoff", json={"note": "Final, reviewed"}, headers=PARTNER)
    assert done.status_code == 200, done.text
    rec = done.json()
    assert rec["is_locked"] is True and rec["signed_off_by_name"] == "Priya Partner" and rec["signoff_note"] == "Final, reviewed"
    snap = rec["snapshot_json"]
    assert D(snap["balance_sheet"]["total_assets"]) == D("272641256.00") == D(snap["balance_sheet"]["total_equity_and_liabilities"])
    assert D(snap["profit_and_loss"]["profit_for_period"]) == D("-481634.00") and snap["checks"] == {"run_id": snap["checks"]["run_id"], "passed": 6, "total": 6}
    again = client.get(f"/api/engagements/{eid}/signoff").json()
    assert again["is_locked"] is True and again["can_sign_off"] is False and again["signoff"]["id"] == rec["id"]
    assert client.get(f"/api/engagements/{eid}").json()["is_locked"] is True
    assert client.post(f"/api/engagements/{eid}/signoff", headers=PARTNER).status_code == 409   # cannot sign twice

    # ---- locked: every data-changing route answers 409 ----
    r = client.post(f"/api/engagements/{eid}/tb/import", files={"file": ("tb.csv", PILOT, "text/csv")}, headers=MAKER)
    assert r.status_code == 409 and r.json()["detail"] == LOCK_MSG
    m = client.get(f"/api/engagements/{eid}/mappings").json()
    led, line = m["ledgers"][0]["ledger_id"], m["ledgers"][0]["line_id"]
    for resp in (client.post(f"/api/engagements/{eid}/mappings/{led}", json={"line_id": line}, headers=MAKER),
                 client.delete(f"/api/engagements/{eid}/mappings/{led}", headers=MAKER),
                 client.post(f"/api/engagements/{eid}/mappings/auto-map", headers=MAKER),
                 client.post(f"/api/engagements/{eid}/adjustments", headers=MAKER, json={"entry_date": "2026-11-18", "description": "late", "lines": [
                     {"ledger_id": led, "debit": "1.00"}, {"ledger_id": m["ledgers"][1]["ledger_id"], "credit": "1.00"}]}),
                 client.post(f"/api/engagements/{eid}/adjustments/1/approve", headers=CHECKER),
                 client.post(f"/api/engagements/{eid}/adjustments/1/reverse", headers=MAKER)):
        assert resp.status_code == 409 and resp.json()["detail"] == LOCK_MSG, resp.text
    assert client.get(f"/api/engagements/{eid}/statements/balance-sheet").json()["check"]["status"] == "BALANCED"   # reading still works
    assert client.post(f"/api/engagements/{eid}/checks/run", headers=CHECKER).status_code == 200                     # so does re-checking

    # ---- database backstop and permanence ----
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.user_id','2',false)")
        with pytest.raises(psycopg.errors.RaiseException, match="locked"):
            conn.execute("DELETE FROM ledger_mappings WHERE ledger_id = %s", (led,))
        with pytest.raises(psycopg.errors.RaiseException, match="locked"):
            conn.execute("UPDATE ledger_balances SET closing_balance = closing_balance WHERE ledger_id = %s", (led,))
        with pytest.raises(psycopg.errors.RaiseException, match="locked"):
            conn.execute("INSERT INTO journal_entries (engagement_id, entry_number, entry_date, description, created_by) VALUES (%s, 99, '2026-11-18', 'x', 2)", (eid,))
        with pytest.raises(psycopg.errors.RaiseException, match="cannot be changed"):
            conn.execute("UPDATE engagement_signoffs SET is_locked = FALSE")
        with pytest.raises(psycopg.errors.RaiseException, match="cannot be changed"):
            conn.execute("DELETE FROM engagement_signoffs")
    assert "ENGAGEMENT_SIGNED_OFF" in {a["event_name"] for a in client.get(f"/api/engagements/{eid}/audit-logs", headers=MAKER).json()}
