"""Central Job Runs logger.

Wraps scheduled / manual jobs with lifecycle hooks that write into the
`job_runs` table.

**Transactional isolation:** every public function uses its own short-lived
`db.engine.connect()` connection and commits inside that connection. It never
touches `db.session`, so calling the logger from inside a business transaction
will not commit half-finished caller work.

**Exception safety:** every public function catches all exceptions, logs at
WARN, and returns a sentinel value. Per brief Section 14, "logging failures
must not stop scheduled jobs from running."

Gated by:
  - `job_runs_enabled`        — master switch
  - `job_runs_write_enabled`  — sub-switch for INSERT/UPDATE writes
"""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)


VALID_STATUSES = {"RUNNING", "SUCCESS", "FAILED", "SKIPPED", "STALE_FAILED", "CANCELLED"}


def _is_enabled():
    """Read flags via an isolated connection so we don't disturb any caller transaction."""
    try:
        with db.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT key, value FROM settings "
                    "WHERE key IN ('job_runs_enabled','job_runs_write_enabled')"
                )
            ).fetchall()
        values = {r[0]: (r[1] or "").lower() for r in rows}
        master = values.get("job_runs_enabled", "true") == "true"
        write = values.get("job_runs_write_enabled", "true") == "true"
        return master and write
    except Exception:
        return False


def _utc_now():
    return datetime.now(timezone.utc)


def start_job_run(job_id, job_name=None, trigger_source="scheduled",
                  created_by=None, parent_run_id=None, metadata=None):
    """Insert a RUNNING row, return its id (or None on any failure)."""
    if not _is_enabled():
        return None
    try:
        with db.engine.connect() as conn:
            result = conn.execute(text("""
                INSERT INTO job_runs (job_id, job_name, trigger_source, status,
                                      started_at, last_heartbeat,
                                      created_by, parent_run_id, metadata)
                VALUES (:job_id, :job_name, :trigger_source, 'RUNNING',
                        :now, :now, :created_by, :parent_run_id,
                        CAST(:metadata AS JSONB))
                RETURNING id
            """), {
                "job_id": job_id,
                "job_name": job_name or job_id,
                "trigger_source": trigger_source,
                "now": _utc_now(),
                "created_by": created_by,
                "parent_run_id": parent_run_id,
                "metadata": json.dumps(metadata) if metadata else None,
            })
            run_id = result.scalar()
            conn.commit()
            return run_id
    except Exception as e:
        logger.warning(f"start_job_run failed for {job_id}: {e}")
        return None


def heartbeat(run_id, current_step=None, progress_current=None,
              progress_total=None, progress_message=None):
    if not run_id or not _is_enabled():
        return
    try:
        with db.engine.connect() as conn:
            conn.execute(text("""
                UPDATE job_runs
                SET last_heartbeat = :now,
                    updated_at = :now,
                    current_step = COALESCE(:current_step, current_step),
                    progress_current = COALESCE(:progress_current, progress_current),
                    progress_total = COALESCE(:progress_total, progress_total),
                    progress_message = COALESCE(:progress_message, progress_message)
                WHERE id = :id
            """), {
                "id": run_id,
                "now": _utc_now(),
                "current_step": current_step,
                "progress_current": progress_current,
                "progress_total": progress_total,
                "progress_message": progress_message,
            })
            conn.commit()
    except Exception as e:
        logger.warning(f"heartbeat failed for run {run_id}: {e}")


def finish_job_run(run_id, status="SUCCESS", result_summary=None, error_message=None):
    if not run_id or not _is_enabled():
        return
    if status not in VALID_STATUSES:
        logger.warning(f"finish_job_run: invalid status {status!r}, coercing to FAILED")
        status = "FAILED"
    try:
        with db.engine.connect() as conn:
            conn.execute(text("""
                UPDATE job_runs
                SET status = :status,
                    finished_at = :now,
                    updated_at = :now,
                    duration_seconds = EXTRACT(EPOCH FROM (:now - started_at)),
                    result_summary = COALESCE(CAST(:result_summary AS JSONB), result_summary),
                    error_message = COALESCE(:error_message, error_message)
                WHERE id = :id
            """), {
                "id": run_id,
                "now": _utc_now(),
                "status": status,
                "result_summary": json.dumps(result_summary) if result_summary else None,
                "error_message": error_message,
            })
            conn.commit()
    except Exception as e:
        logger.warning(f"finish_job_run failed for run {run_id}: {e}")


