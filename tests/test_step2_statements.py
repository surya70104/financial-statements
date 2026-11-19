"""Step 2 acceptance tests (T-07 Balance Sheet, T-08 P&L) against a real PostgreSQL."""
import csv
import io
from decimal import Decimal
from pathlib import Path

import pytest

D = Decimal
ROOT = Path(__file__).resolve().parent.parent
PILOT = (ROOT / "sample_data" / "9and9_pilot_tb_76_ledgers.csv").read_bytes()
MAKER, CHECKER = {"X-User-Id": "2"}, {"X-User-Id": "3"}


def upload(client, eid, data=PILOT, headers=MAKER):
    return client.post(f"/api/engagements/{eid}/tb/import", files={"file": ("tb.csv", data, "text/csv")}, headers=headers)


@pytest.fixture(scope="module")
def eid(client):
    return client.get("/api/engagements").json()[0]["id"]


def csv_sum(group: str) -> Decimal:
    """Presented-sign closing total of one Tally group straight from the pilot file (Dr +, Cr -)."""
    total = D(0)
    for r in csv.DictReader(io.StringIO(PILOT.decode("utf-8-sig"))):
        if r["Under Group"] == group:
            v = D(r["Closing Balance"])
            total += v if r["Closing Type"] == "Dr" else -v
    return total


def find(statement_sections, code):
    for s in statement_sections:
        for l in s["lines"]:
            if l["code"] == code:
                return l
    raise AssertionError(code)


def bs_lines(bs):
    return [s for p in bs["parts"] for s in p["sections"]]


def test_statement_endpoints_open_but_auto_map_is_maker_only(client, eid):
    assert upload(client, eid).status_code == 200
    assert client.post(f"/api/engagements/{eid}/mappings/auto-map").status_code == 401
    assert client.post(f"/api/engagements/{eid}/mappings/auto-map", headers=CHECKER).status_code == 403
    assert client.get(f"/api/engagements/{eid}/statements/pnl").status_code == 200


def test_unmapped_ledgers_are_flagged_and_excluded(client, eid):
    # unmap one revenue ledger: statements must warn and the Balance Sheet must say it is incomplete
    m = client.get(f"/api/engagements/{eid}/mappings").json()
    victim = next(l for l in m["ledgers"] if l["tally_group"] == "Sales Accounts")
    client.delete(f"/api/engagements/{eid}/mappings/{victim['ledger_id']}", headers=MAKER)
    bs = client.get(f"/api/engagements/{eid}/statements/balance-sheet").json()
    assert bs["warnings"]["unmapped_ledgers"] >= 1 and bs["check"]["status"] == "INCOMPLETE"
    pnl = client.get(f"/api/engagements/{eid}/statements/pnl").json()
    assert pnl["warnings"]["unmapped_ledgers"] >= 1


def test_auto_map_then_statements_balance(client, eid):
    r = client.post(f"/api/engagements/{eid}/mappings/auto-map", headers=MAKER)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["skipped"] == [] and res["counts"]["unmapped_ledgers"] == 0 and res["counts"]["open_mapping_exceptions"] == 0
    assert res["counts"]["matches"]
    # idempotent: a second run changes nothing
    again = client.post(f"/api/engagements/{eid}/mappings/auto-map", headers=MAKER).json()
    assert again["mapped"] == 0 and again["changed"] is False

    # audit trail records the bulk action
    logs = client.get(f"/api/engagements/{eid}/audit-logs", headers=MAKER).json()
    assert any(a["event_name"] == "AUTO_MAP" for a in logs)

    # ---- T-08 P&L ----
    pnl = client.get(f"/api/engagements/{eid}/statements/pnl").json()
    assert pnl["current_label"] == "FY 2026-27" and pnl["previous_label"] == "FY 2025-26"
    sections = pnl["sections"]
    revenue = find(sections, "PL.REV")
    assert D(revenue["current"]) == -csv_sum("Sales Accounts") != 0       # Cr balances shown positive
    assert revenue["note_no"] == "23" and D(revenue["previous"]) == 0
    other_income = D(find(sections, "PL.OI")["current"])
    total_income = D(find(sections, "PL.INCOME.TOTAL")["current"])
    assert total_income == D(revenue["current"]) + other_income
    expenses = D(find(sections, "PL.EXP.TOTAL")["current"])
    assert expenses == csv_sum("Direct Expenses") + csv_sum("Indirect Expenses")
    assert D(pnl["profit_for_period"]["current"]) == D("-481634.00")      # net loss Rs (4,81,634)
    assert D(find(sections, "PL.PAT")["current"]) == total_income - expenses

    # ---- T-07 Balance Sheet ----
    bs = client.get(f"/api/engagements/{eid}/statements/balance-sheet").json()
    assert bs["check"]["status"] == "BALANCED"
    assert D(bs["check"]["total_assets"]) == D("272641256.00")             # 27,26,41,256
    assert D(bs["check"]["total_equity_and_liabilities"]) == D("272641256.00")
    assert D(bs["check"]["difference"]) == 0
    assert [p["name"] for p in bs["parts"]] == ["EQUITY AND LIABILITIES", "ASSETS"]
    lines = bs_lines(bs)
    reserves = find(lines, "BS.EQ.RS")
    b = reserves["breakdown"]                                              # net loss ties to the reserves movement
    assert D(b["profit_for_period"]) == D(pnl["profit_for_period"]["current"]) == D("-481634.00")
    assert D(b["per_trial_balance"]) == -csv_sum("Reserves & Surplus")
    assert D(reserves["current"]) == D(b["per_trial_balance"]) + D(b["profit_for_period"]) + D(b["other_movements"])
    assert D(b["other_movements"]) == 0
    assert find(lines, "BS.EQ.SC")["note_no"] == "1"
    assert D(find(lines, "BS.ASSETS.TOTAL")["previous"]) == 0              # comparatives seeded 0.00
    assert bs["warnings"]["unmapped_ledgers"] == 0


def test_other_reserves_movement_flows_and_shows_difference(client, eid, test_db_url):
    import psycopg
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.user_id','2',false)")
        conn.execute("UPDATE engagements SET other_reserves_movement = 1000.50 WHERE id = %s", (eid,))
        try:
            bs = client.get(f"/api/engagements/{eid}/statements/balance-sheet").json()
            assert D(bs["check"]["difference"]) == D("-1000.50") and bs["check"]["status"] == "NOT_BALANCED"
        finally:
            conn.execute("UPDATE engagements SET other_reserves_movement = 0 WHERE id = %s", (eid,))
    bs = client.get(f"/api/engagements/{eid}/statements/balance-sheet").json()
    assert bs["check"]["status"] == "BALANCED"
