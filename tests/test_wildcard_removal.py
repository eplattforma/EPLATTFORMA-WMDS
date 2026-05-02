"""Integration tests for Task #14 — wildcard removal in per-user permission editor.

Exercises the new `remove_wildcards` form field and confirmation gates
added to `manage_user_permissions` in routes.py.
"""
import os
import sys
import uuid

# Ensure the project root is importable regardless of the cwd pytest is
# invoked from. Mirrors the bootstrap used by
# tests/test_override_ordering_pipeline.py so this file works under both
# `pytest tests/...` from the repo root and from any other cwd.
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest
from sqlalchemy import text


@pytest.fixture
def app_ctx():
    # Import the gunicorn entry-point module (`main.py`, run as `main:app`
    # by gunicorn_config.py). It registers every blueprint base.html
    # references — warehouse, batch, routes, etc. Importing only `routes`
    # is not enough to render even a single permission editor page.
    import main  # noqa: F401
    from app import app, db
    # Function-scoped: close any leftover scoped session from a prior test
    # so the ORM identity map cannot carry stale User instances across
    # tests (Flask-Login's user_loader and the editor view both populate
    # the map on every request).
    with app.app_context():
        db.session.remove()
        yield app, db
        db.session.remove()


def _seed_target_user(db, username, role="warehouse_manager"):
    """Create a user via raw SQL to bypass the ORM identity map.

    The user_loader (`User.query.get(username)`) in routes.py runs on every
    request via Flask-Login. Using the ORM here pulls the new user into a
    long-lived identity map that persists across tests, which causes
    `ObjectDeletedError` when later tests reuse the session after cleanup.
    Raw INSERT keeps the session pristine.
    """
    from werkzeug.security import generate_password_hash
    db.session.execute(
        text(
            "INSERT INTO users (username, password, role, is_active) "
            "VALUES (:u, :p, :r, true)"
        ),
        {"u": username, "p": generate_password_hash("dummy123"), "r": role},
    )


def _seed_perm_rows(db, username, keys, granted_by="seed"):
    for k in keys:
        db.session.execute(
            text(
                "INSERT INTO user_permissions (username, permission_key, granted_by) "
                "VALUES (:u, :k, :by) "
                "ON CONFLICT (username, permission_key) DO NOTHING"
            ),
            {"u": username, "k": k, "by": granted_by},
        )


def _perm_keys(db, username):
    rows = db.session.execute(
        text("SELECT permission_key FROM user_permissions WHERE username = :u"),
        {"u": username},
    ).fetchall()
    return {r[0] for r in rows}


def _login(client, app, db, username):
    """Inject Flask-Login session for `username` without going through /login.

    The User model uses `username` as primary key and `get_id` returns the
    username, so Flask-Login expects `_user_id` to be the username string.
    Existence is verified via raw SQL (no ORM identity map pollution).
    """
    row = db.session.execute(
        text("SELECT 1 FROM users WHERE username = :u"),
        {"u": username},
    ).fetchone()
    assert row is not None, f"login fixture user {username} missing"
    with client.session_transaction() as sess:
        sess["_user_id"] = username
        sess["_fresh"] = True


@pytest.fixture
def admin_user(app_ctx):
    app, db = app_ctx
    name = f"t14_admin_{uuid.uuid4().hex[:6]}"
    _seed_target_user(db, name, role="admin")
    _seed_perm_rows(db, name, ["*"])
    db.session.commit()
    yield name
    db.session.execute(text("DELETE FROM user_permissions WHERE username = :u"), {"u": name})
    db.session.execute(text("DELETE FROM users WHERE username = :u"), {"u": name})
    db.session.commit()


@pytest.fixture
def target_user(app_ctx):
    app, db = app_ctx
    name = f"t14_tgt_{uuid.uuid4().hex[:6]}"
    _seed_target_user(db, name, role="warehouse_manager")
    _seed_perm_rows(
        db, name,
        ["*", "picking.*", "menu.dashboard", "picking.perform"],
    )
    db.session.commit()
    yield name
    db.session.execute(text("DELETE FROM user_permissions WHERE username = :u"), {"u": name})
    db.session.execute(text("DELETE FROM users WHERE username = :u"), {"u": name})
    db.session.commit()


def test_get_renders_with_wildcard_card(app_ctx, admin_user, target_user):
    app, db = app_ctx
    client = app.test_client()
    _login(client, app, db, admin_user)
    r = client.get(f"/admin/users/{target_user}/permissions")
    assert r.status_code == 200
    body = r.data.decode()
    assert "Wildcard Grants" in body
    assert 'name="remove_wildcards"' in body
    assert 'name="confirm_remove_star"' in body
    # Both wildcards rendered as removal checkboxes
    assert 'value="*"' in body
    assert 'value="picking.*"' in body


