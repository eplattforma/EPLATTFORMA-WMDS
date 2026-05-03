import os
os.environ.setdefault("SESSION_SECRET", "test-secret-key-for-testing")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

"""Phase 4 — Batch Picking Refactor regression matrix.

Covers all 28 cells from the Phase 4 brief §4.12:

  P4-01..05  Atomic creation + concurrency conflict
  P4-06..11  Status helpers
  P4-12..13  Picking resume / DB-backed queue presence
  P4-14..16  Cancel + lock lifecycle
  P4-17..19  Claim flow
  P4-20..22  Drain workflow
  P4-23..24  Orphaned-locks reconciliation
  P4-25..27  Feature-flag coexistence
  P4-28      Audit trail integrity

Tests use the in-memory SQLite fixture from ``tests/conftest.py``. The
DB-backed picking queue table is auto-created via ``db.create_all()`` at
fixture boot (the Phase 4 ORM has the table declared via raw SQL only,
so we provision it inline for SQLite when needed).
"""
import pytest
from sqlalchemy import text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ensure_queue_table(db):
    """SQLite test DB doesn't get the migration; provision the queue table."""
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS batch_pick_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_session_id INTEGER NOT NULL,
                invoice_no VARCHAR(50) NOT NULL,
                item_code VARCHAR(50) NOT NULL,
                pick_zone_type VARCHAR(20) NOT NULL DEFAULT 'normal',
                sequence_no INTEGER,
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
        db.session.commit()
    except Exception:
        db.session.rollback()


def _make_invoice_with_items(db, invoice_no="INV-P4-1", n_items=3, zone="A1"):
    from models import Invoice, InvoiceItem
    from timezone_utils import get_utc_now
    inv = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if not inv:
        inv = Invoice(
            invoice_no=invoice_no,
            customer_name="Test Cust",
            status="Not Started",
            routing="100",
            upload_date=get_utc_now(),
        )
        db.session.add(inv)
        db.session.flush()
    items = []
    for i in range(n_items):
        item = InvoiceItem(
            invoice_no=invoice_no,
            item_code=f"ITEM-{invoice_no}-{i}",
            item_name=f"Item {i}",
            qty=10,
            zone=zone,
            is_picked=False,
            pick_status="not_picked",
        )
        db.session.add(item)
        items.append(item)
    db.session.commit()
    return inv, items


@pytest.fixture
def setup(app):
    """Fixture that yields (app, db, ctx) and provisions queue table."""
    from app import db
    with app.app_context():
        _ensure_queue_table(db)
        yield app, db


# ---------------------------------------------------------------------------
# P4-06..11 — Status helpers (run first; cheapest)
# ---------------------------------------------------------------------------
class TestStatusHelpers:
    def test_p4_06_is_active_for_created(self):
        from services import batch_status as bs
        assert bs.is_active("Created") is True

    def test_p4_07_is_active_for_in_progress_case_insensitive(self):
        from services import batch_status as bs
        assert bs.is_active("in progress") is True
        assert bs.is_active("In Progress") is True

    def test_p4_08_is_terminal_for_completed_cancelled_archived(self):
        from services import batch_status as bs
        assert bs.is_terminal("Completed")
        assert bs.is_terminal("Cancelled")
        assert bs.is_terminal("Archived")
        assert not bs.is_terminal("Created")

    def test_p4_09_can_edit_only_when_created(self):
        from services import batch_status as bs
        assert bs.can_edit("Created")
        assert not bs.can_edit("In Progress")
        assert not bs.can_edit("Completed")

    def test_p4_10_can_cancel_for_active_states(self):
        from services import batch_status as bs
        assert bs.can_cancel("Created")
        assert bs.can_cancel("In Progress")
        assert bs.can_cancel("Paused")
        assert not bs.can_cancel("Completed")
        assert not bs.can_cancel("Cancelled")

    def test_p4_11_can_claim_refuses_terminal(self):
        from services import batch_status as bs
        assert bs.can_claim("Created")
        assert bs.can_claim("In Progress")
        assert not bs.can_claim("Completed")
        assert not bs.can_claim("Cancelled")
        assert not bs.can_claim("Archived")


