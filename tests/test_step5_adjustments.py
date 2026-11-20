"""Step 5 acceptance tests: adjustments (T-04), approval and immutability (T-05), adjusted trial balance (T-06)."""
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

D = Decimal
ROOT = Path(__file__).resolve().parent.parent
PILOT = (ROOT / "sample_data" / "9and9_pilot_tb_76_ledgers.csv").read_bytes()
MAKER, CHECKER, PARTNER = {"X-User-Id": "2"}, {"X-User-Id": "3"}, {"X-User-Id": "4"}


@pytest.fixture(scope="module")
def eid(client):
    return client.get("/api/engagements").json()[0]["id"]


@pytest.fixture(scope="module")
def mapped(client, eid):
    assert client.post(f"/api/engagements/{eid}/tb/import", files={"file": ("tb.csv", PILOT, "text/csv")}, headers=MAKER).status_code == 200
    assert client.post(f"/api/engagements/{eid}/mappings/auto-map", headers=MAKER).status_code == 200


def ledger_id(client, eid, group):
    m = client.get(f"/api/engagements/{eid}/mappings").json()
    return next(l["ledger_id"] for l in m["ledgers"] if l["tally_group"] == group)


def snapshot(client, eid):
    g = lambda p: client.get(f"/api/engagements/{eid}/{p}").json()
    return {"bs": g("statements/balance-sheet"), "pnl": g("statements/pnl"), "notes": g("notes"), "ratios": g("ratios"), "atb": g("adjusted-tb")}


def entry_body(exp, fa, cr, a="1000.50", b="2000.00", c="3000.50", desc="Accrue expenses and capitalise asset"):
    return {"entry_date": "2026-11-18", "description": desc, "lines": [
        {"ledger_id": exp, "debit": a}, {"ledger_id": fa, "debit": b}, {"ledger_id": cr, "credit": c, "note": "supplier"}]}


def adjustments(client, eid):
    return client.get(f"/api/engagements/{eid}/adjustments").json()


def test_validation_and_permissions(client, eid, mapped):
    exp, fa, cr = (ledger_id(client, eid, g) for g in ("Indirect Expenses", "Fixed Assets", "Sundry Creditors"))
    url = f"/api/engagements/{eid}/adjustments"
    before = len(adjustments(client, eid)["entries"])
    unbalanced = entry_body(exp, fa, cr, c="3000.49")
    r = client.post(url, json=unbalanced, headers=MAKER)
    assert r.status_code == 400 and "not balanced" in r.json()["detail"]
    one_line = {"entry_date": "2026-11-18", "description": "x", "lines": [{"ledger_id": exp, "debit": "5"}]}
    assert client.post(url, json=one_line, headers=MAKER).status_code == 400
    both = {"entry_date": "2026-11-18", "description": "x", "lines": [{"ledger_id": exp, "debit": "5", "credit": "5"}, {"ledger_id": fa, "credit": "0"}]}
    assert client.post(url, json=both, headers=MAKER).status_code == 400
    assert client.post(url, json=entry_body(exp, fa, cr, a="1000.505", c="3000.505"), headers=MAKER).status_code == 400   # sub-paisa
    assert client.post(url, json=entry_body(exp, fa, cr, a="-1000.50", c="999.50"), headers=MAKER).status_code == 400
    assert client.post(url, json={**entry_body(exp, fa, cr), "lines": [{"ledger_id": 999999, "debit": "1"}, {"ledger_id": fa, "credit": "1"}]},
                       headers=MAKER).status_code == 400
    assert client.post(url, json=entry_body(exp, fa, cr), headers=CHECKER).status_code == 403
    assert client.post(url, json=entry_body(exp, fa, cr)).status_code == 401
    assert len(adjustments(client, eid)["entries"]) == before                 # nothing was saved by any rejected request


