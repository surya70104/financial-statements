"""Engagement lock (T-13). Once the Partner has signed off, the engagement's data is frozen.

Every service that changes trial balance, mapping, comparatives or journals calls assert_unlocked first and the caller
gets HTTP 409. The database also refuses such writes (trigger block_if_engagement_locked), so no code path can slip past.
"""
from fastapi import HTTPException

LOCK_MESSAGE = "Engagement is locked after Partner sign-off"


def is_locked(cur, engagement_id: int) -> bool:
    cur.execute("SELECT 1 FROM engagement_signoffs WHERE engagement_id = %s AND is_locked", (engagement_id,))
    return cur.fetchone() is not None


def assert_unlocked(cur, engagement_id: int) -> None:
    if is_locked(cur, engagement_id):
        raise HTTPException(409, LOCK_MESSAGE)
