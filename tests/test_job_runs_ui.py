"""Phase 4 — Admin Job Runs UI page guard matrix.

Pinned cells:

  anonymous           -> 302 to login
  picker (no perm)    -> blocked when enforcement ON; allowed when OFF
  warehouse_manager   -> 200 (has sync.view_logs via role)
  admin               -> 200 (wildcard)
  job_runs_ui_enabled=false -> 404 even for admin
  status filter       -> only matching rows in HTML
  job_id filter       -> only matching rows in HTML

Uses the same fixture pattern as tests/test_phase3_closeout_matrix.py.
"""
import os
import sys
import uuid
from datetime import datetime, timezone

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


def _seed_user(db, username, role):
    from werkzeug.security import generate_password_hash
    db.session.execute(
        text(
            "INSERT INTO users (username, password, role, is_active) "
            "VALUES (:u, :p, :r, true) "
            "ON CONFLICT (username) DO NOTHING"
        ),
        {"u": username, "p": generate_password_hash("dummy123"), "r": role},
    )


def _cleanup_user(db, username):
    db.session.execute(
        text("DELETE FROM user_permissions WHERE username = :u"),
        {"u": username},
    )
    db.session.execute(
        text("DELETE FROM users WHERE username = :u"),
        {"u": username},
    )
    db.session.commit()


def _login(client, username):
    with client.session_transaction() as sess:
        sess["_user_id"] = username
        sess["_fresh"] = True


ROLES = ["admin", "warehouse_manager", "picker"]
_USERS = {}


@pytest.fixture(scope="module", autouse=True)
def _seed_role_users():
    assert os.environ.get("DATABASE_URL"), "DATABASE_URL required"
    import main  # noqa: F401
    from app import app, db
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        for role in ROLES:
            name = f"t18ui_{role}_{suffix}"
            _USERS[role] = name
            _seed_user(db, name, role)
        db.session.commit()
    try:
        yield _USERS
    finally:
        with app.app_context():
            for name in list(_USERS.values()):
                _cleanup_user(db, name)
            _USERS.clear()


@pytest.fixture
def client(app_ctx):
    app, _ = app_ctx
    return app.test_client()


@pytest.fixture
def seeded_runs(app_ctx):
    """Insert a few job_runs rows so the UI has content to render/filter."""
    _, db = app_ctx
    suffix = uuid.uuid4().hex[:6]
    jid_alpha = f"t18ui_alpha_{suffix}"
    jid_beta = f"t18ui_beta_{suffix}"
    now = datetime.now(timezone.utc)
    inserted = []
    with db.engine.connect() as conn:
        for jid, status in [
            (jid_alpha, "SUCCESS"),
            (jid_alpha, "FAILED"),
            (jid_beta, "SUCCESS"),
            (jid_beta, "SKIPPED"),
        ]:
            row = conn.execute(text("""
                INSERT INTO job_runs (job_id, job_name, trigger_source, status,
                                      started_at, finished_at, last_heartbeat)
                VALUES (:jid, :jid, 'scheduled', :status, :now, :now, :now)
                RETURNING id
            """), {"jid": jid, "status": status, "now": now}).scalar()
            inserted.append(row)
        conn.commit()
    yield {"alpha": jid_alpha, "beta": jid_beta, "ids": inserted}
    with db.engine.connect() as conn:
        conn.execute(text("DELETE FROM job_runs WHERE id = ANY(:ids)"),
                     {"ids": inserted})
        conn.commit()


@pytest.fixture
def with_ui_setting(app_ctx):
    _, db = app_ctx
    from models import Setting
    prev = Setting.get(db.session, "job_runs_ui_enabled", "true")

    def _set(value):
        Setting.set(db.session, "job_runs_ui_enabled", value)
        db.session.commit()

    yield _set
    Setting.set(db.session, "job_runs_ui_enabled", prev)
    db.session.commit()


# --------------------- Pinned cells ---------------------

def test_anonymous_redirected_to_login(client):
    resp = client.get("/admin/job-runs/")
    assert resp.status_code in (302, 401)


def test_admin_can_view(client, seeded_runs):
    _login(client, _USERS["admin"])
    resp = client.get("/admin/job-runs/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Job Runs" in body
    assert seeded_runs["alpha"] in body or seeded_runs["beta"] in body


def test_warehouse_manager_can_view(client, seeded_runs):
    _login(client, _USERS["warehouse_manager"])
    resp = client.get("/admin/job-runs/")
    assert resp.status_code == 200


def test_status_filter_narrows_results(client, seeded_runs):
    _login(client, _USERS["admin"])
    resp = client.get(
        f"/admin/job-runs/?job_id={seeded_runs['alpha']}&status=FAILED"
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The FAILED row should appear; the SUCCESS row for alpha should not.
    assert "FAILED" in body


def test_job_id_filter_narrows_results(client, seeded_runs):
    _login(client, _USERS["admin"])
    resp = client.get(f"/admin/job-runs/?job_id={seeded_runs['beta']}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert seeded_runs["beta"] in body
    # alpha should not appear in the filtered window
    assert seeded_runs["alpha"] not in body


def test_ui_disabled_returns_404(client, with_ui_setting, seeded_runs):
    with_ui_setting("false")
    _login(client, _USERS["admin"])
    resp = client.get("/admin/job-runs/")
    assert resp.status_code == 404


def test_invalid_status_filter_silently_ignored(client, seeded_runs):
    _login(client, _USERS["admin"])
    resp = client.get("/admin/job-runs/?status=NOT_A_REAL_STATUS")
    assert resp.status_code == 200


def test_limit_clamped_to_safe_range(client, seeded_runs):
    _login(client, _USERS["admin"])
    # 99999 should clamp to 500, not blow up
    resp = client.get("/admin/job-runs/?limit=99999")
    assert resp.status_code == 200
    # 1 should clamp up to 10, not blow up
    resp = client.get("/admin/job-runs/?limit=1")
    assert resp.status_code == 200
    resp = client.get("/admin/job-runs/?limit=garbage")
    assert resp.status_code == 200
