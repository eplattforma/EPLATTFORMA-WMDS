"""Phase 4 — services.maintenance.log_cleanup behaviour matrix.

Pinned cells (only these matter for production safety):

  flag OFF  -> raises JobSkipped, no DELETE issued
  flag ON, no rows past cutoff -> deleted_count = 0
  flag ON, rows past cutoff    -> deleted_count > 0, RUNNING rows untouched
  invalid retention            -> clamped to default (90), never crashes
  setting missing              -> defaults to OFF / 90d

Uses the same `_seed_role_users` + isolated app_context pattern as
`tests/test_phase3_closeout_matrix.py`. Runs against the dev DB; rows
are inserted with a unique `job_id` prefix and torn down on exit.
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
    """Yield a helper that inserts test rows; teardown wipes them."""
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
    """Snapshot the cleanup flags, restore on teardown."""
    _, db = app_ctx
    from models import Setting
    prev_enabled = Setting.get(db.session, "job_log_cleanup_enabled", "false")
    prev_days = Setting.get(db.session, "job_log_retention_days", "90")

    def _set(enabled=None, days=None):
        if enabled is not None:
            Setting.set(db.session, "job_log_cleanup_enabled", enabled)
        if days is not None:
            Setting.set(db.session, "job_log_retention_days", str(days))
        db.session.commit()

    yield _set

    Setting.set(db.session, "job_log_cleanup_enabled", prev_enabled)
    Setting.set(db.session, "job_log_retention_days", str(prev_days))
    db.session.commit()


# --------------------- Pinned cells ---------------------

def test_flag_off_raises_jobskipped_and_does_not_delete(
        app_ctx, with_settings, cleanup_rows):
    """Default posture: OFF flag -> JobSkipped, zero deletions."""
    _, db = app_ctx
    with_settings(enabled="false", days=30)
    old_id = cleanup_rows("SUCCESS", started_offset_days=200,
                          finished_offset_days=200, suffix="off_old")

    from scheduler import JobSkipped
    from services.maintenance.log_cleanup import run_log_cleanup

    with pytest.raises(JobSkipped) as exc:
        run_log_cleanup()
    assert "job_log_cleanup_enabled=false" in str(exc.value)

    with db.engine.connect() as conn:
        still_there = conn.execute(
            text("SELECT 1 FROM job_runs WHERE id = :id"),
            {"id": old_id},
        ).fetchone()
    assert still_there is not None, "OFF flag must not delete any rows"


def test_flag_on_no_rows_past_cutoff_returns_zero(
        app_ctx, with_settings, cleanup_rows):
    with_settings(enabled="true", days=30)
    young_id = cleanup_rows("SUCCESS", started_offset_days=5,
                            finished_offset_days=5, suffix="young")

    from services.maintenance.log_cleanup import run_log_cleanup
    summary = run_log_cleanup()

    assert summary["enabled"] is True
    assert summary["retention_days"] == 30
    assert summary["deleted_count"] == 0

    _, db = app_ctx
    with db.engine.connect() as conn:
        assert conn.execute(
            text("SELECT 1 FROM job_runs WHERE id = :id"),
            {"id": young_id},
        ).fetchone() is not None


def test_flag_on_deletes_old_rows_but_keeps_running(
        app_ctx, with_settings, cleanup_rows):
    with_settings(enabled="true", days=30)
    old_done = cleanup_rows("SUCCESS", started_offset_days=200,
                            finished_offset_days=200, suffix="old_done")
    old_failed = cleanup_rows("FAILED", started_offset_days=120,
                              finished_offset_days=120, suffix="old_failed")
    old_skipped = cleanup_rows("SKIPPED", started_offset_days=90,
                               finished_offset_days=90, suffix="old_skip")
    old_running = cleanup_rows("RUNNING", started_offset_days=200,
                               finished_offset_days=None, suffix="old_run")
    young_done = cleanup_rows("SUCCESS", started_offset_days=10,
                              finished_offset_days=10, suffix="young_done")

    from services.maintenance.log_cleanup import run_log_cleanup
    summary = run_log_cleanup()

    assert summary["enabled"] is True
    assert summary["deleted_count"] >= 3

    _, db = app_ctx
    with db.engine.connect() as conn:
        present = {
            row[0] for row in conn.execute(
                text("SELECT id FROM job_runs WHERE id = ANY(:ids)"),
                {"ids": [old_done, old_failed, old_skipped, old_running, young_done]},
            ).fetchall()
        }
    assert old_done not in present
    assert old_failed not in present
    assert old_skipped not in present
    assert old_running in present, "RUNNING rows must never be pruned"
    assert young_done in present, "Rows newer than cutoff must survive"


def test_invalid_retention_falls_back_to_default(
        app_ctx, with_settings, cleanup_rows):
    """Garbage retention value -> default 90 used, no crash."""
    with_settings(enabled="true", days="not-a-number")
    from services.maintenance.log_cleanup import run_log_cleanup
    summary = run_log_cleanup()
    assert summary["enabled"] is True
    assert summary["retention_days"] == 90


def test_retention_clamped_below_minimum(app_ctx, with_settings):
    with_settings(enabled="true", days=1)
    from services.maintenance.log_cleanup import run_log_cleanup, MIN_RETENTION_DAYS
    summary = run_log_cleanup()
    assert summary["retention_days"] == MIN_RETENTION_DAYS


def test_retention_clamped_above_maximum(app_ctx, with_settings):
    with_settings(enabled="true", days=99999)
    from services.maintenance.log_cleanup import run_log_cleanup, MAX_RETENTION_DAYS
    summary = run_log_cleanup()
    assert summary["retention_days"] == MAX_RETENTION_DAYS


def test_truthy_enabled_values_all_activate(app_ctx, with_settings):
    """`true`/`1`/`yes`/`on` (any case) all enable cleanup."""
    from services.maintenance.log_cleanup import run_log_cleanup
    for v in ("true", "True", "TRUE", "1", "yes", "ON"):
        with_settings(enabled=v, days=30)
        summary = run_log_cleanup()
        assert summary["enabled"] is True, f"value {v!r} should enable"


def test_falsy_enabled_values_all_skip(app_ctx, with_settings):
    """`false`/`0`/blank all keep cleanup OFF."""
    from scheduler import JobSkipped
    from services.maintenance.log_cleanup import run_log_cleanup
    for v in ("false", "False", "0", "no", "off", ""):
        with_settings(enabled=v, days=30)
        with pytest.raises(JobSkipped):
            run_log_cleanup()
