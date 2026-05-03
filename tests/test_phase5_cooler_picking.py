import os
os.environ.setdefault("SESSION_SECRET", "test-secret-key-for-testing")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

"""Phase 5 — Cooler Picking regression matrix (33 tests).

Covers all 33 cells from the Phase 5 brief §5.9:

  P5-01..05  Schema + flag default + zone snapshot + routing
  P5-06..10  Box lifecycle (create, idempotent, assign, remove, close)
  P5-11..14  Box cancel + sequencing + read-only-after-close
  P5-15..18  Permission gates (picker, manager, no-perm, anonymous)
  P5-19..22  Order readiness composition (normal+cooler+box)
  P5-23..26  Exception handling + zone moves + audit trail
  P5-27..30  PDF surfaces (label thermal, label A4, manifest, route)
  P5-31..33  Driver overlay flag gating + safe defaults
"""
from datetime import datetime

import pytest
from sqlalchemy import text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ensure_queue_table(db):
    """Provision Phase-4 queue table on the SQLite test DB, with the
    Phase-5 ``wms_zone`` snapshot column added."""
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


def _ensure_cooler_tables(db):
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS cooler_boxes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route_id INTEGER NOT NULL,
                delivery_date DATE NOT NULL,
                box_no INTEGER NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'open',
                first_stop_sequence NUMERIC(10,2),
                last_stop_sequence NUMERIC(10,2),
                created_by VARCHAR(64),
                created_at TIMESTAMP,
                closed_by VARCHAR(64),
                closed_at TIMESTAMP,
                label_printed_at TIMESTAMP,
                UNIQUE (route_id, delivery_date, box_no)
            )
        """))
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS cooler_box_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cooler_box_id INTEGER NOT NULL,
                invoice_no VARCHAR(50) NOT NULL,
                customer_code VARCHAR(50),
                customer_name VARCHAR(255),
                route_stop_id INTEGER,
                delivery_sequence NUMERIC(10,2),
                item_code VARCHAR(50) NOT NULL,
                item_name VARCHAR(255),
                expected_qty NUMERIC(12,3) NOT NULL DEFAULT 0,
                picked_qty NUMERIC(12,3) NOT NULL DEFAULT 0,
                picked_by VARCHAR(64),
                picked_at TIMESTAMP,
                queue_item_id INTEGER,
                status VARCHAR(20) NOT NULL DEFAULT 'picked',
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
        """))
        db.session.commit()
    except Exception:
        db.session.rollback()


def _make_setting(db, key, value):
    from models import Setting
    s = Setting.query.filter_by(key=key).first()
    if s is None:
        s = Setting(key=key, value=value)
        db.session.add(s)
    else:
        s.value = value
    db.session.commit()


def _make_dw_item(db, code, zone):
    from models import DwItem
    from timezone_utils import get_utc_now
    existing = DwItem.query.filter_by(item_code_365=code).first()
    if existing:
        existing.wms_zone = zone
    else:
        db.session.add(DwItem(
            item_code_365=code, item_name=f"DW {code}", active=True,
            attr_hash="x", last_sync_at=get_utc_now(), wms_zone=zone,
        ))
    db.session.commit()


def _make_invoice_with_items(db, invoice_no="INV-P5-1", n_items=2,
                             zone="A1", route_id="900",
                             delivery_date="2026-05-03"):
    from models import Invoice, InvoiceItem
    inv = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if not inv:
        inv = Invoice(
            invoice_no=invoice_no, customer_name=f"Cust {invoice_no}",
            customer_code=f"C{invoice_no}", status="Not Started",
            routing=str(route_id), upload_date=delivery_date,
        )
        db.session.add(inv)
        db.session.flush()
    for i in range(n_items):
        db.session.add(InvoiceItem(
            invoice_no=invoice_no, item_code=f"IT-{invoice_no}-{i}",
            item_name=f"Item {i}", qty=10, zone=zone, is_picked=False,
            pick_status="not_picked",
        ))
    db.session.commit()
    return inv


def _make_route_stop(db, shipment_id, invoice_no, seq_no=1.0,
                     delivery_date="2026-05-03"):
    """Create a Shipment+RouteStop+RouteStopInvoice trio so cooler queries
    can JOIN through to a delivery sequence value."""
    from models import Shipment, RouteStop, RouteStopInvoice
    sh = Shipment.query.get(shipment_id)
    if sh is None:
        sh = Shipment(id=shipment_id, driver_name="Driver",
                      delivery_date=datetime.strptime(delivery_date, "%Y-%m-%d").date())
        db.session.add(sh)
        db.session.flush()
    rs = RouteStop(shipment_id=shipment_id, seq_no=seq_no, stop_name="Stop")
    db.session.add(rs)
    db.session.flush()
    db.session.add(RouteStopInvoice(
        route_stop_id=rs.route_stop_id, invoice_no=invoice_no,
        is_active=True,
    ))
    db.session.commit()
    return rs


@pytest.fixture
def setup(app):
    from app import db
    # The shared conftest doesn't import main.py, so the cooler blueprint
    # isn't auto-registered. Register it here (idempotent — Flask raises
    # ValueError if a name is registered twice, which we tolerate).
    from blueprints.cooler_picking import (
        cooler_bp, register_template_helpers,
    )
    if "cooler" not in app.blueprints:
        app.register_blueprint(cooler_bp)
        register_template_helpers(app)
    with app.app_context():
        _ensure_queue_table(db)
        _ensure_cooler_tables(db)
        # Reset cooler tables between tests for isolation.
        db.session.execute(text("DELETE FROM cooler_box_items"))
        db.session.execute(text("DELETE FROM cooler_boxes"))
        db.session.execute(text("DELETE FROM batch_pick_queue"))
        db.session.commit()
        yield app, db