def test_remove_non_star_wildcard(app_ctx, admin_user, target_user):
    app, db = app_ctx
    client = app.test_client()
    _login(client, app, db, admin_user)

    before = _perm_keys(db, target_user)
    assert "picking.*" in before
    assert "*" in before
    assert "menu.dashboard" in before

    r = client.post(
        f"/admin/users/{target_user}/permissions",
        data={
            "action": "save",
            "remove_wildcards": ["picking.*"],
            "permission_keys": ["menu.dashboard", "picking.perform"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 302

    after = _perm_keys(db, target_user)
    assert "picking.*" not in after, "non-star wildcard should be removed"
    assert "*" in after, "star must be untouched"
    assert "menu.dashboard" in after
    assert "picking.perform" in after


def test_star_removal_blocked_without_confirm(app_ctx, admin_user, target_user):
    app, db = app_ctx
    client = app.test_client()
    _login(client, app, db, admin_user)

    r = client.post(
        f"/admin/users/{target_user}/permissions",
        data={
            "action": "save",
            "remove_wildcards": ["*"],
            "permission_keys": ["menu.dashboard"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 302

    after = _perm_keys(db, target_user)
    assert "*" in after, "star must remain when confirm_remove_star not set"
    # Non-wildcard pass must NOT have been applied either (whole save aborts)
    assert "picking.perform" in after, "non-wildcard rows should not be rewritten when star removal is blocked"


def test_star_removal_blocked_on_self(app_ctx, admin_user):
    app, db = app_ctx
    client = app.test_client()
    _login(client, app, db, admin_user)

    r = client.post(
        f"/admin/users/{admin_user}/permissions",
        data={
            "action": "save",
            "remove_wildcards": ["*"],
            "confirm_remove_star": "YES",
            "permission_keys": [],
        },
        follow_redirects=False,
    )
    assert r.status_code == 302

    after = _perm_keys(db, admin_user)
    assert "*" in after, "self-lockout guard must keep star on the actor's own account"


def test_star_removal_succeeds_with_confirm_on_other_user(app_ctx, admin_user, target_user):
    app, db = app_ctx
    client = app.test_client()
    _login(client, app, db, admin_user)

    r = client.post(
        f"/admin/users/{target_user}/permissions",
        data={
            "action": "save",
            "remove_wildcards": ["*", "picking.*"],
            "confirm_remove_star": "YES",
            "permission_keys": ["menu.dashboard"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 302

    after = _perm_keys(db, target_user)
    assert "*" not in after
    assert "picking.*" not in after
    assert after == {"menu.dashboard"}, f"unexpected key set: {after}"


def test_forged_non_wildcard_in_remove_field_is_ignored(app_ctx, admin_user, target_user):
    """A malicious form putting non-wildcards in remove_wildcards should be filtered out."""
    app, db = app_ctx
    client = app.test_client()
    _login(client, app, db, admin_user)

    r = client.post(
        f"/admin/users/{target_user}/permissions",
        data={
            "action": "save",
            # menu.dashboard is NOT a wildcard — should be filtered out by the route guard.
            "remove_wildcards": ["menu.dashboard", "picking.*"],
            "permission_keys": ["menu.dashboard", "picking.perform"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 302

    after = _perm_keys(db, target_user)
    assert "picking.*" not in after, "wildcard from remove_wildcards must be removed"
    assert "menu.dashboard" in after, "non-wildcard must NOT be removed via remove_wildcards"
    assert "*" in after


def test_forged_star_substring_is_ignored(app_ctx, admin_user, target_user):
    """A key containing '*' but not in valid wildcard form (k == '*' or k.endswith('.*'))
    must be rejected by the server filter — locks in the strict matcher."""
    app, db = app_ctx
    client = app.test_client()
    _login(client, app, db, admin_user)

    r = client.post(
        f"/admin/users/{target_user}/permissions",
        data={
            "action": "save",
            # 'pick*ing' contains '*' but is NOT a valid wildcard. The strict
            # filter (w == '*' or w.endswith('.*')) must drop it. The valid
            # entry 'picking.*' must still be processed in the same request.
            "remove_wildcards": ["pick*ing", "foo*bar.*x", "picking.*"],
            "permission_keys": ["menu.dashboard", "picking.perform"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 302

    after = _perm_keys(db, target_user)
    assert "picking.*" not in after, "valid wildcard must still be removed"
    # Forged substring entries can't have removed anything they didn't match,
    # but more importantly: the legitimate wildcards left over (e.g. '*') must
    # remain untouched, because forged keys never reach the DELETE.
    assert "*" in after, "'*' must NOT be revoked via a forged substring entry"
    assert "menu.dashboard" in after


def test_reset_to_role_defaults_still_works(app_ctx, admin_user, target_user):
    app, db = app_ctx
    client = app.test_client()
    _login(client, app, db, admin_user)

    r = client.post(
        f"/admin/users/{target_user}/permissions",
        data={"action": "reset_role"},
        follow_redirects=False,
    )
    assert r.status_code == 302

    after = _perm_keys(db, target_user)
    # Reset should restore role-default wildcards (picking.*, cooler.*) and
    # role-default granular grants. * itself was never a role default for
    # warehouse_manager, so it gets dropped here.
    assert "picking.*" in after
    assert "cooler.*" in after
    assert "menu.dashboard" in after
