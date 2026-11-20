"""Engagement setup that changes how the statements are presented (Q2): the entity type."""
from fastapi import HTTPException

from app.services import change_coordinator, entity, locking


def set_entity_type(cur, user: dict, engagement_id: int, entity_type: str | None) -> dict:
    """Private Limited Company or Partnership Firm. A partnership shows Partners' Capital / Current Account instead of Share capital /
    Reserves and surplus. Refused (409) once the Partner has signed off; otherwise the statements, notes, checks and pack follow at once."""
    cur.execute("SELECT entity_type FROM engagements WHERE id = %s FOR UPDATE", (engagement_id,))
    eng = cur.fetchone()
    if not eng:
        raise HTTPException(404, "Engagement not found.")
    locking.assert_unlocked(cur, engagement_id)
    if entity_type not in entity.ENTITY_TYPES:
        raise HTTPException(400, f"Entity type must be one of: {', '.join(entity.ENTITY_TYPES)}.")
    changed = eng["entity_type"] != entity_type
    if changed:
        cur.execute("UPDATE engagements SET entity_type = %s WHERE id = %s", (entity_type, engagement_id))
        change_coordinator.after_change(cur, engagement_id, "ENTITY_TYPE_CHANGED",
                                        {"from": eng["entity_type"], "to": entity_type, "changed_by": user["full_name"]})
    return {"entity_type": entity_type, "label": entity.ENTITY_LABELS[entity_type], "changed": changed}
