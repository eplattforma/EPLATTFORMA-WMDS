"""Phase 4: scheduled cleanup of the ``job_runs`` table.

Mirrors the transactional / exception-safety contract of
``services.job_run_logger``:

  * Uses an isolated short-lived ``db.engine.connect()`` connection.
  * Never touches ``db.session`` so it cannot commit half-finished
    caller work in a parent transaction.
  * Catches all exceptions, logs at WARN, and returns a sentinel
    summary instead of raising into the scheduler executor (per the
    WMDS brief Section 14: "logging failures must not stop scheduled
    jobs").

The scheduled wrapper (``scheduler._run_log_cleanup``) reads the
``job_log_cleanup_enabled`` flag itself and raises ``JobSkipped`` when
OFF, so each tick still produces a SKIPPED row in ``job_runs``. The
pure function in this module is therefore "delete-only" and assumes
the caller has already decided to run it.

Default posture: ``job_log_cleanup_enabled=false`` (seeded by Phase 1)
keeps the cleanup body unreached on production until an operator
flips the flag in the Settings UI. Retention defaults to 90 days
(``job_runs_retention_days``); a 0 or negative value is treated as a
no-op so an operator can pause cleanup without disabling the cron.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)


DEFAULT_RETENTION_DAYS = 90


def _read_retention_days():
    """Read ``job_runs_retention_days`` from the settings table.

    Returns an ``int`` or ``DEFAULT_RETENTION_DAYS`` if the row is
    missing / unparseable. Read uses an isolated connection so a
    failure here cannot poison a parent transaction.
    """
    try:
        with db.engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM settings WHERE key = :k"),
                {"k": "job_runs_retention_days"},
            ).fetchone()
        if row is None or row[0] is None:
            return DEFAULT_RETENTION_DAYS
        return int(str(row[0]).strip())
    except (ValueError, TypeError):
        logger.warning(
            "log_cleanup: invalid job_runs_retention_days; "
            f"using default {DEFAULT_RETENTION_DAYS}"
        )
        return DEFAULT_RETENTION_DAYS
    except Exception as e:
        logger.warning(
            f"log_cleanup: could not read job_runs_retention_days: {e}; "
            f"using default {DEFAULT_RETENTION_DAYS}"
        )
        return DEFAULT_RETENTION_DAYS


def delete_old_job_runs(retention_days=None):
    """Delete ``job_runs`` rows older than ``retention_days``.

    Per the Phase 4 brief: parameterised
    ``DELETE FROM job_runs WHERE started_at < (now() - interval ...)``.
    No status filter — the cleanup horizon is purely time-based.
    A 0 or negative ``retention_days`` is treated as a no-op (defensive
    pause-without-disable behaviour).

    Returns ``{rows_deleted, retention_days, cutoff_utc}``. Never
    raises.
    """
    if retention_days is None:
        retention_days = _read_retention_days()

    try:
        retention_days = int(retention_days)
    except (ValueError, TypeError):
        retention_days = DEFAULT_RETENTION_DAYS

    cutoff = datetime.now(timezone.utc)
    summary = {
        "rows_deleted": 0,
        "retention_days": retention_days,
        "cutoff_utc": cutoff.isoformat(),
    }

    if retention_days <= 0:
        logger.info(
            f"log_cleanup: retention_days={retention_days} is non-positive; "
            "no-op (use job_log_cleanup_enabled=false to disable cron entirely)"
        )
        return summary

    try:
        with db.engine.connect() as conn:
            result = conn.execute(
                text(
                    """
                    DELETE FROM job_runs
                    WHERE started_at < (NOW() - (:days || ' days')::interval)
                    """
                ),
                {"days": retention_days},
            )
            conn.commit()
            summary["rows_deleted"] = result.rowcount or 0
        logger.info(
            f"log_cleanup: pruned {summary['rows_deleted']} job_runs row(s) "
            f"older than {retention_days}d (cutoff_utc={summary['cutoff_utc']})"
        )
        return summary
    except Exception as e:
        logger.warning(f"log_cleanup: DELETE failed (non-fatal): {e}")
        summary["rows_deleted"] = -1
        summary["error"] = str(e)[:500]
        return summary
