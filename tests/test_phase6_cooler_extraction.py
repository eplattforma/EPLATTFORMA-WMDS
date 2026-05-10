"""Phase 6 — Cooler / Regular-Picking Integration: Phase 1 auto-extract.

P1.1  summer_cooler_mode OFF → no extraction
P1.2  basic SENSITIVE extraction creates queue rows + locks items
P1.3  idempotent: re-running attach_invoices_to_stop adds no duplicates
P1.4  already-picked SENSITIVE item → ActivityLog warning + DQ log,
      no queue row, no lock change
P1.5  missing-dimensions item is still locked + queued, DQ log entry
P1.6  non-SENSITIVE items are NOT extracted
P1.7  invoice moved between routes: queue rows transfer to new session
"""
import os
os.environ.setdefault("SESSION_SECRET", "test-secret-key-for-testing")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from datetime import datetime

import pytest
from sqlalchemy import text


def _ensure_phase6_tables(db):
    """Create the Phase 4 + Phase 6 raw-SQL tables on the in-memory
    SQLite DB used by tests."""
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS batch_pick_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_session_id INTEGER NOT NULL,
            invoice_no VARCHAR(50) NOT NULL,
            item_code VARCHAR(50) NOT NULL,
            pick_zone_type VARCHAR(20) NOT NULL DEFAULT 'normal',
            sequence_no INTEGER,
            delivery_sequence NUMERIC(10, 2),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            qty_required NUMERIC(12,3),
            qty_picked NUMERIC(12,3) DEFAULT 0,
            picked_by VARCHAR(64),
            picked_at TIMESTAMP,
            cancelled_at TIMESTAMP,
            wms_zone VARCHAR(50),
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS cooler_data_quality_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_no VARCHAR(50),
            item_code VARCHAR(50),
            issue_type VARCHAR(40) NOT NULL,
            details TEXT,
            route_id INTEGER,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    db.session.commit()


def _make_setting(db, key, value):
    from models import Setting
    s = Setting.query.filter_by(key=key).first()
    if s is None:
        db.session.add(Setting(key=key, value=value))
    else:
        s.value = value
    db.session.commit()


def _make_dw_item(db, code, zone="SENSITIVE",
                  length=10.0, width=10.0, height=10.0, weight=0.5):
    from models import DwItem
    from timezone_utils import get_utc_now
    existing = DwItem.query.filter_by(item_code_365=code).first()
    if existing:
        existing.wms_zone = zone
        existing.item_length = length
        existing.item_width = width
        existing.item_height = height
        existing.item_weight = weight
    else:
        db.session.add(DwItem(
            item_code_365=code, item_name=f"DW {code}", active=True,
            attr_hash="x", last_sync_at=get_utc_now(), wms_zone=zone,
            item_length=length, item_width=width, item_height=height,
            item_weight=weight,
        ))
    db.session.commit()


def _make_invoice(db, invoice_no, route_id, items, delivery_date="2026-05-03"):
    from models import Invoice, InvoiceItem, Shipment
    sh = Shipment.query.get(route_id)
    if sh is None:
        sh = Shipment(
            id=route_id, driver_name=f"Driver-{route_id}",
            delivery_date=datetime.strptime(delivery_date, "%Y-%m-%d").date(),
            status="PLANNED",
        )
        db.session.add(sh)
        db.session.flush()
    inv = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if inv is None:
        inv = Invoice(
            invoice_no=invoice_no, customer_name=f"Cust {invoice_no}",
            customer_code=f"C{invoice_no}", status="Not Started",
            routing=str(route_id), upload_date=delivery_date,
            route_id=route_id,
        )
        db.session.add(inv)
        db.session.flush()
    for it in items:
        db.session.add(InvoiceItem(
            invoice_no=invoice_no, item_code=it["code"],
            item_name=it.get("name", it["code"]), qty=it.get("qty", 5),
            zone=it.get("zone", "A1"),
            is_picked=it.get("is_picked", False),
            pick_status="picked" if it.get("is_picked") else "not_picked",
        ))
    db.session.commit()
    return inv


