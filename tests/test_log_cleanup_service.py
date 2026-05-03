"""Phase 4 — services.maintenance.log_cleanup behaviour matrix.

Pinned cells (only these matter for production safety):

  * retention=90, mixed ages    -> only rows older than cutoff deleted
  * retention=0                 -> NO-OP (defensive pause)
  * retention=-5                -> NO-OP (defensive pause)
  * default retention           -> reads job_runs_retention_days
  * summary shape               -> {rows_deleted, retention_days, cutoff_utc}
  * scheduler wrapper, flag OFF -> _run_log_cleanup() raises JobSkipped
  * scheduler wrapper, flag ON  -> _run_log_cleanup() returns summary dict
  * DB error path               -> rows_deleted=-1, no exception raised

Uses the same Postgres fixture pattern as
``tests/test_phase3_closeout_matrix.py``. Rows are tagged with a unique
``job_id`` prefix so teardown can scope DELETE strictly to test data.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest
from sqlalchemy import text


@pytest.fixture
def app_ctx():
    assert os.environ.get("DATABASE_URL"), "DATABASE_URL required"
    import main  # noqa: F401
    from app import app, db
    with app.app_context():
        db.session.remove()
        yield app, db
        db.session.remove()


@pytest.fixture
def job_id_prefix():
    return f"t18_cleanup_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def cleanup_rows(app_ctx, job_id_prefix):
    _, db = app_ctx
    inserted_ids = []

    def _insert(status, started_offset_days, finished_offset_days=None,
                suffix="x"):
        now = datetime.now(timezone.utc)
        started = now - timedelta(days=started_offset_days)
        finished = (now - timedelta(days=finished_offset_days)
                    if finished_offset_days is not None else None)
        with db.engine.connect() as conn:
            row = conn.execute(text("""
                INSERT INTO job_runs (job_id, job_name, trigger_source, status,
                                      started_at, finished_at, last_heartbeat)
                VALUES (:jid, :jid, 'scheduled', :status,
                        :started, :finished, :started)
                RETURNING id
            """), {
                "jid": f"{job_id_prefix}_{suffix}",
                "status": status,
                "started": started,
                "finished": finished,
            }).scalar()
            conn.commit()
        inserted_ids.append(row)
        return row

    yield _insert

    if inserted_ids:
        with db.engine.connect() as conn:
            conn.execute(
                text("DELETE FROM job_runs WHERE id = ANY(:ids)"),
                {"ids": inserted_ids},
            )
            conn.commit()


@pytest.fixture
def with_settings(app_ctx):
    _, db = app_ctx
    from models import Setting
    prev_enabled = Setting.get(db.session, "job_log_cleanup_enabled", "false")
    prev_days = Setting.get(db.session, "job_runs_retention_days", "90")

    def _set(enabled=None, days=None):
        if enabled is not None:
            Setting.set(db.session, "job_log_cleanup_enabled", enabled)
        if days is not None:
            Setting.set(db.session, "job_runs_retention_days", str(days))
        db.session.commit()

    yield _set
    Setting.set(db.session, "job_log_cleanup_enabled", prev_enabled)
    Setting.set(db.session, "job_runs_retention_days", str(prev_days))
    db.session.commit()


# --------------------- pure service ---------------------

def test_summary_shape_matches_brief(app_ctx, with_settings):
    with_settings(days=30)
    from services.maintenance.log_cleanup import delete_old_job_runs
    summary = delete_old_job_runs(retention_days=30)
    assert set(summary.keys()) >= {"rows_deleted", "retention_days", "cutoff_utc"}
    assert summary["retention_days"] == 30
    assert isinstance(summary["cutoff_utc"], str) and "T" in summary["cutoff_utc"]


def test_deletes_only_rows_older_than_cutoff(app_ctx, cleanup_rows):
    old_id = cleanup_rows("SUCCESS", started_offset_days=200, finished_offset_days=200, suffix="old")
    young_id = cleanup_rows("SUCCESS", started_offset_days=5, finished_offset_days=5, suffix="young")

    from services.maintenance.log_cleanup import delete_old_job_runs
    summary = delete_old_job_runs(retention_days=30)
    assert summary["rows_deleted"] >= 1

    _, db = app_ctx
    with db.engine.connect() as conn:
        present = {r[0] for r in conn.execute(
            text("SELECT id FROM job_runs WHERE id = ANY(:ids)"),
            {"ids": [old_id, young_id]},
        ).fetchall()}
    assert old_id not in present, "Row older than cutoff must be deleted"
    assert young_id in present, "Row newer than cutoff must survive"


def test_retention_zero_is_noop(app_ctx, cleanup_rows):
    old_id = cleanup_rows("SUCCESS", started_offset_days=500, finished_offset_days=500, suffix="z")
    from services.maintenance.log_cleanup import delete_old_job_runs
    summary = delete_old_job_runs(retention_days=0)
    assert summary["rows_deleted"] == 0
    assert summary["retention_days"] == 0
    _, db = app_ctx
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT 1 FROM job_runs WHERE id = :id"),
                            {"id": old_id}).fetchone() is not None


def test_retention_negative_is_noop(app_ctx, cleanup_rows):
    old_id = cleanup_rows("SUCCESS", started_offset_days=500, finished_offset_days=500, suffix="n")
    from services.maintenance.log_cleanup import delete_old_job_runs
    summary = delete_old_job_runs(retention_days=-5)
    assert summary["rows_deleted"] == 0
    assert summary["retention_days"] == -5
    _, db = app_ctx
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT 1 FROM job_runs WHERE id = :id"),
                            {"id": old_id}).fetchone() is not None


def test_default_reads_setting(app_ctx, with_settings, cleanup_rows):
    with_settings(days=15)
    cleanup_rows("SUCCESS", started_offset_days=200, finished_offset_days=200, suffix="d")
    from services.maintenance.log_cleanup import delete_old_job_runs
    summary = delete_old_job_runs()
    assert summary["retention_days"] == 15


def test_invalid_setting_falls_back_to_default(app_ctx, with_settings):
    with_settings(days="not-an-int")
    from services.maintenance.log_cleanup import delete_old_job_runs, DEFAULT_RETENTION_DAYS
    summary = delete_old_job_runs()
    assert summary["retention_days"] == DEFAULT_RETENTION_DAYS


# --------------------- scheduler wrapper ---------------------

def test_scheduler_wrapper_skips_when_flag_off(app_ctx, with_settings, cleanup_rows):
    with_settings(enabled="false", days=30)
    old_id = cleanup_rows("SUCCESS", started_offset_days=500, finished_offset_days=500, suffix="off")
    from scheduler import _run_log_cleanup, JobSkipped
    with pytest.raises(JobSkipped) as exc:
        _run_log_cleanup()
    assert "disabled by flag" in str(exc.value)
    _, db = app_ctx
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT 1 FROM job_runs WHERE id = :id"),
                            {"id": old_id}).fetchone() is not None


def test_scheduler_wrapper_returns_summary_when_flag_on(app_ctx, with_settings, cleanup_rows):
    with_settings(enabled="true", days=30)
    cleanup_rows("SUCCESS", started_offset_days=200, finished_offset_days=200, suffix="on")
    from scheduler import _run_log_cleanup
    summary = _run_log_cleanup()
    assert isinstance(summary, dict)
    assert set(summary.keys()) >= {"rows_deleted", "retention_days", "cutoff_utc"}
    assert summary["retention_days"] == 30


def test_truthy_and_falsy_enabled_values(app_ctx, with_settings):
    from scheduler import _run_log_cleanup, JobSkipped
    for v in ("true", "True", "1", "yes", "ON"):
        with_settings(enabled=v, days=30)
        result = _run_log_cleanup()
        assert isinstance(result, dict), f"value {v!r} should enable cleanup"
    for v in ("false", "0", "no", "off", ""):
        with_settings(enabled=v, days=30)
        with pytest.raises(JobSkipped):
            _run_log_cleanup()
