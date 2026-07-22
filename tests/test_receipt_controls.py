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


class TestRound2Fixes:
    """R2: void unlocks payment, deferred PS365 commit, PENDING_RETRY guard,
    cancellation-request logging."""

    def _login(self, client, username):
        resp = client.post('/login', data={'username': username,
                                           'password': 'test_password'})
        assert resp.status_code == 302
        return client

    def test_void_deactivates_payment_entry(self, recon_app, admin_client):
        from app import db
        from models import PaymentEntry
        rid = _make_receipt(recon_app)
        from models import CODReceipt
        with recon_app.app_context():
            stop_id = db.session.get(CODReceipt, rid).route_stop_id
            pe = PaymentEntry(route_stop_id=stop_id, method='cash',
                              amount=Decimal('100'), commit_mode='COMMIT',
                              doc_type='official', ps_status='SUCCESS',
                              ps_reference='PS-77', is_active=True)
            db.session.add(pe)
            db.session.commit()
            pe_id = pe.id
        resp = admin_client.post(f'/reconciliation/api/receipts/{rid}/void',
                                 json={'reason': 'wrong amount'})
        assert resp.status_code == 200
        with recon_app.app_context():
            assert db.session.get(PaymentEntry, pe_id).is_active is False

    def test_driver_can_reenter_payment_after_void(self, recon_app, admin_client):
        """Bug 1 acceptance: void -> driver re-enters payment at same stop."""
        from app import db
        from models import CODReceipt, utc_now
        rid = _make_receipt(recon_app, print_count=1,
                            first_printed_at=utc_now())
        with recon_app.app_context():
            stop_id = db.session.get(CODReceipt, rid).route_stop_id
        # locked while ISSUED+printed
        driver = recon_app.test_client()
        self._login(driver, 'test_driver_user')
        resp = driver.post(f'/api/route-stops/{stop_id}/payment',
                           json={'method': 'cash', 'amount': 100})
        assert resp.status_code == 409
        assert resp.get_json().get('receipt_locked')
        # void it
        resp = admin_client.post(f'/reconciliation/api/receipts/{rid}/void',
                                 json={'reason': 'redo', 'slips_recovered': 1})
        assert resp.status_code == 200
        # driver can now re-enter
        resp = driver.post(f'/api/route-stops/{stop_id}/payment',
                           json={'method': 'cash', 'amount': 100})
        assert resp.status_code == 200

    def test_confirm_defers_ps365_commit(self, recon_app):
        """Bug 2: confirming cash leaves ps_status NEW, no PS365 call."""
        route_id, stop_id = _make_route_and_stop(recon_app)
        client = recon_app.test_client()
        self._login(client, 'test_driver_user')
        resp = client.post(f'/api/route-stops/{stop_id}/payment',
                           json={'method': 'cash', 'amount': 50})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['ps_status'] == 'NEW'
        assert not data.get('ps_reference')

    def test_online_still_skipped_on_confirm(self, recon_app):
        route_id, stop_id = _make_route_and_stop(recon_app)
        client = recon_app.test_client()
        self._login(client, 'test_driver_user')
        resp = client.post(f'/api/route-stops/{stop_id}/payment',
                           json={'method': 'online', 'amount': 50})
        assert resp.status_code == 200
        assert resp.get_json()['ps_status'] == 'SKIPPED'

    def test_change_blocked_while_pending_retry(self, recon_app):
        """Bug 3: change payment rejected while PENDING_RETRY."""
        from app import db
        from models import PaymentEntry
        route_id, stop_id = _make_route_and_stop(recon_app)
        with recon_app.app_context():
            pe = PaymentEntry(route_stop_id=stop_id, method='cash',
                              amount=Decimal('50'), commit_mode='COMMIT',
                              doc_type='official', ps_status='PENDING_RETRY',
                              is_active=True)
            db.session.add(pe)
            db.session.commit()
        client = recon_app.test_client()
        self._login(client, 'test_driver_user')
        resp = client.post(f'/api/route-stops/{stop_id}/payment',
                           json={'method': 'card', 'amount': 50})
        assert resp.status_code == 409
        assert 'confirmed' in resp.get_json()['error'].lower()

    def test_sync_at_print_uses_payment_entry(self, recon_app, monkeypatch):
        """Bug 2: print-time sync commits via the PaymentEntry and copies the ref."""
        from app import db
        from models import PaymentEntry, CODReceipt, RouteStop
        import services.payments as sp
        rid = _make_receipt(recon_app, status='DRAFT', doc_type='official')
        with recon_app.app_context():
            receipt = db.session.get(CODReceipt, rid)
            stop = db.session.get(RouteStop, receipt.route_stop_id)
            pe = PaymentEntry(route_stop_id=receipt.route_stop_id, method='cash',
                              amount=Decimal('100'), commit_mode='COMMIT',
                              doc_type='official', ps_status='NEW',
                              is_active=True)
            db.session.add(pe)
            db.session.commit()

            def fake_commit(pe_arg, customer_code, invoice_nos, driver):
                pe_arg.ps_status = 'SUCCESS'
                pe_arg.ps_reference = 'PS-999'
                return pe_arg

            monkeypatch.setattr(sp, 'commit_to_ps365', fake_commit)
            sp.sync_receipt_ps365_at_print(receipt, stop, 'test_driver_user')
            db.session.commit()
            assert receipt.ps365_reference_number == 'PS-999'

    def test_sync_at_print_failure_does_not_raise(self, recon_app, monkeypatch):
        from app import db
        from models import PaymentEntry, CODReceipt, RouteStop
        import services.payments as sp
        rid = _make_receipt(recon_app, status='DRAFT', doc_type='official')
        with recon_app.app_context():
            receipt = db.session.get(CODReceipt, rid)
            stop = db.session.get(RouteStop, receipt.route_stop_id)
            pe = PaymentEntry(route_stop_id=receipt.route_stop_id, method='cash',
                              amount=Decimal('100'), commit_mode='COMMIT',
                              doc_type='official', ps_status='NEW',
                              is_active=True)
            db.session.add(pe)
            db.session.commit()

            def boom(*a, **k):
                raise RuntimeError('PS365 down')

            monkeypatch.setattr(sp, 'commit_to_ps365', boom)
            sp.sync_receipt_ps365_at_print(receipt, stop, 'test_driver_user')
            assert receipt.ps365_reference_number is None

    def test_cancellation_request_logged_and_surfaced(self, recon_app, admin_client):
        from app import db
        from models import CODReceipt
        rid = _make_receipt(recon_app, print_count=1)
        driver = recon_app.test_client()
        self._login(driver, 'test_driver_user')
        resp = driver.post(f'/driver/receipts/{rid}/request-cancellation')
        assert resp.status_code == 200
        with recon_app.app_context():
            r = db.session.get(CODReceipt, rid)
            assert r.cancellation_requested_at is not None
            assert r.cancellation_requested_by == 'test_driver_user'
        # surfaced in the office lookup API
        resp = admin_client.get(f'/reconciliation/api/receipts/lookup?q={rid}')
        data = resp.get_json()['receipt']
        assert data['cancellation_requested_by'] == 'test_driver_user'
        assert data['cancellation_requested_at']

    def test_void_wording_customer_copies(self, recon_app, admin_client):
        rid = _make_receipt(recon_app, print_count=1)
        resp = admin_client.post(f'/reconciliation/api/receipts/{rid}/void',
                                 json={'reason': 'x'})
        assert resp.status_code == 400
        assert 'customer copies' in resp.get_json()['error']

    def test_reissue_after_void_posts_at_print(self, recon_app, admin_client, monkeypatch):
        """Bug 1 end-to-end: voided synced receipt + replacement receipt ->
        print-time sync posts to PS365 despite the old ReceiptLog."""
        from app import db
        from models import CODReceipt, RouteStop, ReceiptLog, PaymentEntry, utc_now
        import services.payments as sp
        rid = _make_receipt(recon_app, status='VOIDED',
                            ps365_reference_number='PS-OLD',
                            ps365_reversal_ref='CN-1')
        with recon_app.app_context():
            old = db.session.get(CODReceipt, rid)
            stop_id = old.route_stop_id
            # old ReceiptLog from the first (now-voided) post
            db.session.add(ReceiptLog(route_stop_id=stop_id,
                                      reference_number='PS-OLD',
                                      customer_code_365='C1',
                                      amount=Decimal('100'),
                                      success=1))
            # live replacement receipt, not yet posted
            new_r = CODReceipt(route_id=old.route_id, route_stop_id=stop_id,
                               driver_username='test_driver_user',
                               invoice_nos='INV-1',
                               expected_amount=Decimal('100'),
                               received_amount=Decimal('100'),
                               variance=Decimal('0'), payment_method='cash',
                               status='DRAFT', doc_type='official',
                               created_at=utc_now())
            db.session.add(new_r)
            pe = PaymentEntry(route_stop_id=stop_id, method='cash',
                              amount=Decimal('100'), commit_mode='COMMIT',
                              doc_type='official', ps_status='NEW',
                              is_active=True)
            db.session.add(pe)
            db.session.commit()
            new_id = new_r.id

            class FakeResp:
                status_code = 200
                ok = True
                def json(self):
                    return {'api_response': {'response_code': '1',
                                             'response_id': 'TX-123'}}
            import routes_receipts as rr
            # sqlite can't run the FOR UPDATE sequence query
            monkeypatch.setattr(rr, 'next_reference_number', lambda: 'PS-NEW')
            monkeypatch.setattr(rr.requests, 'post', lambda *a, **k: FakeResp())
            # simpler: patch commit_to_ps365 outcome only if HTTP patch not effective
            def fake_commit(pe_arg, customer_code, invoice_nos, driver):
                from routes_receipts import create_receipt_core
                ok, ref, resp_id, _, _ = create_receipt_core(
                    customer_code='C1', amount_val=100.0, comments='x',
                    user_code=driver, invoice_no='INV-1',
                    driver_username=driver, route_stop_id=stop_id)
                pe_arg.ps_status = 'SUCCESS'
                pe_arg.ps_reference = ref
                return pe_arg
            monkeypatch.setattr(sp, 'commit_to_ps365', fake_commit)

            stop = db.session.get(RouteStop, stop_id)
            receipt = db.session.get(CODReceipt, new_id)
            sp.sync_receipt_ps365_at_print(receipt, stop, 'test_driver_user')
            db.session.commit()
            assert receipt.ps365_reference_number, \
                'reissued receipt must get a PS365 reference at first print'

    @pytest.fixture()
    def lenient_urls(self, recon_app):
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

    def test_cancellation_request_403_for_other_driver(self, recon_app, lenient_urls):
        from app import db
        from models import User
        from werkzeug.security import generate_password_hash
        rid = _make_receipt(recon_app)
        with recon_app.app_context():
            if not User.query.filter_by(username='other_driver2').first():
                db.session.add(User(username='other_driver2',
                                    password=generate_password_hash('test_password'),
                                    role='driver'))
                db.session.commit()
        client = recon_app.test_client()
        self._login(client, 'other_driver2')
        resp = client.post(f'/driver/receipts/{rid}/request-cancellation')
        assert resp.status_code == 403


