"""Phase 7: tests for the deferred ("Send to Batch") batch picking flow.

Covers T1–T15 from the Task #31 brief.
"""
import os
import sys

# IMPORTANT: must override DATABASE_URL BEFORE importing app (the live env
# points at Postgres; force a clean SQLite per test).
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SESSION_SECRET", "test-secret")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from sqlalchemy import text

from app import app, db
# Importing ``routes`` registers the @app.route handlers we exercise here
# (the picker/send-to-batch endpoint). We deliberately do NOT import
# ``main`` because main runs Postgres-only schema migrations on import.
import routes  # noqa: F401

# The picker_dashboard template references ``url_for('help.help_dashboard')``,
# so the help blueprint has to be registered for the rendered HTML pages.
# Tests only register the @app.route handlers from ``routes`` plus the few
# blueprints they exercise directly. ``picker_dashboard.html`` and
# ``base.html`` use ``url_for(...)`` across many other blueprints, so
# install a build-error handler that returns a stub URL for unknown
# endpoints — this lets the template render without us having to bring up
# the entire app's blueprint surface (which transitively triggers
# Postgres-only schema migrations).
def _missing_endpoint_stub(error, endpoint, values):
    return "/__missing__/" + endpoint
app.url_build_error_handlers.append(_missing_endpoint_stub)

# Register the ``has_permission`` Jinja helper used by picker_dashboard.html.
try:
    from services.permissions import register_template_helpers
    register_template_helpers(app)
except Exception:
    pass
from models import (
    BatchPickingSession,
    BatchSessionInvoice,
    Invoice,
    InvoiceItem,
    User,
)
from services.deferred_batch_service import (
    DEFERRED_PICK_STATUS,
    DEFERRED_SESSION_TYPE,
    DeferredBatchError,
    get_or_create_deferred_session,
    list_open_deferred_batches,
    send_item_to_batch,
)


def _ensure_queue_table():
    """Provision Phase 4 batch_pick_queue table on the SQLite test DB."""
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
            delivery_sequence INTEGER,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    db.session.commit()


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        # SQLite in-memory: tables are gone between fixtures.
        db.create_all()
        _ensure_queue_table()
        yield app.test_client()
        db.session.rollback()
        db.session.remove()
        # Drop everything (including bare-SQL tables) so the next test
        # starts from a clean slate.
        try:
            db.session.execute(text("DROP TABLE IF EXISTS batch_pick_queue"))
            db.session.commit()
        except Exception:
            db.session.rollback()
        db.drop_all()


def _make_invoice(invoice_no, route_id=410, assigned_to="picker1"):
    from datetime import datetime
    inv = Invoice()
    inv.invoice_no = invoice_no
    inv.customer_name = f"Customer {invoice_no}"
    inv.assigned_to = assigned_to
    inv.status = "picking"
    inv.route_id = route_id
    inv.upload_date = datetime.utcnow()
    db.session.add(inv)
    return inv


def _make_item(invoice_no, item_code, qty=2, is_picked=False, locked_by=None):
    it = InvoiceItem()
    it.invoice_no = invoice_no
    it.item_code = item_code
    it.item_name = f"Item {item_code}"
    it.qty = qty
    it.is_picked = is_picked
    it.locked_by_batch_id = locked_by
    it.zone = "ZONE-A"
    db.session.add(it)
    return it


def _make_user(username="picker1", role="picker"):
    u = User()
    u.username = username
    u.role = role
    u.password = "x"
    try:
        u.password_hash = "x"
    except Exception:
        pass
    db.session.add(u)
    return u


def _login(client, username="picker1", role="picker"):
    """Bypass Flask-Login by writing the user_id session key directly.
    The User model uses ``username`` as the primary key, so that's what
    Flask-Login serialises into the session.
    """
    with app.app_context():
        u = User.query.filter_by(username=username).first()
        if not u:
            _make_user(username, role)
            db.session.commit()
    with client.session_transaction() as sess:
        sess["_user_id"] = username
        sess["_fresh"] = True


# ---------------------------------------------------------------------------
# T1–T6: send_item_to_batch service contract
# ---------------------------------------------------------------------------

def test_T1_send_normal_item_to_batch(client):
    _make_invoice("INV001", route_id=410)
    _make_item("INV001", "SKU-A")
    db.session.commit()

    session = send_item_to_batch("INV001", "SKU-A", "picker1")

    assert session.session_type == DEFERRED_SESSION_TYPE
    assert session.name == "DEFERRED-ROUTE-410"
    assert session.route_id == 410

    item = InvoiceItem.query.filter_by(invoice_no="INV001", item_code="SKU-A").one()
    assert item.pick_status == DEFERRED_PICK_STATUS
    assert item.locked_by_batch_id == session.id

    queue_count = db.session.execute(
        text("SELECT COUNT(*) FROM batch_pick_queue WHERE batch_session_id = :sid"),
        {"sid": session.id},
    ).scalar()
    assert queue_count == 1