def _attach_route(db, route_id, invoice_nos, seq_no=1.0,
                  delivery_date="2026-05-03"):
    """Create a route stop + RouteStopInvoice rows and run attach
    via the production hook (services.attach_invoices_to_stop)."""
    from models import Shipment, RouteStop
    sh = Shipment.query.get(route_id)
    if sh is None:
        sh = Shipment(
            id=route_id, driver_name=f"Driver-{route_id}",
            delivery_date=datetime.strptime(delivery_date, "%Y-%m-%d").date(),
            status="PLANNED",
        )
        db.session.add(sh)
        db.session.flush()
    rs = RouteStop(shipment_id=route_id, seq_no=seq_no, stop_name="Stop")
    db.session.add(rs)
    db.session.commit()

    from services import attach_invoices_to_stop
    return attach_invoices_to_stop(rs.route_stop_id, invoice_nos)


@pytest.fixture
def setup(app):
    from app import db
    with app.app_context():
        _ensure_phase6_tables(db)
        yield app, db


def test_p1_1_extraction_disabled_when_flag_off(setup):
    app, db = setup
    with app.app_context():
        _make_setting(db, "summer_cooler_mode_enabled", "false")
        _make_dw_item(db, "ITEM-A", zone="SENSITIVE")
        _make_invoice(db, "INV-A", 100, [{"code": "ITEM-A", "qty": 3}])

        _attach_route(db, 100, ["INV-A"])

        rows = db.session.execute(text(
            "SELECT COUNT(*) FROM batch_pick_queue "
            "WHERE pick_zone_type = 'cooler'"
        )).scalar()
        assert rows == 0


def test_p1_2_basic_sensitive_extraction(setup):
    app, db = setup
    with app.app_context():
        from models import InvoiceItem, BatchPickingSession
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        _make_dw_item(db, "ITEM-S", zone="SENSITIVE")
        _make_dw_item(db, "ITEM-N", zone="DRY")
        _make_invoice(db, "INV-B", 101, [
            {"code": "ITEM-S", "qty": 4},
            {"code": "ITEM-N", "qty": 2},
        ])

        _attach_route(db, 101, ["INV-B"])

        cooler_rows = db.session.execute(text(
            "SELECT invoice_no, item_code, qty_required, status, "
            "       wms_zone, delivery_sequence "
            "FROM batch_pick_queue WHERE pick_zone_type = 'cooler'"
        )).fetchall()
        assert len(cooler_rows) == 1
        r = cooler_rows[0]
        assert r[0] == "INV-B"
        assert r[1] == "ITEM-S"
        assert float(r[2]) == 4.0
        assert r[3] == "pending"
        assert r[4] == "SENSITIVE"
        assert r[5] is None  # delivery_sequence NULL pre-lock

        # Item is locked
        sensitive_item = InvoiceItem.query.filter_by(
            invoice_no="INV-B", item_code="ITEM-S",
        ).first()
        assert sensitive_item.locked_by_batch_id is not None

        # Non-SENSITIVE item is NOT locked
        dry_item = InvoiceItem.query.filter_by(
            invoice_no="INV-B", item_code="ITEM-N",
        ).first()
        assert dry_item.locked_by_batch_id is None

        # Cooler session was created
        session = BatchPickingSession.query.filter_by(
            name="COOLER-ROUTE-101"
        ).first()
        assert session is not None
        assert session.session_type == "cooler_route"
        assert session.zones == "SENSITIVE"
        assert session.picking_mode == "Cooler"


def test_p1_3_idempotent_re_attach(setup):
    app, db = setup
    with app.app_context():
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        _make_dw_item(db, "ITEM-S", zone="SENSITIVE")
        _make_invoice(db, "INV-C", 102, [{"code": "ITEM-S", "qty": 2}])

        _attach_route(db, 102, ["INV-C"], seq_no=1.0)
        # Re-extract directly (simulates a second route attachment).
        from services.cooler_route_extraction import (
            extract_sensitive_for_route_stop_invoices,
        )
        from models import RouteStopInvoice
        rsis = RouteStopInvoice.query.filter_by(invoice_no="INV-C").all()
        extract_sensitive_for_route_stop_invoices(rsis)
        extract_sensitive_for_route_stop_invoices(rsis)

        n = db.session.execute(text(
            "SELECT COUNT(*) FROM batch_pick_queue "
            "WHERE pick_zone_type = 'cooler' AND invoice_no = 'INV-C'"
        )).scalar()
        assert n == 1


