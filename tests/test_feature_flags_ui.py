"""Task #27 — Feature Flags Admin UI tests.

Runs against the project's standard pytest harness (in-memory SQLite via
``tests/conftest.py``). Verifies that:

- The Feature Flags section is gated to ``admin`` only.
- The POST handler enforces a strict whitelist of writable keys.
- Boolean toggles round-trip the value into the ``settings`` table.
- The ``job_runs_retention_days`` numeric field validates 0..365.
- Every flag change writes an ``activity_logs`` row with a structured
  JSON payload.
- The read-only ``permissions_auto_seed_done`` key cannot be written.
- All keys declared in ``FEATURE_FLAG_KEYS`` are surfaced on the page
  with their current value.
"""
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import json

import pytest
from werkzeug.security import generate_password_hash


# ---------------------------------------------------------------------------
# Blueprint registration: base.html references many blueprints (warehouse,
# admin_job_runs, analytics, …) that the slim conftest does not register. We
# need them present for ``/admin/settings`` to render. Register them once,
# tolerating re-registration when the test app is re-created per function.
# ---------------------------------------------------------------------------

_REQUIRED_BLUEPRINTS_LOADED = False


@pytest.fixture(autouse=True)
def _register_required_blueprints(app):
    """Auto-discover every ``routes_*.py`` and ``blueprints/*.py`` module
    and register any Flask ``Blueprint`` it defines on the test app, so the
    shared ``base.html`` navbar can resolve every ``url_for(...)``.
    """
    global _REQUIRED_BLUEPRINTS_LOADED
    if _REQUIRED_BLUEPRINTS_LOADED:
        return
    import glob
    import importlib
    import logging
    from flask import Blueprint as _BP

    candidates = []
    for path in glob.glob(os.path.join(PROJECT_ROOT, 'routes_*.py')):
        name = os.path.basename(path)[:-3]
        candidates.append(name)
    for path in glob.glob(os.path.join(PROJECT_ROOT, 'blueprints', '*.py')):
        base = os.path.basename(path)[:-3]
        if base == '__init__':
            continue
        candidates.append(f'blueprints.{base}')
    # `datawarehouse_routes` and `dw_analytics_routes` are also already loaded
    # by `import routes` in conftest, but list them defensively.
    candidates += [
        'datawarehouse_routes',
        'dw_analytics_routes',
        'forecast_workbench',
    ]

    for mod_name in candidates:
        try:
            mod = importlib.import_module(mod_name)
        except Exception as e:
            logging.debug(f"ff-ui setup: skip import {mod_name}: {e}")
            continue
        for attr in dir(mod):
            obj = getattr(mod, attr)
            if not isinstance(obj, _BP):
                continue
            if obj.name in app.blueprints:
                continue
            try:
                app.register_blueprint(obj)
            except Exception as e:
                logging.debug(
                    f"ff-ui setup: register {mod_name}.{attr} ({obj.name}) failed: {e}"
                )

    # base.html and admin_settings.html call `has_permission(...)`, which is
    # exposed to Jinja by `services.permissions.register_template_helpers`.
    # The slim conftest does not register this; do it here for our renders.
    try:
        from services.permissions import register_template_helpers
        register_template_helpers(app)
    except Exception as e:
        logging.debug(f"ff-ui setup: register_template_helpers failed: {e}")

    _REQUIRED_BLUEPRINTS_LOADED = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_warehouse_user(app):
    """conftest.py provides admin/picker/driver/crm — add a warehouse_manager."""
    from app import db
    from models import User
    with app.app_context():
        existing = User.query.filter_by(username='test_wh_manager').first()
        if not existing:
            u = User(
                username='test_wh_manager',
                password=generate_password_hash('test_password'),
                role='warehouse_manager',
            )
            db.session.add(u)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()


def _login(client, username, password='test_password'):
    resp = client.post('/login', data={
        'username': username,
        'password': password,
    })
    assert resp.status_code == 302, f"login failed for {username}: {resp.status_code}"
    return client


def _get_setting_value(app, key):
    from app import db
    from models import Setting
    with app.app_context():
        return Setting.get(db.session, key, None)


def _count_flag_change_logs(app, key=None):
    from app import db
    from models import ActivityLog
    with app.app_context():
        q = db.session.query(ActivityLog).filter_by(
            activity_type='feature_flag_change'
        )
        rows = q.all()
        if key is None:
            return len(rows)
        return sum(
            1 for r in rows
            if r.details and json.loads(r.details).get('key') == key
        )


