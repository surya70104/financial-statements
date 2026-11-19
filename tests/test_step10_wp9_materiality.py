"""Step 10 acceptance tests: T-23 Materiality (WP9) and its link to the Issue Tracker.
Starts from the pilot engagement as Step 9 left it (signed off v2, locked): the Partner re-opens it, the tests run, and
the last test signs it off again (v3) to prove materiality is frozen by the lock."""
from decimal import ROUND_HALF_UP, Decimal

import psycopg
import pytest

D = Decimal
MAKER, CHECKER, PARTNER = {"X-User-Id": "2"}, {"X-User-Id": "3"}, {"X-User-Id": "4"}
LOCK_MSG = "Engagement is locked after Partner sign-off"
q = lambda v: D(v).quantize(D("0.01"), rounding=ROUND_HALF_UP)


@pytest.fixture(scope="module")
def eid(client):
    return client.get("/api/engagements").json()[0]["id"]


@pytest.fixture(scope="module")
def reopened(client, eid):
    if client.get(f"/api/engagements/{eid}/signoff").json()["is_locked"]:
        assert client.post(f"/api/engagements/{eid}/signoff/reopen", json={"reopen_reason": "Set materiality"}, headers=PARTNER).status_code == 200


@pytest.fixture(scope="module")
def comparatives(client, eid, reopened, test_db_url):
    """The product has no screen for prior-year amounts yet (they are seeded as 0.00), so the tests set them in the database:
    the first five mapped lines with a balance get a prior year that differs by 1, 2, 3, 4 and 5 lakh; every other line is unchanged."""
    bs = client.get(f"/api/engagements/{eid}/statements/balance-sheet").json()
    pnl = client.get(f"/api/engagements/{eid}/statements/pnl").json()
    lines = [l for part in bs["parts"] for sec in part["sections"] for l in sec["lines"]] + [l for sec in pnl["sections"] for l in sec["lines"]]
    live = [l for l in lines if l["kind"] == "mapped" and D(l["current"]) != 0]
    assert len(live) >= 6
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.user_id','3',false)")
        for i, l in enumerate(live):
            prior = D(l["current"]) - (D(i + 1) * 100000 if i < 5 else 0)
            conn.execute("""UPDATE prior_year_amounts SET amount = %s WHERE engagement_id = %s
                               AND line_id = (SELECT id FROM template_lines WHERE code = %s AND template_id = (SELECT template_id FROM engagements WHERE id = %s))""",
                         (prior, eid, l["code"], eid))
    return {l["code"]: D(i + 1) * 100000 for i, l in enumerate(live[:5])}


def mat(client, eid):
    return client.get(f"/api/engagements/{eid}/materiality").json()


def post(client, eid, body, who=CHECKER):
    return client.post(f"/api/engagements/{eid}/materiality", json=body, headers=who)


def issues(client, eid, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"/api/engagements/{eid}/issues" + (f"?{qs}" if qs else "")).json()


def open_refs(client, eid):
    return {i["reference_id"] for i in issues(client, eid, source="materiality", status="open")["items"]}


def test_not_set_yet_and_only_reviewers_can_set_it(client, eid, comparatives):
    first = mat(client, eid)
    assert first["materiality"] is None and first["comparatives_available"] is True
    assert set(first["benchmarks"]) == {"revenue", "total_assets", "expenses"} and D(first["benchmarks"]["total_assets"]["amount"]) == D("272641256.00")
    assert client.get(f"/api/engagements/{eid}").json()["materiality"] is None
    assert issues(client, eid)["summary"]["by_source"]["materiality"] == {"open": 0, "cleared": 0}
    body = {"input_mode": "manual", "overall_materiality": "100000.00"}
    assert client.post(f"/api/engagements/{eid}/materiality", json=body).status_code == 401
    assert post(client, eid, body, MAKER).status_code == 403
    assert mat(client, eid)["materiality"] is None                                                  # a refused request saves nothing
    assert post(client, eid, body, CHECKER).status_code == 200
    assert post(client, eid, body, PARTNER).status_code == 200                                      # re-saving is an update, not a second row