def test_draft_changes_nothing_then_approval_flows_everywhere(client, eid, mapped, test_db_url):
    exp, fa, cr = (ledger_id(client, eid, g) for g in ("Indirect Expenses", "Fixed Assets", "Sundry Creditors"))
    url = f"/api/engagements/{eid}/adjustments"
    base = snapshot(client, eid)
    assert D(base["bs"]["profit_for_period"]) == D("-481634.00")

    # ---- draft ----
    r = client.post(url, json=entry_body(exp, fa, cr), headers=MAKER)
    assert r.status_code == 200, r.text
    entry = r.json()
    assert entry["status"] == "draft"
    assert snapshot(client, eid) == base                                       # BS, P&L, notes, ratios, adjusted TB untouched
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        assert conn.execute("SELECT coalesce(sum(adjustment_net), 0), count(*) FILTER (WHERE adjustment_net <> 0) FROM v_adjusted_tb").fetchone() == (0, 0)

    # ---- maker-checker ----
    assert client.post(f"{url}/{entry['id']}/approve", headers=MAKER).status_code == 403      # a Maker may not approve at all
    pr = client.post(url, json=entry_body(exp, fa, cr, a="10.00", b="0.00", c="10.00", desc="partner draft").copy() | {
        "lines": [{"ledger_id": fa, "debit": "10.00"}, {"ledger_id": cr, "credit": "10.00"}]}, headers=PARTNER).json()
    self_approve = client.post(f"{url}/{pr['id']}/approve", headers=PARTNER)
    assert self_approve.status_code == 403 and "someone else" in self_approve.json()["detail"]   # creator cannot approve own entry
    with psycopg.connect(test_db_url, autocommit=True) as conn:                                  # ... and the database agrees
        conn.execute("SELECT set_config('app.user_id','4',false)")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE journal_entries SET status='approved', approved_by=created_by, approved_at=now() WHERE id=%s", (pr["id"],))
        conn.execute("DELETE FROM journal_lines WHERE journal_entry_id=%s", (pr["id"],))        # drafts are deletable; tidy up
        conn.execute("DELETE FROM journal_entries WHERE id=%s", (pr["id"],))
    assert snapshot(client, eid) == base

    # ---- approval by the Checker ----
    ok = client.post(f"{url}/{entry['id']}/approve", headers=CHECKER)
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert client.post(f"{url}/{entry['id']}/approve", headers=CHECKER).status_code == 409   # already approved
    after = snapshot(client, eid)
    atb = {r["ledger_id"]: r for r in after["atb"]["rows"]}
    assert D(atb[exp]["adjustment_net"]) == D("1000.50") and D(atb[fa]["adjustment_net"]) == D("2000.00") and D(atb[cr]["adjustment_net"]) == D("-3000.50")
    base_atb = {r["ledger_id"]: r for r in base["atb"]["rows"]}
    for lid in (exp, fa, cr):
        assert D(atb[lid]["adjusted_balance"]) == D(base_atb[lid]["adjusted_balance"]) + D(atb[lid]["adjustment_net"])
        assert atb[lid]["tb_balance"] == base_atb[lid]["tb_balance"]                       # imported figure never moves
    assert D(after["atb"]["totals"]["adjusted_difference"]) == 0                            # adjusted TB still foots

    bs, pnl = after["bs"], after["pnl"]
    assert D(bs["profit_for_period"]) == D("-482634.50") == D(pnl["profit_for_period"]["current"])
    assert D(bs["check"]["total_assets"]) == D("272641256.00") + D("2000.00")
    assert bs["check"]["status"] == "BALANCED" and D(bs["check"]["difference"]) == 0
    notes = after["notes"]
    assert notes["tie_summary"]["tying"] == 30 and D(next(n for n in notes["notes"] if n["note_no"] == "11")["note_total"]) == \
        D(next(n for n in base["notes"]["notes"] if n["note_no"] == "11")["note_total"]) + D("2000.00")
    r01 = lambda s: next(i for i in s["ratios"]["items"] if i["code"] == "R-01")
    assert D(r01(after)["denominator"]) == D(r01(base)["denominator"]) + D("3000.50")        # ratios follow the approved entry
    checks = client.get(f"/api/engagements/{eid}/checks").json()
    c = {i["code"]: i["status"] for i in checks["items"]}
    assert c["CHK-01"] == "pass" and c["CHK-02"] == "pass" and c["CHK-03"] == "pass"
    assert checks["summary"]["text"] == "6 of 6 active checks passing" and not checks["summary"]["stale"]

    # ---- immutability (database level) ----
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.user_id','2',false)")
        for sql in ("UPDATE journal_entries SET description='tampered' WHERE id=%s", "DELETE FROM journal_entries WHERE id=%s",
                    "UPDATE journal_lines SET debit_amount=1 WHERE journal_entry_id=%s", "DELETE FROM journal_lines WHERE journal_entry_id=%s"):
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(sql, (entry["id"],))
        with pytest.raises(psycopg.errors.RaiseException):                                       # cannot add a line to an approved entry
            conn.execute("INSERT INTO journal_lines (journal_entry_id, engagement_id, ledger_id, debit_amount) VALUES (%s,%s,%s,1)",
                         (entry["id"], eid, exp))
        with pytest.raises(psycopg.errors.CheckViolation):                                       # a line cannot carry both sides
            conn.execute("INSERT INTO journal_lines (journal_entry_id, engagement_id, ledger_id, debit_amount, credit_amount) VALUES (%s,%s,%s,1,1)",
                         (pr["id"] + 1000, eid, exp))

    # ---- audit trail ----
    logs = client.get(f"/api/engagements/{eid}/audit-logs", headers=MAKER).json()
    assert {"ADJUSTMENT_CREATED", "ADJUSTMENT_APPROVED"} <= {a["event_name"] for a in logs}

    # ---- reversal ----
    rev_url = f"{url}/{entry['id']}/reverse"
    assert client.post(rev_url, headers=CHECKER).status_code == 403
    rev = client.post(rev_url, headers=MAKER)
    assert rev.status_code == 200, rev.text
    rev = rev.json()
    assert rev["status"] == "draft"
    assert client.post(rev_url, headers=MAKER).status_code == 409                               # cannot reverse twice
    assert client.post(f"{url}/{rev['id']}/reverse", headers=MAKER).status_code == 409           # a draft cannot be reversed
    assert snapshot(client, eid) == after                                                       # still only a draft: nothing moves
    listed = {e["id"]: e for e in adjustments(client, eid)["entries"]}
    assert listed[rev["id"]]["reverses_entry_id"] == entry["id"] and listed[entry["id"]]["reversed_by_entry_number"] == rev["entry_number"]
    swapped = {l["ledger_id"]: (D(l["debit"]), D(l["credit"])) for l in listed[rev["id"]]["lines"]}
    assert swapped == {exp: (D(0), D("1000.50")), fa: (D(0), D("2000.00")), cr: (D("3000.50"), D(0))}
    assert client.post(f"{url}/{rev['id']}/approve", headers=MAKER).status_code == 403          # Maker still cannot approve
    assert client.post(f"{url}/{rev['id']}/approve", headers=CHECKER).status_code == 200
    restored = snapshot(client, eid)
    assert restored == base | {"atb": restored["atb"]}                                          # BS, P&L, notes, ratios back to baseline
    assert all(D(r["adjustment_net"]) == 0 for r in restored["atb"]["rows"])
    assert restored["atb"]["rows"] == base["atb"]["rows"]
    assert D(restored["bs"]["check"]["total_assets"]) == D("272641256.00") and D(restored["bs"]["profit_for_period"]) == D("-481634.00")
    counts = adjustments(client, eid)["counts"]
    assert counts == {"draft": 0, "approved": 2}