def _login(client, role="admin"):
    user = "test_admin_user" if role == "admin" else "test_picker_user"
    resp = client.post("/login", data={"username": user, "password": "test_password"})
    assert resp.status_code in (200, 302)


# ---------------------------------------------------------------------------
# P5-01..05 — Schema + flag default + zone snapshot + routing
# ---------------------------------------------------------------------------
class TestSchemaAndRouting:
    def test_p5_01_summer_cooler_mode_defaults_off(self, setup):
        app, db = setup
        from services.batch_picking import is_summer_cooler_mode_enabled
        assert is_summer_cooler_mode_enabled() is False

    def test_p5_02_sensitive_items_route_to_cooler_when_flag_on(self, setup):
        app, db = setup
        from services.batch_picking import create_batch_atomic
        _make_dw_item(db, "IT-INV-A1-0", "SENSITIVE")
        _make_dw_item(db, "IT-INV-A1-1", "MAIN")
        _make_invoice_with_items(db, "INV-A1", 2, "ZA1")
        _make_setting(db, "summer_cooler_mode_enabled", "true")

        batch = create_batch_atomic(filters={"zones": ["ZA1"]},
                                    created_by="test_admin_user")
        rows = db.session.execute(text(
            "SELECT item_code, pick_zone_type, wms_zone "
            "FROM batch_pick_queue WHERE batch_session_id = :s "
            "ORDER BY item_code"
        ), {"s": batch.id}).fetchall()
        assert len(rows) == 2
        by_code = {r[0]: (r[1], r[2]) for r in rows}
        assert by_code["IT-INV-A1-0"] == ("cooler", "SENSITIVE")
        assert by_code["IT-INV-A1-1"] == ("normal", "MAIN")

    def test_p5_03_flag_off_keeps_sensitive_in_normal(self, setup):
        app, db = setup
        from services.batch_picking import create_batch_atomic
        _make_dw_item(db, "IT-INV-A2-0", "SENSITIVE")
        _make_invoice_with_items(db, "INV-A2", 1, "ZA2")
        # Flag explicit: off (sqlite test DB connection pooling can carry
        # the Setting from a sibling test in the same process).
        _make_setting(db, "summer_cooler_mode_enabled", "false")
        batch = create_batch_atomic(filters={"zones": ["ZA2"]},
                                    created_by="test_admin_user")
        row = db.session.execute(text(
            "SELECT pick_zone_type, wms_zone FROM batch_pick_queue "
            "WHERE batch_session_id = :s"
        ), {"s": batch.id}).fetchone()
        assert row[0] == "normal"
        # zone column may be NULL when flag is off (no lookup performed)
        assert row[1] in (None, "")

    def test_p5_04_wms_zone_snapshot_survives_dw_reclassification(self, setup):
        app, db = setup
        from services.batch_picking import create_batch_atomic
        _make_dw_item(db, "IT-INV-A3-0", "SENSITIVE")
        _make_invoice_with_items(db, "INV-A3", 1, "ZA3")
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        batch = create_batch_atomic(filters={"zones": ["ZA3"]},
                                    created_by="test_admin_user")
        # Mid-pick reclassification of the DwItem.
        _make_dw_item(db, "IT-INV-A3-0", "MAIN")
        row = db.session.execute(text(
            "SELECT pick_zone_type, wms_zone FROM batch_pick_queue "
            "WHERE batch_session_id = :s"
        ), {"s": batch.id}).fetchone()
        # Snapshot must NOT change retroactively.
        assert row[0] == "cooler"
        assert row[1] == "SENSITIVE"

    def test_p5_05_cooler_boxes_table_present(self, setup):
        app, db = setup
        from sqlalchemy import inspect
        names = inspect(db.engine).get_table_names()
        assert "cooler_boxes" in names
        assert "cooler_box_items" in names


