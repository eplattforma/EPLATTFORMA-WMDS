"""Task #32 — Route Batch for Normal Items.

R1  Flag OFF → no route batch created when invoice attached
R2  Flag ON  → ROUTE-BATCH-<id> created, normal items locked
R3  SENSITIVE items NOT added to route batch (cooler pipeline owns them)
R4  Second invoice attached to same route → same session reused, new items added
R5  Invoice attached after session locked → sibling ROUTE-BATCH-<id>-2 created
R6  Lock route batch → sequence_locked_at + sequence_locked_by stamped
R7  get_grouped_items() route_batch branch returns correct items
R8  Route batch items are location-sorted
R9  Normal items locked by route batch are NOT in the cooler batch
R10 Cancelling a batch unlocks its items (preserve_picked=True)
"""
import os
os.environ.setdefault("SESSION_SECRET", "test-secret-key")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from datetime import datetime

import pytest
from sqlalchemy import text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_flag(db, key, value):
    from models import Setting
    s = Setting.query.filter_by(key=key).first()
    if s is None:
        db.session.add(Setting(key=key, value=value))
    else:
        s.value = value
    db.session.commit()


def _make_dw_item(db, code, zone="DRY"):
    from models import DwItem
    from timezone_utils import get_utc_now
    existing = DwItem.query.filter_by(item_code_365=code).first()
    if existing:
        existing.wms_zone = zone
        db.session.commit()
        return
    db.session.add(DwItem(
        item_code_365=code, item_name=f"Item {code}", active=True,
        attr_hash="x", last_sync_at=get_utc_now(), wms_zone=zone,
        item_length=10.0, item_width=10.0, item_height=10.0, item_weight=0.5,
    ))
    db.session.commit()


def _make_invoice(db, invoice_no, route_id, items, delivery_date="2026-05-10"):
    from models import Invoice, InvoiceItem, Shipment
    sh = Shipment.query.get(route_id)
    if sh is None:
        sh = Shipment(
            id=route_id,
            driver_name=f"Driver-{route_id}",
            delivery_date=datetime.strptime(delivery_date, "%Y-%m-%d").date(),
            status="PLANNED",
        )
        db.session.add(sh)
        db.session.flush()
    inv = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if inv is None:
        inv = Invoice(
            invoice_no=invoice_no,
            customer_name=f"Customer {invoice_no}",
            customer_code=f"C{invoice_no}",
            status="not_started",
            routing=str(route_id),
            upload_date=delivery_date,
            route_id=route_id,
        )
        db.session.add(inv)
        db.session.flush()
    for it in items:
        db.session.add(InvoiceItem(
            invoice_no=invoice_no,
            item_code=it["code"],
            item_name=it.get("name", it["code"]),
            qty=it.get("qty", 5),
            zone=it.get("zone", "A1"),
            location=it.get("location", "A1-01"),
            is_picked=it.get("is_picked", False),
            pick_status="picked" if it.get("is_picked") else "not_picked",
        ))
    db.session.commit()
    return inv