def mark_stale_runs(timeout_seconds, job_id_filter=None):
    """Mark RUNNING rows older than `timeout_seconds` as STALE_FAILED.

    Returns the number of rows marked, or 0 on any failure.
    """
    if not _is_enabled():
        return 0
    try:
        params = {"timeout": timeout_seconds, "now": _utc_now()}
        sql = """
            UPDATE job_runs
            SET status = 'STALE_FAILED',
                finished_at = :now,
                updated_at = :now,
                duration_seconds = EXTRACT(EPOCH FROM (:now - started_at)),
                error_message = COALESCE(error_message,
                    'Marked STALE_FAILED by watchdog: no heartbeat for ' ||
                    :timeout || ' seconds')
            WHERE status = 'RUNNING'
              AND COALESCE(last_heartbeat, started_at) <
                  (:now - (:timeout || ' seconds')::interval)
        """
        if job_id_filter:
            sql += " AND job_id = :job_id"
            params["job_id"] = job_id_filter
        with db.engine.connect() as conn:
            result = conn.execute(text(sql), params)
            conn.commit()
            return result.rowcount or 0
    except Exception as e:
        logger.warning(f"mark_stale_runs failed: {e}")
        return 0


def get_recent_runs(limit=100, job_id=None, statuses=None, hours=None):
    """Return recent runs as list of dicts. Returns [] on failure.

    All filters are server-side / parameterised — no string concat:
      * ``job_id``  — exact match
      * ``statuses`` — iterable of status strings (multi-select)
      * ``hours``    — only rows with ``started_at`` newer than N hours ago
    """
    try:
        clauses = []
        params = {"limit": int(limit)}
        if job_id:
            clauses.append("job_id = :job_id")
            params["job_id"] = job_id
        if statuses:
            status_list = [str(s).upper() for s in statuses if s]
            if status_list:
                clauses.append("status = ANY(:statuses)")
                params["statuses"] = status_list
        if hours is not None:
            try:
                hrs = float(hours)
            except (TypeError, ValueError):
                hrs = None
            if hrs is not None and hrs > 0:
                clauses.append(
                    "started_at >= (NOW() - (:hours || ' hours')::interval)"
                )
                params["hours"] = str(hrs)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT id, job_id, job_name, trigger_source, status,
                   started_at, finished_at, duration_seconds, last_heartbeat,
                   current_step, progress_current, progress_total,
                   progress_message, result_summary, error_message,
                   created_by, parent_run_id
            FROM job_runs
            {where}
            ORDER BY started_at DESC
            LIMIT :limit
        """
        with db.engine.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception as e:
        logger.warning(f"get_recent_runs failed: {e}")
        return []


def get_run_by_id(run_id):
    """Return one ``job_runs`` row as a dict, or ``None`` if not found / on error."""
    if not run_id:
        return None
    try:
        with db.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT id, job_id, job_name, trigger_source, status,
                       started_at, finished_at, duration_seconds, last_heartbeat,
                       current_step, progress_current, progress_total,
                       progress_message, result_summary, error_message,
                       created_by, parent_run_id, metadata, created_at, updated_at
                FROM job_runs
                WHERE id = :id
            """), {"id": int(run_id)}).fetchone()
        return dict(row._mapping) if row else None
    except Exception as e:
        logger.warning(f"get_run_by_id failed for {run_id!r}: {e}")
        return None


def get_distinct_job_ids():
    """Return sorted distinct job_id values from job_runs. [] on failure."""
    try:
        with db.engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT job_id FROM job_runs WHERE job_id IS NOT NULL "
                "ORDER BY job_id"
            )).fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        logger.warning(f"get_distinct_job_ids failed: {e}")
        return []
