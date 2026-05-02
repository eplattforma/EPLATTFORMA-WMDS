"""Task #14 — wildcard removal in the per-user permission editor."""
import os
import sys
import uuid
from types import SimpleNamespace

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest
from sqlalchemy import text


@pytest.fixture
def app_ctx():
    # Import main (not routes) so every blueprint base.html references is registered.
    import main  # noqa: F401
    from app import app, db
    # Function-scoped session reset to avoid ORM identity-map carryover between tests.
    with app.app_context():
        db.session.remove()
        yield app, db
        db.session.remove()


def _seed_target_user(db, username, role="warehouse_manager"):
    # Raw SQL avoids polluting the ORM identity map (Flask-Login's user_loader
    # picks up ORM-created users and they then survive raw-SQL cleanup).
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


def _has_perm(username, role, key):
    """Run the real has_permission() resolver (explicit rows + role fallback)."""
    from services.permissions import has_permission
    fake_user = SimpleNamespace(
        username=username,
        role=role,
        is_authenticated=True,
        get_id=lambda: username,
    )
    return has_permission(fake_user, key)


def _login(client, app, db, username):
    """Inject a Flask-Login session for `username` (User PK == username)."""
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
    # warehouse_manager target. '*' and 'comms.*' are direct (not in role);
    # 'picking.*' is inherited (warehouse_manager role grants picking.*).
    app, db = app_ctx
    name = f"t14_tgt_{uuid.uuid4().hex[:6]}"
    _seed_target_user(db, name, role="warehouse_manager")
    _seed_perm_rows(
        db, name,
        ["*", "comms.*", "picking.*", "menu.dashboard", "picking.perform"],
    )
    db.session.commit()
    yield name
    db.session.execute(text("DELETE FROM user_permissions WHERE username = :u"), {"u": name})
    db.session.execute(text("DELETE FROM users WHERE username = :u"), {"u": name})
    db.session.commit()


# ---------------------------------------------------------------------------
# GET render
# ---------------------------------------------------------------------------

def test_get_renders_direct_and_inherited_sections(app_ctx, admin_user, target_user):
    app, db = app_ctx
    client = app.test_client()
    _login(client, app, db, admin_user)
    r = client.get(f"/admin/users/{target_user}/permissions")
    assert r.status_code == 200
    body = r.data.decode()

    # Both sections rendered with their distinct headers
    assert "Direct Wildcard Grants" in body, "removable section missing"
    assert "Inherited Wildcards" in body, "read-only inherited section missing"

    # Direct wildcards (* and comms.*) get remove checkboxes
    assert 'name="remove_wildcards" value="*"' in body or 'value="*"' in body
    assert 'name="remove_wildcards"' in body
    assert 'value="comms.*"' in body
    assert 'name="confirm_remove_star"' in body

    # Inherited wildcard (picking.*) is rendered, but NOT as a remove checkbox.
    inherited_block = body[body.index("Inherited Wildcards"):]
    assert "picking.*" in inherited_block, "inherited wildcard must appear in read-only section"
    # No remove checkbox should target picking.* anywhere on the page.
    assert 'name="remove_wildcards" value="picking.*"' not in body, (
        "picking.* must not have a remove checkbox when inherited from role"
    )


# ---------------------------------------------------------------------------
# Removal of a direct (user-specific, non-role) wildcard
# ---------------------------------------------------------------------------

