"""
Driver Receipt Controls — void/reissue hardening, manual receipts, lookup.
Covers:
  - void gate: slips recovered must equal print count
  - void gate: synced receipt needs a PS365 reversal reference
  - reissue blocked while original is still posted in PS365
  - manual receipt logging: duplicate book number is a 409
  - lookup: replaced receipt reports status REISSUED
"""

import pytest
from decimal import Decimal


@pytest.fixture(scope='function')
def recon_app(app):
    """App with the reconciliation blueprint registered."""
    from routes_reconciliation import reconciliation_bp
    if 'reconciliation' not in app.blueprints:
        app.register_blueprint(reconciliation_bp)
    # Register up-front (Flask blocks blueprint registration after the app
    # has served its first request), so later test classes can use them.
    from routes_payments import payments_bp
    if 'payments' not in app.blueprints:
        app.register_blueprint(payments_bp)
    from routes_driver import driver_bp
    if 'driver' not in app.blueprints:
        app.register_blueprint(driver_bp)
    try:
        from blueprints.supplier_returns import supplier_returns_bp
        if 'supplier_returns' not in app.blueprints:
            app.register_blueprint(supplier_returns_bp)
    except Exception:
        pass
    if not getattr(app, '_receipt_tests_helpers_registered', False):
        from services.permissions import register_template_helpers
        register_template_helpers(app)
        app._receipt_tests_helpers_registered = True
    return app


@pytest.fixture(scope='function')
def recon_client(recon_app):
    return recon_app.test_client()


@pytest.fixture(scope='function')
def admin_client(recon_client):
    resp = recon_client.post('/login', data={
        'username': 'test_admin_user',
        'password': 'test_password'
    })
    assert resp.status_code == 302
    return recon_client


def _make_route_and_stop(recon_app):
    from app import db
    from models import Shipment, RouteStop
    from datetime import date
    with recon_app.app_context():
        s = Shipment(driver_name='test_driver_user', delivery_date=date.today())
        db.session.add(s)
        db.session.flush()
        stop = RouteStop(shipment_id=s.id, seq_no=1)
        db.session.add(stop)
        db.session.commit()
        return s.id, stop.route_stop_id


def _make_receipt(recon_app, **overrides):
    from app import db
    from models import CODReceipt, utc_now
    route_id, stop_id = _make_route_and_stop(recon_app)
    with recon_app.app_context():
        fields = dict(
            route_id=route_id,
            route_stop_id=stop_id,
            driver_username='test_driver_user',
            invoice_nos='INV-1',
            expected_amount=Decimal('100.00'),
            received_amount=Decimal('100.00'),
            variance=Decimal('0.00'),
            payment_method='cash',
            status='ISSUED',
            created_at=utc_now(),
        )
        fields.update(overrides)
        r = CODReceipt(**fields)
        db.session.add(r)
        db.session.commit()
        return r.id


class TestVoidHardening:
    def test_void_requires_reason(self, recon_app, admin_client):
        rid = _make_receipt(recon_app)
        resp = admin_client.post(f'/reconciliation/api/receipts/{rid}/void', json={})
        assert resp.status_code == 400
        assert 'reason' in resp.get_json()['error'].lower()

    def test_void_printed_requires_matching_slips(self, recon_app, admin_client):
        rid = _make_receipt(recon_app, print_count=2)
        # missing slips
        resp = admin_client.post(f'/reconciliation/api/receipts/{rid}/void',
                                 json={'reason': 'wrong amount'})
        assert resp.status_code == 400
        # mismatched slips
        resp = admin_client.post(f'/reconciliation/api/receipts/{rid}/void',
                                 json={'reason': 'wrong amount', 'slips_recovered': 1})
        assert resp.status_code == 400
        # matching slips -> success
        resp = admin_client.post(f'/reconciliation/api/receipts/{rid}/void',
                                 json={'reason': 'wrong amount', 'slips_recovered': 2})
        assert resp.status_code == 200
        from models import CODReceipt
        from app import db
        with recon_app.app_context():
            r = db.session.get(CODReceipt, rid)
            assert r.status == 'VOIDED'
            assert r.slips_recovered == 2

    def test_void_synced_requires_reversal_ref(self, recon_app, admin_client):
        rid = _make_receipt(recon_app, ps365_reference_number='PS-123')
        resp = admin_client.post(f'/reconciliation/api/receipts/{rid}/void',
                                 json={'reason': 'duplicate'})
        assert resp.status_code == 400
        assert 'PS365' in resp.get_json()['error']
        resp = admin_client.post(f'/reconciliation/api/receipts/{rid}/void',
                                 json={'reason': 'duplicate', 'ps365_reversal_ref': 'CN-9'})
        assert resp.status_code == 200
        from models import CODReceipt
        from app import db
        with recon_app.app_context():
            r = db.session.get(CODReceipt, rid)
            assert r.ps365_reversal_ref == 'CN-9'
            assert r.ps365_reversed_by == 'test_admin_user'