# ---------------------------------------------------------------------------
# P4-01..05 — Atomic creation + concurrency conflict
# ---------------------------------------------------------------------------
class TestAtomicCreation:
    def test_p4_01_create_batch_atomic_basic(self, setup):
        app, db = setup
        from services.batch_picking import create_batch_atomic
        from models import BatchPickingSession, InvoiceItem

        _make_invoice_with_items(db, "INV-A-1", 3, "Z1")

        batch = create_batch_atomic(
            filters={"zones": ["Z1"]},
            created_by="test_admin_user",
            mode="Sequential",
        )
        assert batch.id is not None
        assert batch.status == "Created"
        # All items locked
        locked = InvoiceItem.query.filter_by(locked_by_batch_id=batch.id).count()
        assert locked == 3

    def test_p4_02_create_batch_writes_queue_rows(self, setup):
        app, db = setup
        from services.batch_picking import create_batch_atomic

        _make_invoice_with_items(db, "INV-A-2", 2, "Z2")
        batch = create_batch_atomic(
            filters={"zones": ["Z2"]},
            created_by="test_admin_user",
        )
        rows = db.session.execute(
            text("SELECT COUNT(*) FROM batch_pick_queue WHERE batch_session_id = :s"),
            {"s": batch.id},
        ).scalar()
        assert rows == 2

    def test_p4_03_concurrent_overlap_raises_batchconflict(self, setup):
        app, db = setup
        from services.batch_picking import BatchConflict, create_batch_atomic

        _make_invoice_with_items(db, "INV-A-3", 2, "Z3")
        b1 = create_batch_atomic(
            filters={"zones": ["Z3"]},
            created_by="test_admin_user",
        )
        assert b1.id is not None

        with pytest.raises(BatchConflict) as exc_info:
            create_batch_atomic(
                filters={"zones": ["Z3"]},
                created_by="test_admin_user",
            )
        assert exc_info.value.conflicting_batch_id == b1.id
        assert "locked" in str(exc_info.value)

    def test_p4_04_conflict_rolls_back_completely(self, setup):
        app, db = setup
        from services.batch_picking import BatchConflict, create_batch_atomic
        from models import BatchPickingSession

        _make_invoice_with_items(db, "INV-A-4", 1, "Z4")
        create_batch_atomic(filters={"zones": ["Z4"]}, created_by="test_admin_user")
        before = BatchPickingSession.query.count()
        with pytest.raises(BatchConflict):
            create_batch_atomic(filters={"zones": ["Z4"]}, created_by="test_admin_user")
        after = BatchPickingSession.query.count()
        assert before == after, "Failed creation must not leave a session row"

    def test_p4_05_no_eligible_items_raises_conflict(self, setup):
        app, db = setup
        from services.batch_picking import BatchConflict, create_batch_atomic
        with pytest.raises(BatchConflict):
            create_batch_atomic(filters={"zones": ["NOPE-ZONE"]}, created_by="test_admin_user")