class TestRound3Fixes:
    """R3: R-number lookup + consistent print gating when PS365 is down."""

    def _login(self, client, username):
        resp = client.post('/login', data={'username': username,
                                           'password': 'test_password'})
        assert resp.status_code == 302
        return client

    def test_lookup_finds_r_number_variants(self, recon_app, admin_client):
        rid = _make_receipt(recon_app, ps365_reference_number='R1000001')
        for q in ('R1000001', '1000001', str(rid)):
            resp = admin_client.get(f'/reconciliation/api/receipts/lookup?q={q}')
            assert resp.status_code == 200, q
            assert resp.get_json()['receipt']['id'] == rid, q

    def test_html_print_refuses_unsynced_official(self, recon_app, monkeypatch):
        """With PS365 down, HTML print views return 503, do not lock."""
        from app import db
        from models import CODReceipt
        import services.payments as sp
        monkeypatch.setattr(sp, 'commit_to_ps365',
                            lambda pe, *a, **k: pe)  # sync fails silently
        rid = _make_receipt(recon_app, status='DRAFT', doc_type='official')
        driver = recon_app.test_client()
        self._login(driver, 'test_driver_user')
        with recon_app.app_context():
            stop_id = db.session.get(CODReceipt, rid).route_stop_id
        for url in (f'/driver/receipts/{rid}/print',
                    f'/driver/receipts/{rid}/print_80mm',
                    f'/driver/stops/{stop_id}/print_receipt',
                    f'/driver/stops/{stop_id}/print_receipt_80mm'):
            resp = driver.get(url)
            assert resp.status_code == 503, url
            assert b'not registered in Powersoft' in resp.data, url
        with recon_app.app_context():
            r = db.session.get(CODReceipt, rid)
            assert r.status == 'DRAFT'
            assert not r.locked_at

    def test_html_print_allows_online_doc(self, recon_app, lenient_urls):
        """Online/PDC docs don't post to PS365 and must still print."""
        from app import db
        from models import CODReceipt
        rid = _make_receipt(recon_app, status='DRAFT', doc_type='online')
        # main.py registers this filter; the test app doesn't load main.py
        recon_app.jinja_env.filters.setdefault('local_time', lambda v, *a: v)
        driver = recon_app.test_client()
        self._login(driver, 'test_driver_user')
        resp = driver.get(f'/driver/receipts/{rid}/print')
        assert resp.status_code == 200
        with recon_app.app_context():
            assert db.session.get(CODReceipt, rid).status == 'ISSUED'

    @pytest.fixture()
    def lenient_urls(self, recon_app):
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

    def test_reprint_synced_official_still_prints(self, recon_app):
        from app import db
        from models import CODReceipt
        rid = _make_receipt(recon_app, status='ISSUED', doc_type='official',
                            ps365_reference_number='R1000002', print_count=1)
        recon_app.jinja_env.filters.setdefault('local_time', lambda v, *a: v)
        driver = recon_app.test_client()
        self._login(driver, 'test_driver_user')
        resp = driver.get(f'/driver/receipts/{rid}/print')
        assert resp.status_code == 200
        with recon_app.app_context():
            assert db.session.get(CODReceipt, rid).print_count == 2


