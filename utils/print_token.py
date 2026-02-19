import os
from itsdangerous import URLSafeTimedSerializer

_SECRET = os.environ.get("SESSION_SECRET", "fallback-secret")
_SALT = "print-receipt"
_serializer = URLSafeTimedSerializer(_SECRET)


def make_print_token(stop_id, user_id):
    return _serializer.dumps({"stop_id": stop_id, "user_id": user_id}, salt=_SALT)


def verify_print_token(token, max_age_seconds=300):
    try:
        data = _serializer.loads(token, salt=_SALT, max_age=max_age_seconds)
        return data
    except Exception:
        return None