def test_p1_4_already_picked_logs_warning_no_queue(setup):
    app, db = setup
    with app.app_context():
        from models import ActivityLog, InvoiceItem
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        _make_dw_item(db, "ITEM-AP", zone="SENSITIVE")
        _make_invoice(db, "INV-AP", 103, [
            {"code": "ITEM-AP", "qty": 1, "is_picked": True},
        ])

        _attach_route(db, 103, ["INV-AP"])

        n = db.session.execute(text(
            "SELECT COUNT(*) FROM batch_pick_queue "
            "WHERE pick_zone_type = 'cooler' AND invoice_no = 'INV-AP'"
        )).scalar()
        assert n == 0  # No queue row for already-picked

        # ActivityLog warning was emitted
        warnings = ActivityLog.query.filter_by(
            activity_type="cooler.warning_already_picked"
        ).all()
        assert len(warnings) == 1
        assert warnings[0].invoice_no == "INV-AP"

        # DQ log entry recorded
        dq = db.session.execute(text(
            "SELECT issue_type FROM cooler_data_quality_log "
            "WHERE invoice_no = 'INV-AP'"
        )).fetchall()
        assert ("already_picked",) in [(r[0],) for r in dq]

        # Item NOT locked (regular flow already handled it)
        item = InvoiceItem.query.filter_by(
            invoice_no="INV-AP", item_code="ITEM-AP",
        ).first()
        assert item.locked_by_batch_id is None


def test_p1_5_missing_dimensions_still_locked(setup):
    app, db = setup
    with app.app_context():
        from models import InvoiceItem
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        # No dims
        _make_dw_item(db, "ITEM-MD", zone="SENSITIVE",
                      length=None, width=None, height=None)
        _make_invoice(db, "INV-MD", 104, [{"code": "ITEM-MD", "qty": 5}])

        _attach_route(db, 104, ["INV-MD"])

        n = db.session.execute(text(
            "SELECT COUNT(*) FROM batch_pick_queue "
            "WHERE pick_zone_type = 'cooler' AND invoice_no = 'INV-MD'"
        )).scalar()
        assert n == 1

        item = InvoiceItem.query.filter_by(
            invoice_no="INV-MD", item_code="ITEM-MD",
        ).first()
        assert item.locked_by_batch_id is not None

        dq = db.session.execute(text(
            "SELECT issue_type FROM cooler_data_quality_log "
            "WHERE invoice_no = 'INV-MD'"
        )).fetchall()
        assert ("missing_dimensions",) in [(r[0],) for r in dq]


def test_p1_6_non_sensitive_not_extracted(setup):
    app, db = setup
    with app.app_context():
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        _make_dw_item(db, "ITEM-DRY", zone="DRY")
        _make_dw_item(db, "ITEM-FRZ", zone="FROZEN")
        _make_invoice(db, "INV-NS", 105, [
            {"code": "ITEM-DRY", "qty": 1},
            {"code": "ITEM-FRZ", "qty": 1},
        ])

        _attach_route(db, 105, ["INV-NS"])

        n = db.session.execute(text(
            "SELECT COUNT(*) FROM batch_pick_queue "
            "WHERE pick_zone_type = 'cooler' AND invoice_no = 'INV-NS'"
        )).scalar()
        assert n == 0


def test_p1_7_invoice_moved_between_routes(setup):
    app, db = setup
    with app.app_context():
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        _make_dw_item(db, "ITEM-MV", zone="SENSITIVE")
        _make_invoice(db, "INV-MV", 106, [{"code": "ITEM-MV", "qty": 7}])

        _attach_route(db, 106, ["INV-MV"], seq_no=1.0)

        # Confirm it landed on session 106
        rows = db.session.execute(text(
            "SELECT s.name FROM batch_pick_queue bpq "
            "JOIN batch_picking_sessions s ON s.id = bpq.batch_session_id "
            "WHERE bpq.pick_zone_type = 'cooler' "
            "  AND bpq.invoice_no = 'INV-MV'"
        )).fetchall()
        assert ("COOLER-ROUTE-106",) in [(r[0],) for r in rows]

        # Move INV-MV to a different route by attaching to a new stop.
        _attach_route(db, 107, ["INV-MV"], seq_no=1.0)

        rows2 = db.session.execute(text(
            "SELECT s.name FROM batch_pick_queue bpq "
            "JOIN batch_picking_sessions s ON s.id = bpq.batch_session_id "
            "WHERE bpq.pick_zone_type = 'cooler' "
            "  AND bpq.invoice_no = 'INV-MV' "
            "  AND bpq.status = 'pending'"
        )).fetchall()
        names = [r[0] for r in rows2]
        # Pending row should now be on the new route's session.
        assert "COOLER-ROUTE-107" in names
        assert "COOLER-ROUTE-106" not in names
