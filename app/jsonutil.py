"""JSON responses that keep money exact: Decimal is serialised as a string, never a float."""
import json
from datetime import date, datetime
from decimal import Decimal

from fastapi.responses import Response


def _default(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    raise TypeError(f"Cannot serialise {type(o).__name__}")


def json_response(data, status: int = 200) -> Response:
    return Response(json.dumps(data, default=_default), status_code=status, media_type="application/json")
