"""Central stale-forecast detection helper.

Both the watchdog cron (`scheduler._run_forecast_watchdog`) and the live
Forecast Workbench API (`blueprints.forecast_workbench.api_suppliers`) used to
contain near-identical inline blocks that scanned the most recent
`forecast_runs.status='running'` row, compared its `last_heartbeat_at` against
a hardcoded 45-minute threshold, and marked it failed. Two copies meant two
places to keep in sync; this module is the single source of truth.

Reads the threshold from the `forecast_heartbeat_timeout_seconds` setting
(default 2700s = 45min, matching the Phase 1 default seeded in
`services/settings_defaults.py`). Callers can override the timeout
explicitly when needed.

This module never raises into the caller; on any DB error it logs at
WARN and returns 0 so a stuck or unreachable settings table can never
break the Forecast Workbench page or the watchdog tick.
"""
import logging
from datetime import datetime, timedelta

from models import ForecastRun, Setting
from timezone_utils import get_utc_now

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 2700


def _resolve_timeout_seconds(session, override=None):
    """Return the effective stale threshold in seconds.

    Precedence: explicit override > forecast_heartbeat_timeout_seconds setting
    > DEFAULT_TIMEOUT_SECONDS.
    """
    if override is not None:
        try:
            return max(int(override), 60)
        except (TypeError, ValueError):
            pass
    try:
        raw = Setting.get(session, "forecast_heartbeat_timeout_seconds",
                          str(DEFAULT_TIMEOUT_SECONDS))
        return max(int(raw), 60)
    except Exception:
        return DEFAULT_TIMEOUT_SECONDS


def mark_stale_forecast_run_if_needed(session, timeout_seconds=None):
    """If the most recent `forecast_runs.status='running'` row has no heartbeat
    for `timeout_seconds`, mark it failed and commit. Returns the run id that
    was marked, or None.

    Safe to call from request handlers and from the watchdog cron — uses the
    caller's `session` and never re-raises. Callers needing to skip the DB
    write entirely (e.g. read-only viewers) can simply not call this.
    """
    try:
        timeout = _resolve_timeout_seconds(session, timeout_seconds)
        stale_cutoff = datetime.utcnow() - timedelta(seconds=timeout)

        running = (
            ForecastRun.query
            .filter_by(status="running")
            .order_by(ForecastRun.started_at.desc())
            .first()
        )
        if not running:
            return None

        reference = running.last_heartbeat_at or running.started_at
        if not reference or reference >= stale_cutoff:
            return None

        minutes_silent = int((datetime.utcnow() - reference).total_seconds() / 60)
        timeout_minutes = int(timeout / 60)
        logger.warning(
            f"forecast stale-detection: marking run {running.id} as failed "
            f"(no heartbeat for {minutes_silent} min, threshold {timeout_minutes} min)"
        )
        running.status = "failed"
        running.completed_at = get_utc_now()
        running.notes = (
            f"Auto-marked failed: no heartbeat for {minutes_silent} min "
            f"(threshold {timeout_minutes} min)"
        )
        session.commit()

        # Also flip the matching `job_runs` row to STALE_FAILED so the
        # operations dashboard does not leave the tick stuck in RUNNING.
        # The forecast pipeline calls `scheduler.heartbeat(...)` which
        # bumps `job_runs.last_heartbeat`, so the same threshold applies.
        # `mark_stale_runs` is best-effort and never raises.
        try:
            from services.job_run_logger import mark_stale_runs
            stale_jr_count = mark_stale_runs(timeout, job_id_filter="forecast_run")
            if stale_jr_count:
                logger.warning(
                    f"forecast stale-detection: also marked {stale_jr_count} "
                    f"job_runs row(s) as STALE_FAILED for forecast_run"
                )
        except Exception as e:
            logger.warning(f"mark_stale_runs (job_runs lifecycle) failed: {e}")

        return running.id
    except Exception as e:
        logger.warning(f"mark_stale_forecast_run_if_needed failed: {e}")
        try:
            session.rollback()
        except Exception:
            pass
        return None
