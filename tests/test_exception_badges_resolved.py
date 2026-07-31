"""
Regression tests: resolved-exception badges must not reappear.

Exception badges on the admin dashboard and operations open-orders page,
and the exception report header, must count only UNRESOLVED picking
exceptions. A NULL is_resolved value must be treated as open (unresolved).

Covers:
- routes.py admin_dashboard exception_counts query (is_resolved.isnot(True))
- routes_operations.py open_orders exception_counts query
- templates/admin_view_exceptions.html header open/total counts
"""

import pytest
from sqlalchemy import text


INVOICE_NO = 'IN19999'


def _register_filter_stubs(app):
    """Register lightweight stand-ins for filters normally added by main.py
    (importing main.py runs Postgres-only schema migrations that break on
    the SQLite test DB — same pattern as test_receipt_controls.py)."""
    f = app.jinja_env.filters
    f.setdefault('local_time', lambda v, *a: str(v) if v else 'N/A')
    f.setdefault('current_athens_time', lambda v, *a: '')
    f.setdefault('display_name', lambda u: u or '')
    f.setdefault('status_badge', lambda s: f'<span class="badge">{s}</span>')
    f.setdefault('batch_badge', lambda inv: '')

    # base.html / admin_dashboard.html call has_permission(...) and build
    # URLs for blueprints the slim test conftest does not register.
    from flask_login import current_user
    from services.permissions import has_permission
    app.jinja_env.globals.setdefault(
        'has_permission', lambda key: has_permission(current_user, key))
    if not getattr(app, '_missing_endpoint_stub_added', False):
        app.url_build_error_handlers.append(
            lambda error, endpoint, values: "/__missing__/" + endpoint)
        app._missing_endpoint_stub_added = True


def _cleanup(db, invoice_nos):
    """Remove leftovers from earlier tests (shared in-memory SQLite)."""
    for inv in invoice_nos:
        db.session.execute(
            text("DELETE FROM picking_exceptions WHERE invoice_no = :inv"), {"inv": inv})
        db.session.execute(
            text("DELETE FROM invoice_items WHERE invoice_no = :inv"), {"inv": inv})
        db.session.execute(
            text("DELETE FROM invoices WHERE invoice_no = :inv"), {"inv": inv})
    db.session.commit()


@pytest.fixture(scope='function')
def mixed_exception_invoice(app):
    """Invoice with 4 exceptions: 1 resolved, 2 unresolved (False), 1 NULL.

    Expected open count = 3 (False + False + NULL), total = 4.
    """
    _register_filter_stubs(app)

    with app.app_context():
        from app import db
        from models import Invoice, InvoiceItem, PickingException

        _cleanup(db, [INVOICE_NO])

        invoice = Invoice(
            invoice_no=INVOICE_NO,
            routing='42',
            customer_name='Badge Regression Customer',
            upload_date='2026-07-30',
            total_lines=2,
            total_items=8,
            total_weight=3.0,
            status='not_started',
        )
        db.session.add(invoice)

        db.session.add(InvoiceItem(
            invoice_no=INVOICE_NO, item_code='ITEM-A',
            item_name='Item A', qty=4, is_picked=False,
        ))
        db.session.add(InvoiceItem(
            invoice_no=INVOICE_NO, item_code='ITEM-B',
            item_name='Item B', qty=4, is_picked=False,
        ))

        def add_exc(item_code, resolved):
            exc = PickingException(
                invoice_no=INVOICE_NO,
                item_code=item_code,
                expected_qty=4,
                picked_qty=2,
                picker_username='test_picker_user',
                is_resolved=resolved,
            )
            db.session.add(exc)
            return exc

        add_exc('ITEM-A', True)      # resolved -> must NOT be counted
        add_exc('ITEM-A', False)     # open
        add_exc('ITEM-B', False)     # open
        null_exc = add_exc('ITEM-B', False)  # will be forced to NULL below
        db.session.commit()

        # Force a genuine NULL (SQLAlchemy column default would otherwise
        # turn is_resolved=None into False on insert).
        db.session.execute(
            text("UPDATE picking_exceptions SET is_resolved = NULL WHERE id = :id"),
            {"id": null_exc.id},
        )
        db.session.commit()

        # Sanity-check the fixture itself: exactly one resolved row, one NULL.
        rows = db.session.execute(
            text("SELECT is_resolved FROM picking_exceptions WHERE invoice_no = :inv"),
            {"inv": INVOICE_NO},
        ).fetchall()
        vals = [r[0] for r in rows]
        assert len(vals) == 4
        assert sum(1 for v in vals if v is None) == 1
        assert sum(1 for v in vals if v) == 1

    return INVOICE_NO


def test_admin_dashboard_counts_only_unresolved(admin_auth, mixed_exception_invoice):
    """Dashboard badge must show 3 (2 open + 1 NULL), never 4."""
    response = admin_auth.get('/admin/dashboard')
    assert response.status_code == 200
    html = response.data.decode('utf-8')

    assert INVOICE_NO in html
    # Badge tooltip renders "<count> picking exceptions"
    assert '3 picking exceptions' in html
    assert '4 picking exceptions' not in html
    assert '1 picking exceptions' not in html


def test_open_orders_counts_only_unresolved(admin_auth, mixed_exception_invoice):
    """Open-orders badge must show '3 issues' (unresolved incl. NULL only)."""
    response = admin_auth.get('/operations/open-orders')
    assert response.status_code == 200
    html = response.data.decode('utf-8')

    assert INVOICE_NO in html
    assert '3 issues' in html
    assert '4 issues' not in html


def test_exception_report_header_open_vs_total(admin_auth, mixed_exception_invoice):
    """Exception report header must show '3 open / 4 total'."""
    response = admin_auth.get(f'/admin/view_exceptions/{INVOICE_NO}')
    assert response.status_code == 200
    html = response.data.decode('utf-8')

    assert '3 open / 4 total' in html
    # Resolved row still listed, marked resolved (row highlighted + badge).
    assert 'Resolved' in html


def test_all_resolved_shows_zero_badges(admin_auth, app):
    """When every exception is resolved, no danger badge should render."""
    _register_filter_stubs(app)

    inv_no = 'IN19998'
    with app.app_context():
        from app import db
        from models import Invoice, PickingException

        _cleanup(db, [inv_no])

        db.session.add(Invoice(
            invoice_no=inv_no,
            customer_name='All Resolved Customer',
            upload_date='2026-07-30',
            total_lines=1, total_items=1, total_weight=1.0,
            status='not_started',
        ))
        db.session.add(PickingException(
            invoice_no=inv_no, item_code='ITEM-C',
            expected_qty=2, picked_qty=1,
            picker_username='test_picker_user',
            is_resolved=True,
        ))
        db.session.commit()

    dash = admin_auth.get('/admin/dashboard').data.decode('utf-8')
    assert inv_no in dash
    assert '1 picking exceptions' not in dash

    report = admin_auth.get(f'/admin/view_exceptions/{inv_no}').data.decode('utf-8')
    assert '0 open / 1 total' in report
