"""JSON API for the optional PostgreSQL activity timeline."""

from __future__ import annotations

import re
from functools import wraps
from typing import Any

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from services.activity_service import (
    DECLARABLE_STATES,
    SUPERVISOR_ROLES,
    ActivityError,
)
from services.activity_tracking import activity_schema_available, get_activity_service

activity_bp = Blueprint("activity", __name__, url_prefix="/api/activity")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


class _HttpError(ActivityError):
    def __init__(self, message: str, status: int = 400, code: str = "BAD_REQUEST"):
        super().__init__(message)
        self.status = status
        self.code = code


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _shift_id() -> int:
    try:
        return int(_body().get("shift_id"))
    except (TypeError, ValueError) as exc:
        raise _HttpError("shift_id (integer) is required", code="MISSING_SHIFT_ID") from exc


def _action_id() -> str:
    action_id = _body().get("action_id")
    if not isinstance(action_id, str) or not _UUID_RE.fullmatch(action_id):
        raise _HttpError(
            "action_id (UUID) is required — it makes retries safe",
            code="MISSING_ACTION_ID",
        )
    return action_id


def _scalar(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "total_seconds"):
        return value.total_seconds()
    if type(value).__name__ == "Decimal":
        return float(value)
    return value


def _serialise(value: Any) -> Any:
    if isinstance(value, list):
        return [{key: _scalar(item) for key, item in row.items()} for row in value]
    if isinstance(value, dict):
        return {
            key: _serialise(item) if isinstance(item, (list, dict)) else _scalar(item)
            for key, item in value.items()
        }
    return _scalar(value)


def _route(rule: str, **options: Any):
    def decorate(function):
        @wraps(function)
        @login_required
        def wrapped(*args, **kwargs):
            if not activity_schema_available():
                return jsonify({"error": "Activity tracking is not installed", "code": "NOT_INSTALLED"}), 503
            try:
                return function(*args, **kwargs)
            except ActivityError as exc:
                payload = {"error": str(exc), "code": exc.code}
                if exc.code == "STALE_SEGMENT":
                    payload["action"] = "resync"
                return jsonify(payload), exc.status

        activity_bp.add_url_rule(rule, view_func=wrapped, **options)
        return wrapped

    return decorate


def _supervisor() -> str:
    if current_user.role not in SUPERVISOR_ROLES:
        raise _HttpError("Supervisor role required", status=403, code="FORBIDDEN")
    return current_user.username


@_route("/session", methods=["GET"])
def session_state():
    service = get_activity_service()
    shift = service.current_shift(current_user.username)
    return jsonify(
        {
            "username": current_user.username,
            "tracking_enabled": service.tracking_enabled(current_user.username),
            "shift": shift.to_dict() if shift else None,
            "declarable": list(DECLARABLE_STATES),
        }
    )


@_route("/check-in", methods=["POST"])
def check_in():
    shift = get_activity_service().check_in(
        current_user.username, _body().get("coordinates")
    )
    return jsonify({"shift": shift.to_dict(), "prompt_activity": True}), 201


@_route("/check-out", methods=["POST"])
def check_out():
    output = get_activity_service().check_out(
        _shift_id(), current_user.username, _body().get("coordinates")
    )
    return jsonify(_serialise(output))


@_route("/heartbeat", methods=["POST"])
def heartbeat():
    get_activity_service().heartbeat(_shift_id(), current_user.username)
    return jsonify({"ok": True})


@_route("/transition", methods=["POST"])
def transition():
    body = _body()
    segment_id = get_activity_service().declare(
        _shift_id(),
        current_user.username,
        body.get("state"),
        _action_id(),
        body.get("expected_open_segment_id"),
    )
    return jsonify({"segment_id": segment_id})


@_route("/packing-complete", methods=["POST"])
def packing_complete():
    segment_id = get_activity_service().packing_complete(
        _shift_id(), current_user.username, _action_id()
    )
    return jsonify({"segment_id": segment_id, "prompt_activity": True})


@_route("/awaiting-order", methods=["POST"])
def awaiting_order():
    segment_id = get_activity_service().awaiting_order(
        _shift_id(), current_user.username, _action_id()
    )
    return jsonify({"segment_id": segment_id})


@_route("/unresolved/<int:shift_id>", methods=["GET"])
def unresolved(shift_id: int):
    return jsonify(
        _serialise(get_activity_service().unresolved_for_shift(shift_id, current_user.username))
    )


@_route("/segment/<int:segment_id>/classify", methods=["POST"])
def classify(segment_id: int):
    get_activity_service().classify(
        segment_id, _body().get("state"), current_user.username
    )
    return jsonify({"ok": True})


@_route("/segment/<int:segment_id>/decline", methods=["POST"])
def decline(segment_id: int):
    get_activity_service().decline(segment_id, current_user.username)
    return jsonify({"ok": True, "escalated": True})


@_route("/review-queue", methods=["GET"])
def review_queue():
    _supervisor()
    return jsonify(_serialise(get_activity_service().review_queue()))


@_route("/closure-blockers", methods=["GET"])
def closure_blockers():
    _supervisor()
    return jsonify(_serialise(get_activity_service().closure_blockers()))


@_route("/segment/<int:segment_id>/resolve", methods=["POST"])
def resolve(segment_id: int):
    service = get_activity_service()
    body = _body()
    service.supervisor_resolve(segment_id, body.get("state"), _supervisor(), body.get("note"))
    return jsonify({"ok": True})


@_route("/roster", methods=["GET"])
def roster():
    _supervisor()
    return jsonify(_serialise(get_activity_service().roster()))


@_route("/roster/<username>", methods=["POST"])
def set_roster(username: str):
    enabled = _body().get("enabled")
    if not isinstance(enabled, bool):
        raise _HttpError("enabled (boolean) is required")
    get_activity_service().set_tracking(username, enabled, _supervisor())
    return jsonify({"ok": True})


@_route("/master-switch", methods=["GET", "POST"])
def master_switch():
    _supervisor()
    service = get_activity_service()
    if request.method == "GET":
        return jsonify({"enabled": service.master_switch_state()})
    enabled = _body().get("enabled")
    if not isinstance(enabled, bool):
        raise _HttpError("enabled (boolean) is required")
    service.master_switch(enabled)
    return jsonify({"ok": True, "enabled": service.master_switch_state()})


@_route("/health", methods=["GET"])
def health():
    result = get_activity_service().integrity_check()
    return jsonify(_serialise(result)), (200 if result["healthy"] else 500)