class TestRound4EditWindow:
    """R4: payment edit window survives stop close until print/post/submit."""

    def _login(self, client, username):
        resp = client.post('/login', data={'username': username,
                                           'password': 'test_password'})
        assert resp.status_code == 302
        return client

    def _closed_stop_with_receipt(self, recon_app, **receipt_overrides):
        """Simulate closeStop's snapshot: DRAFT receipt + allocation rows."""
        from app import db
        from models import CODReceipt, CODInvoiceAllocation
        rid = _make_receipt(recon_app, status='DRAFT', doc_type='official',
                            expected_amount=Decimal('500.00'),
                            received_amount=Decimal('500.00'),
                            **receipt_overrides)
        with recon_app.app_context():
            r = db.session.get(CODReceipt, rid)
            db.session.add(CODInvoiceAllocation(
                cod_receipt_id=r.id, invoice_no='INV-1', route_id=r.route_id,
                expected_amount=Decimal('500.00'),
                received_amount=Decimal('500.00'),
                deduct_amount=Decimal('0'), payment_method='cash',
                is_pending=False))
            db.session.commit()
            return rid, r.route_stop_id, r.route_id

    def test_edit_after_close_updates_receipt_and_allocations(self, recon_app):
        from app import db
        from models import CODReceipt, CODInvoiceAllocation
        rid, stop_id, _ = self._closed_stop_with_receipt(recon_app)
        driver = recon_app.test_client()
        self._login(driver, 'test_driver_user')
        resp = driver.post(f'/api/route-stops/{stop_id}/payment',
                           json={'method': 'cash', 'amount': 50,
                                 'variance_reason': 'customer short paid'})
        assert resp.status_code == 200
        with recon_app.app_context():
            r = db.session.get(CODReceipt, rid)
            assert r.received_amount == Decimal('50')
            assert r.variance == Decimal('-450')
            assert r.variance_reason == 'customer short paid'
            allocs = CODInvoiceAllocation.query.filter_by(cod_receipt_id=rid).all()
            assert sum(a.received_amount for a in allocs) == Decimal('50')
            assert r.expected_amount == Decimal('500.00')

    def test_edit_to_cheque_updates_doc_fields(self, recon_app):
        from app import db
        from models import CODReceipt, CODInvoiceAllocation
        rid, stop_id, _ = self._closed_stop_with_receipt(recon_app)
        driver = recon_app.test_client()
        self._login(driver, 'test_driver_user')
        resp = driver.post(f'/api/route-stops/{stop_id}/payment',
                           json={'method': 'cheque', 'amount': 500,
                                 'cheque_no': 'CHQ-9',
                                 'cheque_date': '2026-01-01'})
        assert resp.status_code == 200
        with recon_app.app_context():
            r = db.session.get(CODReceipt, rid)
            assert r.payment_method == 'cheque'
            assert r.cheque_number == 'CHQ-9'
            assert r.doc_type == 'official'  # past-dated cheque commits
            alloc = CODInvoiceAllocation.query.filter_by(cod_receipt_id=rid).first()
            assert alloc.payment_method == 'cheque'
            assert alloc.cheque_number == 'CHQ-9'

    def test_edit_blocked_when_printed(self, recon_app):
        from models import utc_now
        rid, stop_id, _ = self._closed_stop_with_receipt(
            recon_app, first_printed_at=utc_now())
        driver = recon_app.test_client()
        self._login(driver, 'test_driver_user')
        resp = driver.post(f'/api/route-stops/{stop_id}/payment',
                           json={'method': 'cash', 'amount': 50})
        assert resp.status_code == 409
        assert resp.get_json().get('receipt_locked')

    def test_edit_blocked_after_route_submit(self, recon_app):
        from app import db
        from models import Shipment, utc_now
        rid, stop_id, route_id = self._closed_stop_with_receipt(recon_app)
        with recon_app.app_context():
            db.session.get(Shipment, route_id).driver_submitted_at = utc_now()
            db.session.commit()
        driver = recon_app.test_client()
        self._login(driver, 'test_driver_user')
        resp = driver.post(f'/api/route-stops/{stop_id}/payment',
                           json={'method': 'cash', 'amount': 50})
        assert resp.status_code == 409

    @pytest.fixture()
    def lenient_urls(self, recon_app):
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

    def test_stops_list_shows_edit_or_lock(self, recon_app, lenient_urls):
        from app import db
        from models import CODReceipt, RouteStop, utc_now
        rid, stop_id, route_id = self._closed_stop_with_receipt(recon_app)
        recon_app.jinja_env.filters.setdefault('local_time', lambda v, *a: v)
        recon_app.jinja_env.globals.setdefault('cooler_driver_view_enabled',
                                               lambda: False)
        with recon_app.app_context():
            stop = db.session.get(RouteStop, stop_id)
            stop.delivered_at = utc_now()
            db.session.commit()
        driver = recon_app.test_client()
        self._login(driver, 'test_driver_user')
        resp = driver.get(f'/driver/routes/{route_id}/stops')
        assert resp.status_code == 200
        assert b'onclick="openEditPayment' in resp.data
        # now printed -> lock chip instead
        with recon_app.app_context():
            db.session.get(CODReceipt, rid).first_printed_at = utc_now()
            db.session.commit()
        resp = driver.get(f'/driver/routes/{route_id}/stops')
        assert resp.status_code == 200
        assert b'onclick="openEditPayment' not in resp.data
        assert b'Locked (printed)' in resp.data
        assert b'Request Cancellation' in resp.data

    def test_edit_targets_newest_live_receipt(self, recon_app):
        """Two unprinted non-VOIDED receipts on one stop: the API must
        update the newest one (matching what the stops list shows)."""
        from app import db
        from models import CODReceipt, utc_now
        from datetime import timedelta
        rid_old, stop_id, route_id = self._closed_stop_with_receipt(recon_app)
        with recon_app.app_context():
            old = db.session.get(CODReceipt, rid_old)
            old.created_at = utc_now() - timedelta(hours=2)
            newer = CODReceipt(
                route_id=route_id, route_stop_id=stop_id,
                driver_username='test_driver_user',
                invoice_nos=['INV-1'],
                expected_amount=Decimal('500.00'),
                received_amount=Decimal('500.00'),
                variance=Decimal('0.00'), payment_method='cash',
                status='DRAFT', doc_type='official', created_at=utc_now())
            db.session.add(newer)
            db.session.commit()
            rid_new = newer.id
        driver = recon_app.test_client()
        self._login(driver, 'test_driver_user')
        resp = driver.post(f'/api/route-stops/{stop_id}/payment',
                           json={'method': 'cash', 'amount': 50,
                                 'variance_reason': 'short paid'})
        assert resp.status_code == 200
        with recon_app.app_context():
            assert db.session.get(CODReceipt, rid_new).received_amount == Decimal('50')
            assert db.session.get(CODReceipt, rid_old).received_amount == Decimal('500.00')


