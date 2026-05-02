"""Flask test-client coverage for @require_permission on protected routes.

Routes (decorator key, body role check):
  GET /admin/batch/manage           picking.manage_batches  body: {admin, wm}
  GET /datawarehouse/full-sync      sync.run_manual         body: {admin}
  GET /admin/users/<u>/permissions  settings.manage_users   body: none

The explicit-grant user is role='warehouse_manager' (non-admin) with
permissions_role_fallback_enabled=False, so role contributes nothing and
only the explicit user_permissions rows can pass the decorator.
"""
import os
import sys
import uuid

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


def _seed_perm(db, username, key):
    db.session.execute(
        text(
            "INSERT INTO user_permissions (username, permission_key, granted_by) "
            "VALUES (:u, :k, 't15') "
            "ON CONFLICT (username, permission_key) DO NOTHING"
        ),
        {"u": username, "k": key},
    )


def _cleanup(db, username):
    db.session.execute(
        text("DELETE FROM user_permissions WHERE username = :u"), {"u": username}
    )
    db.session.execute(text("DELETE FROM users WHERE username = :u"), {"u": username})
    db.session.commit()


def _login(client, username):
    with client.session_transaction() as sess:
        sess["_user_id"] = username
        sess["_fresh"] = True


@pytest.fixture
def role_users(app_ctx):
    _, db = app_ctx
    suffix = uuid.uuid4().hex[:6]
    users = {
        "admin": f"t15_admin_{suffix}",
        "warehouse_manager": f"t15_wm_{suffix}",
        "picker": f"t15_picker_{suffix}",
    }
    for role, name in users.items():
        _seed_user(db, name, role)
    db.session.commit()
    try:
        yield users
    finally:
        for name in users.values():
            _cleanup(db, name)


@pytest.fixture
def explicit_grant_user(app_ctx):
    """Non-admin (role='warehouse_manager') with no '*' row, only three
    exact explicit grants. Used with role_fallback OFF so the explicit
    rows are the sole source of permissions."""
    _, db = app_ctx
    name = f"t15_explicit_{uuid.uuid4().hex[:6]}"
    _seed_user(db, name, "warehouse_manager")
    _seed_perm(db, name, "picking.manage_batches")
    _seed_perm(db, name, "sync.run_manual")
    _seed_perm(db, name, "settings.manage_users")
    db.session.commit()
    try:
        yield name
    finally:
        _cleanup(db, name)


def _set_settings(db, enforce, fallback):
    from models import Setting
    prev_e = Setting.get(db.session, "permissions_enforcement_enabled", "false")
    prev_f = Setting.get(db.session, "permissions_role_fallback_enabled", "true")
    Setting.set(db.session, "permissions_enforcement_enabled", "true" if enforce else "false")
    Setting.set(db.session, "permissions_role_fallback_enabled", "true" if fallback else "false")
    db.session.commit()
    return prev_e, prev_f


def _restore_settings(db, prev_e, prev_f):
    from models import Setting
    Setting.set(db.session, "permissions_enforcement_enabled", prev_e)
    Setting.set(db.session, "permissions_role_fallback_enabled", prev_f)
    db.session.commit()


@pytest.fixture
def enforcement_on(app_ctx):
    _, db = app_ctx
    prev = _set_settings(db, enforce=True, fallback=True)
    yield
    _restore_settings(db, *prev)


@pytest.fixture
def enforcement_no_fallback(app_ctx):
    _, db = app_ctx
    prev = _set_settings(db, enforce=True, fallback=False)
    yield
    _restore_settings(db, *prev)


# ---------------------------------------------------------------------------
# Role matrix: 3 routes x 3 standard roles. Cells reflect the union of
# decorator + body role check. Cells that are 403 because of admin-only
# ROLE_PERMISSIONS or admin-only body are deliberate — they pin the role
# table so future widening of role grants would flip them and fail loudly.
# ---------------------------------------------------------------------------
ROUTE_MATRIX = [
    ("/admin/batch/manage",            "admin",             200),
    ("/admin/batch/manage",            "warehouse_manager", 200),
    ("/admin/batch/manage",            "picker",            403),
    ("/datawarehouse/full-sync",       "admin",             200),
    ("/datawarehouse/full-sync",       "warehouse_manager", 403),
    ("/datawarehouse/full-sync",       "picker",            403),
    ("__user_perms__",                 "admin",             200),
    ("__user_perms__",                 "warehouse_manager", 403),
    ("__user_perms__",                 "picker",            403),
]


@pytest.mark.parametrize("path,role,expected", ROUTE_MATRIX)
def test_role_matrix(app_ctx, role_users, enforcement_on, path, role, expected):
    client = app_ctx[0].test_client()
    _login(client, role_users[role])
    if path == "__user_perms__":
        path = f"/admin/users/{role_users['picker']}/permissions"
    assert client.get(path).status_code == expected


# ---------------------------------------------------------------------------
# Explicit-grant non-admin user: role='warehouse_manager', no role fallback.
# Body checks: batch allows wm; user-mgmt has no body. Sync body is
# admin-only, so wm is rejected by the route body even with the explicit
# decorator key — documented as 302 and asserted.
# ---------------------------------------------------------------------------

def test_explicit_grant_passes_decorator_on_batch(
    app_ctx, explicit_grant_user, enforcement_no_fallback
):
    client = app_ctx[0].test_client()
    _login(client, explicit_grant_user)
    assert client.get("/admin/batch/manage").status_code == 200


def test_explicit_grant_passes_decorator_on_user_mgmt(
    app_ctx, explicit_grant_user, enforcement_no_fallback
):
    client = app_ctx[0].test_client()
    _login(client, explicit_grant_user)
    r = client.get(f"/admin/users/{explicit_grant_user}/permissions")
    assert r.status_code == 200


def test_explicit_grant_blocked_by_admin_only_body_on_sync(
    app_ctx, explicit_grant_user, enforcement_no_fallback
):
    client = app_ctx[0].test_client()
    _login(client, explicit_grant_user)
    r = client.get("/datawarehouse/full-sync", follow_redirects=False)
    assert r.status_code == 302


def test_explicit_grant_user_without_grants_is_denied(
    app_ctx, enforcement_no_fallback
):
    """Same role but NO user_permissions rows -> decorator denies."""
    _, db = app_ctx
    name = f"t15_nogrants_{uuid.uuid4().hex[:6]}"
    _seed_user(db, name, "warehouse_manager")
    db.session.commit()
    try:
        client = app_ctx[0].test_client()
        _login(client, name)
        assert client.get("/admin/batch/manage").status_code == 403
        assert client.get(f"/admin/users/{name}/permissions").status_code == 403
    finally:
        _cleanup(db, name)
