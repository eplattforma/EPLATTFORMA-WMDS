"""Admin Job Runs page (Phase 4).

Read-only listing of recent rows from the ``job_runs`` table. Per the
Phase 4 brief, the page is gated solely by ``@require_permission(
'sync.view_logs')`` — there is no separate UI kill-switch (the Phase 1
``job_runs_ui_enabled`` flag is intentionally not consulted here so
the new page cannot be hidden out from under operators trying to
investigate a live incident).

The page intentionally does not expose any mutation endpoints — pause /
resume / reschedule / "Run Now" all live on the scheduler admin page
(``/datawarehouse/database-settings``). This page is purely a window
onto the lifecycle rows that ``services.job_run_logger`` writes for
every scheduled and manual tick.
"""
import json
import logging

from flask import Blueprint, abort, render_template, request
from flask_login import login_required

from services.job_run_logger import (
    get_distinct_job_ids,
    get_recent_runs,
    get_run_by_id,
)
from services.permissions import require_permission

logger = logging.getLogger(__name__)

admin_job_runs_bp = Blueprint(
    "admin_job_runs", __name__, url_prefix="/admin/job-runs"
)


VALID_STATUSES = (
    "RUNNING", "SUCCESS", "FAILED", "SKIPPED", "STALE_FAILED", "CANCELLED",
)


def _parse_int(raw, default, lo=None, hi=None):
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    if lo is not None and v < lo:
        v = lo
    if hi is not None and v > hi:
        v = hi
    return v


def _selected_statuses():
    """Return the multi-select status filter as a deduped uppercase tuple."""
    raw = request.args.getlist("status")
    out = []
    for s in raw:
        u = (s or "").strip().upper()
        if u in VALID_STATUSES and u not in out:
            out.append(u)
    return tuple(out)


@admin_job_runs_bp.route("/", methods=["GET"])
@admin_job_runs_bp.route("", methods=["GET"])
@login_required
@require_permission("sync.view_logs")
def job_runs_page():
    limit = _parse_int(request.args.get("limit"), default=200, lo=10, hi=500)
    hours = _parse_int(request.args.get("hours"), default=24, lo=0, hi=24 * 30)
    job_id_filter = (request.args.get("job_id") or "").strip() or None
    statuses = _selected_statuses()

    rows = get_recent_runs(
        limit=limit,
        job_id=job_id_filter,
        statuses=statuses or None,
        hours=hours if hours > 0 else None,
    )

    distinct_job_ids = get_distinct_job_ids()

    counts = {s: 0 for s in VALID_STATUSES}
    for r in rows:
        s = (r.get("status") or "").upper()
        if s in counts:
            counts[s] += 1

    return render_template(
        "admin/job_runs.html",
        rows=rows,
        limit=limit,
        hours=hours,
        job_id_filter=job_id_filter or "",
        selected_statuses=set(statuses),
        distinct_job_ids=distinct_job_ids,
        valid_statuses=VALID_STATUSES,
        counts=counts,
    )


@admin_job_runs_bp.route("/<int:run_id>", methods=["GET"])
@login_required
@require_permission("sync.view_logs")
def job_run_detail(run_id):
    row = get_run_by_id(run_id)
    if row is None:
        abort(404)

    def _pretty(val):
        if val is None:
            return None
        if isinstance(val, (dict, list)):
            try:
                return json.dumps(val, indent=2, sort_keys=True, default=str)
            except Exception:
                return str(val)
        return str(val)

    return render_template(
        "admin/job_run_detail.html",
        run=row,
        result_summary_pretty=_pretty(row.get("result_summary")),
        metadata_pretty=_pretty(row.get("metadata")),
    )
