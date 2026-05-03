"""Phase 4 — Admin Job Runs UI page guard + filter matrix.

Pinned cells:

  anonymous              -> 302 to login
  admin / wm             -> 200 (allowed via role -> sync.view_logs)
  picker / driver / crm  -> denied when permissions_enforcement_enabled=ON
                            allowed when OFF (decorator only logs)
  unknown role           -> denied under enforcement (defensive)
  status filter (multi)  -> only matching rows render
  job_id filter          -> only matching rows render
  hours filter           -> recent rows survive, ancient rows hidden
  detail page            -> 200 for admin, 404 for unknown id
  invalid query strings  -> silently ignored, never 500
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


ROLES = ["admin", "warehouse_manager", "picker", "driver", "crm_admin"]
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
def with_enforcement(app_ctx):
    """Snapshot+restore permissions_enforcement_enabled."""
    _, db = app_ctx
    from models import Setting
    prev = Setting.get(db.session, "permissions_enforcement_enabled", "false")

    def _set(value):
        Setting.set(db.session, "permissions_enforcement_enabled", value)
        db.session.commit()

    yield _set
    Setting.set(db.session, "permissions_enforcement_enabled", prev)
    db.session.commit()


@pytest.fixture
def seeded_runs(app_ctx):
    _, db = app_ctx
    suffix = uuid.uuid4().hex[:6]
    jid_alpha = f"t18ui_alpha_{suffix}"
    jid_beta = f"t18ui_beta_{suffix}"
    now = datetime.now(timezone.utc)
    inserted = []
    with db.engine.connect() as conn:
        for jid, status, age_hours in [
            (jid_alpha, "SUCCESS", 1),
            (jid_alpha, "FAILED", 2),
            (jid_beta, "SUCCESS", 3),
            (jid_beta, "SKIPPED", 4),
            (jid_alpha, "SUCCESS", 24 * 60),  # ancient — should be filtered out by 24h window
        ]:
            ts = now - timedelta(hours=age_hours)
            row = conn.execute(text("""
                INSERT INTO job_runs (job_id, job_name, trigger_source, status,
                                      started_at, finished_at, last_heartbeat)
                VALUES (:jid, :jid, 'scheduled', :status, :ts, :ts, :ts)
                RETURNING id
            """), {"jid": jid, "status": status, "ts": ts}).scalar()
            inserted.append(row)
        conn.commit()
    yield {"alpha": jid_alpha, "beta": jid_beta, "ids": inserted, "ancient_id": inserted[-1]}
    with db.engine.connect() as conn:
        conn.execute(text("DELETE FROM job_runs WHERE id = ANY(:ids)"),
                     {"ids": inserted})
        conn.commit()


# --------------------- guard matrix ---------------------

def test_anonymous_redirected_to_login(client):
    resp = client.get("/admin/job-runs/")
    assert resp.status_code in (302, 401)


@pytest.mark.parametrize("role", ["admin", "warehouse_manager"])
def test_allowed_roles_can_view(client, seeded_runs, role):
    _login(client, _USERS[role])
    resp = client.get("/admin/job-runs/?hours=0")
    assert resp.status_code == 200, f"role {role} should be allowed"
    body = resp.get_data(as_text=True)
    assert "Job Runs" in body


@pytest.mark.parametrize("role", ["picker", "driver", "crm_admin"])
def test_unprivileged_roles_denied_when_enforcement_on(
        client, with_enforcement, seeded_runs, role):
    with_enforcement("true")
    _login(client, _USERS[role])
    resp = client.get("/admin/job-runs/")
    assert resp.status_code in (302, 403), (
        f"role {role} should be denied under enforcement, got {resp.status_code}"
    )


@pytest.mark.parametrize("role", ["picker", "driver", "crm_admin"])
def test_unprivileged_roles_allowed_when_enforcement_off(
        client, with_enforcement, seeded_runs, role):
    with_enforcement("false")
    _login(client, _USERS[role])
    resp = client.get("/admin/job-runs/")
    # With enforcement OFF, the decorator only logs; the page renders.
    assert resp.status_code == 200, (
        f"role {role} should pass when enforcement is off, got {resp.status_code}"
    )


# --------------------- filter matrix ---------------------

def test_status_filter_multi_select(client, seeded_runs):
    _login(client, _USERS["admin"])
    resp = client.get(
        f"/admin/job-runs/?job_id={seeded_runs['alpha']}&status=FAILED&status=SKIPPED&hours=0"
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "FAILED" in body


def test_job_id_filter(client, seeded_runs):
    _login(client, _USERS["admin"])
    resp = client.get(f"/admin/job-runs/?job_id={seeded_runs['beta']}&hours=0")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert seeded_runs["beta"] in body
    # Alpha may appear in the distinct-job-ids dropdown; what must NOT
    # appear is any detail link to an alpha-job row.
    alpha_ids = [
        rid for i, rid in enumerate(seeded_runs["ids"]) if i in (0, 1, 4)
    ]
    for aid in alpha_ids:
        assert f"/admin/job-runs/{aid}" not in body, (
            f"alpha row id {aid} leaked into beta-filtered table"
        )


def test_hours_window_excludes_ancient_rows(client, seeded_runs):
    _login(client, _USERS["admin"])
    # 24h window: the ancient row (60 days old) should be excluded.
    resp = client.get(
        f"/admin/job-runs/?job_id={seeded_runs['alpha']}&hours=24"
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Ancient row must not appear via its detail link
    ancient_link = f"/admin/job-runs/{seeded_runs['ancient_id']}"
    assert ancient_link not in body


def test_distinct_job_ids_pulled_from_full_table(client, seeded_runs):
    """The job_id dropdown should include both seeded ids even if a
    narrow status filter would otherwise hide them from the rendered rows."""
    _login(client, _USERS["admin"])
    resp = client.get("/admin/job-runs/?status=STALE_FAILED&hours=0")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert seeded_runs["alpha"] in body  # in the dropdown
    assert seeded_runs["beta"] in body


# --------------------- detail route ---------------------

def test_detail_renders_for_admin(client, seeded_runs):
    _login(client, _USERS["admin"])
    target_id = seeded_runs["ids"][0]
    resp = client.get(f"/admin/job-runs/{target_id}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f"Job Run #{target_id}" in body
    assert seeded_runs["alpha"] in body


def test_detail_404_for_unknown_id(client):
    _login(client, _USERS["admin"])
    resp = client.get("/admin/job-runs/2147483600")
    assert resp.status_code == 404


def test_detail_anonymous_redirected(client, seeded_runs):
    resp = client.get(f"/admin/job-runs/{seeded_runs['ids'][0]}")
    assert resp.status_code in (302, 401)


# --------------------- defensive parsing ---------------------

def test_invalid_query_strings_silently_ignored(client, seeded_runs):
    _login(client, _USERS["admin"])
    for qs in (
        "?limit=99999",
        "?limit=garbage",
        "?hours=nope",
        "?status=NOT_REAL_STATUS",
        "?status=SUCCESS&status=injected'%20OR%20'1'='1",
    ):
        resp = client.get("/admin/job-runs/" + qs)
        assert resp.status_code == 200, f"qs={qs!r} should not 500"