class TestRound4Part2:
    """R4 Part 2: Cancellation loop, PS365 chip, NOT PRINTED, settlement gate."""

    def _login(self, client, username):
        resp = client.post('/login', data={'username': username, 'password': 'test_password'})
        assert resp.status_code == 302

    def _make_delivered_stop(self, recon_app, **receipt_overrides):
        """Delivered stop with a DRAFT receipt."""
        from app import db
        from models import CODReceipt, RouteStop, utc_now
        rid = _make_receipt(recon_app, status='DRAFT', doc_type='official',
                            expected_amount=Decimal('200.00'),
                            received_amount=Decimal('200.00'),
                            **receipt_overrides)
        with recon_app.app_context():
            r = db.session.get(CODReceipt, rid)
            stop = db.session.get(RouteStop, r.route_stop_id)
            stop.delivered_at = utc_now()
            db.session.commit()
            return rid, r.route_stop_id, r.route_id

    # --- Part 2A: cancellation state flags ---

    def test_cancellation_state_pending(self, recon_app):
        from app import db
        from models import CODReceipt, utc_now
        rid, stop_id, _ = self._make_delivered_stop(recon_app)
        with recon_app.app_context():
            db.session.get(CODReceipt, rid).cancellation_requested_at = utc_now()
            db.session.commit()
        # Check via the stops_list endpoint
        recon_app.jinja_env.filters.setdefault('local_time', lambda v, *a: v)
        recon_app.jinja_env.globals.setdefault('cooler_driver_view_enabled', lambda: False)
        recon_app.jinja_env.globals.setdefault('url_for', lambda e, **kw: '#')
        with recon_app.app_context():
            r = db.session.get(CODReceipt, rid)
            assert r.cancellation_requested_at is not None

    def test_cancellation_state_replacement_ready(self, recon_app):
        """When office voids + reissues: live_r IS the replacement, live_r.replaces
        is non-empty → cancellation_state == 'replacement_ready'."""
        from app import db
        from models import CODReceipt, utc_now
        rid, stop_id, route_id = self._make_delivered_stop(recon_app)
        # Void original + link replacement via replaced_by_cod_receipt_id on orig
        with recon_app.app_context():
            orig = db.session.get(CODReceipt, rid)
            replacement = CODReceipt(
                route_id=route_id, route_stop_id=stop_id,
                driver_username='test_driver_user',
                invoice_nos=['INV-1'],
                expected_amount=Decimal('200.00'),
                received_amount=Decimal('200.00'),
                variance=Decimal('0.00'), payment_method='cash',
                status='DRAFT', doc_type='official', created_at=utc_now())
            db.session.add(replacement)
            db.session.flush()
            orig.status = 'VOIDED'
            orig.replaced_by_cod_receipt_id = replacement.id
            db.session.commit()
            rep_id = replacement.id
        # The replacement receipt should have live_r.replaces non-empty
        with recon_app.app_context():
            from models import CODReceipt as CR
            rep = db.session.get(CR, rep_id)
            assert rep.status == 'DRAFT'
            # replaces backref: list of receipts replaced by rep
            assert len(rep.replaces) == 1
            assert rep.replaces[0].id == rid
            assert rep.replaces[0].status == 'VOIDED'

    # --- Part 2D: is_unprinted flag and NOT PRINTED chip ---

    def test_is_unprinted_set_for_unprinted_stop(self, recon_app):
        from app import db
        from models import CODReceipt
        rid, stop_id, _ = self._make_delivered_stop(recon_app)
        with recon_app.app_context():
            r = db.session.get(CODReceipt, rid)
            assert r.first_printed_at is None  # still unprinted

    def test_is_unprinted_false_after_print(self, recon_app):
        from app import db
        from models import CODReceipt, utc_now
        rid, stop_id, _ = self._make_delivered_stop(recon_app)
        with recon_app.app_context():
            db.session.get(CODReceipt, rid).first_printed_at = utc_now()
            db.session.commit()
            r = db.session.get(CODReceipt, rid)
            assert r.first_printed_at is not None

    # --- Part 2D: settlement gate ---

    def test_submit_settlement_blocks_on_unprinted(self, recon_app):
        rid, stop_id, route_id = self._make_delivered_stop(recon_app)
        driver = recon_app.test_client()
        self._login(driver, 'test_driver_user')
        resp = driver.post(f'/driver/routes/{route_id}/settlement/submit',
                           json={'amount': 200, 'notes': ''})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get('requires_unprinted_confirm')
        assert data.get('unprinted_stops')

    def test_submit_settlement_proceeds_with_confirm(self, recon_app, monkeypatch):
        from app import db
        from models import CODReceipt
        rid, stop_id, route_id = self._make_delivered_stop(recon_app)
        # Stub PS365 sync so no network call
        import services.payments as sp
        monkeypatch.setattr(sp, 'sync_receipt_ps365_at_print',
                            lambda *a, **kw: None)
        driver = recon_app.test_client()
        self._login(driver, 'test_driver_user')
        resp = driver.post(f'/driver/routes/{route_id}/settlement/submit',
                           json={'amount': 200, 'notes': '',
                                 'unprinted_confirmed': True})
        assert resp.status_code == 200
        with recon_app.app_context():
            r = db.session.get(CODReceipt, rid)
            assert r.confirmed_unprinted_at is not None
            assert r.confirmed_unprinted_by == 'test_driver_user'

    def test_submit_settlement_no_gate_when_printed(self, recon_app, monkeypatch):
        from app import db
        from models import CODReceipt, utc_now
        rid, stop_id, route_id = self._make_delivered_stop(recon_app)
        with recon_app.app_context():
            db.session.get(CODReceipt, rid).first_printed_at = utc_now()
            db.session.commit()
        import services.payments as sp
        monkeypatch.setattr(sp, 'sync_receipt_ps365_at_print',
                            lambda *a, **kw: None)
        driver = recon_app.test_client()
        self._login(driver, 'test_driver_user')
        resp = driver.post(f'/driver/routes/{route_id}/settlement/submit',
                           json={'amount': 200, 'notes': ''})
        assert resp.status_code == 200
        assert not resp.get_json().get('requires_unprinted_confirm')

    # --- Part 2A: office badge context processor ---

    def test_open_cancellation_count_context_processor(self, recon_app):
        from app import db
        from models import CODReceipt, utc_now
        rid, stop_id, _ = self._make_delivered_stop(recon_app)
        with recon_app.app_context():
            before = CODReceipt.query.filter(
                CODReceipt.cancellation_requested_at.isnot(None),
                CODReceipt.status != 'VOIDED'
            ).count()
            db.session.get(CODReceipt, rid).cancellation_requested_at = utc_now()
            db.session.commit()
            after = CODReceipt.query.filter(
                CODReceipt.cancellation_requested_at.isnot(None),
                CODReceipt.status != 'VOIDED'
            ).count()
            assert after == before + 1


