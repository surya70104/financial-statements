"""Step 7 acceptance tests: working paper links on statement lines and notes (T-14).
The pilot engagement is already signed off and locked by the Step 6 tests, so these tests use their own engagement
(same client and template, FY 2027-28); locking it later also proves the lock is per engagement."""
import psycopg
import pytest

MAKER, CHECKER, PARTNER = {"X-User-Id": "2"}, {"X-User-Id": "3"}, {"X-User-Id": "4"}
LOCK_MSG = "Engagement is locked after Partner sign-off"


@pytest.fixture(scope="module")
def eid(client, test_db_url):
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.user_id','1',false)")
        return conn.execute("""INSERT INTO engagements (client_id, fy_label, period_start, period_end, engagement_type, status, template_id)
                               SELECT client_id, '2027-28', DATE '2027-04-01', DATE '2028-03-31', 'STATUTORY_AUDIT', 'planning', template_id
                                 FROM engagements ORDER BY id LIMIT 1 RETURNING id""").fetchone()[0]


def wp_body(code="WP-B1", target_type="statement_line", target_code="BS.CA.CASH", title="Bank reconciliation", **extra):
    return {"target_type": target_type, "target_code": target_code, "wp_ref_code": code, "wp_title": title, **extra}


def listing(client, eid):
    return client.get(f"/api/engagements/{eid}/working-papers").json()


def test_attach_to_line_and_note_is_stored_and_audited(client, eid, test_db_url):
    url = f"/api/engagements/{eid}/working-papers"
    empty = listing(client, eid)["summary"]
    assert empty["statement_lines"] == {"total": 30, "covered": 0, "fully_reviewed": 0} and empty["notes"]["total"] == 30 and empty["total_links"] == 0

    a = client.post(url, json=wp_body(wp_url_or_path="https://files.example.com/wp/b1.xlsx", notes="Signed bank letter"), headers=MAKER)
    assert a.status_code == 200, a.text
    n = client.post(url, json=wp_body("WP-N20", "note", "NOTE-20", "Cash and bank confirmation"), headers=CHECKER)
    assert n.status_code == 200, n.text

    got = listing(client, eid)
    line = got["by_target"]["statement_line:BS.CA.CASH"][0]
    assert (line["wp_ref_code"], line["wp_title"], line["status"], line["attached_by_name"]) == ("WP-B1", "Bank reconciliation", "attached", "Maya Maker (Preparer)")
    assert line["wp_url_or_path"] == "https://files.example.com/wp/b1.xlsx" and line["notes"] == "Signed bank letter"
    assert got["by_target"]["note:NOTE-20"][0]["wp_ref_code"] == "WP-N20"
    s = got["summary"]
    assert s["statement_lines"]["covered"] == 1 and s["notes"]["covered"] == 1 and s["total_links"] == 2 and s["unreviewed_links"] == 2
    register = {(t["target_type"], t["target_code"]): t for t in got["targets"]}
    assert len(register) == 60 and register[("note", "NOTE-20")]["link_count"] == 1 and register[("statement_line", "BS.CA.TR")]["link_count"] == 0

    names = {e["event_name"] for e in client.get(f"/api/engagements/{eid}/audit-logs", headers=MAKER).json()}
    assert "WORKING_PAPER_ATTACHED" in names
    with psycopg.connect(test_db_url, autocommit=True) as conn:                      # and the row-level trail written by the database
        assert conn.execute("SELECT count(*) FROM audit_logs WHERE entity_table='working_paper_links' AND action='INSERT' AND engagement_id=%s",
                            (eid,)).fetchone()[0] == 2

    # validation
    assert client.post(url, json=wp_body(), headers=MAKER).status_code == 409                       # same paper on the same line twice
    assert client.post(url, json=wp_body("wp-b1"), headers=MAKER).status_code == 409                # ... case-insensitively
    assert client.post(url, json=wp_body("WP-X", target_code="BS.NOPE"), headers=MAKER).status_code == 400
    assert client.post(url, json=wp_body("WP-X", "note", "NOTE-99"), headers=MAKER).status_code == 400
    assert client.post(url, json=wp_body("WP-X", target_code="BS.ASSETS.TOTAL"), headers=MAKER).status_code == 400   # totals carry no papers
    assert client.post(url, json=wp_body("  "), headers=MAKER).status_code == 400
    assert client.post(url, json=wp_body("WP-X", title=" "), headers=MAKER).status_code == 400
    assert client.post(url, json=wp_body("WP-X", wp_url_or_path="javascript:alert(1)"), headers=MAKER).status_code == 400
    assert client.post(url, json=wp_body("WP-X")).status_code == 401
    assert client.post(url, json=wp_body("WP-X", target_code="BS.CA.TR"), headers=PARTNER).status_code == 200   # all three roles may attach
    assert client.get("/api/engagements/999999/working-papers").status_code == 404