def test_T2_second_item_same_route_reuses_session(client):
    _make_invoice("INV002", route_id=420)
    _make_item("INV002", "SKU-B")
    _make_item("INV002", "SKU-C")
    db.session.commit()

    s1 = send_item_to_batch("INV002", "SKU-B", "picker1")
    s2 = send_item_to_batch("INV002", "SKU-C", "picker1")

    assert s1.id == s2.id
    assert s1.name == "DEFERRED-ROUTE-420"

    queue_count = db.session.execute(
        text("SELECT COUNT(*) FROM batch_pick_queue WHERE batch_session_id = :sid"),
        {"sid": s1.id},
    ).scalar()
    assert queue_count == 2


def test_T3_different_invoice_same_route_shares_session(client):
    _make_invoice("INV003A", route_id=430)
    _make_invoice("INV003B", route_id=430)
    _make_item("INV003A", "SKU-D")
    _make_item("INV003B", "SKU-E")
    db.session.commit()

    s1 = send_item_to_batch("INV003A", "SKU-D", "picker1")
    s2 = send_item_to_batch("INV003B", "SKU-E", "picker1")
    assert s1.id == s2.id

    links = BatchSessionInvoice.query.filter_by(batch_session_id=s1.id).all()
    invs = sorted(link.invoice_no for link in links)
    assert invs == ["INV003A", "INV003B"]


def test_T4_different_route_creates_new_session(client):
    _make_invoice("INV004A", route_id=440)
    _make_invoice("INV004B", route_id=441)
    _make_item("INV004A", "SKU-F")
    _make_item("INV004B", "SKU-G")
    db.session.commit()

    s1 = send_item_to_batch("INV004A", "SKU-F", "picker1")
    s2 = send_item_to_batch("INV004B", "SKU-G", "picker1")
    assert s1.id != s2.id
    assert s1.name == "DEFERRED-ROUTE-440"
    assert s2.name == "DEFERRED-ROUTE-441"


def test_T5_already_picked_rejected(client):
    _make_invoice("INV005", route_id=450)
    _make_item("INV005", "SKU-H", is_picked=True)
    db.session.commit()

    with pytest.raises(DeferredBatchError) as exc_info:
        send_item_to_batch("INV005", "SKU-H", "picker1")
    assert exc_info.value.code == "already_picked"


def test_T6_already_locked_rejected(client):
    _make_invoice("INV006", route_id=460)
    _make_item("INV006", "SKU-I", locked_by=999)
    db.session.commit()

    with pytest.raises(DeferredBatchError) as exc_info:
        send_item_to_batch("INV006", "SKU-I", "picker1")
    assert exc_info.value.code == "already_locked"


# ---------------------------------------------------------------------------
# T7–T8: order-status transitions
# ---------------------------------------------------------------------------

def test_T7_invoice_with_deferred_items_lands_at_awaiting_batch(client):
    _make_invoice("INV007", route_id=470)
    _make_item("INV007", "SKU-J1")
    _make_item("INV007", "SKU-J2")
    db.session.commit()

    # Pick one item normally.
    item1 = InvoiceItem.query.filter_by(invoice_no="INV007", item_code="SKU-J1").one()
    item1.is_picked = True
    item1.pick_status = "picked"
    item1.picked_qty = item1.qty
    db.session.commit()

    # Defer the other.
    send_item_to_batch("INV007", "SKU-J2", "picker1")

    inv = Invoice.query.get("INV007")
    assert inv.status == "awaiting_batch_items"


def test_T8_ready_for_dispatch_only_after_batch_picked(client):
    _make_invoice("INV008", route_id=480)
    _make_item("INV008", "SKU-K")
    db.session.commit()

    session = send_item_to_batch("INV008", "SKU-K", "picker1")

    # Item still not picked → invoice should NOT be ready for dispatch.
    inv = Invoice.query.get("INV008")
    assert inv.status != "ready_for_dispatch"

    # Simulate batch picking the item and completing the batch.
    item = InvoiceItem.query.filter_by(invoice_no="INV008", item_code="SKU-K").one()
    item.is_picked = True
    item.pick_status = "picked"
    item.picked_qty = item.qty
    session.status = "Completed"
    db.session.commit()

    from batch_aware_order_status import update_all_orders_after_batch_completion
    update_all_orders_after_batch_completion(session.id)

    inv = Invoice.query.get("INV008")
    # All items picked + batch completed → invoice clears the batch gate.
    # Goes to awaiting_packing (or ready_for_dispatch if order_readiness OK).
    assert inv.status in ("awaiting_packing", "ready_for_dispatch")


# ---------------------------------------------------------------------------
# T9: session_type stamping
# ---------------------------------------------------------------------------

def test_T9_session_type_is_deferred_route(client):
    _make_invoice("INV009", route_id=490)
    _make_item("INV009", "SKU-L")
    db.session.commit()

    session = send_item_to_batch("INV009", "SKU-L", "picker1")
    persisted = BatchPickingSession.query.get(session.id)
    assert persisted.session_type == DEFERRED_SESSION_TYPE