def test_remove_direct_wildcard_actually_revokes_access(app_ctx, admin_user, target_user):
    """Removing comms.* from warehouse_manager flips has_permission(comms.send) to False."""
    app, db = app_ctx
    client = app.test_client()
    _login(client, app, db, admin_user)

    # Strip '*' first so this test isolates the comms.* revocation
    # ('*' would still grant comms.send on its own).
    db.session.execute(
        text("DELETE FROM user_permissions WHERE username = :u AND permission_key = '*'"),
        {"u": target_user},
    )
    db.session.commit()

    # Sanity: before removal, has_permission grants comms.send via comms.*.
    assert _has_perm(target_user, "warehouse_manager", "comms.send") is True

    r = client.post(
        f"/admin/users/{target_user}/permissions",
        data={
            "action": "save",
            "remove_wildcards": ["comms.*"],
            "permission_keys": ["menu.dashboard", "picking.perform"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 302

    after = _perm_keys(db, target_user)
    assert "comms.*" not in after, "direct wildcard row must be deleted"

    # Effective access really flipped (no role fallback for comms.* on warehouse_manager,
    # and we cleared '*' above so nothing else can grant comms.send).
    assert _has_perm(target_user, "warehouse_manager", "comms.send") is False, (
        "comms.send must be denied after revoking comms.* "
        "(warehouse_manager role does not grant comms.*)"
    )


def test_remove_star_revokes_full_admin_when_role_is_not_admin(
    app_ctx, admin_user, target_user
):
    """Revoking '*' from warehouse_manager flips arbitrary keys to denied
    (warehouse_manager role doesn't grant '*')."""
    app, db = app_ctx
    client = app.test_client()
    _login(client, app, db, admin_user)

    # Before: the explicit '*' grants any arbitrary key via wildcard match.
    assert _has_perm(target_user, "warehouse_manager", "settings.manage_users") is True

    r = client.post(
        f"/admin/users/{target_user}/permissions",
        data={
            "action": "save",
            "remove_wildcards": ["*"],
            "confirm_remove_star": "YES",
            "permission_keys": ["menu.dashboard"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 302

    after = _perm_keys(db, target_user)
    assert "*" not in after

    # After: settings.manage_users is NOT a warehouse_manager role default,
    # and there's no explicit row for it, so access flips to denied.
    assert _has_perm(target_user, "warehouse_manager", "settings.manage_users") is False, (
        "settings.manage_users must be denied after revoking '*' from warehouse_manager"
    )


# ---------------------------------------------------------------------------
# Inherited wildcards: server must refuse to remove, effective access stays
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_target(app_ctx):
    # Second admin user (distinct from the actor) for the role-* coverage test.
    app, db = app_ctx
    name = f"t14_admt_{uuid.uuid4().hex[:6]}"
    _seed_target_user(db, name, role="admin")
    # 'picking.*' is not literally in admin's role list, but admin's '*' covers it.
    _seed_perm_rows(db, name, ["*", "picking.*"])
    db.session.commit()
    yield name
    db.session.execute(text("DELETE FROM user_permissions WHERE username = :u"), {"u": name})
    db.session.execute(text("DELETE FROM users WHERE username = :u"), {"u": name})
    db.session.commit()


def test_admin_role_star_covers_narrower_user_wildcard(app_ctx, admin_user, admin_target):
    """Admin role '*' covers narrower user wildcards (e.g. 'picking.*'):
    classified as inherited and refused for removal."""
    app, db = app_ctx
    client = app.test_client()
    _login(client, app, db, admin_user)

    # GET: picking.* must render in the read-only "Inherited" section, not direct.
    r = client.get(f"/admin/users/{admin_target}/permissions")
    assert r.status_code == 200
    body = r.data.decode()
    assert "Inherited Wildcards" in body
    inherited_block = body[body.index("Inherited Wildcards"):]
    assert "picking.*" in inherited_block, (
        "picking.* must render in Inherited section because role '*' covers it"
    )
    # No remove checkbox should target picking.* anywhere on the page.
    assert 'name="remove_wildcards" value="picking.*"' not in body, (
        "picking.* must not have a remove checkbox when role covers it"
    )

    # POST: even a forged form submitting picking.* must be refused.
    assert _has_perm(admin_target, "admin", "picking.perform") is True
    r = client.post(
        f"/admin/users/{admin_target}/permissions",
        data={
            "action": "save",
            "remove_wildcards": ["picking.*"],
            "permission_keys": [],
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    after = _perm_keys(db, admin_target)
    assert "picking.*" in after, (
        "picking.* row must be preserved: role '*' covers it, so removing "
        "would be a no-op for access (and a hidden time-bomb)"
    )
    # Effective access stays granted via role's '*'.
    assert _has_perm(admin_target, "admin", "picking.perform") is True


def test_inherited_wildcard_cannot_be_revoked_via_form(app_ctx, admin_user, target_user):
    """Submitting an inherited wildcard (picking.* for warehouse_manager) is
    refused; row stays and has_permission stays True via role fallback."""
    app, db = app_ctx
    client = app.test_client()
    _login(client, app, db, admin_user)

    assert "picking.*" in _perm_keys(db, target_user)
    assert _has_perm(target_user, "warehouse_manager", "picking.perform") is True

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
    assert "picking.*" in after, (
        "picking.* must NOT be deleted via this form: it's inherited from "
        "the warehouse_manager role and removing the explicit row would "
        "create a misleading 'revoked' UX without changing effective access."
    )
    # Belt-and-braces: even if the row had been deleted, role fallback grants it.
    assert _has_perm(target_user, "warehouse_manager", "picking.perform") is True


# ---------------------------------------------------------------------------
# Confirmation gates for '*'
# ---------------------------------------------------------------------------

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
    assert "picking.perform" in after, (
        "non-wildcard rows should not be rewritten when star removal is blocked"
    )


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


# ---------------------------------------------------------------------------
# Forgery protections
# ---------------------------------------------------------------------------

def test_forged_non_wildcard_in_remove_field_is_ignored(app_ctx, admin_user, target_user):
    """A malicious form putting non-wildcards in remove_wildcards must be filtered out."""
    app, db = app_ctx
    client = app.test_client()
    _login(client, app, db, admin_user)

    r = client.post(
        f"/admin/users/{target_user}/permissions",
        data={
            "action": "save",
            # menu.dashboard is NOT a wildcard — must be filtered out by the route guard.
            # comms.* IS a removable direct wildcard (not in warehouse_manager role).
            "remove_wildcards": ["menu.dashboard", "comms.*"],
            "permission_keys": ["menu.dashboard", "picking.perform"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 302

    after = _perm_keys(db, target_user)
    assert "comms.*" not in after, "valid wildcard from remove_wildcards must be removed"
    assert "menu.dashboard" in after, "non-wildcard must NOT be removed via remove_wildcards"
    assert "*" in after


def test_forged_star_substring_is_ignored(app_ctx, admin_user, target_user):
    """A key containing '*' but not in valid wildcard form (k == '*' or k.endswith('.*'))
    must be rejected by the strict server filter."""
    app, db = app_ctx
    client = app.test_client()
    _login(client, app, db, admin_user)

    r = client.post(
        f"/admin/users/{target_user}/permissions",
        data={
            "action": "save",
            # 'pick*ing' contains '*' but is NOT a valid wildcard. The strict
            # filter (w == '*' or w.endswith('.*')) must drop it. The valid
            # entry 'comms.*' must still be processed in the same request.
            "remove_wildcards": ["pick*ing", "foo*bar.*x", "comms.*"],
            "permission_keys": ["menu.dashboard", "picking.perform"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 302

    after = _perm_keys(db, target_user)
    assert "comms.*" not in after, "valid wildcard must still be removed"
    # Forged substring entries can't have removed anything they didn't match,
    # but more importantly: the legitimate wildcards left over (e.g. '*') must
    # remain untouched, because forged keys never reach the DELETE.
    assert "*" in after, "'*' must NOT be revoked via a forged substring entry"
    assert "menu.dashboard" in after


# ---------------------------------------------------------------------------
# Reset to role defaults (existing flow must still work)
# ---------------------------------------------------------------------------

def test_reset_to_role_defaults_still_works(app_ctx, admin_user, target_user):
    app, db = app_ctx
    client = app.test_client()
    _login(client, app, db, admin_user)

    # Sanity: before reset, target has the user-specific comms.* and *.
    before = _perm_keys(db, target_user)
    assert "comms.*" in before
    assert "*" in before

    r = client.post(
        f"/admin/users/{target_user}/permissions",
        data={"action": "reset_role"},
        follow_redirects=False,
    )
    assert r.status_code == 302

    after = _perm_keys(db, target_user)
    # Reset restores role-default wildcards (picking.*, cooler.*) and
    # role-default granular grants. Direct/user-specific wildcards
    # (* and comms.*) get dropped because they aren't role defaults.
    assert "picking.*" in after
    assert "cooler.*" in after
    assert "menu.dashboard" in after
    assert "*" not in after, "direct '*' grant must be cleared by reset"
    assert "comms.*" not in after, "direct 'comms.*' grant must be cleared by reset"
