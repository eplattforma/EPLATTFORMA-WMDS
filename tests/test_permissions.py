"""Unit tests for services.permissions."""
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
    assert os.environ.get("DATABASE_URL"), "DATABASE_URL required"
    import main  # noqa: F401
    from app import app, db
    with app.app_context():
        db.session.remove()
        yield app, db
        db.session.remove()


@pytest.fixture
def role_fallback_on(app_ctx):
    _, db = app_ctx
    from models import Setting
    prev = Setting.get(db.session, "permissions_role_fallback_enabled", "true")
    Setting.set(db.session, "permissions_role_fallback_enabled", "true")
    db.session.commit()
    yield
    Setting.set(db.session, "permissions_role_fallback_enabled", prev)
    db.session.commit()


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


def _user(username, role):
    return SimpleNamespace(
        username=username, role=role, is_authenticated=True,
        get_id=lambda: username,
    )


def test_explicit_grant_beats_role_fallback(app_ctx, role_fallback_on):
    _, db = app_ctx
    name = f"t15_eg_{uuid.uuid4().hex[:6]}"
    _seed_user(db, name, "picker")
    db.session.commit()
    from services.permissions import has_permission
    u = _user(name, "picker")
    try:
        assert has_permission(u, "settings.manage_users") is False
        _seed_perm(db, name, "settings.manage_users")
        db.session.commit()
        assert has_permission(u, "settings.manage_users") is True
    finally:
        _cleanup(db, name)


def test_wildcard_picking_star_covers_picking_manage_batches(app_ctx, role_fallback_on):
    from services.permissions import has_permission
    wm = _user("t15_wm_synth", "warehouse_manager")
    assert has_permission(wm, "picking.manage_batches") is True
    assert has_permission(wm, "settings.manage_users") is False


def test_matches_unit():
    from services.permissions import _matches
    assert _matches("*", "any.thing") is True
    assert _matches("picking.manage_batches", "picking.manage_batches") is True
    assert _matches("picking.*", "picking.manage_batches") is True
    assert _matches("picking.*", "picking") is True
    assert _matches("picking.*", "pickup.manage_batches") is False
    assert _matches("picking.manage_batches", "picking.other") is False


def test_unauthenticated_user_always_denied(app_ctx):
    from services.permissions import has_permission
    assert has_permission(None, "menu.dashboard") is False
    anon = SimpleNamespace(
        username=None, role=None, is_authenticated=False, get_id=lambda: None
    )
    assert has_permission(anon, "menu.dashboard") is False
    assert has_permission(anon, "anything.goes") is False


def test_anon_check_does_not_poison_request_cache(app_ctx):
    app, db = app_ctx
    from flask import g
    from services.permissions import has_permission
    name = f"t15_ac_{uuid.uuid4().hex[:6]}"
    _seed_user(db, name, "picker")
    _seed_perm(db, name, "settings.manage_users")
    db.session.commit()
    try:
        with app.test_request_context("/"):
            anon = SimpleNamespace(
                username=None, role=None, is_authenticated=False, get_id=lambda: None
            )
            assert has_permission(anon, "menu.dashboard") is False
            cache = getattr(g, "_user_permissions_cache", None)
            if cache is not None:
                assert None not in cache and "" not in cache
                assert name not in cache
            assert has_permission(_user(name, "picker"), "settings.manage_users") is True
            cache = getattr(g, "_user_permissions_cache", None)
            assert cache is not None and name in cache
            assert "settings.manage_users" in cache[name]
    finally:
        _cleanup(db, name)


def test_crm_admin_role_grants(app_ctx, role_fallback_on):
    from services.permissions import has_permission, ROLE_PERMISSIONS
    assert "menu.communications" in ROLE_PERMISSIONS["crm_admin"]
    crm = _user("t15_crm_synth", "crm_admin")
    assert has_permission(crm, "menu.communications") is True
    assert has_permission(crm, "comms.send") is True
    assert has_permission(crm, "menu.warehouse") is False


def test_seeder_grants_admin_star_and_is_idempotent(app_ctx, role_fallback_on):
    _, db = app_ctx
    from models import Setting

    before = set(db.session.execute(
        text("SELECT username, permission_key FROM user_permissions")
    ).fetchall())
    prev_marker = Setting.get(db.session, "permissions_auto_seed_done", "false")

    name = f"t15_admin_{uuid.uuid4().hex[:6]}"
    _seed_user(db, name, "admin")
    db.session.commit()
    try:
        from services.permission_seeding import seed_permissions_from_roles
        seed_permissions_from_roles(force=True)
        keys = {r[0] for r in db.session.execute(
            text("SELECT permission_key FROM user_permissions WHERE username = :u"),
            {"u": name},
        ).fetchall()}
        assert "*" in keys

        seed_permissions_from_roles(force=True)
        keys2 = {r[0] for r in db.session.execute(
            text("SELECT permission_key FROM user_permissions WHERE username = :u"),
            {"u": name},
        ).fetchall()}
        assert keys2 == keys

        from services.permissions import has_permission
        assert has_permission(_user(name, "admin"), "any.unmapped.key") is True
    finally:
        _cleanup(db, name)
        after = set(db.session.execute(
            text("SELECT username, permission_key FROM user_permissions")
        ).fetchall())
        for u, k in (after - before):
            db.session.execute(
                text("DELETE FROM user_permissions WHERE username = :u AND permission_key = :k"),
                {"u": u, "k": k},
            )
        Setting.set(db.session, "permissions_auto_seed_done", prev_marker)
        db.session.commit()