class TestRound4Part2Html:
    """Rendering acceptance tests: assert HTML stop-card state transitions."""

    def _setup_globals(self, recon_app):
        recon_app.jinja_env.filters.setdefault('local_time', lambda v, *a: v)
        recon_app.jinja_env.globals.setdefault('cooler_driver_view_enabled', lambda: False)
        from werkzeug.routing.exceptions import BuildError
        orig = recon_app.jinja_env.globals.get('url_for')
        def safe_url(ep, **kw):
            try:
                return orig(ep, **kw) if orig else '#'
            except BuildError:
                return '#'
        recon_app.jinja_env.globals['url_for'] = safe_url

    def _login(self, client):
        client.post('/login', data={'username': 'test_driver_user',
                                    'password': 'test_password'})

    def _deliver(self, recon_app, route_id, stop_id):
        from app import db
        from models import RouteStop, utc_now
        with recon_app.app_context():
            db.session.get(RouteStop, stop_id).delivered_at = utc_now()
            db.session.commit()

    def _basic_stop(self, recon_app, **receipt_kw):
        """Delivered stop with one CODReceipt."""
        from app import db
        from models import CODReceipt, RouteStop
        rid = _make_receipt(recon_app, status='DRAFT', doc_type='official',
                            expected_amount=Decimal('100.00'),
                            received_amount=Decimal('100.00'), **receipt_kw)
        with recon_app.app_context():
            r = db.session.get(CODReceipt, rid)
            stop_id = r.route_stop_id
            route_id = r.route_id
        self._deliver(recon_app, route_id, stop_id)
        return rid, stop_id, route_id

    def test_unprinted_stop_shows_chip_and_print_receipt_label(self, recon_app):
        self._setup_globals(recon_app)
        rid, stop_id, route_id = self._basic_stop(recon_app)
        c = recon_app.test_client()
        self._login(c)
        html = c.get(f'/driver/routes/{route_id}/stops').data.decode()
        assert 'NOT PRINTED' in html
        assert 'Print Receipt' in html
        assert 'Reprint Receipt' not in html

    def test_printed_stop_shows_reprint_and_synced_chip(self, recon_app):
        from app import db
        from models import CODReceipt, utc_now
        self._setup_globals(recon_app)
        rid, stop_id, route_id = self._basic_stop(recon_app)
        with recon_app.app_context():
            r = db.session.get(CODReceipt, rid)
            r.first_printed_at = utc_now()
            r.print_count = 1
            r.ps365_reference_number = 'R-TEST-001'
            db.session.commit()
        c = recon_app.test_client()
        self._login(c)
        html = c.get(f'/driver/routes/{route_id}/stops').data.decode()
        assert 'Reprint Receipt' in html
        assert 'NOT PRINTED' not in html
        assert 'R-TEST-001' in html            # ps365_reference_number shown on chip
        assert 'SYNCED' in html
        assert 'Locked (printed)' in html

    def test_print_count_gt1_shows_times(self, recon_app):
        from app import db
        from models import CODReceipt, utc_now
        self._setup_globals(recon_app)
        rid, stop_id, route_id = self._basic_stop(recon_app)
        with recon_app.app_context():
            r = db.session.get(CODReceipt, rid)
            r.first_printed_at = utc_now()
            r.print_count = 3
            r.ps365_reference_number = 'R-TEST-002'
            db.session.commit()
        c = recon_app.test_client()
        self._login(c)
        html = c.get(f'/driver/routes/{route_id}/stops').data.decode()
        assert 'printed' in html and '3' in html

    def test_cancellation_pending_chip(self, recon_app):
        from app import db
        from models import CODReceipt, utc_now
        self._setup_globals(recon_app)
        rid, stop_id, route_id = self._basic_stop(
            recon_app, first_printed_at=utc_now())
        with recon_app.app_context():
            r = db.session.get(CODReceipt, rid)
            r.cancellation_requested_at = utc_now()
            r.print_count = 1
            db.session.commit()
        c = recon_app.test_client()
        self._login(c)
        html = c.get(f'/driver/routes/{route_id}/stops').data.decode()
        assert 'Cancellation requested' in html
        assert 'waiting for office' in html
        assert 'Request Cancellation' not in html  # not shown when pending

    def test_voided_receipt_shows_voided_chip(self, recon_app):
        from app import db
        from models import CODReceipt, utc_now
        self._setup_globals(recon_app)
        rid, stop_id, route_id = self._basic_stop(recon_app)
        with recon_app.app_context():
            db.session.get(CODReceipt, rid).status = 'VOIDED'
            db.session.commit()
        c = recon_app.test_client()
        self._login(c)
        html = c.get(f'/driver/routes/{route_id}/stops').data.decode()
        assert 'voided by office' in html.lower()
        assert 'onclick="reprintReceipt' not in html

    def test_replacement_ready_shows_green_button(self, recon_app):
        from app import db
        from models import CODReceipt, utc_now
        self._setup_globals(recon_app)
        rid, stop_id, route_id = self._basic_stop(recon_app)
        with recon_app.app_context():
            orig = db.session.get(CODReceipt, rid)
            rep = CODReceipt(
                route_id=route_id, route_stop_id=stop_id,
                driver_username='test_driver_user', invoice_nos=['INV-1'],
                expected_amount=Decimal('100.00'), received_amount=Decimal('100.00'),
                variance=Decimal('0'), payment_method='cash',
                status='DRAFT', doc_type='official', created_at=utc_now())
            db.session.add(rep)
            db.session.flush()
            orig.status = 'VOIDED'
            orig.replaced_by_cod_receipt_id = rep.id
            db.session.commit()
            rep_id = rep.id
        c = recon_app.test_client()
        self._login(c)
        html = c.get(f'/driver/routes/{route_id}/stops').data.decode()
        assert 'New receipt ready' in html
        assert f'reprintReceipt({rep_id})' in html
        # old receipt button should NOT appear
        assert f'reprintReceipt({rid})' not in html