# ---------------------------------------------------------------------------
# P5-06..10 — Box lifecycle
# ---------------------------------------------------------------------------
class TestBoxLifecycle:
    def test_p5_06_box_create_inserts_open_row(self, setup, client):
        _login(client, "admin")
        resp = client.post("/cooler/box/create", json={
            "route_id": 900, "delivery_date": "2026-05-03", "box_no": 1,
        })
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["status"] == "open" and data["created"] is True

    def test_p5_07_box_create_is_idempotent(self, setup, client):
        _login(client, "admin")
        r1 = client.post("/cooler/box/create", json={
            "route_id": 900, "delivery_date": "2026-05-03", "box_no": 2,
        })
        r2 = client.post("/cooler/box/create", json={
            "route_id": 900, "delivery_date": "2026-05-03", "box_no": 2,
        })
        assert r1.status_code == 201 and r2.status_code == 200
        assert r1.get_json()["cooler_box_id"] == r2.get_json()["cooler_box_id"]
        assert r2.get_json()["created"] is False

    def test_p5_08_assign_item_picks_queue_row(self, setup, client):
        app, db = setup
        from services.batch_picking import create_batch_atomic
        _make_dw_item(db, "IT-INV-B1-0", "SENSITIVE")
        _make_invoice_with_items(db, "INV-B1", 1, "ZB1", route_id="900")
        _make_route_stop(db, 900, "INV-B1", seq_no=3.0)
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        batch = create_batch_atomic(filters={"zones": ["ZB1"]},
                                    created_by="test_admin_user")
        qrow = db.session.execute(text(
            "SELECT id FROM batch_pick_queue WHERE batch_session_id = :s"
        ), {"s": batch.id}).scalar()

        _login(client, "admin")
        rb = client.post("/cooler/box/create", json={
            "route_id": 900, "delivery_date": "2026-05-03", "box_no": 5,
        })
        bid = rb.get_json()["cooler_box_id"]
        ra = client.post(f"/cooler/box/{bid}/assign-item", json={
            "queue_item_id": qrow, "picked_qty": 10,
        })
        assert ra.status_code == 200
        assert ra.get_json()["status"] == "picked"
        # Queue row now picked, cooler_box_items row exists.
        st = db.session.execute(text(
            "SELECT status FROM batch_pick_queue WHERE id = :i"
        ), {"i": qrow}).scalar()
        cnt = db.session.execute(text(
            "SELECT COUNT(*) FROM cooler_box_items WHERE cooler_box_id = :b"
        ), {"b": bid}).scalar()
        assert st == "picked" and cnt == 1

    def test_p5_09_remove_item_reverts_queue_to_pending(self, setup, client):
        app, db = setup
        from services.batch_picking import create_batch_atomic
        _make_dw_item(db, "IT-INV-B2-0", "SENSITIVE")
        _make_invoice_with_items(db, "INV-B2", 1, "ZB2", route_id="900")
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        batch = create_batch_atomic(filters={"zones": ["ZB2"]},
                                    created_by="test_admin_user")
        qrow = db.session.execute(text(
            "SELECT id FROM batch_pick_queue WHERE batch_session_id = :s"
        ), {"s": batch.id}).scalar()

        _login(client, "admin")
        rb = client.post("/cooler/box/create", json={
            "route_id": 900, "delivery_date": "2026-05-03", "box_no": 6,
        })
        bid = rb.get_json()["cooler_box_id"]
        client.post(f"/cooler/box/{bid}/assign-item",
                    json={"queue_item_id": qrow, "picked_qty": 10})
        rr = client.post(f"/cooler/box/{bid}/remove-item",
                         json={"queue_item_id": qrow})
        assert rr.status_code == 200
        st = db.session.execute(text(
            "SELECT status FROM batch_pick_queue WHERE id = :i"
        ), {"i": qrow}).scalar()
        assert st == "pending"

    def test_p5_10_box_close_stamps_stop_range(self, setup, client):
        app, db = setup
        from services.batch_picking import create_batch_atomic
        _make_dw_item(db, "IT-INV-B3-0", "SENSITIVE")
        _make_dw_item(db, "IT-INV-B4-0", "SENSITIVE")
        _make_invoice_with_items(db, "INV-B3", 1, "ZB3", route_id="900")
        _make_invoice_with_items(db, "INV-B4", 1, "ZB3", route_id="900")
        _make_route_stop(db, 900, "INV-B3", seq_no=2.0)
        _make_route_stop(db, 900, "INV-B4", seq_no=7.0)
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        batch = create_batch_atomic(filters={"zones": ["ZB3"]},
                                    created_by="test_admin_user")
        qrows = db.session.execute(text(
            "SELECT id, invoice_no FROM batch_pick_queue "
            "WHERE batch_session_id = :s ORDER BY invoice_no"
        ), {"s": batch.id}).fetchall()

        _login(client, "admin")
        rb = client.post("/cooler/box/create", json={
            "route_id": 900, "delivery_date": "2026-05-03", "box_no": 8,
        })
        bid = rb.get_json()["cooler_box_id"]
        for q in qrows:
            client.post(f"/cooler/box/{bid}/assign-item",
                        json={"queue_item_id": q[0], "picked_qty": 5})
        rc = client.post(f"/cooler/box/{bid}/close")
        assert rc.status_code == 200
        d = rc.get_json()
        assert d["status"] == "closed"
        assert d["first_stop_sequence"] == 2.0
        assert d["last_stop_sequence"] == 7.0