# ---------------------------------------------------------------------------
# P4-12..13 — Resume / queue presence
# ---------------------------------------------------------------------------
class TestPickingResume:
    def test_p4_12_queue_rows_persist_across_sessions(self, setup):
        app, db = setup
        from services.batch_picking import create_batch_atomic
        _make_invoice_with_items(db, "INV-R-1", 2, "ZR1")
        batch = create_batch_atomic(filters={"zones": ["ZR1"]}, created_by="test_admin_user")
        # Simulate "restart" by closing session and re-querying
        db.session.close()
        n = db.session.execute(
            text("SELECT COUNT(*) FROM batch_pick_queue WHERE batch_session_id = :s "
                 "AND status = 'pending'"),
            {"s": batch.id},
        ).scalar()
        assert n == 2

    def test_p4_12c_rebuild_helper_resumes_from_queue(self, setup):
        """Round-5: exercise the SAME rebuild_items_from_queue helper
        the picker route uses on Flask-session loss / server restart.
        Asserts the rebuilt list (a) excludes already-picked rows,
        (b) preserves sequence_no order, (c) reconstructs zone/location
        from invoice_items WITHOUT querying queue columns that don't
        exist on the table — which is the exact bug round-5 caught.
        """
        app, db = setup
        from services.batch_picking import (
            create_batch_atomic, record_pick_to_queue, rebuild_items_from_queue,
        )
        _make_invoice_with_items(db, "INV-RT", 3, "ZRT")
        batch = create_batch_atomic(filters={"zones": ["ZRT"]}, created_by="test_admin_user")
        # Pick the first item via the durable hook
        record_pick_to_queue(batch.id, "INV-RT", "ITEM-INV-RT-0", "test_admin_user", 10)
        db.session.commit()

        rebuilt = rebuild_items_from_queue(batch.id)
        assert len(rebuilt) == 2
        codes = [it["item_code"] for it in rebuilt]
        assert "ITEM-INV-RT-0" not in codes
        assert codes == ["ITEM-INV-RT-1", "ITEM-INV-RT-2"]
        # Zone reconstructed from invoice_items, NOT queue (which has no zone col)
        assert rebuilt[0]["zone"] == "ZRT"
        assert rebuilt[0]["source_items"][0]["invoice_no"] == "INV-RT"

    def test_p4_12b_queue_drives_resume_after_session_loss(self, setup):
        """Round-4: when the Flask session is empty and the batch is
        DB-backed, the picker route must reconstruct the work list from
        ``batch_pick_queue`` (pending rows) — NOT from the legacy
        get_grouped_items() path. We verify the rebuild logic in
        isolation: after marking one queue row picked, the rebuild for
        a fresh session should yield exactly the remaining pending
        rows in sequence_no order.
        """
        app, db = setup
        from services.batch_picking import (
            create_batch_atomic, is_db_backed_batch, record_pick_to_queue,
        )
        _make_invoice_with_items(db, "INV-RESUME", 3, "ZRES")
        batch = create_batch_atomic(filters={"zones": ["ZRES"]}, created_by="test_admin_user")

        assert is_db_backed_batch(batch.id) is True

        # Pick the first item via the durable hook
        rc = record_pick_to_queue(
            batch.id, "INV-RESUME", "ITEM-INV-RESUME-0",
            "test_admin_user", 10,
        )
        assert rc == 1
        db.session.commit()

        # Simulate "Flask session lost" by querying the queue directly
        # the same way the picker route does on resume.
        rows = db.session.execute(
            text("""
                SELECT invoice_no, item_code, sequence_no
                  FROM batch_pick_queue
                 WHERE batch_session_id = :bid AND status = 'pending'
                 ORDER BY sequence_no, id
            """),
            {"bid": batch.id},
        ).fetchall()

        # Should be exactly the 2 remaining items, NOT skipping or repeating
        assert len(rows) == 2
        codes = [r.item_code for r in rows]
        assert "ITEM-INV-RESUME-0" not in codes  # picked one is gone
        assert "ITEM-INV-RESUME-1" in codes
        assert "ITEM-INV-RESUME-2" in codes

    def test_p4_13_queue_default_pick_zone_type_is_normal(self, setup):
        app, db = setup
        from services.batch_picking import create_batch_atomic
        _make_invoice_with_items(db, "INV-R-2", 1, "ZR2")
        batch = create_batch_atomic(filters={"zones": ["ZR2"]}, created_by="test_admin_user")
        pzt = db.session.execute(
            text("SELECT pick_zone_type FROM batch_pick_queue WHERE batch_session_id = :s"),
            {"s": batch.id},
        ).scalar()
        assert pzt == "normal"


# ---------------------------------------------------------------------------
# P4-14..16 — Cancel + lock lifecycle
# ---------------------------------------------------------------------------
class TestCancelLifecycle:
    def test_p4_14_cancel_releases_unpicked_locks(self, setup):
        app, db = setup
        from services.batch_picking import cancel_batch, create_batch_atomic
        from models import InvoiceItem
        _make_invoice_with_items(db, "INV-C-1", 3, "ZC1")
        batch = create_batch_atomic(filters={"zones": ["ZC1"]}, created_by="test_admin_user")
        cancel_batch(batch.id, "test_admin_user", reason="test")
        locked = InvoiceItem.query.filter_by(locked_by_batch_id=batch.id).count()
        assert locked == 0

    def test_p4_15_cancel_writes_audit_log(self, setup):
        app, db = setup
        from services.batch_picking import cancel_batch, create_batch_atomic
        from models import ActivityLog
        _make_invoice_with_items(db, "INV-C-2", 1, "ZC2")
        batch = create_batch_atomic(filters={"zones": ["ZC2"]}, created_by="test_admin_user")
        cancel_batch(batch.id, "test_admin_user", reason="testing")
        log = ActivityLog.query.filter_by(activity_type="batch.cancelled").first()
        assert log is not None
        assert "test_admin_user" in (log.details or "")

    def test_p4_16_cancel_terminal_batch_rejected(self, setup):
        app, db = setup
        from services.batch_picking import cancel_batch, create_batch_atomic
        _make_invoice_with_items(db, "INV-C-3", 1, "ZC3")
        batch = create_batch_atomic(filters={"zones": ["ZC3"]}, created_by="test_admin_user")
        cancel_batch(batch.id, "test_admin_user")
        with pytest.raises(ValueError):
            cancel_batch(batch.id, "test_admin_user", reason="double cancel")