class TestRound4Part2CreditStop:
    """Credit-stop guard: is_unprinted never fires, settlement gate never fires."""

    def _login(self, client):
        client.post('/login', data={'username': 'test_driver_user',
                                    'password': 'test_password'})

    def _make_credit_stop_with_receipt(self, recon_app):
        """Delivered stop whose customer has is_credit=True, with an unprinted DRAFT receipt."""
        from app import db
        from models import (CODReceipt, RouteStop, Shipment,
                            CreditTerms, utc_now)
        from datetime import date
        with recon_app.app_context():
            ship = Shipment(driver_name='test_driver_user',
                            delivery_date=date.today())
            db.session.add(ship)
            db.session.flush()
            stop = RouteStop(shipment_id=ship.id, seq_no=1,
                             customer_code='CREDIT_CUST_TEST')
            db.session.add(stop)
            db.session.flush()
            stop.delivered_at = utc_now()
            # Ensure credit terms exist for this customer
            ct = CreditTerms.query.filter_by(
                customer_code='CREDIT_CUST_TEST', is_credit=True).first()
            if not ct:
                ct = CreditTerms(
                    customer_code='CREDIT_CUST_TEST',
                    terms_code='NET30', is_credit=True,
                    allow_cash=False, allow_cheque=False,
                    allow_bank_transfer=True, allow_card_pos=False)
                db.session.add(ct)
            # Add an unprinted receipt (edge-case: manual creation on credit stop)
            r = CODReceipt(
                route_id=ship.id, route_stop_id=stop.route_stop_id,
                driver_username='test_driver_user', invoice_nos=['INV-C'],
                expected_amount=Decimal('0'), received_amount=Decimal('0'),
                variance=Decimal('0'), payment_method=None,
                status='DRAFT', doc_type='official', created_at=utc_now())
            db.session.add(r)
            db.session.commit()
            return r.id, stop.route_stop_id, ship.id

    def test_credit_stop_is_unprinted_false(self, recon_app):
        """Credit stops must NOT show the NOT PRINTED chip."""
        from app import db
        from models import CODReceipt
        rid, stop_id, route_id = self._make_credit_stop_with_receipt(recon_app)
        recon_app.jinja_env.filters.setdefault('local_time', lambda v, *a: v)
        recon_app.jinja_env.globals.setdefault('cooler_driver_view_enabled', lambda: False)
        from werkzeug.routing.exceptions import BuildError
        orig = recon_app.jinja_env.globals.get('url_for')
        def safe_url(ep, **kw):
            try:
                return orig(ep, **kw) if orig else '#'
            except BuildError:
                return '#'
        recon_app.jinja_env.globals['url_for'] = safe_url
        c = recon_app.test_client()
        self._login(c)
        html = c.get(f'/driver/routes/{route_id}/stops').data.decode()
        assert 'NOT PRINTED' not in html

    def test_credit_stop_settlement_no_gate(self, recon_app, monkeypatch):
        """Settlement submit must not gate when the only unprinted receipt is a credit stop."""
        import services.payments as sp
        monkeypatch.setattr(sp, 'sync_receipt_ps365_at_print', lambda *a, **kw: None)
        rid, stop_id, route_id = self._make_credit_stop_with_receipt(recon_app)
        c = recon_app.test_client()
        self._login(c)
        resp = c.post(f'/driver/routes/{route_id}/settlement/submit',
                      json={'amount': 0, 'notes': ''})
        data = resp.get_json()
        assert not data.get('requires_unprinted_confirm'), \
            f"Should not gate on credit stop receipt; got {data}"