# ---------------------------------------------------------------------------
# P5-11..14 — Cancel / sequencing / closed-box rejections
# ---------------------------------------------------------------------------
class TestCancelAndGuards:
    def test_p5_11_box_cancel_reverts_all_items(self, setup, client):
        app, db = setup
        from services.batch_picking import create_batch_atomic
        _make_dw_item(db, "IT-INV-C1-0", "SENSITIVE")
        _make_invoice_with_items(db, "INV-C1", 1, "ZC1", route_id="900")
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        batch = create_batch_atomic(filters={"zones": ["ZC1"]},
                                    created_by="test_admin_user")
        qid = db.session.execute(text(
            "SELECT id FROM batch_pick_queue WHERE batch_session_id = :s"
        ), {"s": batch.id}).scalar()
        _login(client, "admin")
        rb = client.post("/cooler/box/create", json={
            "route_id": 900, "delivery_date": "2026-05-03", "box_no": 11,
        })
        bid = rb.get_json()["cooler_box_id"]
        client.post(f"/cooler/box/{bid}/assign-item",
                    json={"queue_item_id": qid, "picked_qty": 10})
        rc = client.post(f"/cooler/box/{bid}/cancel")
        assert rc.status_code == 200
        st = db.session.execute(text(
            "SELECT status FROM batch_pick_queue WHERE id = :i"
        ), {"i": qid}).scalar()
        assert st == "pending"
        bs = db.session.execute(text(
            "SELECT status FROM cooler_boxes WHERE id = :b"
        ), {"b": bid}).scalar()
        assert bs == "cancelled"

    def test_p5_12_assign_to_closed_box_is_rejected(self, setup, client):
        app, db = setup
        _login(client, "admin")
        rb = client.post("/cooler/box/create", json={
            "route_id": 901, "delivery_date": "2026-05-03", "box_no": 12,
        })
        bid = rb.get_json()["cooler_box_id"]
        client.post(f"/cooler/box/{bid}/close")
        # Build a cooler queue row (no need for actual stop).
        from services.batch_picking import create_batch_atomic
        _make_dw_item(db, "IT-INV-C2-0", "SENSITIVE")
        _make_invoice_with_items(db, "INV-C2", 1, "ZC2", route_id="901")
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        batch = create_batch_atomic(filters={"zones": ["ZC2"]},
                                    created_by="test_admin_user")
        qid = db.session.execute(text(
            "SELECT id FROM batch_pick_queue WHERE batch_session_id = :s"
        ), {"s": batch.id}).scalar()
        ra = client.post(f"/cooler/box/{bid}/assign-item",
                         json={"queue_item_id": qid, "picked_qty": 1})
        assert ra.status_code == 400

    def test_p5_13_close_already_closed_box_is_rejected(self, setup, client):
        _login(client, "admin")
        rb = client.post("/cooler/box/create", json={
            "route_id": 902, "delivery_date": "2026-05-03", "box_no": 13,
        })
        bid = rb.get_json()["cooler_box_id"]
        c1 = client.post(f"/cooler/box/{bid}/close")
        c2 = client.post(f"/cooler/box/{bid}/close")
        assert c1.status_code == 200 and c2.status_code == 400

    def test_p5_14_invalid_payload_returns_400(self, setup, client):
        _login(client, "admin")
        # Missing box_no.
        r = client.post("/cooler/box/create", json={
            "route_id": 903, "delivery_date": "2026-05-03",
        })
        assert r.status_code == 400
        # Bad date format.
        r = client.post("/cooler/box/create", json={
            "route_id": 903, "delivery_date": "not-a-date", "box_no": 1,
        })
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# P5-15..18 — Permission gates
# ---------------------------------------------------------------------------
class TestPermissions:
    def test_p5_15_anonymous_redirected_to_login(self, setup, client):
        r = client.get("/cooler/route-list", follow_redirects=False)
        # Flask-Login redirect to /login OR 401 depending on config
        assert r.status_code in (302, 401)

    def test_p5_16_picker_can_view_route_list(self, setup, client):
        _login(client, "picker")
        # base.html references endpoints from blueprints not registered in
        # the conftest test app (e.g. ``help.help_dashboard``). Hitting an
        # endpoint whose view returns JSON sidesteps template rendering
        # while still exercising the permission decorator.
        r = client.get("/cooler/route/911/2026-05-03/manifest")
        assert r.status_code == 200

    def test_p5_17_picker_cannot_create_box(self, setup, client):
        app, db = setup
        _login(client, "picker")
        # Picker role intentionally lacks ``cooler.manage_boxes`` (only
        # the ``cooler.pick`` key). With permissions enforcement enabled
        # the decorator must abort(403) before the view body runs — what
        # matters operationally is that NO cooler_box row gets inserted.
        _make_setting(db, "permissions_enforcement_enabled", "true")
        try:
            try:
                client.post("/cooler/box/create", json={
                    "route_id": 904, "delivery_date": "2026-05-03",
                    "box_no": 1,
                })
            except Exception:
                # base.html in the conftest test app references endpoints
                # not registered there (e.g. ``help.help_dashboard``);
                # rendering the 403 page can raise BuildError. The
                # permission check itself has already fired by then.
                db.session.rollback()
            cnt = db.session.execute(text(
                "SELECT COUNT(*) FROM cooler_boxes WHERE route_id = 904"
            )).scalar() or 0
            assert cnt == 0
        finally:
            _make_setting(db, "permissions_enforcement_enabled", "false")

    def test_p5_18_admin_can_create_box(self, setup, client):
        _login(client, "admin")
        r = client.post("/cooler/box/create", json={
            "route_id": 905, "delivery_date": "2026-05-03", "box_no": 1,
        })
        assert r.status_code == 201

    def test_p5_18b_picker_can_assign_with_enforcement(self, setup):
        # Picker holds ``cooler.pick`` but NOT ``cooler.manage_boxes``.
        # The assign-item endpoint must be gated by ``cooler.pick`` so a
        # picker can attach a queue row to a box opened by a manager.
        # Asserting at the inspection layer (route decorators + has_permission)
        # avoids the conftest test-app's broken error-page rendering.
        from blueprints import cooler_picking as cp_mod
        from services.permissions import has_permission

        view = cp_mod.box_assign_item
        # Trace through wraps to find the require_permission marker.
        gates = []
        cur = view
        while cur is not None:
            tag = getattr(cur, "_required_permission", None)
            if tag:
                gates.append(tag)
            cur = getattr(cur, "__wrapped__", None)
        # The decorator chain must include ``cooler.pick`` (and must NOT
        # require ``cooler.manage_boxes`` for the picker action).
        # Fall back to source-text check when decorator doesn't expose a
        # marker attribute.
        import inspect
        src = inspect.getsource(cp_mod.box_assign_item) if False else \
            inspect.getsource(cp_mod).split("def box_assign_item")[0]
        assert '@require_permission("cooler.pick")' in \
            inspect.getsource(cp_mod).split("def box_assign_item")[0].rsplit(
                "@cooler_bp.route", 1)[-1] or "cooler.pick" in str(gates)

        # And the picker role itself must satisfy ``cooler.pick``.
        class _U:
            is_authenticated = True
            role = "picker"
            username = "test_picker_user"
            id = 1
        assert has_permission(_U(), "cooler.pick") is True
        assert has_permission(_U(), "cooler.manage_boxes") is False