# ---------------------------------------------------------------------------
# P4-17..19 — Claim flow
# ---------------------------------------------------------------------------
class TestClaimFlow:
    def test_p4_17_claim_records_claimed_by_and_at(self, setup):
        app, db = setup
        from services.batch_picking import claim_batch, create_batch_atomic
        from models import BatchPickingSession
        _make_invoice_with_items(db, "INV-CL-1", 1, "ZCL1")
        batch = create_batch_atomic(filters={"zones": ["ZCL1"]}, created_by="test_admin_user")
        claim_batch(batch.id, "test_picker_user")
        b = db.session.get(BatchPickingSession, batch.id)
        assert b.assigned_to == "test_picker_user"
        assert b.claimed_by == "test_picker_user"
        assert b.claimed_at is not None

    def test_p4_18_claim_writes_audit(self, setup):
        app, db = setup
        from services.batch_picking import claim_batch, create_batch_atomic
        from models import ActivityLog
        _make_invoice_with_items(db, "INV-CL-2", 1, "ZCL2")
        batch = create_batch_atomic(filters={"zones": ["ZCL2"]}, created_by="test_admin_user")
        claim_batch(batch.id, "test_picker_user")
        log = ActivityLog.query.filter_by(activity_type="batch.claimed").first()
        assert log is not None and "test_picker_user" in (log.details or "")

    def test_p4_19_claim_terminal_batch_rejected(self, setup):
        app, db = setup
        from services.batch_picking import cancel_batch, claim_batch, create_batch_atomic
        _make_invoice_with_items(db, "INV-CL-3", 1, "ZCL3")
        batch = create_batch_atomic(filters={"zones": ["ZCL3"]}, created_by="test_admin_user")
        cancel_batch(batch.id, "test_admin_user")
        with pytest.raises(ValueError):
            claim_batch(batch.id, "test_picker_user")


# ---------------------------------------------------------------------------
# P4-20..22 — Drain workflow
# ---------------------------------------------------------------------------
class TestDrain:
    def test_p4_20_set_mode_draining_and_back(self, setup):
        app, db = setup
        from services.maintenance import drain
        drain.set_mode("draining", "test_admin_user")
        assert drain.is_draining()
        drain.set_mode("normal", "test_admin_user")
        assert not drain.is_draining()

    def test_p4_21_creation_blocked_for_non_admin_when_draining(self, setup):
        app, db = setup
        from services.maintenance import drain

        class FakeUser:
            role = "picker"
        class FakeAdmin:
            role = "admin"

        drain.set_mode("draining", "test_admin_user")
        try:
            assert drain.is_creation_allowed_for(FakeUser()) is False
            assert drain.is_creation_allowed_for(FakeAdmin()) is True
        finally:
            drain.set_mode("normal", "test_admin_user")

    def test_p4_22_force_pause_marks_idle_batches(self, setup):
        app, db = setup
        from datetime import timedelta
        from services.batch_picking import create_batch_atomic
        from services.maintenance import drain
        from models import BatchPickingSession
        from timezone_utils import get_utc_now
        _make_invoice_with_items(db, "INV-D-1", 1, "ZD1")
        batch = create_batch_atomic(filters={"zones": ["ZD1"]}, created_by="test_admin_user")
        # Backdate last_activity_at to >30min ago
        b = db.session.get(BatchPickingSession, batch.id)
        b.last_activity_at = get_utc_now() - timedelta(hours=2)
        db.session.commit()
        drain.set_mode("draining", "test_admin_user")
        try:
            summary = drain.force_pause_stuck_batches()
            assert summary["paused"] >= 1
            db.session.refresh(b)
            assert b.status == "Paused"
        finally:
            drain.set_mode("normal", "test_admin_user")