class TestTokenizedPrintGuard:
    """Phase 1.2: tokenized PDF/PNG print paths must also refuse an official
    receipt that has no PS365 reference after the print-time sync — same
    behavior as the HTML views: 503, no lock, no print_count bump."""

    def _token(self, stop_id):
        from utils.print_token import make_print_token
        return make_print_token(stop_id, 'test_driver_user')

    def test_tokenized_paths_refuse_unsynced_official(self, recon_app, monkeypatch):
        from app import db
        from models import CODReceipt
        import services.payments as sp
        monkeypatch.setattr(sp, 'commit_to_ps365', lambda pe, *a, **k: pe)
        rid = _make_receipt(recon_app, status='DRAFT', doc_type='official')
        with recon_app.app_context():
            stop_id = db.session.get(CODReceipt, rid).route_stop_id
        client = recon_app.test_client()
        token = self._token(stop_id)
        for url in (f'/driver/print/receipt/{stop_id}.pdf?token={token}',
                    f'/driver/print/receipt/{stop_id}.png?token={token}'):
            resp = client.get(url)
            assert resp.status_code == 503, url
            assert b'not registered in Powersoft' in resp.data, url
        with recon_app.app_context():
            r = db.session.get(CODReceipt, rid)
            assert r.status == 'DRAFT'
            assert not r.first_printed_at
            assert (r.print_count or 0) == 0

    def test_tokenized_png_prints_online_doc(self, recon_app, monkeypatch):
        """Online/PDC docs never post and must still print via tokenized paths."""
        from app import db
        from models import CODReceipt
        rid = _make_receipt(recon_app, status='DRAFT', doc_type='online_notice',
                            payment_method='online',
                            received_amount=Decimal('0.00'))
        with recon_app.app_context():
            stop_id = db.session.get(CODReceipt, rid).route_stop_id
        client = recon_app.test_client()
        token = self._token(stop_id)
        resp = client.get(f'/driver/print/receipt/{stop_id}.png?token={token}')
        assert resp.status_code == 200
        with recon_app.app_context():
            r = db.session.get(CODReceipt, rid)
            assert r.status == 'ISSUED'
            assert r.first_printed_at is not None


