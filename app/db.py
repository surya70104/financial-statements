"""Connection pool and the one transaction helper every write goes through."""
import uuid
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(settings.database_url, min_size=1, max_size=8,
                               kwargs={"row_factory": dict_row}, open=True)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def tx(user_id: int | None = None):
    """One transaction: commit on success, rollback on any exception.

    When user_id is given it is published to the database (app.user_id) so the audit trigger
    attributes every row change to that user. Writes without a user are refused by the trigger.
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            if user_id is not None:
                cur.execute("SELECT set_config('app.user_id', %s, true), set_config('app.request_id', %s, true)",
                            (str(user_id), uuid.uuid4().hex))
            yield cur
