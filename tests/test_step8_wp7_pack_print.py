"""Step 8 acceptance tests: print-ready statements (T-15) and the Audit Committee / IA pack and audit trail views (T-16).
Runs against the pilot engagement, which the Step 6 tests have signed off and locked: exporting must work there and change no data."""
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

D = Decimal
ROOT = Path(__file__).resolve().parent.parent
MAKER, CHECKER, PARTNER = {"X-User-Id": "2"}, {"X-User-Id": "3"}, {"X-User-Id": "4"}


@pytest.fixture(scope="module")
def eid(client):
    return client.get("/api/engagements").json()[0]["id"]          # the pilot engagement


@pytest.fixture(scope="module")
def pending_q1(client, eid, test_db_url):
    """Put CHK-06 back to 'pending Q1' (the Step 6 test had stood in a rule for it) and re-run the checks: 5 of 6 passing."""
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("""UPDATE check_definitions SET rule_type='pending_definition', definition_status='pending_definition',
                               rule_config='{"message":"Pending Product Owner confirmation on Q1"}' WHERE code='CHK-06'""")
    r = client.post(f"/api/engagements/{eid}/checks/run", headers=CHECKER)
    assert r.status_code == 200 and r.json()["summary"]["text"] == "5 of 6 active checks passing"


def get(client, eid, path):
    return client.get(f"/api/engagements/{eid}/{path}").json()


def test_pack_matches_the_live_figures(client, eid, pending_q1):
    pack = client.get(f"/api/engagements/{eid}/pack")
    assert pack.status_code == 200, pack.text
    pack = pack.json()
    assert set(pack) == {"header", "financials", "adjustments", "checks_and_issues", "working_papers",
                         "last_built_revision", "current_revision", "is_stale", "last_built_by", "last_built_at"}     # + the Step 14 freshness fields

    # 1. governance header
    hd = pack["header"]
    assert hd["entity"]["name"].startswith("9AND9 DIGISOFT") and hd["financial_year"] == "2026-27"
    assert hd["template"]["confirmed"] is False and hd["template"]["open_question"] == "Q2" and "Q2" in hd["template"]["note"]
    assert hd["governance"]["is_locked"] is True and hd["governance"]["signed_off_by"] == "Priya Partner"
    assert hd["governance"]["status"] == "SIGNED OFF AND LOCKED"

    # 2. statements and ratios
    bs, pnl, ratios = get(client, eid, "statements/balance-sheet"), get(client, eid, "statements/pnl"), get(client, eid, "ratios")
    fin = pack["financials"]
    assert fin["balance_sheet"]["verification"] == bs["check"] and bs["check"]["status"] == "BALANCED"
    assert D(fin["balance_sheet"]["total_assets"]["current"]) == D(bs["check"]["total_assets"]) == D("272641256.00")
    assert D(fin["balance_sheet"]["total_equity_and_liabilities"]["current"]) == D(bs["check"]["total_equity_and_liabilities"])
    assert D(fin["profit_and_loss"]["profit_for_period"]["current"]) == D(pnl["profit_for_period"]["current"]) == D("-481634.00")
    rev = next(l for s in pnl["sections"] for l in s["lines"] if l["code"] == "PL.REV")
    assert D(fin["profit_and_loss"]["revenue_from_operations"]["current"]) == D(rev["current"]) > 0
    assert fin["ratios"]["provisional"] is True and fin["ratios"]["open_question"] == "Q7" and "Q7" in fin["ratios"]["note"]
    assert [(r["code"], r["value"], r["status"]) for r in fin["ratios"]["items"]] == [(r["code"], r["value"], r["status"]) for r in ratios["items"]]

    # 3. adjustments (the Step 5 tests left one approved entry and its approved reversal)
    adj, reg = pack["adjustments"], get(client, eid, "adjustments")
    assert adj["counts"]["approved"] == reg["counts"]["approved"] >= 2 and adj["counts"]["draft"] == reg["counts"]["draft"] == 0
    assert adj["counts"]["total"] == len(adj["entries"]) == len(reg["entries"])
    assert adj["counts"]["reversed"] >= 1 and adj["counts"]["reversal_entries"] >= 1
    assert any(e["reverses_entry_number"] for e in adj["entries"]) and any(e["reversed_by_entry_number"] for e in adj["entries"])
    assert all(D(e["total"]) > 0 and e["lines"] for e in adj["entries"])
    assert adj["ledgers_with_net_adjustment"] == 0                                           # the reversal cancelled the entry

    # 4. checks and issues
    chk, ci = get(client, eid, "checks"), pack["checks_and_issues"]
    assert ci["checks_summary"]["text"] == chk["summary"]["text"] == "5 of 6 active checks passing"
    assert (ci["checks_summary"]["passed"], ci["checks_summary"]["total"]) == (5, 6)
    by_code = {c["code"]: c for c in ci["checks"]}
    assert [c["code"] for c in ci["checks"]] == [f"CHK-0{i}" for i in range(1, 7)]
    assert all(by_code[c]["status"] == "pass" for c in ("CHK-01", "CHK-02", "CHK-03", "CHK-04", "CHK-05"))
    assert by_code["CHK-06"]["status"] == "fail" and by_code["CHK-06"]["pending_definition"] and "Q1" in by_code["CHK-06"]["message"]
    issues = get(client, eid, "issues")["summary"]
    assert ci["issues"]["open"] == issues["open"] == 2                                          # CHK-06, and (Step 14) the signed report v1 that has no UDIN yet
    assert any("CHK-06" in x["title"] for x in ci["issues"]["open_items"]) and any(x["source"] == "udin" for x in ci["issues"]["open_items"])
    assert ci["issues"]["by_source"] == issues["by_source"]

    # 5. working paper coverage
    wp, reg = pack["working_papers"], get(client, eid, "working-papers")
    for k in ("statement_lines", "notes"):
        assert wp[k] == reg["summary"][k] and wp[k]["total"] == 30
    assert wp["total_links"] == reg["summary"]["total_links"]
    assert len(wp["uncovered"]) == 60 - wp["statement_lines"]["covered"] - wp["notes"]["covered"]

    assert client.get("/api/engagements/999999/pack").status_code == 404


