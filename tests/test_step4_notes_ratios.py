"""Step 4 acceptance tests (T-09 Notes, T-10 Ratios, CHK-03 on) against a real PostgreSQL."""
from decimal import Decimal
from pathlib import Path

import pytest

D = Decimal
ROOT = Path(__file__).resolve().parent.parent
PILOT = (ROOT / "sample_data" / "9and9_pilot_tb_76_ledgers.csv").read_bytes()
MAKER = {"X-User-Id": "2"}


@pytest.fixture(scope="module")
def eid(client):
    return client.get("/api/engagements").json()[0]["id"]


@pytest.fixture(scope="module")
def mapped(client, eid):
    assert client.post(f"/api/engagements/{eid}/tb/import", files={"file": ("tb.csv", PILOT, "text/csv")}, headers=MAKER).status_code == 200
    assert client.post(f"/api/engagements/{eid}/mappings/auto-map", headers=MAKER).status_code == 200


def lines_of(bs, pnl):
    out = {}
    for p in bs["parts"]:
        for s in p["sections"]:
            out.update({l["code"]: l for l in s["lines"]})
    for s in pnl["sections"]:
        out.update({l["code"]: l for l in s["lines"]})
    return out


def test_every_note_ties_to_its_statement_line(client, eid, mapped):
    notes = client.get(f"/api/engagements/{eid}/notes").json()
    bs = client.get(f"/api/engagements/{eid}/statements/balance-sheet").json()
    pnl = client.get(f"/api/engagements/{eid}/statements/pnl").json()
    lines = lines_of(bs, pnl)
    assert [n["note_no"] for n in notes["notes"]] == [str(i) for i in range(1, 31)]
    for n in notes["notes"]:
        assert n["ties"] is True and D(n["difference"]) == 0, n["note_no"]
        assert D(n["note_total"]) == D(lines[n["line_code"]]["current"])          # exact rupee, from the statements
        assert D(n["note_total"]) == sum(D(l["closing"]) for l in n["ledgers"]) + sum(D(e["closing"]) for e in n["extras"])
        for l in n["ledgers"]:                                                      # opening + movement = closing
            assert D(l["opening"]) + D(l["movement"]) == D(l["closing"])
    assert notes["tie_summary"]["tying"] == 30 and notes["tie_summary"]["failing"] == []
    assert sum(len(n["ledgers"]) for n in notes["notes"]) == 76                    # every ledger sits in exactly one note

    n2 = next(n for n in notes["notes"] if n["note_no"] == "2")                     # Reserves: profit/(loss) + other movements inside
    assert [D(e["closing"]) for e in n2["extras"]] == [D("-481634.00"), D("0.00")]
    assert D(n2["note_total"]) == D(lines["BS.EQ.RS"]["current"])
    assert D(next(n for n in notes["notes"] if n["note_no"] == "11")["note_total"]) == D(lines["BS.NCA.PPE"]["current"])


def test_chk03_is_enabled_and_passes(client, eid, mapped):
    r = client.post(f"/api/engagements/{eid}/checks/run", headers=MAKER).json()
    s, items = r["summary"], {i["code"]: i for i in r["items"]}
    assert items["CHK-03"]["is_enabled"] is True and items["CHK-03"]["status"] == "pass"
    assert D(items["CHK-03"]["variance"]) == 0
    assert (s["passed"], s["total"], s["text"]) == (5, 6, "5 of 6 active checks passing")
    assert [c for c, i in items.items() if i["status"] != "pass"] == ["CHK-06"]
    assert s["notes"] == ["CHK-06 awaits Q1"]