# ---------------------------------------------------------------------------
# T1 — Admin sees the Feature Flags section
# ---------------------------------------------------------------------------

def test_t1_admin_sees_feature_flags_section(client):
    _login(client, 'test_admin_user')
    resp = client.get('/admin/settings')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'Feature Flags' in body
    assert 'feature-flags-card' in body
    # All five group headings (as defined in routes.FEATURE_FLAG_METADATA)
    # should render. Jinja escapes ``&`` to ``&amp;``, so accept either form.
    for group in ['Permissions', 'Job Runs & Logging', 'Batch Picking',
                  'Cooler Picking (not yet built)', 'Cockpit']:
        escaped = group.replace('&', '&amp;')
        assert (group in body) or (escaped in body), \
            f"missing group heading: {group}"


# ---------------------------------------------------------------------------
# T2 — Warehouse manager does NOT see the section
# ---------------------------------------------------------------------------

def test_t2_warehouse_manager_does_not_see_feature_flags(app, client):
    _ensure_warehouse_user(app)
    _login(client, 'test_wh_manager')
    resp = client.get('/admin/settings')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'feature-flags-card' not in body
    assert 'Feature Flags' not in body


# ---------------------------------------------------------------------------
# T3 — Admin POSTs a valid flag toggle, value is persisted + logged
# ---------------------------------------------------------------------------

def test_t3_admin_post_valid_flag_persists_and_logs(app, client):
    _login(client, 'test_admin_user')
    resp = client.post('/admin/settings', data={
        'flag_key': 'forecast_watchdog_enabled',
        'flag_value': 'true',
    }, follow_redirects=False)
    assert resp.status_code == 302  # redirect after POST
    assert _get_setting_value(app, 'forecast_watchdog_enabled') == 'true'
    assert _count_flag_change_logs(app, 'forecast_watchdog_enabled') == 1


# ---------------------------------------------------------------------------
# T4 — Whitelist enforcement: arbitrary keys are rejected
# ---------------------------------------------------------------------------

def test_t4_admin_post_unknown_flag_key_rejected(app, client):
    _login(client, 'test_admin_user')
    resp = client.post('/admin/settings', data={
        'flag_key': 'arbitrary_setting_not_in_whitelist',
        'flag_value': 'true',
    }, follow_redirects=False)
    assert resp.status_code == 400
    # The setting must not have been created.
    assert _get_setting_value(app, 'arbitrary_setting_not_in_whitelist') is None


# ---------------------------------------------------------------------------
# T5 — `permissions_auto_seed_done` is read-only (not in writable whitelist)
# ---------------------------------------------------------------------------

def test_t5_admin_post_readonly_key_rejected(app, client):
    _login(client, 'test_admin_user')
    resp = client.post('/admin/settings', data={
        'flag_key': 'permissions_auto_seed_done',
        'flag_value': 'true',
    }, follow_redirects=False)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# T6 — `job_runs_retention_days` accepts 0
# ---------------------------------------------------------------------------

def test_t6_retention_days_zero_is_valid(app, client):
    _login(client, 'test_admin_user')
    resp = client.post('/admin/settings', data={
        'flag_key': 'job_runs_retention_days',
        'flag_value': '0',
    }, follow_redirects=False)
    assert resp.status_code == 302
    assert _get_setting_value(app, 'job_runs_retention_days') == '0'


# ---------------------------------------------------------------------------
# T7 — `job_runs_retention_days` rejects negative values
# ---------------------------------------------------------------------------

def test_t7_retention_days_negative_rejected(app, client):
    _login(client, 'test_admin_user')
    # Seed a known good value so we can detect non-mutation.
    client.post('/admin/settings', data={
        'flag_key': 'job_runs_retention_days',
        'flag_value': '7',
    }, follow_redirects=False)
    resp = client.post('/admin/settings', data={
        'flag_key': 'job_runs_retention_days',
        'flag_value': '-1',
    }, follow_redirects=False)
    assert resp.status_code == 400
    assert _get_setting_value(app, 'job_runs_retention_days') == '7'


# ---------------------------------------------------------------------------
# T8 — `job_runs_retention_days` rejects > 365
# ---------------------------------------------------------------------------