class TestReissue:
    def test_reissue_blocked_when_still_posted(self, recon_app, admin_client):
        from app import db
        from models import CODReceipt
        rid = _make_receipt(recon_app, status='VOIDED',
                            ps365_reference_number='PS-55')
        resp = admin_client.post(f'/reconciliation/api/receipts/{rid}/reissue', json={})
        assert resp.status_code == 400
        assert 'PS365' in resp.get_json()['error']

    def test_reissue_links_and_lookup_reports_reissued(self, recon_app, admin_client):
        rid = _make_receipt(recon_app, status='VOIDED')
        resp = admin_client.post(f'/reconciliation/api/receipts/{rid}/reissue', json={})
        assert resp.status_code == 200
        new_id = resp.get_json()['new_receipt_id']

        # old receipt now shows VOIDED (voided wins over reissued)
        resp = admin_client.get(f'/reconciliation/api/receipts/lookup?q={rid}')
        data = resp.get_json()
        assert data['success']
        assert data['receipt']['replaced_by_cod_receipt_id'] == new_id

        # new receipt links back to the old one
        resp = admin_client.get(f'/reconciliation/api/receipts/lookup?q={new_id}')
        data = resp.get_json()
        assert data['receipt']['replaces_receipt_id'] == rid

        # double reissue blocked
        resp = admin_client.post(f'/reconciliation/api/receipts/{rid}/reissue', json={})
        assert resp.status_code == 400

    def test_lookup_status_reissued_for_replaced_nonvoided(self, recon_app, admin_client):
        rid2 = _make_receipt(recon_app)
        rid = _make_receipt(recon_app, status='ISSUED',
                            replaced_by_cod_receipt_id=rid2)
        resp = admin_client.get(f'/reconciliation/api/receipts/lookup?q={rid}')
        assert resp.get_json()['receipt']['status'] == 'REISSUED'


class TestManualReceipts:
    def test_log_and_duplicate_409(self, recon_app, admin_client):
        payload = {'manual_book_number': 'MB-100',
                   'driver_username': 'test_driver_user',
                   'amount': '55.20', 'reason': 'printer_failure'}
        resp = admin_client.post('/reconciliation/api/manual-receipts', json=payload)
        assert resp.status_code == 200
        assert resp.get_json()['success']

        resp = admin_client.post('/reconciliation/api/manual-receipts', json=payload)
        assert resp.status_code == 409

    def test_validation(self, recon_app, admin_client):
        resp = admin_client.post('/reconciliation/api/manual-receipts',
                                 json={'manual_book_number': '', 'driver_username': 'd',
                                       'amount': '10'})
        assert resp.status_code == 400
        resp = admin_client.post('/reconciliation/api/manual-receipts',
                                 json={'manual_book_number': 'MB-2',
                                       'driver_username': 'test_driver_user',
                                       'amount': '-5'})
        assert resp.status_code == 400
        # linked receipt must exist
        resp = admin_client.post('/reconciliation/api/manual-receipts',
                                 json={'manual_book_number': 'MB-3',
                                       'driver_username': 'test_driver_user',
                                       'amount': '10',
                                       'matched_cod_receipt_id': 999999})
        assert resp.status_code == 404

    def test_match_endpoint(self, recon_app, admin_client):
        rid = _make_receipt(recon_app)
        resp = admin_client.post('/reconciliation/api/manual-receipts',
                                 json={'manual_book_number': 'MB-4',
                                       'driver_username': 'test_driver_user',
                                       'amount': '10'})
        entry_id = resp.get_json()['id']
        resp = admin_client.post(f'/reconciliation/api/manual-receipts/{entry_id}/match',
                                 json={'cod_receipt_id': rid})
        assert resp.status_code == 200