class TestReconciliationTotalsExcludeVoided:
    """Phase 3.3: voided receipts render on reconciliation but never count."""

    def test_totals_exclude_voided(self, recon_app):
        from app import db
        from models import CODReceipt, utc_now
        rid = _make_receipt(recon_app, status='ISSUED',
                            received_amount=Decimal('80.00'),
                            expected_amount=Decimal('80.00'))
        with recon_app.app_context():
            live = db.session.get(CODReceipt, rid)
            route_id = live.route_id
            voided = CODReceipt(
                route_id=route_id,
                route_stop_id=live.route_stop_id,
                driver_username='test_driver_user',
                invoice_nos='INV-1',
                expected_amount=Decimal('96.16'),
                received_amount=Decimal('96.16'),
                variance=Decimal('0.00'),
                payment_method='cash',
                status='VOIDED',
                void_reason='wrong amount',
                voided_at=utc_now(),
                created_at=utc_now(),
            )
            db.session.add(voided)
            db.session.commit()

            live_total = db.session.query(CODReceipt).filter(
                CODReceipt.route_id == route_id,
                CODReceipt.status != 'VOIDED').all()
            assert sum(r.received_amount for r in live_total) == Decimal('80.00')

class TestOfficePostPs365:
    """Office 'Post to PS365' for reissued receipts that will never be printed
    (route already finished, so print-time sync never fires)."""

    def test_post_endpoint_posts_draft_reissue(self, recon_app, admin_client, monkeypatch):
        from app import db
        from models import CODReceipt
        import services.payments as sp
        rid = _make_receipt(recon_app, status='DRAFT', doc_type='official',
                            received_amount=Decimal('92.00'))

        posted = {}
        def fake_sync(receipt, stop, user_code):
            posted['amount'] = receipt.received_amount
            receipt.ps365_reference_number = 'R9999999'
        monkeypatch.setattr(sp, 'sync_receipt_ps365_at_print', fake_sync)
        # routes_reconciliation imports it inside the function → patch source module
        resp = admin_client.post(f'/reconciliation/api/receipts/{rid}/post-ps365', json={})
        assert resp.status_code == 200, resp.data
        data = resp.get_json()
        assert data['success'] and data['ps365_reference_number'] == 'R9999999'
        assert posted['amount'] == Decimal('92.00')
        with recon_app.app_context():
            assert db.session.get(CODReceipt, rid).ps365_reference_number == 'R9999999'

    def test_post_endpoint_refuses_bad_states(self, recon_app, admin_client, monkeypatch):
        import services.payments as sp
        monkeypatch.setattr(sp, 'sync_receipt_ps365_at_print',
                            lambda *a, **k: None)
        # voided
        rid = _make_receipt(recon_app, status='VOIDED')
        assert admin_client.post(f'/reconciliation/api/receipts/{rid}/post-ps365',
                                 json={}).status_code == 400
        # already posted
        rid = _make_receipt(recon_app, ps365_reference_number='R1'
                            )
        assert admin_client.post(f'/reconciliation/api/receipts/{rid}/post-ps365',
                                 json={}).status_code == 400
        # non-official
        rid = _make_receipt(recon_app, doc_type='online_notice')
        assert admin_client.post(f'/reconciliation/api/receipts/{rid}/post-ps365',
                                 json={}).status_code == 400
        # sync ran but PS365 returned nothing -> 502
        rid = _make_receipt(recon_app, status='DRAFT', doc_type='official')
        assert admin_client.post(f'/reconciliation/api/receipts/{rid}/post-ps365',
                                 json={}).status_code == 502