def test_pages_print_support_and_favicon(client):
    for path in ("/pack", "/audit-trail"):
        r = client.get(path)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"] and "</html>" in r.text, path
        assert 'id="print-header"' in r.text                                                    # statutory header is on every page
    nav = client.get("/pack").text
    assert 'href="/pack"' in nav and 'href="/audit-trail"' in nav
    for path in ("/balance-sheet", "/pnl", "/notes", "/ratios"):
        html = client.get(path).text
        assert "data-print" in html and "Print / Save PDF" in html, path
    assert 'id="pack-print"' in client.get("/pack").text and 'id="pack-json"' in client.get("/pack").text

    fav = client.get("/favicon.ico")
    assert fav.status_code == 204 and fav.content == b""

    css = client.get("/static/css/app.css").text
    assert "@media print" in css and "@page" in css and "break-inside" in css
    printed = css.split("@media print", 1)[1]
    for hidden in (".topbar", ".tabs", ".toast", ".no-print"):
        assert hidden in printed.split("{", 1)[0] or hidden in printed, hidden
    assert ".print-header" in printed and "display: table-header-group" in printed


def test_export_logs_an_event_and_changes_nothing(client, eid, test_db_url):
    def fingerprint():
        with psycopg.connect(test_db_url, autocommit=True) as conn:
            counts = [conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in
                      ("ledgers", "ledger_balances", "ledger_mappings", "journal_entries", "journal_lines", "working_paper_links", "engagement_signoffs")]
            rev = conn.execute("SELECT data_revision FROM engagements WHERE id = %s", (eid,)).fetchone()[0]
            exported = conn.execute("SELECT count(*) FROM audit_logs WHERE event_name='AUDIT_PACK_EXPORTED' AND engagement_id=%s", (eid,)).fetchone()[0]
            return counts, rev, exported

    url = f"/api/engagements/{eid}/pack/export"
    before_counts, before_rev, before_exports = fingerprint()
    assert client.post(url, json={"format": "print"}).status_code == 401
    assert client.post(url, json={"format": "pdf"}, headers=MAKER).status_code == 400
    assert client.post("/api/engagements/999999/pack/export", json={"format": "print"}, headers=MAKER).status_code == 404
    assert client.post(url, json={"format": "print"}, headers=MAKER).status_code == 200
    r = client.post(url, json={"format": "json"}, headers=PARTNER)
    assert r.status_code == 200 and r.json() == {"logged": True, "event": "AUDIT_PACK_EXPORTED", "format": "json", "data_revision": before_rev}
    assert client.post(url, json={"format": "print"}, headers=CHECKER).status_code == 200

    after_counts, after_rev, after_exports = fingerprint()
    assert (after_counts, after_rev) == (before_counts, before_rev)                           # nothing in the locked engagement changed
    assert after_exports == before_exports + 3
    assert get(client, eid, "signoff")["is_locked"] is True

    events = [a for a in client.get(f"/api/engagements/{eid}/audit-logs?scope=events", headers=MAKER).json() if a["event_name"] == "AUDIT_PACK_EXPORTED"]
    assert {a["after_data"]["format"] for a in events} == {"print", "json"} and {a["user_name"][:5] for a in events[:3]} == {"Maya ", "Priya", "Cheta"}


def test_audit_trail_api_scopes_and_paging(client, eid):
    url = f"/api/engagements/{eid}/audit-logs"
    assert client.get(url).status_code == 401
    events = client.get(url + "?scope=events&limit=500", headers=MAKER).json()
    assert events and all(a["action"] == "EVENT" for a in events)
    everything = client.get(url + "?scope=all&limit=500", headers=MAKER).json()
    assert len(everything) > len(events)                                                         # row-level changes are included too
    for table in ("journal_entries", "ledger_balances", "engagement_signoffs", "ledger_mappings"):
        rows = client.get(url + f"?scope=all&entity_table={table}&limit=500", headers=MAKER).json()
        assert rows and {a["entity_table"] for a in rows} == {table}, table
    default = client.get(url + "?limit=500", headers=MAKER).json()                              # Step 1 behaviour is unchanged
    assert {a["entity_table"] for a in default if a["action"] != "EVENT"} <= {"ledger_mappings", "exceptions"}
    only = client.get(url + "?scope=all&entity_table=journal_entries&limit=500", headers=MAKER).json()
    assert only and {a["entity_table"] for a in only} == {"journal_entries"}
    assert all(k in everything[0] for k in ("before_data", "after_data", "changed_fields", "reason", "user_name"))

    first = client.get(url + "?scope=all&limit=5", headers=MAKER).json()
    older = client.get(url + f"?scope=all&limit=5&before_id={first[-1]['id']}", headers=MAKER).json()
    ids = [a["id"] for a in first + older]
    assert len(first) == len(older) == 5 and ids == sorted(ids, reverse=True) and len(set(ids)) == 10     # newest first, no overlap
    assert client.get(url + "?scope=bogus", headers=MAKER).status_code == 422
    assert client.get(url + "?limit=0", headers=MAKER).status_code == 422