def test_review_rules(client, eid):
    url = f"/api/engagements/{eid}/working-papers"
    by = lambda ref: next(i for i in listing(client, eid)["items"] if i["wp_ref_code"] == ref)
    b1, n20 = by("WP-B1"), by("WP-N20")                                  # B1 attached by Maya (Maker); N20 by Chetan (Checker)

    assert client.post(f"{url}/{b1['id']}/review", headers=MAKER).status_code == 403          # a Maker can never review
    assert client.post(f"{url}/{b1['id']}/review").status_code == 401
    own = client.post(f"{url}/{n20['id']}/review", headers=CHECKER)                            # creator cannot self-review
    assert own.status_code == 403 and "someone else" in own.json()["detail"]
    assert by("WP-B1")["status"] == "attached" and by("WP-N20")["status"] == "attached"

    ok = client.post(f"{url}/{b1['id']}/review", headers=CHECKER)                              # Checker reviews the Maker's paper
    assert ok.status_code == 200 and ok.json()["status"] == "reviewed"
    done = by("WP-B1")
    assert done["status"] == "reviewed" and done["reviewed_by_name"].startswith("Chetan") and done["reviewed_at"]
    assert client.post(f"{url}/{n20['id']}/review", headers=PARTNER).status_code == 200        # Partner reviews the Checker's paper
    assert client.post(f"{url}/{b1['id']}/review", headers=PARTNER).status_code == 409         # already reviewed
    assert client.post(f"{url}/999999/review", headers=PARTNER).status_code == 404
    s = listing(client, eid)["summary"]
    assert s["reviewed_links"] == 2 and s["statement_lines"]["fully_reviewed"] == 1 and s["notes"]["fully_reviewed"] == 1
    assert "WORKING_PAPER_REVIEWED" in {e["event_name"] for e in client.get(f"/api/engagements/{eid}/audit-logs", headers=MAKER).json()}


def test_delete_rules(client, eid, test_db_url):
    url = f"/api/engagements/{eid}/working-papers"
    items = {i["wp_ref_code"]: i for i in listing(client, eid)["items"]}
    reviewed, unreviewed = items["WP-B1"], items["WP-X"] if "WP-X" in items else None
    assert reviewed["status"] == "reviewed" and unreviewed and unreviewed["status"] == "attached"

    r = client.delete(f"{url}/{reviewed['id']}", headers=MAKER)
    assert r.status_code == 409 and "reviewed" in r.json()["detail"]                           # reviewed: cannot be removed
    assert client.delete(f"{url}/{unreviewed['id']}").status_code == 401
    assert client.delete(f"{url}/999999", headers=MAKER).status_code == 404
    with psycopg.connect(test_db_url, autocommit=True) as conn:                                # the database enforces it too
        conn.execute("SELECT set_config('app.user_id','2',false)")
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            conn.execute("DELETE FROM working_paper_links WHERE id = %s", (reviewed["id"],))
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            conn.execute("UPDATE working_paper_links SET wp_title = 'tampered' WHERE id = %s", (reviewed["id"],))

    assert client.delete(f"{url}/{unreviewed['id']}", headers=MAKER).status_code == 200       # unreviewed: removable
    assert all(i["id"] != unreviewed["id"] for i in listing(client, eid)["items"])
    assert "WORKING_PAPER_REMOVED" in {e["event_name"] for e in client.get(f"/api/engagements/{eid}/audit-logs", headers=MAKER).json()}


def test_everything_returns_409_once_the_engagement_is_locked(client, eid, test_db_url):
    url = f"/api/engagements/{eid}/working-papers"
    fresh = client.post(url, json=wp_body("WP-C1", target_code="BS.CA.OTH"), headers=MAKER).json()      # an unreviewed link to test review/remove on
    other = client.post(url, json=wp_body("WP-C2", target_code="BS.CA.INV"), headers=MAKER).json()
    with psycopg.connect(test_db_url, autocommit=True) as conn:                                          # the Partner signs off (as Step 6 does)
        conn.execute("SELECT set_config('app.user_id','4',false)")
        conn.execute("INSERT INTO engagement_signoffs (engagement_id, signed_off_by, snapshot_json) VALUES (%s, 4, '{}')", (eid,))

    for resp in (client.post(url, json=wp_body("WP-L1", target_code="BS.CA.TR"), headers=MAKER),
                 client.post(url, json=wp_body("WP-L2", "note", "NOTE-5"), headers=PARTNER),
                 client.post(f"{url}/{fresh['id']}/review", headers=CHECKER),
                 client.post(f"{url}/{fresh['id']}/review", headers=PARTNER),
                 client.delete(f"{url}/{other['id']}", headers=MAKER),
                 client.delete(f"{url}/{other['id']}", headers=PARTNER)):
        assert resp.status_code == 409 and resp.json()["detail"] == LOCK_MSG, resp.text
    assert client.get(url).status_code == 200                                                            # reading still works
    assert {i["wp_ref_code"] for i in listing(client, eid)["items"]} >= {"WP-C1", "WP-C2"}               # nothing changed

    with psycopg.connect(test_db_url, autocommit=True) as conn:                                          # the database says the same
        conn.execute("SELECT set_config('app.user_id','2',false)")
        with pytest.raises(psycopg.errors.RaiseException, match="locked"):
            conn.execute("INSERT INTO working_paper_links (engagement_id, target_type, target_code, wp_ref_code, wp_title, attached_by) "
                         "VALUES (%s,'statement_line','BS.CA.TR','WP-Z','z',2)", (eid,))
        with pytest.raises(psycopg.errors.RaiseException, match="locked"):
            conn.execute("DELETE FROM working_paper_links WHERE id = %s", (other["id"],))
        with pytest.raises(psycopg.errors.RaiseException, match="locked"):
            conn.execute("UPDATE working_paper_links SET status='reviewed', reviewed_by=3, reviewed_at=now() WHERE id = %s", (fresh["id"],))