def test_manual_mode_stores_exact_paise_and_shows_in_header_and_audit(client, eid, reopened):
    r = post(client, eid, {"input_mode": "manual", "overall_materiality": "1,23,456.78", "notes": "Agreed with the engagement partner"}, CHECKER)
    assert r.status_code == 200, r.text
    m = r.json()["materiality"]
    assert m["input_mode"] == "manual" and m["benchmark_basis"] == "manual" and m["benchmark_pct"] is None and m["benchmark_amount"] is None
    assert m["overall_materiality"] == "123456.78"
    assert m["performance_materiality"] == "92592.59"                                               # 75%: 92,592.585 rounds half-up
    assert m["trivial_threshold"] == "6172.84"                                                      # 5%: 6,172.839
    assert m["set_by"] == "Chetan Checker (Reviewer)" and m["notes"] == "Agreed with the engagement partner"
    head = client.get(f"/api/engagements/{eid}").json()["materiality"]
    assert head["overall_materiality"] == "123456.78" and head["set_by"] == "Chetan Checker (Reviewer)"
    assert mat(client, eid)["materiality"]["overall_materiality"] == "123456.78"

    explicit = post(client, eid, {"input_mode": "manual", "overall_materiality": "200000", "performance_materiality": "150000.50", "trivial_threshold": "9999.99"}, PARTNER).json()["materiality"]
    assert (explicit["overall_materiality"], explicit["performance_materiality"], explicit["trivial_threshold"]) == ("200000.00", "150000.50", "9999.99")
    assert explicit["set_by"] == "Priya Partner"

    ev = [a for a in client.get(f"/api/engagements/{eid}/audit-logs?scope=events&limit=500", headers=MAKER).json() if a["event_name"] == "MATERIALITY_SET"]
    assert len(ev) >= 3 and ev[0]["after_data"]["overall_materiality"] == "200000.00" and ev[0]["after_data"]["set_by"] == "Priya Partner"
    assert any(a["after_data"]["overall_materiality"] == "123456.78" and a["reason"] == "Agreed with the engagement partner" for a in ev)

    for bad in ({"input_mode": "manual"}, {"input_mode": "manual", "overall_materiality": "0"}, {"input_mode": "manual", "overall_materiality": "-5"},
                {"input_mode": "manual", "overall_materiality": "10.005"}, {"input_mode": "manual", "overall_materiality": "abc"},
                {"input_mode": "manual", "overall_materiality": "1000", "performance_materiality": "1000.01"},
                {"input_mode": "manual", "overall_materiality": "1000", "trivial_threshold": "2000"}, {"input_mode": "other"}, {}):
        assert post(client, eid, bad).status_code == 400, bad
    assert mat(client, eid)["materiality"]["overall_materiality"] == "200000.00"                    # invalid requests changed nothing


def test_calculated_mode_uses_exact_decimal_math_on_the_live_benchmark(client, eid, reopened):
    bench = mat(client, eid)["benchmarks"]
    for basis, pct in (("revenue", "1.25"), ("total_assets", "0.50"), ("expenses", "2")):
        amount = D(bench[basis]["amount"])
        assert amount > 0, basis
        r = post(client, eid, {"input_mode": "calculated", "benchmark_basis": basis, "benchmark_pct": pct}, CHECKER)
        assert r.status_code == 200, r.text
        m = r.json()["materiality"]
        assert m["input_mode"] == "calculated" and m["benchmark_basis"] == basis and D(m["benchmark_pct"]) == D(pct)
        assert D(m["benchmark_amount"]) == amount                                                    # read from the statements, never typed
        assert D(m["overall_materiality"]) == q(amount * D(pct) / 100)
        assert D(m["performance_materiality"]) == q(D(m["overall_materiality"]) * D("0.75"))
        assert D(m["trivial_threshold"]) == q(D(m["overall_materiality"]) * D("0.05"))
    assert client.get(f"/api/engagements/{eid}").json()["materiality"]["benchmark_basis"] == "expenses"
    for bad in ({"benchmark_basis": "manual", "benchmark_pct": "1"}, {"benchmark_basis": "revenue"}, {"benchmark_basis": "revenue", "benchmark_pct": "0"},
                {"benchmark_basis": "revenue", "benchmark_pct": "100.01"}, {"benchmark_basis": "revenue", "benchmark_pct": "1.005"}, {"benchmark_basis": "bogus", "benchmark_pct": "1"}):
        assert post(client, eid, {"input_mode": "calculated", **bad}).status_code == 400, bad