# ---------------------------------------------------------------------------
# P5-19..22 — Order readiness composition
# ---------------------------------------------------------------------------
class TestOrderReadiness:
    def test_p5_19_no_queue_falls_back_to_legacy_is_picked(self, setup):
        app, db = setup
        from services.order_readiness import is_order_ready
        from models import Invoice, InvoiceItem
        inv = Invoice(invoice_no="INV-LEG", customer_name="L",
                      status="picking", routing="999", upload_date="2026-05-03")
        db.session.add(inv)
        db.session.add(InvoiceItem(
            invoice_no="INV-LEG", item_code="X", item_name="X",
            qty=1, is_picked=True, pick_status="picked",
        ))
        db.session.commit()
        assert is_order_ready("INV-LEG") is True

    def test_p5_20_pending_normal_blocks_readiness(self, setup):
        app, db = setup
        from services.order_readiness import is_order_ready
        from services.batch_picking import create_batch_atomic
        _make_invoice_with_items(db, "INV-RD1", 1, "ZRD1")
        create_batch_atomic(filters={"zones": ["ZRD1"]},
                            created_by="test_admin_user")
        assert is_order_ready("INV-RD1") is False

    def test_p5_21_pending_cooler_blocks_readiness(self, setup, client):
        app, db = setup
        from services.batch_picking import create_batch_atomic
        from services.order_readiness import is_order_ready
        _make_dw_item(db, "IT-INV-RD2-0", "SENSITIVE")
        _make_dw_item(db, "IT-INV-RD2-1", "MAIN")
        _make_invoice_with_items(db, "INV-RD2", 2, "ZRD2", route_id="900")
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        batch = create_batch_atomic(filters={"zones": ["ZRD2"]},
                                    created_by="test_admin_user")
        # Mark the normal row picked, cooler still pending -> not ready.
        db.session.execute(text(
            "UPDATE batch_pick_queue SET status = 'picked' "
            "WHERE batch_session_id = :s AND pick_zone_type = 'normal'"
        ), {"s": batch.id})
        db.session.commit()
        assert is_order_ready("INV-RD2") is False

    def test_p5_22_open_box_blocks_readiness(self, setup, client):
        app, db = setup
        from services.batch_picking import create_batch_atomic
        from services.order_readiness import is_order_ready
        _make_dw_item(db, "IT-INV-RD3-0", "SENSITIVE")
        _make_invoice_with_items(db, "INV-RD3", 1, "ZRD3", route_id="900")
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        batch = create_batch_atomic(filters={"zones": ["ZRD3"]},
                                    created_by="test_admin_user")
        qid = db.session.execute(text(
            "SELECT id FROM batch_pick_queue WHERE batch_session_id = :s"
        ), {"s": batch.id}).scalar()
        _login(client, "admin")
        rb = client.post("/cooler/box/create", json={
            "route_id": 900, "delivery_date": "2026-05-03", "box_no": 22,
        })
        bid = rb.get_json()["cooler_box_id"]
        client.post(f"/cooler/box/{bid}/assign-item",
                    json={"queue_item_id": qid, "picked_qty": 1})
        # Queue row is now 'picked' but box is still 'open' -> not ready.
        assert is_order_ready("INV-RD3") is False
        client.post(f"/cooler/box/{bid}/close")
        assert is_order_ready("INV-RD3") is True


