"""Step 3 acceptance tests (T-11 Checks engine) against a real PostgreSQL."""
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

D = Decimal
ROOT = Path(__file__).resolve().parent.parent
PILOT = (ROOT / "sample_data" / "9and9_pilot_tb_76_ledgers.csv").read_bytes()
MAKER, CHECKER = {"X-User-Id": "2"}, {"X-User-Id": "3"}


@pytest.fixture(scope="module")
def eid(client):
    return client.get("/api/engagements").json()[0]["id"]


def checks(client, eid):
    body = client.get(f"/api/engagements/{eid}/checks").json()
    return body["summary"], {i["code"]: i for i in body["items"]}


def test_all_mapped_state(client, eid):
    assert client.post(f"/api/engagements/{eid}/tb/import", files={"file": ("tb.csv", PILOT, "text/csv")}, headers=MAKER).status_code == 200
    assert client.post(f"/api/engagements/{eid}/mappings/auto-map", headers=MAKER).status_code == 200

    # the auto-map above re-ran the checks by itself: no manual run needed
    summary, c = checks(client, eid)
    assert summary["has_run"] and not summary["stale"]
    assert (summary["passed"], summary["total"]) == (6, 6)         # CHK-03 is on since Step 4, CHK-06 is real since Step 15 (Q1)
    assert summary["text"] == "6 of 6 active checks passing"
    for code in ("CHK-01", "CHK-02", "CHK-03", "CHK-04", "CHK-05", "CHK-06"):
        assert c[code]["status"] == "pass", code
        assert D(c[code]["variance"]) == 0
    assert c["CHK-01"]["lhs_amount"] == "272641256.00" and c["CHK-01"]["rhs_amount"] == "272641256.00"
    assert c["CHK-06"]["detail"]["notes_checked"] == c["CHK-06"]["detail"]["notes_tying"] > 0 and summary["notes"] == []


def test_unmap_one_ledger_flips_checks_and_remap_restores(client, eid):
    m = client.get(f"/api/engagements/{eid}/mappings").json()
    victim = next(l for l in m["ledgers"] if l["tally_group"] == "Sales Accounts")
    line_id = victim["line_id"]
    bal = D(victim["closing_balance"])                                   # Cr, negative

    assert client.delete(f"/api/engagements/{eid}/mappings/{victim['ledger_id']}", headers=MAKER).status_code == 200
    summary, c = checks(client, eid)
    assert not summary["stale"] and summary["passed"] == 4 and summary["total"] == 6          # CHK-01 and CHK-04 fail; CHK-06 still ties (the ledger is out of both sides)
    assert c["CHK-04"]["status"] == "fail" and D(c["CHK-04"]["variance"]) == 1
    assert c["CHK-04"]["detail"]["unmapped_ledgers"] == 1 and D(c["CHK-04"]["detail"]["unmapped_net_balance"]) == bal
    # CHK-01 differs by the unmapped sales ledger exactly (Cr balance left out => assets exceed E&L by that rupee amount)
    assert c["CHK-01"]["status"] == "fail" and D(c["CHK-01"]["variance"]) == -bal   # assets - E&L, here the ledger's credit missing

    assert client.post(f"/api/engagements/{eid}/mappings/{victim['ledger_id']}", json={"line_id": line_id}, headers=MAKER).status_code == 200
    summary, c = checks(client, eid)
    assert (summary["passed"], summary["total"]) == (6, 6)
    assert c["CHK-01"]["status"] == "pass" and c["CHK-04"]["status"] == "pass" and D(c["CHK-04"]["variance"]) == 0


def test_run_endpoint_permissions_and_audit(client, eid):
    assert client.post(f"/api/engagements/{eid}/checks/run").status_code == 401
    r = client.post(f"/api/engagements/{eid}/checks/run", headers=CHECKER)
    assert r.status_code == 200 and r.json()["summary"]["text"] == "6 of 6 active checks passing"
    logs = client.get(f"/api/engagements/{eid}/audit-logs", headers=MAKER).json()
    assert any(a["event_name"] == "CHECKS_RUN" for a in logs)


def test_chk05_fails_when_a_comparative_is_missing(client, eid, test_db_url):
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.user_id','2',false)")
        row = conn.execute("DELETE FROM prior_year_amounts WHERE id = (SELECT min(id) FROM prior_year_amounts) RETURNING engagement_id, line_id").fetchone()
        try:
            r = client.post(f"/api/engagements/{eid}/checks/run", headers=MAKER).json()
            c5 = next(i for i in r["items"] if i["code"] == "CHK-05")
            assert c5["status"] == "fail" and D(c5["variance"]) == 1
        finally:
            conn.execute("INSERT INTO prior_year_amounts (engagement_id, line_id, amount) VALUES (%s,%s,0)", row)
    r = client.post(f"/api/engagements/{eid}/checks/run", headers=MAKER).json()
    assert next(i for i in r["items"] if i["code"] == "CHK-05")["status"] == "pass"


def test_check_history_is_immutable(test_db_url):
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("UPDATE check_runs SET passed_count = 0")
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM check_results")
