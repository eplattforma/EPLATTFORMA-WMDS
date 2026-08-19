"""
activity_routes.py — Flask blueprint for the gapless picker timeline

Register:
    from activity_service import ActivityService
    from activity_routes import make_activity_blueprint

    activity = ActivityService(get_connection)      # see ADAPTERS in the service
    app.register_blueprint(
        make_activity_blueprint(activity, current_user=my_current_user),
        url_prefix="/api/activity",
    )

`current_user` must be a zero-arg callable returning a dict with at least
`username` and `role`, or None when unauthenticated. Wire it to whatever your
app already uses (flask_login.current_user, session, a JWT decoder...).

DELETE FROM YOUR CODEBASE when adopting this:
  * every `role == 'picker'` / `role != 'picker'` check used to gate tracking
  * any hardcoded DEDICATED_PICKERS list
Replace with: activity.tracking_enabled(username)

`role` still appears below, but ONLY to gate supervisor/admin endpoints —
never to decide who gets tracked.
"""

from __future__ import annotations

import re
from functools import wraps
from typing import Any, Callable, Optional

from flask import Blueprint, jsonify, request

from activity_service import (
    DECLARABLE_STATES,
    SUPERVISOR_ROLES,
    ActivityError,
    ActivityService,
)

_UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


def make_activity_blueprint(
    activity: ActivityService,
    current_user: Callable[[], Optional[dict]],
    name: str = "activity",
) -> Blueprint:
    bp = Blueprint(name, __name__)

    # -- helpers -----------------------------------------------------------

    class _HttpError(ActivityError):
        def __init__(self, message: str, status: int = 400, code: str = "BAD_REQUEST"):
            super().__init__(message)
            self.status = status
            self.code = code

    def _user() -> dict:
        u = current_user()
        if not u or not u.get("username"):
            raise _HttpError("Unauthenticated", 401, "UNAUTHENTICATED")
        return u

    def _me() -> str:
        return _user()["username"]

    def _supervisor() -> str:
        u = _user()
        if u.get("role") not in SUPERVISOR_ROLES:
            raise _HttpError("Supervisor role required", 403, "FORBIDDEN")
        return u["username"]

    def _body() -> dict:
        return request.get_json(silent=True) or {}

    def _action_id() -> str:
        v = _body().get("action_id")
        if not isinstance(v, str) or not _UUID_RE.match(v):
            raise _HttpError(
                "action_id (UUID) is required — it is what makes retries safe",
                400, "MISSING_ACTION_ID")
        return v

    def _shift_id() -> int:
        v = _body().get("shift_id")
        try:
            return int(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise _HttpError("shift_id (integer) is required", 400, "MISSING_SHIFT_ID")

    def route(rule: str, **opts: Any):
        """Wraps a handler so ActivityError subclasses become clean JSON."""
        def decorator(fn):
            @wraps(fn)
            def inner(*a, **kw):
                try:
                    return fn(*a, **kw)
                except ActivityError as e:
                    payload = {"error": str(e), "code": e.code}
                    if e.code == "STALE_SEGMENT":
                        # The client must re-fetch /session, not retry blindly.
                        payload["action"] = "resync"
                    return jsonify(payload), e.status
            bp.add_url_rule(rule, view_func=inner, **opts)
            return inner
        return decorator

    # -- session -----------------------------------------------------------

    @route("/session", methods=["GET"])
    def session():
        """Client calls this on load. `tracking_enabled` decides whether the
        shift controls render at all."""
        username = _me()
        shift = activity.current_shift(username)
        return jsonify({
            "username": username,
            "tracking_enabled": activity.tracking_enabled(username),
            "shift": shift.to_dict() if shift else None,
            "declarable": list(DECLARABLE_STATES),
        })

    @route("/check-in", methods=["POST"])
    def check_in():
        shift = activity.check_in(_me(), _body().get("coordinates"))
        # prompt_activity: show the activity picker IMMEDIATELY. Do not assume
        # an order is waiting — if the queue is empty, POST /awaiting-order.
        return jsonify({"shift": shift.to_dict(), "prompt_activity": True}), 201

    @route("/check-out", methods=["POST"])
    def check_out():
        out = activity.check_out(_shift_id(), _body().get("coordinates"))
        return jsonify(out)

    @route("/heartbeat", methods=["POST"])
    def heartbeat():
        activity.heartbeat(_shift_id())
        return jsonify({"ok": True})

    # -- transitions -------------------------------------------------------

    @route("/transition", methods=["POST"])
    def transition():
        b = _body()
        segment_id = activity.declare(
            shift_id=_shift_id(),
            username=_me(),
            state=b.get("state"),
            action_id=_action_id(),
            expected_open_segment_id=b.get("expected_open_segment_id"),
        )
        return jsonify({"segment_id": segment_id})

    @route("/packing-complete", methods=["POST"])
    def packing_complete():
        segment_id = activity.packing_complete(_shift_id(), _me(), _action_id())
        # Time is already owned server-side; the modal only labels it.
        return jsonify({"segment_id": segment_id, "prompt_activity": True})

    @route("/awaiting-order", methods=["POST"])
    def awaiting_order():
        segment_id = activity.awaiting_order(_shift_id(), _me(), _action_id())
        return jsonify({"segment_id": segment_id})

    # -- resolution --------------------------------------------------------

    @route("/unresolved/<int:shift_id>", methods=["GET"])
    def unresolved(shift_id: int):
        return jsonify(_serialise(activity.unresolved_for_shift(shift_id)))

    @route("/segment/<int:segment_id>/classify", methods=["POST"])
    def classify(segment_id: int):
        activity.classify(segment_id, _body().get("state"), _me())
        return jsonify({"ok": True})

    @route("/segment/<int:segment_id>/decline", methods=["POST"])
    def decline(segment_id: int):
        """'Can't recall' — escalates to the supervisor, does NOT close."""
        activity.decline(segment_id, _me())
        return jsonify({"ok": True, "escalated": True})

    # -- supervisor --------------------------------------------------------

    @route("/review-queue", methods=["GET"])
    def review_queue():
        _supervisor()
        return jsonify(_serialise(activity.review_queue()))

    @route("/closure-blockers", methods=["GET"])
    def closure_blockers():
        _supervisor()
        return jsonify(_serialise(activity.closure_blockers()))

    @route("/segment/<int:segment_id>/resolve", methods=["POST"])
    def resolve(segment_id: int):
        by = _supervisor()
        b = _body()
        # state=None is an explicit write-off, so it must bypass DECLARABLE
        # validation; anything else is validated in the service.
        activity.supervisor_resolve(segment_id, b.get("state"), by, b.get("note"))
        return jsonify({"ok": True})

    # -- admin: per-user roster -------------------------------------------

    @route("/roster", methods=["GET"])
    def roster():
        _supervisor()
        return jsonify(_serialise(activity.roster()))

    @route("/roster/<username>", methods=["POST"])
    def set_roster(username: str):
        by = _supervisor()
        enabled = _body().get("enabled")
        if not isinstance(enabled, bool):
            raise _HttpError("enabled (boolean) required", 400, "BAD_REQUEST")
        activity.set_tracking(username, enabled, by)
        return jsonify({"ok": True})

    @route("/master-switch", methods=["POST", "GET"])
    def master_switch():
        _supervisor()
        if request.method == "GET":
            return jsonify({"enabled": activity.master_switch_state()})
        activity.master_switch(bool(_body().get("enabled")))
        return jsonify({"ok": True, "enabled": activity.master_switch_state()})

    # -- health ------------------------------------------------------------

    @route("/health", methods=["GET"])
    def health():
        """Wire to monitoring. 500 means an invariant broke — page someone.
        Should be boring forever."""
        h = activity.integrity_check()
        return jsonify(_serialise_obj(h)), (200 if h["healthy"] else 500)

    return bp


# ---------------------------------------------------------------------------
# JSON serialisation — dates and Decimals are not JSON-native
# ---------------------------------------------------------------------------

def _scalar(v: Any) -> Any:
    if hasattr(v, "isoformat"):          # date / datetime
        return v.isoformat()
    if hasattr(v, "total_seconds"):      # timedelta (vw_supervisor_review_queue.age)
        return v.total_seconds()
    if type(v).__name__ == "Decimal":    # numeric columns (minutes, pct)
        return float(v)
    return v


def _serialise(rows: list[dict]) -> list[dict]:
    return [{k: _scalar(v) for k, v in row.items()} for row in rows]


def _serialise_obj(obj: dict) -> dict:
    return {
        k: _serialise(v) if isinstance(v, list) else _scalar(v)
        for k, v in obj.items()
    }


# ---------------------------------------------------------------------------
# CRON — register with APScheduler, Celery beat, or Replit Scheduled Deployments
# ---------------------------------------------------------------------------
#
#   from apscheduler.schedulers.background import BackgroundScheduler
#
#   sched = BackgroundScheduler()
#   sched.add_job(activity.reap_stale_shifts, "interval", minutes=5)
#   sched.add_job(activity.auto_write_off,    "cron", hour=2)
#   sched.start()
#
# reap_stale_shifts() recovers crashed devices and never-checked-out shifts.
# auto_write_off() is the 7-day backstop so unresolved blocks cannot block
# reporting indefinitely. Neither is optional: without the reaper, a dead
# tablet leaves a segment open forever.
# ---------------------------------------------------------------------------
