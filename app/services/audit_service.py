"""Business-level audit events. Row-level before/after changes are written by the database trigger."""
import json

from psycopg.types.json import Jsonb


def event(cur, engagement_id: int, event_name: str, detail: dict | None = None, entity_table: str = "engagements",
          entity_id: str | None = None, reason: str | None = None) -> None:
    cur.execute(
        """INSERT INTO audit_logs (user_id, request_id, engagement_id, entity_table, entity_id, action, event_name, after_data, reason)
           VALUES (current_setting('app.user_id')::bigint, nullif(current_setting('app.request_id', true), ''),
                   %s, %s, %s, 'EVENT', %s, %s, %s)""",
        (engagement_id, entity_table, entity_id or str(engagement_id), event_name,
         Jsonb(detail or {}, dumps=lambda o: json.dumps(o, default=str)), reason))       # Decimal/date values are stored as text