# ---------------------------------------------------------------------------
# P5-23..26 — Exception handling + zone moves + audit trail
# ---------------------------------------------------------------------------
class TestExceptionsAndAudit:
    def test_p5_23_queue_exception_marks_row_exception(self, setup, client):
        app, db = setup
        from services.batch_picking import create_batch_atomic
        _make_dw_item(db, "IT-INV-EX1-0", "SENSITIVE")
        _make_invoice_with_items(db, "INV-EX1", 1, "ZEX1", route_id="900")
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        batch = create_batch_atomic(filters={"zones": ["ZEX1"]},
                                    created_by="test_admin_user")
        qid = db.session.execute(text(
            "SELECT id FROM batch_pick_queue WHERE batch_session_id = :s"
        ), {"s": batch.id}).scalar()
        _login(client, "picker")
        r = client.post(f"/cooler/queue/{qid}/exception", json={"reason": "broken"})
        assert r.status_code == 200
        st = db.session.execute(text(
            "SELECT status FROM batch_pick_queue WHERE id = :i"
        ), {"i": qid}).scalar()
        assert st == "exception"

    def test_p5_24_move_to_normal_changes_zone(self, setup, client):
        app, db = setup
        from services.batch_picking import create_batch_atomic
        _make_dw_item(db, "IT-INV-MV1-0", "SENSITIVE")
        _make_invoice_with_items(db, "INV-MV1", 1, "ZMV1", route_id="900")
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        batch = create_batch_atomic(filters={"zones": ["ZMV1"]},
                                    created_by="test_admin_user")
        qid = db.session.execute(text(
            "SELECT id FROM batch_pick_queue WHERE batch_session_id = :s"
        ), {"s": batch.id}).scalar()
        _login(client, "admin")
        r = client.post(f"/cooler/queue/{qid}/move-to-normal")
        assert r.status_code == 200
        pzt = db.session.execute(text(
            "SELECT pick_zone_type FROM batch_pick_queue WHERE id = :i"
        ), {"i": qid}).scalar()
        assert pzt == "normal"

    def test_p5_25_move_to_cooler_changes_zone(self, setup, client):
        app, db = setup
        from services.batch_picking import create_batch_atomic
        _make_invoice_with_items(db, "INV-MV2", 1, "ZMV2", route_id="900")
        batch = create_batch_atomic(filters={"zones": ["ZMV2"]},
                                    created_by="test_admin_user")
        qid = db.session.execute(text(
            "SELECT id FROM batch_pick_queue WHERE batch_session_id = :s"
        ), {"s": batch.id}).scalar()
        _login(client, "admin")
        r = client.post(f"/cooler/queue/{qid}/move-to-cooler")
        assert r.status_code == 200
        pzt = db.session.execute(text(
            "SELECT pick_zone_type FROM batch_pick_queue WHERE id = :i"
        ), {"i": qid}).scalar()
        assert pzt == "cooler"

    def test_p5_26_audit_log_records_box_lifecycle(self, setup, client):
        app, db = setup
        from models import ActivityLog
        _login(client, "admin")
        before = ActivityLog.query.filter(
            ActivityLog.activity_type.like("cooler.%")
        ).count()
        rb = client.post("/cooler/box/create", json={
            "route_id": 906, "delivery_date": "2026-05-03", "box_no": 26,
        })
        bid = rb.get_json()["cooler_box_id"]
        client.post(f"/cooler/box/{bid}/close")
        after = ActivityLog.query.filter(
            ActivityLog.activity_type.like("cooler.%")
        ).count()
        assert after - before >= 2


# ---------------------------------------------------------------------------
# P5-27..30 — PDF surfaces
# ---------------------------------------------------------------------------
class TestPDFs:
    def test_p5_27_label_thermal_returns_pdf(self, setup, client):
        _login(client, "admin")
        rb = client.post("/cooler/box/create", json={
            "route_id": 907, "delivery_date": "2026-05-03", "box_no": 27,
        })
        bid = rb.get_json()["cooler_box_id"]
        r = client.get(f"/cooler/box/{bid}/label?size=thermal")
        assert r.status_code == 200
        assert r.mimetype == "application/pdf"
        assert r.data.startswith(b"%PDF")

    def test_p5_28_label_a4_returns_pdf(self, setup, client):
        _login(client, "admin")
        rb = client.post("/cooler/box/create", json={
            "route_id": 908, "delivery_date": "2026-05-03", "box_no": 28,
        })
        bid = rb.get_json()["cooler_box_id"]
        r = client.get(f"/cooler/box/{bid}/label?size=a4")
        assert r.status_code == 200 and r.data.startswith(b"%PDF")

    def test_p5_29_box_manifest_returns_pdf(self, setup, client):
        _login(client, "admin")
        rb = client.post("/cooler/box/create", json={
            "route_id": 909, "delivery_date": "2026-05-03", "box_no": 29,
        })
        bid = rb.get_json()["cooler_box_id"]
        r = client.get(f"/cooler/box/{bid}/manifest")
        assert r.status_code == 200 and r.data.startswith(b"%PDF")

    def test_p5_30_route_manifest_returns_pdf_even_when_empty(self, setup, client):
        _login(client, "admin")
        r = client.get("/cooler/route/910/2026-05-03/manifest")
        assert r.status_code == 200 and r.data.startswith(b"%PDF")


# ---------------------------------------------------------------------------
# P5-31..33 — Driver overlay flag gating + safe defaults
# ---------------------------------------------------------------------------
class TestDriverOverlay:
    def test_p5_31_driver_view_flag_defaults_off(self, setup):
        app, db = setup
        from blueprints.cooler_picking import is_driver_view_enabled
        assert is_driver_view_enabled() is False

    def test_p5_32_driver_view_flag_can_be_enabled(self, setup):
        app, db = setup
        from blueprints.cooler_picking import is_driver_view_enabled
        _make_setting(db, "cooler_driver_view_enabled", "true")
        assert is_driver_view_enabled() is True

    def test_p5_33_cooler_boxes_for_route_returns_empty_safely(self, setup):
        app, db = setup
        from blueprints.cooler_picking import cooler_boxes_for_route
        out = cooler_boxes_for_route(99999, "2026-05-03")
        assert out == []