# ---------------------------------------------------------------------------
# T10–T11: dashboard visibility
# ---------------------------------------------------------------------------

def test_T10_warehouse_manager_sees_batches_section(client):
    _make_user("wm1", role="warehouse_manager")
    _make_invoice("INV010", route_id=500, assigned_to="wm1")
    _make_item("INV010", "SKU-M")
    db.session.commit()
    send_item_to_batch("INV010", "SKU-M", "wm1")

    _login(client, "wm1", "warehouse_manager")
    resp = client.get("/picker/dashboard")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "DEFERRED-ROUTE-500" in body or "Batches" in body
    assert "Route 500" in body


def test_T11_picker_does_not_see_batches_section(client):
    _make_user("picker_only", role="picker")
    _make_user("wm2", role="warehouse_manager")
    _make_invoice("INV011", route_id=510, assigned_to="wm2")
    _make_item("INV011", "SKU-N")
    db.session.commit()
    send_item_to_batch("INV011", "SKU-N", "wm2")

    _login(client, "picker_only", "picker")
    resp = client.get("/picker/dashboard")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Picker dashboard renders, but no Deferred Batches section card.
    assert "DEFERRED-ROUTE-510" not in body


# ---------------------------------------------------------------------------
# T12: batch completion clears the deferred lock
# ---------------------------------------------------------------------------

def test_T12_batch_completion_promotes_invoice(client):
    _make_invoice("INV012", route_id=520)
    _make_item("INV012", "SKU-O")
    db.session.commit()

    session = send_item_to_batch("INV012", "SKU-O", "picker1")
    item = InvoiceItem.query.filter_by(invoice_no="INV012", item_code="SKU-O").one()
    item.is_picked = True
    item.pick_status = "picked"
    item.picked_qty = item.qty
    session.status = "Completed"
    db.session.commit()

    from batch_aware_order_status import update_order_status_batch_aware
    update_order_status_batch_aware("INV012")

    inv = Invoice.query.get("INV012")
    assert inv.status in ("awaiting_packing", "ready_for_dispatch")


# ---------------------------------------------------------------------------
# T13: cooler backfill
# ---------------------------------------------------------------------------

def test_T13_cooler_backfill_runs(client):
    # Simulate a legacy COOLER-ROUTE-* row with default 'standard' type.
    s = BatchPickingSession(
        name="COOLER-ROUTE-9999",
        zones="SENSITIVE",
        created_by="legacy",
        status="Created",
        picking_mode="Sequential",
        session_type="standard",
    )
    db.session.add(s)
    db.session.commit()
    sid = s.id

    from update_phase7_deferred_batch_schema import update_phase7_deferred_batch_schema
    update_phase7_deferred_batch_schema()

    db.session.expire_all()
    refreshed = db.session.get(BatchPickingSession, sid)
    assert refreshed.session_type == "cooler_route"


# ---------------------------------------------------------------------------
# T14–T15: API endpoint
# ---------------------------------------------------------------------------

def test_T14_api_send_to_batch_success(client):
    _make_user("api_picker", role="picker")
    _make_invoice("INV014", route_id=410, assigned_to="api_picker")
    _make_item("INV014", "SKU-API")
    db.session.commit()

    _login(client, "api_picker", "picker")
    resp = client.post(
        "/api/picker/invoice/INV014/send-to-batch",
        data={"item_code": "SKU-API"},
    )
    assert resp.status_code == 200
    j = resp.get_json()
    assert j["ok"] is True
    assert j["session_name"] == "DEFERRED-ROUTE-410"
    assert j["route_id"] == 410


def test_T15_api_missing_item_code_returns_400(client):
    _make_user("api_picker2", role="picker")
    _make_invoice("INV015", route_id=410, assigned_to="api_picker2")
    db.session.commit()

    _login(client, "api_picker2", "picker")
    resp = client.post(
        "/api/picker/invoice/INV015/send-to-batch",
        data={},
    )
    assert resp.status_code == 400
    j = resp.get_json()
    assert j["ok"] is False
    assert j["error"] == "missing_item_code"


# ---------------------------------------------------------------------------
# Bonus: list_open_deferred_batches summary
# ---------------------------------------------------------------------------

def test_list_open_deferred_batches_summary(client):
    _make_invoice("INVL1", route_id=600)
    _make_invoice("INVL2", route_id=600)
    _make_item("INVL1", "SKU-L1")
    _make_item("INVL2", "SKU-L2")
    db.session.commit()

    send_item_to_batch("INVL1", "SKU-L1", "picker1")
    send_item_to_batch("INVL2", "SKU-L2", "picker1")

    rows = list_open_deferred_batches()
    assert len(rows) == 1
    row = rows[0]
    assert row["route_id"] == 600
    assert row["total_count"] == 2
    assert row["picked_count"] == 0
    assert row["invoice_count"] == 2
    assert row["assigned_to"] is None
