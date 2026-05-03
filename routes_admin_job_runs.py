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
from sqlalchemy import text

from app import db
from services.job_run_logger import (
    get_distinct_job_ids,
    get_recent_runs,
    get_run_by_id,
)
from services.maintenance.log_cleanup import (
    DEFAULT_RETENTION_DAYS,
    _read_retention_days,
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


def _storage_stats():
    """Return read-only sizing info for the ``job_runs`` table.

    Mirrors the cleanup body's WHERE clause so the "would be pruned"
    figure stays in sync with what the next cron tick would actually
    delete. Each read uses its own short-lived ``db.engine.connect()``
    transaction and degrades gracefully on DB error.
    """
    stats = {
        "total_rows": None,
        "oldest_started_at": None,
        "retention_days": None,
        "would_prune": None,
        "error": None,
    }
    try:
        retention_days = _read_retention_days()
    except Exception:
        retention_days = DEFAULT_RETENTION_DAYS
    stats["retention_days"] = retention_days

    try:
        with db.engine.connect() as conn:
            row = conn.execute(
                text("SELECT COUNT(*), MIN(started_at) FROM job_runs")
            ).fetchone()
        if row is not None:
            stats["total_rows"] = int(row[0] or 0)
            stats["oldest_started_at"] = row[1]
    except Exception as e:
        logger.warning(f"job_runs storage_stats: count/min failed: {e}")
        stats["error"] = str(e)[:200]
        return stats

    if retention_days and retention_days > 0:
        try:
            with db.engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM job_runs
                        WHERE started_at < (NOW() - (:days || ' days')::interval)
                        """
                    ),
                    {"days": retention_days},
                ).fetchone()
            stats["would_prune"] = int(row[0] or 0) if row is not None else 0
        except Exception as e:
            logger.warning(f"job_runs storage_stats: would_prune failed: {e}")
            stats["would_prune"] = None
    else:
        stats["would_prune"] = 0

    return stats


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
    storage_stats = _storage_stats()

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
        storage_stats=storage_stats,
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