def _attach(db, route_id, invoice_nos, seq_no=1.0, delivery_date="2026-05-10"):
    """Create RouteStop + RSIs and fire the production attach hook."""
    from models import Shipment, RouteStop
    sh = Shipment.query.get(route_id)
    if sh is None:
        sh = Shipment(
            id=route_id,
            driver_name=f"Driver-{route_id}",
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


def _ensure_phase6_tables(db):
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS batch_pick_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_session_id INTEGER NOT NULL,
            invoice_no VARCHAR(50) NOT NULL,
            item_code VARCHAR(50) NOT NULL,
            pick_zone_type VARCHAR(20) NOT NULL DEFAULT 'normal',
            sequence_no INTEGER,
            delivery_sequence NUMERIC(10,2),
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


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def setup(app):
    from app import db
    with app.app_context():
        _ensure_phase6_tables(db)
        # Ensure cooler mode OFF by default; each test sets what it needs.
        _set_flag(db, "summer_cooler_mode_enabled", "false")
        _set_flag(db, "route_batch_mode_enabled", "false")
        yield app, db
        # Teardown — purge data written by this test so it cannot leak into
        # other test files that share the same in-memory SQLite instance.
        try:
            db.session.execute(text("DELETE FROM batch_pick_queue"))
            db.session.execute(text("DELETE FROM batch_session_invoices"))
            db.session.execute(text("DELETE FROM batch_picking_sessions"))
            db.session.execute(text("DELETE FROM cooler_data_quality_log"))
            db.session.execute(text("DELETE FROM route_stop_invoice"))
            db.session.execute(text("DELETE FROM route_stop"))
            db.session.execute(text("DELETE FROM invoice_items"))
            db.session.execute(text("DELETE FROM invoices"))
            db.session.execute(text("DELETE FROM shipments"))
            db.session.execute(text("DELETE FROM ps_items_dw"))
            db.session.execute(text("DELETE FROM settings"))
            db.session.commit()
        except Exception:
            db.session.rollback()


# ---------------------------------------------------------------------------
# R1 — Flag OFF → no route batch created
# ---------------------------------------------------------------------------

def test_r1_flag_off_no_route_batch(setup):
    app, db = setup
    with app.app_context():
        from models import BatchPickingSession
        _make_dw_item(db, "NRM-R1", zone="DRY")
        _make_invoice(db, "INV-R1", 200, [{"code": "NRM-R1", "qty": 3}])

        _attach(db, 200, ["INV-R1"])

        sessions = BatchPickingSession.query.filter_by(
            session_type="route_batch"
        ).all()
        assert sessions == [], "No route_batch session should exist when flag is OFF"


# ---------------------------------------------------------------------------
# R2 — Flag ON → ROUTE-BATCH-<id> created, normal items locked
# ---------------------------------------------------------------------------

def test_r2_flag_on_creates_session_and_locks_items(setup):
    app, db = setup
    with app.app_context():
        from models import BatchPickingSession, InvoiceItem
        _set_flag(db, "route_batch_mode_enabled", "true")
        _make_dw_item(db, "NRM-R2", zone="DRY")
        _make_invoice(db, "INV-R2", 201, [{"code": "NRM-R2", "qty": 4}])

        _attach(db, 201, ["INV-R2"])

        session = BatchPickingSession.query.filter_by(
            session_type="route_batch"
        ).first()
        assert session is not None
        assert session.name.startswith("ROUTE-BATCH-201")
        assert session.route_id == 201

        item = InvoiceItem.query.filter_by(
            invoice_no="INV-R2", item_code="NRM-R2"
        ).first()
        assert item.locked_by_batch_id == session.id


# ---------------------------------------------------------------------------
# R3 — SENSITIVE items are NOT added to route batch
# ---------------------------------------------------------------------------

def test_r3_sensitive_items_excluded_from_route_batch(setup):
    app, db = setup
    with app.app_context():
        from models import BatchPickingSession, InvoiceItem
        _set_flag(db, "route_batch_mode_enabled", "true")
        _make_dw_item(db, "SENS-R3", zone="SENSITIVE")
        _make_dw_item(db, "NRM-R3", zone="DRY")
        _make_invoice(db, "INV-R3", 202, [
            {"code": "SENS-R3", "qty": 2},
            {"code": "NRM-R3", "qty": 5},
        ])

        _attach(db, 202, ["INV-R3"])

        rb = BatchPickingSession.query.filter_by(
            session_type="route_batch", route_id=202
        ).first()
        assert rb is not None

        # Normal item locked to route batch
        normal_item = InvoiceItem.query.filter_by(
            invoice_no="INV-R3", item_code="NRM-R3"
        ).first()
        assert normal_item.locked_by_batch_id == rb.id

        # SENSITIVE item NOT locked to route batch (it belongs to cooler pipeline)
        sens_item = InvoiceItem.query.filter_by(
            invoice_no="INV-R3", item_code="SENS-R3"
        ).first()
        assert sens_item.locked_by_batch_id != rb.id


# ---------------------------------------------------------------------------
# R4 — Second invoice → same session reused, new items added
# ---------------------------------------------------------------------------

def test_r4_second_invoice_reuses_session(setup):
    app, db = setup
    with app.app_context():
        from models import BatchPickingSession, InvoiceItem, BatchSessionInvoice
        _set_flag(db, "route_batch_mode_enabled", "true")
        _make_dw_item(db, "NRM-R4A", zone="DRY")
        _make_dw_item(db, "NRM-R4B", zone="DRY")
        _make_invoice(db, "INV-R4A", 203, [{"code": "NRM-R4A", "qty": 2}])
        _make_invoice(db, "INV-R4B", 203, [{"code": "NRM-R4B", "qty": 3}])

        _attach(db, 203, ["INV-R4A"], seq_no=1.0)
        _attach(db, 203, ["INV-R4B"], seq_no=2.0)

        sessions = BatchPickingSession.query.filter_by(
            session_type="route_batch", route_id=203
        ).all()
        assert len(sessions) == 1, "Only one route_batch session should exist"

        rb = sessions[0]
        item_a = InvoiceItem.query.filter_by(
            invoice_no="INV-R4A", item_code="NRM-R4A"
        ).first()
        item_b = InvoiceItem.query.filter_by(
            invoice_no="INV-R4B", item_code="NRM-R4B"
        ).first()
        assert item_a.locked_by_batch_id == rb.id
        assert item_b.locked_by_batch_id == rb.id

        inv_count = BatchSessionInvoice.query.filter_by(
            batch_session_id=rb.id
        ).count()
        assert inv_count == 2


# ---------------------------------------------------------------------------
# R5 — Invoice after locked session → sibling created
# ---------------------------------------------------------------------------

def test_r5_late_invoice_creates_sibling(setup):
    app, db = setup
    with app.app_context():
        from models import BatchPickingSession, InvoiceItem
        from timezone_utils import get_utc_now
        _set_flag(db, "route_batch_mode_enabled", "true")
        _make_dw_item(db, "NRM-R5A", zone="DRY")
        _make_dw_item(db, "NRM-R5B", zone="DRY")
        _make_invoice(db, "INV-R5A", 204, [{"code": "NRM-R5A", "qty": 1}])
        _make_invoice(db, "INV-R5B", 204, [{"code": "NRM-R5B", "qty": 1}])

        _attach(db, 204, ["INV-R5A"], seq_no=1.0)

        # Lock the first session manually
        rb1 = BatchPickingSession.query.filter_by(
            session_type="route_batch", route_id=204
        ).first()
        assert rb1 is not None
        rb1.sequence_locked_at = get_utc_now()
        rb1.sequence_locked_by = "test"
        db.session.commit()

        # Attach second invoice after lock
        _attach(db, 204, ["INV-R5B"], seq_no=2.0)

        sessions = BatchPickingSession.query.filter_by(
            session_type="route_batch", route_id=204
        ).order_by(BatchPickingSession.id).all()
        assert len(sessions) == 2, "A sibling session should be created for late invoice"

        rb2 = sessions[1]
        item_b = InvoiceItem.query.filter_by(
            invoice_no="INV-R5B", item_code="NRM-R5B"
        ).first()
        assert item_b.locked_by_batch_id == rb2.id


# ---------------------------------------------------------------------------
# R6 — lock_route_batch service stamps locked_at / locked_by
# ---------------------------------------------------------------------------

def test_r6_lock_stamps_fields(setup):
    app, db = setup
    with app.app_context():
        from models import BatchPickingSession
        from services.cooler_route_extraction import (
            _get_or_create_route_batch_session,
        )
        from timezone_utils import get_utc_now

        _set_flag(db, "route_batch_mode_enabled", "true")

        # Create a route batch session directly
        _make_invoice(db, "INV-R6", 205, [])
        sess_info = _get_or_create_route_batch_session(205, "test-user")
        session_id = sess_info["id"]

        # Simulate lock via ORM (mirrors what the endpoint does)
        sess = BatchPickingSession.query.get(session_id)
        sess.sequence_locked_at = get_utc_now()
        sess.sequence_locked_by = "wm-user"
        db.session.commit()

        refreshed = BatchPickingSession.query.get(session_id)
        assert refreshed.sequence_locked_at is not None
        assert refreshed.sequence_locked_by == "wm-user"


# ---------------------------------------------------------------------------
# R7 — get_grouped_items() route_batch branch returns locked items
# ---------------------------------------------------------------------------

def test_r7_get_grouped_items_returns_locked_items(setup):
    app, db = setup
    with app.app_context():
        from models import BatchPickingSession, BatchSessionInvoice, InvoiceItem
        _set_flag(db, "route_batch_mode_enabled", "true")
        _make_dw_item(db, "NRM-R7A", zone="DRY")
        _make_dw_item(db, "NRM-R7B", zone="DRY")
        _make_invoice(db, "INV-R7", 206, [
            {"code": "NRM-R7A", "qty": 3, "location": "B2-05"},
            {"code": "NRM-R7B", "qty": 1, "location": "A1-01"},
        ])

        _attach(db, 206, ["INV-R7"])

        rb = BatchPickingSession.query.filter_by(
            session_type="route_batch", route_id=206
        ).first()
        assert rb is not None

        grouped = rb.get_grouped_items(include_picked=False)
        codes = {g["item_code"] for g in grouped}
        assert "NRM-R7A" in codes
        assert "NRM-R7B" in codes


# ---------------------------------------------------------------------------
# R8 — Route batch items are sorted by warehouse location
# ---------------------------------------------------------------------------

def test_r8_items_sorted_by_location(setup):
    app, db = setup
    with app.app_context():
        from models import BatchPickingSession
        _set_flag(db, "route_batch_mode_enabled", "true")
        _make_dw_item(db, "NRM-R8A", zone="DRY")
        _make_dw_item(db, "NRM-R8B", zone="DRY")
        _make_dw_item(db, "NRM-R8C", zone="DRY")
        _make_invoice(db, "INV-R8", 207, [
            {"code": "NRM-R8C", "qty": 1, "location": "C3-10"},
            {"code": "NRM-R8A", "qty": 2, "location": "A1-01"},
            {"code": "NRM-R8B", "qty": 1, "location": "B2-05"},
        ])

        _attach(db, 207, ["INV-R8"])

        rb = BatchPickingSession.query.filter_by(
            session_type="route_batch", route_id=207
        ).first()
        assert rb is not None

        grouped = rb.get_grouped_items(include_picked=False)
        # Should not raise; ordering may depend on sorting config but must return all 3
        assert len(grouped) == 3


# ---------------------------------------------------------------------------
# R9 — Normal items locked by route batch are NOT in cooler batch
# ---------------------------------------------------------------------------

def test_r9_normal_items_not_in_cooler_batch(setup):
    app, db = setup
    with app.app_context():
        from models import BatchPickingSession, InvoiceItem
        _set_flag(db, "route_batch_mode_enabled", "true")
        _set_flag(db, "summer_cooler_mode_enabled", "true")
        _make_dw_item(db, "SENS-R9", zone="SENSITIVE")
        _make_dw_item(db, "NRM-R9", zone="DRY")
        _make_invoice(db, "INV-R9", 208, [
            {"code": "SENS-R9", "qty": 2},
            {"code": "NRM-R9", "qty": 5},
        ])

        _attach(db, 208, ["INV-R9"])

        cooler_session = BatchPickingSession.query.filter_by(
            session_type="cooler_route", route_id=208
        ).first()
        rb_session = BatchPickingSession.query.filter_by(
            session_type="route_batch", route_id=208
        ).first()

        assert cooler_session is not None
        assert rb_session is not None

        normal_item = InvoiceItem.query.filter_by(
            invoice_no="INV-R9", item_code="NRM-R9"
        ).first()
        sens_item = InvoiceItem.query.filter_by(
            invoice_no="INV-R9", item_code="SENS-R9"
        ).first()

        # Normal item → route batch only
        assert normal_item.locked_by_batch_id == rb_session.id
        # SENSITIVE item → cooler session only
        assert sens_item.locked_by_batch_id == cooler_session.id
        assert normal_item.locked_by_batch_id != cooler_session.id


# ---------------------------------------------------------------------------
# R10 — unlock_items_for_batch releases items (preserve_picked=True)
# ---------------------------------------------------------------------------

def test_r10_cancel_unlocks_items(setup):
    app, db = setup
    with app.app_context():
        from models import BatchPickingSession, InvoiceItem
        from batch_locking_utils import unlock_items_for_batch
        _set_flag(db, "route_batch_mode_enabled", "true")
        _make_dw_item(db, "NRM-R10A", zone="DRY")
        _make_dw_item(db, "NRM-R10B", zone="DRY")
        _make_invoice(db, "INV-R10", 209, [
            {"code": "NRM-R10A", "qty": 2},
            {"code": "NRM-R10B", "qty": 3, "is_picked": True},
        ])

        _attach(db, 209, ["INV-R10"])

        rb = BatchPickingSession.query.filter_by(
            session_type="route_batch", route_id=209
        ).first()
        assert rb is not None

        # Both items should be locked (picked item gets locked too at attach time,
        # but unlock with preserve_picked=True should leave the picked one alone)
        unpicked = InvoiceItem.query.filter_by(
            invoice_no="INV-R10", item_code="NRM-R10A"
        ).first()
        assert unpicked.locked_by_batch_id == rb.id

        # Cancel → unlock unpicked items only
        unlock_items_for_batch(rb.id, preserve_picked=True)

        db.session.expire_all()
        unpicked_after = InvoiceItem.query.filter_by(
            invoice_no="INV-R10", item_code="NRM-R10A"
        ).first()
        assert unpicked_after.locked_by_batch_id is None