def test_chk03_fails_with_exact_difference_when_a_note_is_missing(client, eid, mapped, test_db_url):
    import psycopg
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.user_id','2',false)")
        # map one ledger to its line but WITHOUT a note: the line includes it, the note cannot
        row = conn.execute("""SELECT m.id, m.note_id FROM ledger_mappings m JOIN ledgers l ON l.id = m.ledger_id
                              WHERE l.name = 'Cash in Hand'""").fetchone()
        bal = conn.execute("SELECT closing_balance FROM ledger_balances lb JOIN ledgers l ON l.id = lb.ledger_id WHERE l.name = 'Cash in Hand'").fetchone()[0]
        conn.execute("UPDATE ledger_mappings SET note_id = NULL WHERE id = %s", (row[0],))
        try:
            notes = client.get(f"/api/engagements/{eid}/notes").json()
            assert [n["note_no"] for n in notes["notes"] if not n["ties"]] == ["20"]
            r = client.post(f"/api/engagements/{eid}/checks/run", headers=MAKER).json()
            chk3 = next(i for i in r["items"] if i["code"] == "CHK-03")
        finally:
            conn.execute("UPDATE ledger_mappings SET note_id = %s WHERE id = %s", (row[1], row[0]))
    assert chk3["status"] == "fail" and D(chk3["variance"]) == abs(D(bal))
    assert chk3["detail"]["failing_notes"][0]["note_no"] == "20"
    r = client.post(f"/api/engagements/{eid}/checks/run", headers=MAKER).json()
    assert next(i for i in r["items"] if i["code"] == "CHK-03")["status"] == "pass"


def test_ratios_come_from_statement_figures(client, eid, mapped):
    ratios = client.get(f"/api/engagements/{eid}/ratios").json()
    bs = client.get(f"/api/engagements/{eid}/statements/balance-sheet").json()
    pnl = client.get(f"/api/engagements/{eid}/statements/pnl").json()
    lines = lines_of(bs, pnl)
    cur = lambda c: D(lines[c]["current"])
    by = {i["code"]: i for i in ratios["items"]}
    assert ratios["provisional"] is True and len(by) == 9 and all(i["is_provisional"] for i in by.values())
    assert [i["name"] for i in ratios["items"]][0] == "Current Ratio" and all(i["formula"] for i in ratios["items"])

    cr = by["R-01"]
    assert D(cr["numerator"]) == cur("BS.CA.TOTAL") and D(cr["denominator"]) == cur("BS.CL.TOTAL")
    assert D(cr["value"]) == (cur("BS.CA.TOTAL") / cur("BS.CL.TOTAL")).quantize(D("0.01"))
    npr = by["R-08"]                                                                 # net loss => negative %
    assert D(npr["numerator"]) == D("-481634.00") and D(npr["denominator"]) == cur("PL.REV")
    assert D(npr["value"]) == (D("-481634.00") / cur("PL.REV") * 100).quantize(D("0.01")) and npr["unit"] == "%"
    de = by["R-02"]
    assert D(de["numerator"]) == cur("BS.NCL.BORR") + cur("BS.CL.BORR") and D(de["denominator"]) == cur("BS.EQ.TOTAL")
    roe = by["R-04"]                                                                 # no comparatives => closing equity, said so
    assert D(roe["denominator"]) == cur("BS.EQ.TOTAL") and "no comparative" in roe["denominator_label"]


def test_zero_denominator_gives_na_not_a_crash(client, eid, mapped):
    ratios = client.get(f"/api/engagements/{eid}/ratios").json()
    for i in ratios["items"]:
        if D(i["denominator"]) == 0:
            assert i["status"] == "na" and i["value"] is None and "denominator" in i["explanation"], i["code"]
        else:
            assert i["status"] == "ok" and i["value"] is not None
    # force zero denominators: with no ledgers mapped, every statement figure is 0 and the endpoint still answers
    m = client.get(f"/api/engagements/{eid}/mappings").json()
    for l in m["ledgers"]:
        client.delete(f"/api/engagements/{eid}/mappings/{l['ledger_id']}", headers=MAKER)
    empty = client.get(f"/api/engagements/{eid}/ratios")
    assert empty.status_code == 200
    assert all(i["status"] == "na" and i["value"] is None for i in empty.json()["items"])
    assert client.get(f"/api/engagements/{eid}/notes").status_code == 200
    assert client.post(f"/api/engagements/{eid}/mappings/auto-map", headers=MAKER).status_code == 200      # leave data mapped