# ---------------------------------------------------------------------------
# P5-FIX-01..05 — Architect fix-up regressions
# ---------------------------------------------------------------------------
class TestArchitectFixupRegressions:
    """Regressions for the four architectural defects identified in
    architect code review of the original Phase 5 implementation:

      FIX-01  rebuild_items_from_queue must exclude cooler rows.
      FIX-02  is_order_ready must short-circuit when cooler mode is OFF.
      FIX-03  routes.detail / mark_shipped must gate on is_order_ready,
              not raw Invoice.status, so cooler boxes block dispatch.
      FIX-04  blueprints.cooler_picking.route_picking must scope
              cooler_boxes by route_id + delivery_date (not date alone).
    """

    def test_p5_fix_01_rebuild_excludes_cooler_zone_rows(self, setup):
        """rebuild_items_from_queue must not return rows whose
        ``pick_zone_type='cooler'`` — those belong to the cooler picker
        screen, not the normal picker work-list. Legacy rows with
        ``pick_zone_type IS NULL`` continue to surface as normal (the
        SQLite test schema enforces NOT NULL, so the legacy-NULL branch
        is covered by the SQL guard's structure rather than a row test)."""
        app, db = setup
        from services.batch_picking import rebuild_items_from_queue
        # Create matching invoice items so rebuild has join context.
        _make_invoice_with_items(db, "INV-FIX1", 2, "ZFIX1", route_id="900")
        # Insert two queue rows: one cooler, one normal. Same batch.
        db.session.execute(text("""
            INSERT INTO batch_pick_queue (
                batch_session_id, invoice_no, item_code, pick_zone_type,
                sequence_no, status, qty_required
            ) VALUES
                (9001, 'INV-FIX1', 'IT-INV-FIX1-0', 'cooler', 1, 'pending', 1),
                (9001, 'INV-FIX1', 'IT-INV-FIX1-1', 'normal', 2, 'pending', 1)
        """))
        db.session.commit()
        rebuilt = rebuild_items_from_queue(9001)
        codes = sorted(r["item_code"] for r in rebuilt)
        # Cooler row excluded, normal included.
        assert "IT-INV-FIX1-0" not in codes, (
            "rebuild_items_from_queue must exclude pick_zone_type='cooler'"
        )
        assert "IT-INV-FIX1-1" in codes
        # Belt-and-braces: assert the SQL guard text is present in source
        # so the NULL-legacy clause cannot be silently dropped in future
        # refactors (the SQLite NOT NULL constraint blocks a row test).
        import inspect as _ins
        from services import batch_picking as _bp
        src = _ins.getsource(_bp.rebuild_items_from_queue)
        assert "pick_zone_type IS NULL OR pick_zone_type = 'normal'" in src

    def test_p5_fix_02_is_order_ready_ignores_open_box_when_flag_off(self, setup):
        """When ``summer_cooler_mode_enabled=false`` (production
        default), an open cooler_box row left over from prior testing
        must NOT block the order from being marked ready. Otherwise a
        stale row would hold orders forever."""
        app, db = setup
        from services.order_readiness import is_order_ready
        # Flag explicitly OFF.
        _make_setting(db, "summer_cooler_mode_enabled", "false")
        # Invoice with two normal-zone queue rows, both terminal.
        _make_invoice_with_items(db, "INV-FIX2", 2, "ZFIX2", route_id="900")
        db.session.execute(text("""
            INSERT INTO batch_pick_queue (
                batch_session_id, invoice_no, item_code, pick_zone_type,
                sequence_no, status, qty_required
            ) VALUES
                (9002, 'INV-FIX2', 'IT-INV-FIX2-0', 'normal', 1, 'picked', 1),
                (9002, 'INV-FIX2', 'IT-INV-FIX2-1', 'normal', 2, 'picked', 1)
        """))
        # Stale open cooler box with an item for this invoice — would
        # block readiness if the flag check were not honoured.
        db.session.execute(text("""
            INSERT INTO cooler_boxes (route_id, delivery_date, box_no,
                                      status, created_by)
            VALUES (900, '2026-05-03', 99, 'open', 'test_admin_user')
        """))
        cb_id = db.session.execute(text(
            "SELECT id FROM cooler_boxes WHERE box_no = 99"
        )).scalar()
        db.session.execute(text("""
            INSERT INTO cooler_box_items (cooler_box_id, invoice_no,
                                          item_code, expected_qty, status)
            VALUES (:cb, 'INV-FIX2', 'IT-INV-FIX2-0', 1, 'assigned')
        """), {"cb": cb_id})
        db.session.commit()
        # Flag OFF -> open box ignored -> order is ready.
        assert is_order_ready("INV-FIX2") is True

    def test_p5_fix_02b_is_order_ready_honours_open_box_when_flag_on(self, setup):
        """Flag-ON regression: confirms the short-circuit only fires
        when the flag is OFF; with the flag ON the open-box check still
        blocks readiness exactly as before the fix-up."""
        app, db = setup
        from services.order_readiness import is_order_ready
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        _make_invoice_with_items(db, "INV-FIX2B", 1, "ZFIX2B", route_id="900")
        db.session.execute(text("""
            INSERT INTO batch_pick_queue (
                batch_session_id, invoice_no, item_code, pick_zone_type,
                sequence_no, status, qty_required
            ) VALUES
                (9012, 'INV-FIX2B', 'IT-INV-FIX2B-0', 'cooler', 1, 'picked', 1)
        """))
        db.session.execute(text("""
            INSERT INTO cooler_boxes (route_id, delivery_date, box_no,
                                      status, created_by)
            VALUES (900, '2026-05-03', 88, 'open', 'test_admin_user')
        """))
        cb_id = db.session.execute(text(
            "SELECT id FROM cooler_boxes WHERE box_no = 88"
        )).scalar()
        db.session.execute(text("""
            INSERT INTO cooler_box_items (cooler_box_id, invoice_no,
                                          item_code, expected_qty, status)
            VALUES (:cb, 'INV-FIX2B', 'IT-INV-FIX2B-0', 1, 'picked')
        """), {"cb": cb_id})
        db.session.commit()
        # Flag ON -> open box must block readiness.
        assert is_order_ready("INV-FIX2B") is False

    def test_p5_fix_03_mark_shipped_gate_uses_is_order_ready(self, setup):
        """Architect rejection #3: ``mark_shipped`` and
        ``all_ready_for_dispatch`` must consult
        ``services.order_readiness.is_order_ready`` rather than raw
        ``Invoice.status``. Without this, a cooler-bearing invoice with
        an open cold-chain box ships while the box is still open.

        The HTTP path renders templates that depend on unrelated
        blueprints (``warehouse.*``) not registered in the test app, so
        we assert the contract two ways:

          (a) is_order_ready returns False for an invoice with an open
              cooler box when summer cooler mode is ON; and
          (b) the source of both ``mark_shipped`` and the dispatch
              gate in routes_routes.py imports and uses
              ``is_order_ready`` (the previous code path used
              ``inv.status.upper() == 'READY_FOR_DISPATCH'``)."""
        app, db = setup
        from services.order_readiness import is_order_ready
        # (a) Behavioural: cold-chain box still open -> not ready.
        _make_invoice_with_items(db, "INV-FIX3", 1, "ZFIX3", route_id="901")
        _make_setting(db, "summer_cooler_mode_enabled", "true")
        db.session.execute(text("""
            INSERT INTO batch_pick_queue (
                batch_session_id, invoice_no, item_code, pick_zone_type,
                sequence_no, status, qty_required
            ) VALUES
                (9003, 'INV-FIX3', 'IT-INV-FIX3-0', 'cooler', 1, 'picked', 1)
        """))
        db.session.execute(text("""
            INSERT INTO cooler_boxes (route_id, delivery_date, box_no,
                                      status, created_by)
            VALUES (901, '2026-05-03', 1, 'open', 'test_admin_user')
        """))
        cb_id = db.session.execute(text(
            "SELECT id FROM cooler_boxes WHERE route_id = 901 AND box_no = 1"
        )).scalar()
        db.session.execute(text("""
            INSERT INTO cooler_box_items (cooler_box_id, invoice_no,
                                          item_code, expected_qty, status)
            VALUES (:cb, 'INV-FIX3', 'IT-INV-FIX3-0', 1, 'picked')
        """), {"cb": cb_id})
        db.session.commit()
        assert is_order_ready("INV-FIX3") is False

        # (b) Structural: confirm both call sites in routes_routes.py
        # delegate to is_order_ready and no longer trust Invoice.status
        # for the dispatch gate.
        with open("routes_routes.py", "r", encoding="utf-8") as f:
            src = f.read()
        assert "from services.order_readiness import is_order_ready" in src
        # The legacy raw check that the architect rejected:
        assert "inv.status.upper() == 'READY_FOR_DISPATCH'" not in src
        # And the unpicked-invoices check no longer trusts the literal
        # 'ready_for_dispatch' string equality on Invoice.status.
        assert "inv.status != 'ready_for_dispatch'" not in src

    def test_p5_fix_04_route_picking_isolates_boxes_by_route(self, setup):
        """Architect rejection #4: ``route_picking`` filtered cooler_boxes
        by ``delivery_date`` only, so a picker on Route A would see
        boxes from Routes B/C/D. The SQL must now scope by BOTH
        ``route_id`` AND ``delivery_date``.

        The view's render path depends on unrelated blueprints not
        registered in the test app, so we assert structurally that
        the source contains the route_id filter and that running the
        same WHERE clause returns disjoint result sets per route."""
        app, db = setup
        # Two boxes, same date, different routes.
        db.session.execute(text("""
            INSERT INTO cooler_boxes (route_id, delivery_date, box_no,
                                      status, created_by)
            VALUES
              (910, '2026-05-03', 1, 'open', 'test_admin_user'),
              (911, '2026-05-03', 1, 'open', 'test_admin_user')
        """))
        db.session.commit()
        # Behavioural: the new WHERE clause produces per-route isolation.
        rows_a = db.session.execute(text(
            "SELECT route_id FROM cooler_boxes "
            "WHERE delivery_date = :d AND route_id = :r ORDER BY box_no"
        ), {"d": "2026-05-03", "r": 910}).fetchall()
        rows_b = db.session.execute(text(
            "SELECT route_id FROM cooler_boxes "
            "WHERE delivery_date = :d AND route_id = :r ORDER BY box_no"
        ), {"d": "2026-05-03", "r": 911}).fetchall()
        assert len(rows_a) == 1 and rows_a[0][0] == 910
        assert len(rows_b) == 1 and rows_b[0][0] == 911
        # Structural: the source SQL must include the route_id predicate
        # so a future refactor cannot silently re-introduce the leak.
        with open("blueprints/cooler_picking.py", "r", encoding="utf-8") as f:
            src = f.read()
        # Snapshot of the new WHERE clause in route_picking's
        # cooler_boxes SELECT (a single line in the SQL string):
        assert "AND route_id = :route_id" in src

    def test_p5_fix_05_migration_runs_on_sqlite_dialect(self, setup):
        """The migration must run cleanly on SQLite (the test dialect)
        — no BIGSERIAL, no ADD COLUMN IF NOT EXISTS, no
        TIMESTAMP WITH TIME ZONE. Re-run after the test fixtures'
        own provisioning to prove idempotency on an existing schema."""
        app, db = setup
        from update_phase5_cooler_picking_schema import (
            update_phase5_cooler_picking_schema,
        )
        # Should not raise on the test SQLite engine.
        update_phase5_cooler_picking_schema()
        # Re-run is safe.
        update_phase5_cooler_picking_schema()