def test_variances_above_materiality_become_deduplicated_issues_that_auto_clear(client, eid, comparatives):
    variances = mat(client, eid)["variances"]
    assert {v["code"]: D(v["variance"]) for v in variances} == comparatives                        # exactly the five lines we moved, by the amounts we set
    assert len(variances) >= 3 and [D(v["variance"]) for v in variances] == sorted((D(v["variance"]) for v in variances), reverse=True)
    top, threshold = D(variances[0]["variance"]), D(variances[2]["variance"])
    expected = {"MATERIALITY:" + v["code"] for v in variances if D(v["variance"]) > threshold}      # strictly above the threshold
    assert expected and len(expected) < len(variances)

    assert post(client, eid, {"input_mode": "manual", "overall_materiality": str(threshold)}).status_code == 200
    got = issues(client, eid, source="materiality", status="open")
    assert {i["reference_id"] for i in got["items"]} == expected
    first = {i["reference_id"]: i for i in got["items"]}
    sample = next(iter(first.values()))
    assert sample["cause_code"] == "ABOVE_MATERIALITY" and "materiality" in sample["detail"] and sample["title"].startswith("Above materiality")
    by_ref = {"MATERIALITY:" + v["code"]: v for v in variances}
    assert all(D(i["amount"]) == D(by_ref[r]["variance"]) for r, i in first.items())                 # the amount is the exact variance
    assert {"MATERIALITY:" + v["code"] for v in mat(client, eid)["exceeding"]} == expected

    for _ in range(3):                                                                                # saving again must never duplicate
        assert post(client, eid, {"input_mode": "manual", "overall_materiality": str(threshold)}).status_code == 200
    again = [i for i in issues(client, eid, source="materiality")["items"] if i["reference_id"] in expected]       # (earlier thresholds left cleared rows)
    assert len(again) == len(expected) and all(i["status"] == "open" for i in again)
    assert {i["reference_id"]: (i["id"], i["occurrences"]) for i in again} == {r: (i["id"], i["occurrences"]) for r, i in first.items()}
    assert issues(client, eid)["summary"]["by_source"]["materiality"]["open"] == len(expected)
    assert client.post(f"/api/engagements/{eid}/issues/{sample['id']}/clear", json={"resolution_note": "x"}, headers=PARTNER).status_code == 400   # system issue

    # raising materiality above every variance auto-clears them
    assert post(client, eid, {"input_mode": "manual", "overall_materiality": str(top + 1)}).status_code == 200
    assert open_refs(client, eid) == set() and mat(client, eid)["exceeding"] == []
    cleared = [i for i in issues(client, eid, source="materiality", status="cleared")["items"] if i["reference_id"] in expected]
    assert len(cleared) == len(expected) and all(i["cleared_by_system"] and "within materiality" in i["resolution_note"] for i in cleared)

    # lowering it again re-opens the same rows (no new issue rows)
    assert post(client, eid, {"input_mode": "manual", "overall_materiality": str(threshold)}).status_code == 200
    reopened_rows = [i for i in issues(client, eid, source="materiality")["items"] if i["reference_id"] in expected]
    assert open_refs(client, eid) == expected and {i["id"] for i in reopened_rows} == {i["id"] for i in first.values()}

    # a statement change re-evaluates the issues too: an approved adjustment moves two lines
    ledgers = client.get(f"/api/engagements/{eid}/mappings").json()["ledgers"]
    adj = client.post(f"/api/engagements/{eid}/adjustments", headers=MAKER, json={"entry_date": "2026-11-19", "description": "materiality test", "lines": [
        {"ledger_id": ledgers[0]["ledger_id"], "debit": "1.00"}, {"ledger_id": ledgers[1]["ledger_id"], "credit": "1.00"}]})
    assert adj.status_code in (200, 201), adj.text
    assert client.post(f"/api/engagements/{eid}/adjustments/{adj.json().get('id') or adj.json()['entry_number']}/approve", headers=CHECKER).status_code == 200
    now_open = issues(client, eid, source="materiality", status="open")["items"]
    assert now_open and all(D(i["amount"]) > threshold for i in now_open)                           # only genuinely material lines stay open


def test_materiality_is_frozen_once_the_engagement_is_signed_off(client, eid, comparatives, test_db_url):
    top = max(D(v["variance"]) for v in mat(client, eid)["variances"])
    assert post(client, eid, {"input_mode": "manual", "overall_materiality": str(top + 1)}).status_code == 200      # nothing material left: no blockers
    assert open_refs(client, eid) == set()
    assert client.post(f"/api/engagements/{eid}/checks/run", headers=CHECKER).json()["summary"]["text"] == "6 of 6 active checks passing"
    done = client.post(f"/api/engagements/{eid}/signoff", json={"note": "Final with materiality"}, headers=PARTNER)
    assert done.status_code == 200, done.text
    assert done.json()["version_number"] >= 3 and done.json()["is_locked"] is True

    saved = mat(client, eid)["materiality"]
    for who in (CHECKER, PARTNER):
        r = post(client, eid, {"input_mode": "manual", "overall_materiality": "5.00"}, who)
        assert r.status_code == 409 and r.json()["detail"] == LOCK_MSG
    assert post(client, eid, {"input_mode": "manual", "overall_materiality": "5.00"}, MAKER).status_code == 403   # the role check still comes first
    assert mat(client, eid)["materiality"] == saved                                                                # reading works, nothing changed
    assert client.get(f"/api/engagements/{eid}").json()["materiality"]["overall_materiality"] == saved["overall_materiality"]

    with psycopg.connect(test_db_url, autocommit=True) as conn:                                                    # the database says the same
        conn.execute("SELECT set_config('app.user_id','3',false)")
        with pytest.raises(psycopg.errors.RaiseException, match="locked"):
            conn.execute("UPDATE engagement_materiality SET overall_materiality = 1")
        with pytest.raises(psycopg.errors.RaiseException, match="locked"):
            conn.execute("DELETE FROM engagement_materiality")
    assert client.get("/materiality").status_code == 200 and 'href="/materiality"' in client.get("/materiality").text
