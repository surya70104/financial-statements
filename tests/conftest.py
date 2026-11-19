"""Database tests run against a throw-away database named <your database>_test, created and dropped
by the test session (needs a postgres user allowed to CREATE DATABASE). Your real data is never touched."""
import os
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402  (also loads .env)


@pytest.fixture(scope="session")
def test_db_url():
    base = settings.database_url
    name = conninfo_to_dict(base)["dbname"] + "_test"
    try:
        admin = psycopg.connect(make_conninfo(base, dbname="postgres"), autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL not reachable with DATABASE_URL: {exc}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{name}"')
    url = make_conninfo(base, dbname=name)
    with psycopg.connect(url, autocommit=True) as conn:
        for f in sorted((ROOT / "migrations").glob("*.sql")):
            with conn.transaction():
                conn.execute(f.read_text(encoding="utf-8"))
    os.environ["DATABASE_URL"] = url
    yield url
    from app.db import close_pool
    close_pool()
    with psycopg.connect(make_conninfo(base, dbname="postgres"), autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def client(test_db_url):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


def pytest_collection_modifyitems(items):
    """Run the step files in step order (test_step2 ... test_step9, test_step10): plain alphabetical order would put
    test_step10 before test_step1_db. The sort is stable, so tests inside a file keep their order."""
    import re

    def key(item):
        m = re.search(r"test_step(\d+)_", item.nodeid)
        return (1, int(m.group(1))) if m else (0, 0)
    items.sort(key=key)
