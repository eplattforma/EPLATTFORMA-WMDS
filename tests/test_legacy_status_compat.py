"""
Tests for legacy invoice status compatibility.

Retired status names ('In Progress', 'Completed', 'Ready for Packing') can
still exist on old production rows. These tests verify:
- normalize_status maps them to canonical statuses
- expand_legacy_aliases includes them in SQL IN-filter lists
- heal_legacy_invoice_statuses rewrites legacy rows to canonical values
- the admin dashboard query surfaces (and heals) legacy rows
"""
import os
import sys

# Match the project test convention: force SQLite before app import and put
# the project root on sys.path so this file runs standalone.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SESSION_SECRET", "test-secret")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from delivery_status import (
    normalize_status,
    expand_legacy_aliases,
    heal_legacy_invoice_statuses,
)


def test_normalize_retired_names():
    assert normalize_status('In Progress') == 'picking'
    assert normalize_status('Completed') == 'ready_for_dispatch'
    assert normalize_status('Ready for Packing') == 'awaiting_packing'
    # canonical values pass through unchanged
    assert normalize_status('picking') == 'picking'
    assert normalize_status('not_started') == 'not_started'


def test_normalize_case_variants():
    for v in ('IN PROGRESS', 'in progress', 'IN_PROGRESS', 'In Progress'):
        assert normalize_status(v) == 'picking'
    for v in ('COMPLETED', 'completed', 'Completed'):
        assert normalize_status(v) == 'ready_for_dispatch'
    for v in ('READY FOR PACKING', 'READY_FOR_PACKING', 'Ready for Packing'):
        assert normalize_status(v) == 'awaiting_packing'
    assert normalize_status('ASSIGNED') == 'ready_for_dispatch'
    assert normalize_status('Assigned') == 'ready_for_dispatch'


def test_expand_legacy_aliases_case_variants():
    expanded = expand_legacy_aliases(['picking', 'ready_for_dispatch', 'awaiting_packing'])
    for legacy in ('In Progress', 'IN PROGRESS', 'in progress', 'IN_PROGRESS',
                   'Completed', 'COMPLETED', 'completed',
                   'Ready for Packing', 'READY FOR PACKING',
                   'Assigned', 'ASSIGNED', 'assigned'):
        assert legacy in expanded, legacy


def test_expand_legacy_aliases():
    expanded = expand_legacy_aliases(['not_started', 'picking', 'awaiting_packing'])
    assert 'In Progress' in expanded
    assert 'Ready for Packing' in expanded
    # canonical originals preserved
    assert 'not_started' in expanded and 'picking' in expanded

    expanded2 = expand_legacy_aliases(['ready_for_dispatch'])
    assert 'Completed' in expanded2


def _register_template_stubs(app):
    """The test fixture lacks the 'local_time' filter, permission helpers and
    some blueprints referenced by base.html; register lenient stubs so full
    page renders work."""
    app.jinja_env.filters.setdefault('local_time', lambda v, fmt=None: str(v))
    app.jinja_env.globals.setdefault('has_permission', lambda *a, **k: True)

    from flask import url_for as _real_url_for

    def _lenient_url_for(endpoint, **values):
        try:
            return _real_url_for(endpoint, **values)
        except Exception:
            return '#'

    app.jinja_env.globals['url_for'] = _lenient_url_for


def _make_invoice(db, invoice_no, status):
    from models import Invoice
    inv = Invoice()
    inv.invoice_no = invoice_no
    inv.customer_name = 'Legacy Test Customer'
    inv.status = status
    from datetime import datetime
    inv.upload_date = datetime.utcnow()
    db.session.add(inv)
    db.session.commit()
    return inv


def test_heal_legacy_invoice_statuses(app):
    from app import db
    with app.app_context():
        inv1 = _make_invoice(db, 'LEG-001', 'In Progress')
        inv2 = _make_invoice(db, 'LEG-002', 'Completed')
        inv3 = _make_invoice(db, 'LEG-003', 'picking')
        inv4 = _make_invoice(db, 'LEG-004', 'IN PROGRESS')
        inv5 = _make_invoice(db, 'LEG-005', 'ASSIGNED')

        heal_legacy_invoice_statuses([inv1, inv2, inv3, inv4, inv5])

        from models import Invoice
        assert Invoice.query.get('LEG-001').status == 'picking'
        assert Invoice.query.get('LEG-002').status == 'ready_for_dispatch'
        assert Invoice.query.get('LEG-003').status == 'picking'
        assert Invoice.query.get('LEG-004').status == 'picking'
        assert Invoice.query.get('LEG-005').status == 'ready_for_dispatch'


def test_legacy_rows_visible_in_open_status_query(app):
    """A legacy 'In Progress' row must be found by the expanded dashboard
    filter (it would be hidden by the canonical-only list)."""
    from app import db
    from models import Invoice
    with app.app_context():
        _make_invoice(db, 'LEG-010', 'In Progress')

        canonical_only = ['not_started', 'picking', 'awaiting_batch_items',
                          'awaiting_packing', 'ready_for_dispatch']
        hidden = Invoice.query.filter(Invoice.status.in_(canonical_only)).all()
        assert all(i.invoice_no != 'LEG-010' for i in hidden)

        found = Invoice.query.filter(
            Invoice.status.in_(expand_legacy_aliases(canonical_only))
        ).all()
        assert any(i.invoice_no == 'LEG-010' for i in found)

        # and after healing, the canonical-only filter finds it too
        heal_legacy_invoice_statuses(found)
        assert Invoice.query.get('LEG-010').status == 'picking'


def test_filtered_search_includes_legacy_completed(app):
    """Filtering completed-order search by 'ready_for_dispatch' must also
    match old rows still carrying the retired 'Completed' spelling."""
    from app import db
    from models import Invoice
    with app.app_context():
        _make_invoice(db, 'LEG-030', 'Completed')
        _make_invoice(db, 'LEG-031', 'ready_for_dispatch')
        _make_invoice(db, 'LEG-032', 'COMPLETED')
        _make_invoice(db, 'LEG-033', 'ASSIGNED')

        rows = Invoice.query.filter(
            Invoice.status.in_(expand_legacy_aliases(['ready_for_dispatch']))
        ).all()
        nos = {r.invoice_no for r in rows}
        assert {'LEG-030', 'LEG-031', 'LEG-032', 'LEG-033'} <= nos


def test_open_orders_shows_and_heals_legacy_ready_for_packing(app, client, admin_auth):
    """A legacy 'Ready for Packing' invoice must appear on the Operations
    Open Orders view (awaiting_packing column) and be healed to canonical."""
    from app import db
    from models import Invoice
    with app.app_context():
        inv = _make_invoice(db, 'LEG-040', 'Ready for Packing')
        inv.total_weight = 1.0
        db.session.commit()

    _register_template_stubs(app)

    resp = client.get('/operations/open-orders')
    assert resp.status_code == 200
    assert b'LEG-040' in resp.data

    with app.app_context():
        assert Invoice.query.get('LEG-040').status == 'awaiting_packing'


def test_admin_dashboard_heals_and_shows_legacy_row(app, client, admin_auth):
    from app import db
    from models import Invoice
    with app.app_context():
        _make_invoice(db, 'LEG-020', 'In Progress')

    _register_template_stubs(app)

    resp = client.get('/admin/dashboard')
    assert resp.status_code == 200
    assert b'LEG-020' in resp.data

    with app.app_context():
        assert Invoice.query.get('LEG-020').status == 'picking'