def test_t8_retention_days_over_max_rejected(app, client):
    _login(client, 'test_admin_user')
    client.post('/admin/settings', data={
        'flag_key': 'job_runs_retention_days',
        'flag_value': '30',
    }, follow_redirects=False)
    resp = client.post('/admin/settings', data={
        'flag_key': 'job_runs_retention_days',
        'flag_value': '366',
    }, follow_redirects=False)
    assert resp.status_code == 400
    assert _get_setting_value(app, 'job_runs_retention_days') == '30'


# ---------------------------------------------------------------------------
# T9 — Each toggle writes an ActivityLog with structured JSON details
# ---------------------------------------------------------------------------

def test_t9_toggle_writes_structured_activity_log(app, client):
    from app import db
    from models import ActivityLog
    _login(client, 'test_admin_user')
    resp = client.post('/admin/settings', data={
        'flag_key': 'cockpit_enabled',
        'flag_value': 'true',
    }, follow_redirects=False)
    assert resp.status_code == 302
    with app.app_context():
        rows = (
            db.session.query(ActivityLog)
            .filter_by(activity_type='feature_flag_change')
            .all()
        )
        # Find the row we just wrote.
        match = None
        for r in rows:
            if not r.details:
                continue
            payload = json.loads(r.details)
            if payload.get('key') == 'cockpit_enabled':
                match = (r, payload)
                break
        assert match is not None, "no ActivityLog row written for cockpit_enabled"
        row, payload = match
        assert row.picker_username == 'test_admin_user'
        assert payload['key'] == 'cockpit_enabled'
        assert payload['new_value'] == 'true'
        assert 'old_value' in payload


# ---------------------------------------------------------------------------
# T10 — Every flag in FEATURE_FLAG_KEYS (+ the read-only key) is surfaced
# ---------------------------------------------------------------------------

def test_t10_all_flags_surface_with_current_value(client):
    from routes import FEATURE_FLAG_KEYS
    _login(client, 'test_admin_user')
    resp = client.get('/admin/settings')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Every writable key surfaces…
    for key in FEATURE_FLAG_KEYS:
        assert key in body, f"flag key not rendered on the page: {key}"
    # …and the read-only informational key surfaces too.
    assert 'permissions_auto_seed_done' in body


# ---------------------------------------------------------------------------
# Bonus — round-trip: deactivate then reactivate writes two ActivityLog rows
# ---------------------------------------------------------------------------

def test_round_trip_writes_two_activity_log_rows(app, client):
    _login(client, 'test_admin_user')
    # ON
    r1 = client.post('/admin/settings', data={
        'flag_key': 'cooler_labels_enabled',
        'flag_value': 'true',
    }, follow_redirects=False)
    assert r1.status_code == 302
    # OFF
    r2 = client.post('/admin/settings', data={
        'flag_key': 'cooler_labels_enabled',
        'flag_value': 'false',
    }, follow_redirects=False)
    assert r2.status_code == 302
    assert _get_setting_value(app, 'cooler_labels_enabled') == 'false'
    assert _count_flag_change_logs(app, 'cooler_labels_enabled') == 2


# ---------------------------------------------------------------------------
# Security — non-admin POSTing a flag toggle gets 403
# ---------------------------------------------------------------------------

def test_warehouse_manager_post_flag_toggle_rejected(app, client):
    _ensure_warehouse_user(app)
    # The in-memory test DB is reused across tests in this file, so other
    # tests may have toggled `cockpit_enabled`. Pin it to a known value first
    # and verify the warehouse-manager POST does not change it.
    from app import db
    from models import Setting
    with app.app_context():
        Setting.set(db.session, 'cockpit_enabled', 'false')
        db.session.commit()
    initial = _get_setting_value(app, 'cockpit_enabled')

    _login(client, 'test_wh_manager')
    resp = client.post('/admin/settings', data={
        'flag_key': 'cockpit_enabled',
        'flag_value': 'true',
    }, follow_redirects=False)
    assert resp.status_code == 403
    # Value must be unchanged after the rejected POST.
    assert _get_setting_value(app, 'cockpit_enabled') == initial


# ---------------------------------------------------------------------------
# Security — boolean flag with garbage value is rejected
# ---------------------------------------------------------------------------

def test_invalid_boolean_value_rejected(app, client):
    _login(client, 'test_admin_user')
    resp = client.post('/admin/settings', data={
        'flag_key': 'cockpit_enabled',
        'flag_value': 'maybe',
    }, follow_redirects=False)
    assert resp.status_code == 400