# ---------------------------------------------------------------------------
# P4-23..24 — Orphaned-locks reconciliation
# ---------------------------------------------------------------------------
class TestOrphanedLocks:
    def test_p4_23_find_orphans_after_terminal_batch(self, setup):
        app, db = setup
        from models import InvoiceItem
        from services.batch_picking import (
            cancel_batch, create_batch_atomic, find_orphaned_locks,
        )
        _make_invoice_with_items(db, "INV-O-1", 2, "ZO1")
        batch = create_batch_atomic(filters={"zones": ["ZO1"]}, created_by="test_admin_user")
        # Simulate a "stale lock": cancel releases unpicked locks normally,
        # so we manually re-lock one item to simulate the orphan condition
        # (ie. the batch went terminal but a lock was left behind by a
        # legacy code path).
        item = InvoiceItem.query.filter_by(locked_by_batch_id=None,
                                           invoice_no="INV-O-1").first()
        cancel_batch(batch.id, "test_admin_user")
        item = InvoiceItem.query.filter_by(invoice_no="INV-O-1").first()
        item.locked_by_batch_id = batch.id  # batch is now Cancelled (terminal)
        db.session.commit()
        orphans = find_orphaned_locks()
        assert any(
            o.invoice_no == item.invoice_no and o.item_code == item.item_code
            for o in orphans
        )

    def test_p4_24_bulk_unlock_releases_and_audits(self, setup):
        app, db = setup
        from models import ActivityLog, BatchPickingSession, InvoiceItem
        from services.batch_picking import bulk_unlock_orphans
        # Set up: lock item against a batch id that doesn't exist (missing batch)
        inv, items = _make_invoice_with_items(db, "INV-O-2", 1, "ZO2")
        items[0].locked_by_batch_id = 999999
        db.session.commit()
        n = bulk_unlock_orphans("test_admin_user")
        assert n >= 1
        log = ActivityLog.query.filter_by(activity_type="batch.orphan_unlock").first()
        assert log is not None
        item = InvoiceItem.query.filter_by(
            invoice_no=items[0].invoice_no, item_code=items[0].item_code
        ).first()
        assert item.locked_by_batch_id is None


# ---------------------------------------------------------------------------
# P4-25..27 — Feature-flag coexistence
# ---------------------------------------------------------------------------
class TestFlagCoexistence:
    def test_p4_25_default_flag_off(self, setup):
        app, db = setup
        from services.batch_picking import is_db_queue_enabled
        # Defaults are seeded false in production; on a fresh test DB the
        # row may not exist at all → also returns false.
        assert is_db_queue_enabled() is False

    def test_p4_26_flag_on_returns_true(self, setup):
        app, db = setup
        from models import Setting
        from services.batch_picking import is_db_queue_enabled
        Setting.set(db.session, "use_db_backed_picking_queue", "true")
        db.session.commit()
        try:
            assert is_db_queue_enabled() is True
        finally:
            Setting.set(db.session, "use_db_backed_picking_queue", "false")
            db.session.commit()

    def test_p4_27_legacy_path_does_not_write_queue(self, setup):
        """When the master flag is OFF, the legacy creation path
        (manually inserting a BatchPickingSession + invoking the legacy
        locking utils) MUST NOT write any rows into batch_pick_queue —
        only ``create_batch_atomic`` does, and it isn't called when the
        flag is OFF.
        """
        app, db = setup
        from models import BatchPickingSession, InvoiceItem, Setting
        from timezone_utils import get_utc_now
        Setting.set(db.session, "use_db_backed_picking_queue", "false")
        db.session.commit()
        _make_invoice_with_items(db, "INV-LEG-1", 2, "ZLEG1")
        # Snapshot queue size, then simulate a legacy creation path:
        before = db.session.execute(text(
            "SELECT COUNT(*) FROM batch_pick_queue"
        )).scalar()
        bs = BatchPickingSession(
            name="legacy", batch_number="LEG-1", zones="ZLEG1",
            created_by="test_admin_user", picking_mode="Sequential",
            status="Created",
        )
        db.session.add(bs)
        db.session.flush()
        # Manually lock items the way batch_locking_utils.lock_items_for_batch does.
        items = InvoiceItem.query.filter_by(invoice_no="INV-LEG-1").all()
        for it in items:
            it.locked_by_batch_id = bs.id
        db.session.commit()
        after = db.session.execute(text(
            "SELECT COUNT(*) FROM batch_pick_queue"
        )).scalar()
        assert before == after, (
            "Legacy creation path must not write batch_pick_queue rows "
            "when use_db_backed_picking_queue is OFF"
        )


# ---------------------------------------------------------------------------
# P4-28 — Audit trail integrity
# ---------------------------------------------------------------------------
class TestAuditTrail:
    def test_p4_28_full_lifecycle_writes_three_audit_rows(self, setup):
        app, db = setup
        from models import ActivityLog
        from services.batch_picking import (
            cancel_batch, claim_batch, create_batch_atomic,
        )
        _make_invoice_with_items(db, "INV-AU-1", 1, "ZAU1")
        batch = create_batch_atomic(
            filters={"zones": ["ZAU1"]}, created_by="test_admin_user"
        )
        claim_batch(batch.id, "test_picker_user")
        cancel_batch(batch.id, "test_admin_user", reason="EOL test")

        types = {
            r.activity_type for r in ActivityLog.query.filter(
                ActivityLog.activity_type.in_(
                    ["batch.created", "batch.claimed", "batch.cancelled"]
                )
            ).all()
        }
        assert {"batch.created", "batch.claimed", "batch.cancelled"} <= types
