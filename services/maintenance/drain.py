"""Phase 4: Drain mode helpers.

Drain mode is operator-controlled via the ``maintenance_mode`` setting.
When ``maintenance_mode = 'draining'``:

  * New batch creation is blocked for non-admin users (callers check
    ``is_creation_allowed_for(user)``).
  * Active pickers see a banner via ``get_drain_banner()`` so they
    finish work in progress.
  * Batches that have been idle longer than ``DRAIN_TIMEOUT_MINUTES``
    (default 30) are force-paused by ``force_pause_stuck_batches()``.

This module is safe to import from any request handler: every function
catches its own exceptions and returns a safe default rather than
raising into the caller.
"""
import logging
from datetime import timedelta

from sqlalchemy import or_

from app import db
from models import ActivityLog, BatchPickingSession, Setting
from services import batch_status
from timezone_utils import get_utc_now

logger = logging.getLogger(__name__)


DRAIN_TIMEOUT_MINUTES = 30
NORMAL = "normal"
DRAINING = "draining"


def get_mode():
    """Return the current ``maintenance_mode`` value (default 'normal')."""
    try:
        return (Setting.get(db.session, "maintenance_mode", NORMAL) or NORMAL).strip().lower()
    except Exception:
        return NORMAL


def is_draining():
    return get_mode() == DRAINING


def set_mode(new_mode, actor):
    """Flip ``maintenance_mode`` and write an activity-log entry.

    Allowed values: ``normal``, ``draining``.
    """
    new_mode = (new_mode or "").strip().lower()
    if new_mode not in (NORMAL, DRAINING):
        raise ValueError(f"set_mode: invalid mode '{new_mode}'")
    try:
        Setting.set(db.session, "maintenance_mode", new_mode)
        db.session.add(ActivityLog(
            picker_username=actor,
            activity_type="maintenance.mode_changed",
            details=f"maintenance_mode set to '{new_mode}' by {actor}",
        ))
        db.session.commit()
        logger.info(f"drain: maintenance_mode -> {new_mode} by {actor}")
        return new_mode
    except Exception:
        db.session.rollback()
        raise


def is_creation_allowed_for(user):
    """Non-admins are blocked from creating new batches while draining."""
    if not is_draining():
        return True
    role = getattr(user, "role", None)
    return role == "admin"


def get_drain_banner():
    """Return banner text to surface in picker UI when draining (or '' )."""
    if not is_draining():
        return ""
    return (
        "Maintenance mode: the system is draining. Please finish your current "
        "batch — new batches are paused. Active pickers will be force-paused "
        f"after {DRAIN_TIMEOUT_MINUTES} minutes of inactivity."
    )


def force_pause_stuck_batches(timeout_minutes=None):
    """Pause active batches idle longer than ``timeout_minutes``.

    Idle = ``last_activity_at`` older than the cutoff (or NULL with
    ``created_at`` older than the cutoff as fallback). Returns a summary
    dict for the admin page.
    """
    if not is_draining():
        return {"paused": 0, "checked": 0, "skipped_not_draining": True}

    timeout_minutes = timeout_minutes or DRAIN_TIMEOUT_MINUTES
    cutoff = get_utc_now() - timedelta(minutes=timeout_minutes)

    try:
        active = db.session.query(BatchPickingSession).filter(
            BatchPickingSession.status.in_(["Created", "In Progress", "picking", "Active"])
        ).all()

        paused = 0
        for b in active:
            last = getattr(b, "last_activity_at", None) or b.created_at
            if last is None or last < cutoff:
                b.status = "Paused"
                try:
                    b.last_activity_at = get_utc_now()
                except Exception:
                    pass
                db.session.add(ActivityLog(
                    picker_username="system",
                    activity_type="batch.force_paused",
                    details=(
                        f"Batch #{b.id} ({b.batch_number or b.name}) force-paused by "
                        f"drain workflow after >{timeout_minutes}min idle "
                        f"(last_activity={last})"
                    ),
                ))
                paused += 1
        if paused:
            db.session.commit()
        return {
            "paused": paused,
            "checked": len(active),
            "timeout_minutes": timeout_minutes,
            "cutoff_utc": cutoff.isoformat(),
        }
    except Exception as e:
        logger.warning(f"drain.force_pause_stuck_batches failed: {e}")
        db.session.rollback()
        return {"paused": 0, "checked": 0, "error": str(e)[:200]}
