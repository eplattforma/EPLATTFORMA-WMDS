"""FIX-008: batch reports / picking order read route+stop from the Routes
module (route_stop_invoice active links) instead of the dead legacy
``Invoice.routing`` field.

Covers the spec's RT1–RT7 scenarios.
"""
from datetime import date


def _mk_route(db, route_id, route_name, status="PLANNED"):
    from models import Shipment
    if not db.session.get(Shipment, route_id):
        db.session.add(Shipment(
            id=route_id, driver_name="D", route_name=route_name,
            delivery_date=date(2026, 7, 8), status=status,
        ))
        db.session.flush()


def _mk_invoice(db, invoice_no, routing=None):
    from models import Invoice
    db.session.add(Invoice(
        invoice_no=invoice_no, customer_name="C", customer_code="CC",
        status="Not Started", routing=routing, upload_date="2026-07-08",
    ))
    db.session.flush()


def _link(db, route_id, invoice_no, seq_no, is_active=True):
    from models import RouteStop, RouteStopInvoice
    rs = RouteStop(shipment_id=route_id, seq_no=seq_no, stop_name="S")
    db.session.add(rs)
    db.session.flush()
    db.session.add(RouteStopInvoice(
        route_stop_id=rs.route_stop_id, invoice_no=invoice_no,
        is_active=is_active, status="PENDING",
    ))
    db.session.flush()


def _mk_batch(db, invoice_nos, mode="Sequential"):
    from models import BatchPickingSession, BatchSessionInvoice
    bps = BatchPickingSession(
        name="t", created_by="test_admin_user", picking_mode=mode,
        zones="A1", session_type="standard", status="Active",
    )
    db.session.add(bps)
    db.session.flush()
    for inv_no in invoice_nos:
        db.session.add(BatchSessionInvoice(
            batch_session_id=bps.id, invoice_no=inv_no))
    db.session.commit()
    return bps


def test_rt1_standard_batch_route_linked_label(app):
    """RT1: standard zone batch, invoice on active route → route+stop label."""
    from app import db
    from routes_batch import _build_stop_seq_lookup, _routing_label_for_invoice
    from models import Invoice

    with app.app_context():
        _mk_route(db, 483, "PAFOS THU2")
        _mk_invoice(db, "IN10056387")
        _link(db, 483, "IN10056387", 9)
        bps = _mk_batch(db, ["IN10056387"])

        lookup = _build_stop_seq_lookup(bps)
        inv = db.session.get(Invoice, "IN10056387")
        assert _routing_label_for_invoice(inv, lookup) == "PAFOS THU2 · STOP 9"


def test_rt2_legacy_routing_still_prints(app):
    """RT2: invoice with legacy routing, no route link → legacy prints."""
    from app import db
    from routes_batch import _build_stop_seq_lookup, _routing_label_for_invoice
    from models import Invoice

    with app.app_context():
        _mk_invoice(db, "INV-LEG", routing="42")
        bps = _mk_batch(db, ["INV-LEG"])

        lookup = _build_stop_seq_lookup(bps)
        inv = db.session.get(Invoice, "INV-LEG")
        assert _routing_label_for_invoice(inv, lookup) == "42"


def test_rt3_no_route_at_all(app):
    """RT3: invoice on no route, no routing → NO-ROUTING."""
    from app import db
    from routes_batch import _build_stop_seq_lookup, _routing_label_for_invoice
    from models import Invoice

    with app.app_context():
        _mk_invoice(db, "INV-NONE")
        bps = _mk_batch(db, ["INV-NONE"])

        lookup = _build_stop_seq_lookup(bps)
        inv = db.session.get(Invoice, "INV-NONE")
        assert _routing_label_for_invoice(inv, lookup) == "NO-ROUTING"


def test_rt4_batch_spanning_two_routes_grouped(app):
    """RT4: batch spanning two routes → grouped per route, stop order within."""
    from app import db
    from routes_batch import get_sorted_batch_invoices

    with app.app_context():
        _mk_route(db, 601, "ALPHA MON1")
        _mk_route(db, 602, "BETA TUE1")
        for inv_no, rid, seq in [
            ("INV-B2", 602, 2), ("INV-A5", 601, 5),
            ("INV-B1", 602, 1), ("INV-A3", 601, 3),
        ]:
            _mk_invoice(db, inv_no)
            _link(db, rid, inv_no, seq)
        bps = _mk_batch(db, ["INV-B2", "INV-A5", "INV-B1", "INV-A3"])

        order = [bi.invoice_no for bi in get_sorted_batch_invoices(bps)]
        assert order == ["INV-A3", "INV-A5", "INV-B1", "INV-B2"]


