"""Task #29 — Configurable Forecast Week Cutoff: admin-settings UI tests.

Verifies the forecast_workbench /admin/settings GET/POST behaviour for the
two new rollover keys (forecast_week_rollover_weekday, forecast_week_rollover_time).

F1  Admin GET /admin/settings — rollover card is present.
F2  Warehouse manager GET — rollover card is NOT present.
F3  Admin POST valid rollover weekday+time — persisted + ActivityLog written.
F4  Admin POST invalid weekday (value 7) → 400, DB unchanged.
F5  Admin POST invalid time format (letters) → 400, DB unchanged.
F6  Warehouse manager POST rollover keys → 403.
"""

import os
import sys
import json

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(THIS_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pytest
from werkzeug.security import generate_password_hash


# ---------------------------------------------------------------------------
# Blueprint registration (same pattern as test_feature_flags_ui.py)
# ---------------------------------------------------------------------------

_REQUIRED_BLUEPRINTS_LOADED = False


@pytest.fixture(autouse=True)
def _register_required_blueprints(app):
    global _REQUIRED_BLUEPRINTS_LOADED
    if _REQUIRED_BLUEPRINTS_LOADED:
        return
    import glob as _glob
    import importlib
    import logging
    from flask import Blueprint as _BP

    candidates = []
    for path in _glob.glob(os.path.join(PROJECT_ROOT, 'routes_*.py')):
        candidates.append(os.path.basename(path)[:-3])
    for path in _glob.glob(os.path.join(PROJECT_ROOT, 'blueprints', '*.py')):
        base = os.path.basename(path)[:-3]
        if base == '__init__':
            continue
        candidates.append(f'blueprints.{base}')
    candidates += ['datawarehouse_routes', 'dw_analytics_routes', 'forecast_workbench']

    for mod_name in candidates:
        try:
            mod = importlib.import_module(mod_name)
        except Exception as e:
            logging.debug(f"rollover setup: skip {mod_name}: {e}")
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
                logging.debug(f"rollover setup: register {mod_name}.{attr} failed: {e}")

    try:
        from services.permissions import register_template_helpers
        register_template_helpers(app)
    except Exception as e:
        logging.debug(f"rollover setup: register_template_helpers failed: {e}")

    _REQUIRED_BLUEPRINTS_LOADED = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_warehouse_user(app):
    from app import db
    from models import User
    with app.app_context():
        if not User.query.filter_by(username='test_wh_mgr_rollover').first():
            u = User(
                username='test_wh_mgr_rollover',
                password=generate_password_hash('test_password'),
                role='warehouse_manager',
            )
            db.session.add(u)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()


def _login(client, username, password='test_password'):
    resp = client.post('/login', data={'username': username, 'password': password})
    assert resp.status_code == 302, f"login failed for {username}"
    return client


def _get_setting_value(app, key):
    from app import db
    from models import Setting
    with app.app_context():
        return Setting.get(db.session, key, None)


def _count_rollover_change_logs(app, key=None):
    from app import db
    from models import ActivityLog
    with app.app_context():
        rows = db.session.query(ActivityLog).filter_by(
            activity_type='forecast_settings_change'
        ).all()
        if key is None:
            return len(rows)
        return sum(
            1 for r in rows
            if r.details and json.loads(r.details).get('key') == key
        )


# ---------------------------------------------------------------------------
# F1 — Admin GET sees the rollover card
# ---------------------------------------------------------------------------

def test_f1_admin_sees_rollover_card(client):
    _login(client, 'test_admin_user')
    resp = client.get('/forecast/admin/settings')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'rollover-settings-card' in body
    assert 'Forecast Week Rollover' in body
    assert 'forecast_week_rollover_weekday' in body
    assert 'forecast_week_rollover_time' in body


# ---------------------------------------------------------------------------
# F2 — Warehouse manager GET does NOT see the rollover card
# ---------------------------------------------------------------------------

def test_f2_warehouse_manager_does_not_see_rollover_card(app, client):
    _ensure_warehouse_user(app)
    _login(client, 'test_wh_mgr_rollover')
    resp = client.get('/forecast/admin/settings')
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'rollover-settings-card' not in body


# ---------------------------------------------------------------------------
# F3 — Admin POST valid rollover weekday + time → persisted + logged
# ---------------------------------------------------------------------------

def test_f3_admin_post_valid_rollover_persists_and_logs(app, client):
    _login(client, 'test_admin_user')
    resp = client.post('/forecast/admin/settings', data={
        'forecast_week_rollover_weekday': '3',   # Thursday
        'forecast_week_rollover_time': '09:30',
    }, follow_redirects=False)
    assert resp.status_code == 302
    assert _get_setting_value(app, 'forecast_week_rollover_weekday') == '3'
    assert _get_setting_value(app, 'forecast_week_rollover_time') == '09:30'
    assert _count_rollover_change_logs(app, 'forecast_week_rollover_weekday') >= 1
    assert _count_rollover_change_logs(app, 'forecast_week_rollover_time') >= 1


# ---------------------------------------------------------------------------
# F4 — Admin POST invalid weekday (7) → 400, DB unchanged
# ---------------------------------------------------------------------------

def test_f4_admin_post_invalid_weekday_rejected(app, client):
    from app import db
    from models import Setting
    # Seed a known good value
    with app.app_context():
        Setting.set(db.session, 'forecast_week_rollover_weekday', '4')
        db.session.commit()

    _login(client, 'test_admin_user')
    resp = client.post('/forecast/admin/settings', data={
        'forecast_week_rollover_weekday': '7',
    }, follow_redirects=False)
    assert resp.status_code == 400
    assert _get_setting_value(app, 'forecast_week_rollover_weekday') == '4'


# ---------------------------------------------------------------------------
# F5 — Admin POST invalid time format → 400, DB unchanged
# ---------------------------------------------------------------------------

def test_f5_admin_post_invalid_time_format_rejected(app, client):
    from app import db
    from models import Setting
    with app.app_context():
        Setting.set(db.session, 'forecast_week_rollover_time', '10:00')
        db.session.commit()

    _login(client, 'test_admin_user')
    resp = client.post('/forecast/admin/settings', data={
        'forecast_week_rollover_time': 'ten-thirty',
    }, follow_redirects=False)
    assert resp.status_code == 400
    assert _get_setting_value(app, 'forecast_week_rollover_time') == '10:00'


# ---------------------------------------------------------------------------
# F6 — Warehouse manager POST rollover keys → 403
# ---------------------------------------------------------------------------

def test_f6_warehouse_manager_post_rollover_rejected(app, client):
    _ensure_warehouse_user(app)
    from app import db
    from models import Setting
    with app.app_context():
        Setting.set(db.session, 'forecast_week_rollover_weekday', '4')
        db.session.commit()

    _login(client, 'test_wh_mgr_rollover')
    resp = client.post('/forecast/admin/settings', data={
        'forecast_week_rollover_weekday': '2',
        'forecast_week_rollover_time': '08:00',
    }, follow_redirects=False)
    assert resp.status_code == 403
    assert _get_setting_value(app, 'forecast_week_rollover_weekday') == '4'
