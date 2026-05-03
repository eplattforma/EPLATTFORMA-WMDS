"""Phase 4: scheduled cleanup of the ``job_runs`` table.

Mirrors the transactional/exception-safety contract of
``services.job_run_logger``:

  * Uses an isolated short-lived ``db.engine.connect()`` connection.
  * Never touches ``db.session`` so it cannot commit half-finished caller
    work in a parent transaction.
  * Catches all exceptions, logs at WARN, and returns sentinel values.
    Per the WMDS brief Section 14 ("logging failures must not stop
    scheduled jobs"), the scheduled wrapper raises ``JobSkipped`` rather
    than ``Exception`` when the flag is OFF so the tick is recorded as
    SKIPPED in the operations dashboard instead of FAILED.

Default posture: the body is gated by ``job_log_cleanup_enabled``
(default ``'false'``) and only deletes rows older than
``job_log_retention_days`` (default ``'90'``). With both at their
defaults the cron wrapper writes a SKIPPED row every morning at 06:00
Europe/Nicosia and the table is left untouched.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)


DEFAULT_RETENTION_DAYS = 90
MIN_RETENTION_DAYS = 7
MAX_RETENTION_DAYS = 3650


def _get_setting(key, default):
    """Read a single ``settings`` row via an isolated connection.

    Returns ``default`` (string) on any failure or missing row.
    """
    try:
        with db.engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM settings WHERE key = :k"),
                {"k": key},
            ).fetchone()
        if row is None:
            return default
        return (row[0] if row[0] is not None else default)
    except Exception as e:
        logger.warning(f"log_cleanup: could not read setting {key!r}: {e}")
        return default


def _is_enabled():
    raw = _get_setting("job_log_cleanup_enabled", "false")
    return str(raw).strip().lower() in ("true", "1", "yes", "on")


def _retention_days():
    """Return the retention horizon clamped to a safe range.

    Returns ``DEFAULT_RETENTION_DAYS`` if the value is missing or
    cannot be parsed as an integer.
    """
    raw = _get_setting("job_log_retention_days", str(DEFAULT_RETENTION_DAYS))
    try:
        value = int(str(raw).strip())
    except (ValueError, TypeError):
        logger.warning(
            f"log_cleanup: invalid job_log_retention_days={raw!r}; "
            f"using default {DEFAULT_RETENTION_DAYS}"
        )
        return DEFAULT_RETENTION_DAYS
    if value < MIN_RETENTION_DAYS:
        logger.warning(
            f"log_cleanup: job_log_retention_days={value} below minimum "
            f"{MIN_RETENTION_DAYS}; clamping up"
        )
        return MIN_RETENTION_DAYS
    if value > MAX_RETENTION_DAYS:
        logger.warning(
            f"log_cleanup: job_log_retention_days={value} above maximum "
            f"{MAX_RETENTION_DAYS}; clamping down"
        )
        return MAX_RETENTION_DAYS
    return value


def run_log_cleanup():
    """Delete finished ``job_runs`` rows older than the retention horizon.

    Returns a dict ``{enabled, retention_days, deleted_count, cutoff}``.
    Never raises — exceptions are caught, logged, and surfaced via
    ``deleted_count = -1`` so the caller (scheduler ``_tracked``
    wrapper) records SUCCESS while the underlying problem is visible
    in the application log.

    Only deletes rows whose ``status`` is in a terminal state
    (SUCCESS / FAILED / SKIPPED / STALE_FAILED / CANCELLED) so
    long-running jobs that started before the cutoff but are still
    RUNNING are never wiped out.
    """
    retention_days = _retention_days()
    cutoff = datetime.now(timezone.utc)
    summary = {
        "enabled": False,
        "retention_days": retention_days,
        "deleted_count": 0,
        "cutoff": cutoff.isoformat(),
    }

    if not _is_enabled():
        logger.info(
            "log_cleanup: skipped (job_log_cleanup_enabled=false); "
            f"would have pruned rows older than {retention_days}d"
        )
        from scheduler import JobSkipped
        raise JobSkipped(
            f"job_log_cleanup_enabled=false (retention={retention_days}d)"
        )

    summary["enabled"] = True
    try:
        with db.engine.connect() as conn:
            result = conn.execute(
                text(
                    """
                    DELETE FROM job_runs
                    WHERE status IN
                        ('SUCCESS','FAILED','SKIPPED','STALE_FAILED','CANCELLED')
                      AND COALESCE(finished_at, started_at) <
                          (NOW() - (:days || ' days')::interval)
                    """
                ),
                {"days": retention_days},
            )
            conn.commit()
            deleted = result.rowcount or 0
        summary["deleted_count"] = deleted
        logger.info(
            f"log_cleanup: pruned {deleted} job_runs row(s) older than "
            f"{retention_days}d"
        )
        return summary
    except Exception as e:
        logger.warning(f"log_cleanup: DELETE failed (non-fatal): {e}")
        summary["deleted_count"] = -1
        summary["error"] = str(e)[:500]
        return summary
