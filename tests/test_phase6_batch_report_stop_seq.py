"""Phase 6 follow-up: batch picking report uses stop sequence for cooler batches.

Verifies that `_routing_label_for_invoice` and `_build_stop_seq_lookup`
produce the expected labels and ordering when the batch session is bound to
a delivery route, and falls back to legacy Invoice.routing otherwise.
"""
from datetime import date


def _seed(db, route_id, invoice_no, seq_no, with_route=True):
    from models import (
        Invoice, Shipment, RouteStop, RouteStopInvoice,
    )
    if with_route and not db.session.get(Shipment, route_id):
        db.session.add(Shipment(
            id=route_id, driver_name="D",
            delivery_date=date(2026, 6, 1), status="PLANNED",
        ))
        db.session.flush()
    db.session.add(Invoice(
        invoice_no=invoice_no, customer_name="C", customer_code="CC",
        status="Not Started", routing="999",
        upload_date="2026-06-01",
        route_id=route_id if with_route else None,
    ))
    db.session.flush()
    if with_route:
        rs = RouteStop(shipment_id=route_id, seq_no=seq_no, stop_name="S")
        db.session.add(rs)
        db.session.flush()
        db.session.add(RouteStopInvoice(
            route_stop_id=rs.route_stop_id, invoice_no=invoice_no,
            is_active=True, status="ready_for_dispatch",
        ))
    db.session.commit()


def test_label_uses_stop_seq_for_cooler_batch(app):
    from app import db
    from models import BatchPickingSession, Invoice
    from routes_batch import _build_stop_seq_lookup, _routing_label_for_invoice

    with app.app_context():
        _seed(db, route_id=900, invoice_no="INV-RPTA", seq_no=2.0)
        _seed(db, route_id=900, invoice_no="INV-RPTB", seq_no=5.0)

        bps = BatchPickingSession(name="t", created_by="test_admin_user", picking_mode="Sequential",
            zones="SENSITIVE", session_type="cooler_route", route_id=900,
            status="Active",
        )
        db.session.add(bps)
        db.session.commit()

        lookup = _build_stop_seq_lookup(bps)
        assert lookup == {"INV-RPTA": 2.0, "INV-RPTB": 5.0}

        inv_a = db.session.get(Invoice, "INV-RPTA")
        inv_b = db.session.get(Invoice, "INV-RPTB")
        assert _routing_label_for_invoice(inv_a, lookup) == "STOP 2"
        assert _routing_label_for_invoice(inv_b, lookup) == "STOP 5"


def test_label_falls_back_to_routing_for_standard_batch(app):
    from app import db
    from models import BatchPickingSession, Invoice
    from routes_batch import _build_stop_seq_lookup, _routing_label_for_invoice

    with app.app_context():
        _seed(db, route_id=901, invoice_no="INV-RPTS", seq_no=1.0, with_route=False)

        bps = BatchPickingSession(name="t", created_by="test_admin_user", picking_mode="Sequential",zones="A1", session_type="standard",
                                  status="Active")
        db.session.add(bps)
        db.session.commit()

        lookup = _build_stop_seq_lookup(bps)
        assert lookup == {}

        inv = db.session.get(Invoice, "INV-RPTS")
        assert _routing_label_for_invoice(inv, lookup) == "999"


def test_label_unsequenced_invoice_in_cooler_batch_falls_back(app):
    from app import db
    from models import Invoice, BatchPickingSession
    from routes_batch import _build_stop_seq_lookup, _routing_label_for_invoice

    with app.app_context():
        _seed(db, route_id=902, invoice_no="INV-RPTX", seq_no=1.0)
        # Add a second invoice with route_id but no RouteStopInvoice mapping
        db.session.add(Invoice(
            invoice_no="INV-RPTY", customer_name="C", customer_code="CC",
            status="Not Started", routing=None, upload_date="2026-06-01",
            route_id=902,
        ))
        db.session.commit()

        bps = BatchPickingSession(name="t", created_by="test_admin_user", picking_mode="Sequential",zones="SENSITIVE", session_type="cooler_route",
                                  route_id=902, status="Active")
        db.session.add(bps)
        db.session.commit()

        lookup = _build_stop_seq_lookup(bps)
        inv_y = db.session.get(Invoice, "INV-RPTY")
        # No stop seq for INV-RPTY, no routing → falls back to NO-ROUTING
        assert _routing_label_for_invoice(inv_y, lookup) == "NO-ROUTING"