def test_rt5_completed_route_relink_new_route_wins(app):
    """RT5: old route COMPLETED + relinked → the new route's stop wins."""
    from app import db
    from routes_batch import _build_stop_seq_lookup, _routing_label_for_invoice
    from models import Invoice, RouteStopInvoice

    with app.app_context():
        _mk_route(db, 701, "OLD ROUTE", status="COMPLETED")
        _mk_route(db, 702, "NEW ROUTE")
        _mk_invoice(db, "INV-RELINK")
        # Old link left active but its shipment is COMPLETED → excluded
        _link(db, 701, "INV-RELINK", 4)
        _link(db, 702, "INV-RELINK", 7)
        bps = _mk_batch(db, ["INV-RELINK"])

        lookup = _build_stop_seq_lookup(bps)
        inv = db.session.get(Invoice, "INV-RELINK")
        assert _routing_label_for_invoice(inv, lookup) == "NEW ROUTE · STOP 7"

        # Also: deactivated old link on a live route must not win
        RouteStopInvoice.query.filter_by(invoice_no="INV-RELINK").update(
            {"is_active": False})
        db.session.commit()
        lookup = _build_stop_seq_lookup(bps)
        assert _routing_label_for_invoice(inv, lookup) == "NO-ROUTING"


def test_rt6_sequential_rebuild_follows_stop_sequence(app):
    """RT6: rebuild_items_from_queue Sequential order follows stop seq."""
    from app import db
    from sqlalchemy import text
    from models import InvoiceItem

    with app.app_context():
        from tests.test_phase4_batch_picking import _ensure_queue_table
        _ensure_queue_table(db)
        _mk_route(db, 801, "GAMMA WED1")
        for inv_no, seq in [("INV-S2", 2), ("INV-S1", 1)]:
            _mk_invoice(db, inv_no)
            _link(db, 801, inv_no, seq)
            db.session.add(InvoiceItem(
                invoice_no=inv_no, item_code=f"IT-{inv_no}",
                item_name="X", location="A01-01-01", zone="A1",
                qty=1, is_picked=False, pick_status="not_picked",
            ))
        # Legacy-routing invoice: must come after route-linked ones
        _mk_invoice(db, "INV-S9", routing="999")
        db.session.add(InvoiceItem(
            invoice_no="INV-S9", item_code="IT-INV-S9",
            item_name="X", location="A01-01-02", zone="A1",
            qty=1, is_picked=False, pick_status="not_picked",
        ))
        bps = _mk_batch(db, ["INV-S2", "INV-S1", "INV-S9"])
        for i, inv_no in enumerate(["INV-S2", "INV-S1", "INV-S9"], start=1):
            db.session.execute(text(
                "INSERT INTO batch_pick_queue (batch_session_id, invoice_no, "
                "item_code, pick_zone_type, sequence_no, status, qty_required) "
                "VALUES (:bid, :inv, :code, 'normal', :seq, 'pending', 1)"
            ), {"bid": bps.id, "inv": inv_no, "code": f"IT-{inv_no}", "seq": i})
        db.session.commit()

        from services.batch_picking import rebuild_items_from_queue
        rebuilt = rebuild_items_from_queue(bps.id)
        order = [e["current_invoice"] for e in rebuilt]
        assert order == ["INV-S1", "INV-S2", "INV-S9"]
        # RT7: the pick-screen header label comes through 'routing'
        by_inv = {e["current_invoice"]: e for e in rebuilt}
        assert by_inv["INV-S1"]["routing"] == "GAMMA WED1 · STOP 1"
        assert by_inv["INV-S9"]["routing"] == "999"


def test_report_route_verification_pafos(app):
    """Spec verification: IN10056387/IN10056393 → STOP 9 and STOP 13,
    PAFOS THU2, in that order on the printed report."""
    from app import db
    from routes_batch import _build_stop_seq_lookup, _routing_label_for_invoice, get_sorted_batch_invoices

    with app.app_context():
        _mk_route(db, 484, "PAFOS THU2")
        _mk_invoice(db, "IN-PAF-9")
        _mk_invoice(db, "IN-PAF-13")
        _link(db, 484, "IN-PAF-9", 9)
        _link(db, 484, "IN-PAF-13", 13)
        bps = _mk_batch(db, ["IN-PAF-13", "IN-PAF-9"])

        lookup = _build_stop_seq_lookup(bps)
        order = [bi.invoice_no for bi in get_sorted_batch_invoices(bps)]
        assert order == ["IN-PAF-9", "IN-PAF-13"]
        from models import Invoice
        labels = [
            _routing_label_for_invoice(db.session.get(Invoice, n), lookup)
            for n in order
        ]
        assert labels == ["PAFOS THU2 · STOP 9", "PAFOS THU2 · STOP 13"]


def test_multi_active_link_deterministic_lowest_seq_wins(app):
    """Data anomaly: two ACTIVE links for one invoice → the lowest seq_no
    (then lowest route_stop_id) wins deterministically."""
    from app import db
    from services.route_links import route_links_for_invoices

    with app.app_context():
        _mk_route(db, 485, "NICOSIA WED1")
        _mk_invoice(db, "IN-DUP-1")
        _link(db, 485, "IN-DUP-1", 14)
        _link(db, 485, "IN-DUP-1", 3)

        entry = route_links_for_invoices(["IN-DUP-1"])["IN-DUP-1"]
        assert entry["seq"] == 3
        assert entry["route_name"] == "NICOSIA WED1"