class TestLookupAndExceptions:
    def test_lookup_not_found(self, recon_app, admin_client):
        resp = admin_client.get('/reconciliation/api/receipts/lookup?q=999999')
        assert resp.status_code == 404

    def test_lookup_by_ps365_ref(self, recon_app, admin_client):
        rid = _make_receipt(recon_app, ps365_reference_number='ABC-777')
        resp = admin_client.get('/reconciliation/api/receipts/lookup?q=ABC-777')
        assert resp.get_json()['receipt']['id'] == rid

    @pytest.fixture()
    def lenient_urls(self, recon_app):
        """base.html links to many blueprints not registered in the test app;
        fall back to '#' for those so our templates can render."""
        from flask import url_for as real_url_for
        from werkzeug.routing.exceptions import BuildError
        orig = recon_app.jinja_env.globals.get('url_for', real_url_for)

        def safe_url_for(endpoint, **values):
            try:
                return orig(endpoint, **values)
            except BuildError:
                return '#'
        recon_app.jinja_env.globals['url_for'] = safe_url_for
        yield
        recon_app.jinja_env.globals['url_for'] = orig

    def test_exception_report_renders(self, recon_app, admin_client, lenient_urls):
        _make_receipt(recon_app, status='VOIDED', void_reason='test',
                      ps365_reference_number='PS-1',
                      variance=Decimal('5.00'), variance_reason='partial_payment')
        resp = admin_client.get('/reconciliation/receipts/exceptions')
        assert resp.status_code == 200
        assert b'Receipt Exception Report' in resp.data

    def test_lookup_page_renders(self, recon_app, admin_client, lenient_urls):
        resp = admin_client.get('/reconciliation/receipts/lookup')
        assert resp.status_code == 200
        assert b'Receipt Lookup' in resp.data


class TestNightlyVoidCheck:
    def test_flags_dirty_voids(self, recon_app):
        from scheduler import _run_receipt_void_check
        dirty_id = _make_receipt(recon_app, status='VOIDED',
                                 ps365_reference_number='PS-9')
        clean_id = _make_receipt(recon_app, status='VOIDED',
                                 ps365_reference_number='PS-10',
                                 ps365_reversal_ref='CN-10')
        result = _run_receipt_void_check()
        assert dirty_id in result['receipt_ids']
        assert clean_id not in result['receipt_ids']


class TestArchitectFixes:
    """Regression tests for review findings: payment-API authorization and
    finalize blocked by unmatched manual receipts."""

    @pytest.fixture()
    def payments_app(self, recon_app):
        return recon_app

    def _login(self, client, username):
        resp = client.post('/login', data={'username': username,
                                           'password': 'test_password'})
        assert resp.status_code == 302
        return client

    def test_other_driver_cannot_touch_payment(self, payments_app):
        from app import db
        from models import User
        from werkzeug.security import generate_password_hash
        route_id, stop_id = _make_route_and_stop(payments_app)
        with payments_app.app_context():
            if not User.query.filter_by(username='other_driver').first():
                db.session.add(User(username='other_driver',
                                    password=generate_password_hash('test_password'),
                                    role='driver'))
                db.session.commit()
        client = payments_app.test_client()
        self._login(client, 'other_driver')
        resp = client.get(f'/api/route-stops/{stop_id}/payment')
        assert resp.status_code == 403
        resp = client.post(f'/api/route-stops/{stop_id}/payment',
                           json={'method': 'cash', 'amount': 10})
        assert resp.status_code == 403

    def test_assigned_driver_and_admin_allowed(self, payments_app):
        route_id, stop_id = _make_route_and_stop(payments_app)
        client = payments_app.test_client()
        self._login(client, 'test_driver_user')  # shipment driver_name matches
        resp = client.get(f'/api/route-stops/{stop_id}/payment')
        assert resp.status_code == 200
        admin = payments_app.test_client()
        self._login(admin, 'test_admin_user')
        resp = admin.get(f'/api/route-stops/{stop_id}/payment')
        assert resp.status_code == 200

    def test_finalize_blocked_by_unmatched_manual_receipt(self, recon_app, admin_client):
        rid = _make_receipt(recon_app)
        from app import db
        from models import CODReceipt
        with recon_app.app_context():
            route_id = db.session.get(CODReceipt, rid).route_id
        resp = admin_client.post('/reconciliation/api/manual-receipts',
                                 json={'manual_book_number': 'MB-FIN-1',
                                       'driver_username': 'test_driver_user',
                                       'amount': '20', 'route_id': route_id})
        assert resp.status_code == 200
        entry_id = resp.get_json()['id']

        resp = admin_client.post(f'/reconciliation/api/shipments/{route_id}/finalize')
        assert resp.status_code == 400
        assert 'manual receipt' in resp.get_json()['error'].lower()

        # match it, then finalize succeeds
        resp = admin_client.post(f'/reconciliation/api/manual-receipts/{entry_id}/match',
                                 json={'cod_receipt_id': rid})
        assert resp.status_code == 200
        resp = admin_client.post(f'/reconciliation/api/shipments/{route_id}/finalize')
        assert resp.status_code == 200
