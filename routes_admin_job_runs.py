"""Admin Job Runs page (Phase 4).

Read-only listing of recent rows from the ``job_runs`` table, gated by
``job_runs_ui_enabled`` and the ``sync.view_logs`` permission.

The page intentionally does not expose any mutation endpoints — pause
/ resume / reschedule / "Run Now" all live on the scheduler admin page
(``/datawarehouse/database-settings``). This page is purely a window
onto the lifecycle rows that ``services.job_run_logger`` writes for
every scheduled and manual tick.
"""
import logging

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_login import login_required

from app import db
from models import Setting
from services.permissions import require_permission

logger = logging.getLogger(__name__)

admin_job_runs_bp = Blueprint(
    "admin_job_runs", __name__, url_prefix="/admin/job-runs"
)


_VALID_STATUS_FILTERS = {
    "RUNNING", "SUCCESS", "FAILED", "SKIPPED", "STALE_FAILED", "CANCELLED",
}


def _ui_enabled():
    try:
        raw = Setting.get(db.session, "job_runs_ui_enabled", "true")
    except Exception:
        return True
    return str(raw).strip().lower() in ("true", "1", "yes", "on")


@admin_job_runs_bp.route("/", methods=["GET"])
@admin_job_runs_bp.route("", methods=["GET"])
@login_required
@require_permission("sync.view_logs")
def job_runs_page():
    if not _ui_enabled():
        abort(404)

    try:
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        limit = 100
    limit = max(min(limit, 500), 10)

    job_id_filter = (request.args.get("job_id") or "").strip() or None
    status_filter = (request.args.get("status") or "").strip().upper() or None
    if status_filter and status_filter not in _VALID_STATUS_FILTERS:
        status_filter = None

    from services.job_run_logger import get_recent_runs
    # Pull a wider window when a status filter is active so the in-memory
    # filter still shows ~`limit` matching rows for rare statuses.
    fetch_limit = limit if not status_filter else min(limit * 5, 2000)
    rows = get_recent_runs(limit=fetch_limit, job_id=job_id_filter)

    if status_filter:
        rows = [r for r in rows if (r.get("status") or "").upper() == status_filter]
        rows = rows[:limit]

    distinct_job_ids = sorted({r.get("job_id") for r in rows if r.get("job_id")})

    counts = {s: 0 for s in _VALID_STATUS_FILTERS}
    for r in rows:
        s = (r.get("status") or "").upper()
        if s in counts:
            counts[s] += 1

    cleanup_enabled = False
    retention_days = 90
    try:
        cleanup_enabled = str(
            Setting.get(db.session, "job_log_cleanup_enabled", "false")
        ).strip().lower() in ("true", "1", "yes", "on")
        retention_days = int(
            Setting.get(db.session, "job_log_retention_days", "90") or "90"
        )
    except Exception as e:
        logger.warning(f"job_runs_page: could not read cleanup settings: {e}")

    return render_template(
        "admin/job_runs.html",
        rows=rows,
        limit=limit,
        job_id_filter=job_id_filter or "",
        status_filter=status_filter or "",
        distinct_job_ids=distinct_job_ids,
        valid_statuses=sorted(_VALID_STATUS_FILTERS),
        counts=counts,
        cleanup_enabled=cleanup_enabled,
        retention_days=retention_days,
    )
