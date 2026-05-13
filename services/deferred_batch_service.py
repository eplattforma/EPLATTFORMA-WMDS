"""Deferred ("Send to Batch") batch-picking service.

When a picker hits an item they don't want to pick on the spot (e.g. heavy,
needs a forklift, blocked location) they can hit "Send to Batch" on the
picking screen. That:

  * Locks the item via ``locked_by_batch_id`` (the existing
    batch-locked-item filter then hides it from regular picking).
  * Stamps ``pick_status = 'sent_to_batch'`` so we can tell deferred
    items apart from cooler-locked items in reports.
  * Attaches the item to a per-route ``DEFERRED-ROUTE-<route_id>``
    ``BatchPickingSession`` (``session_type = 'deferred_route'``),
    creating that session on demand. One deferred session per route —
    multiple invoices on the same route share it; different routes get
    separate sessions.
  * Inserts a ``batch_pick_queue`` row so the existing batch-picking UI
    (``/picker/batch/list``) can claim and work the deferred items via
    the normal flow.

Idempotent and race-safe: ``get_or_create_deferred_session`` uses a
``begin_nested`` SAVEPOINT around the INSERT so a concurrent picker
losing the race rolls back only its own SAVEPOINT and re-reads the
winning row, leaving the outer transaction intact.
"""
import logging

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import db
from models import (
    ActivityLog,
    BatchPickingSession,
    BatchSessionInvoice,
    Invoice,
    InvoiceItem,
)
from timezone_utils import get_utc_now

logger = logging.getLogger(__name__)


DEFERRED_SESSION_TYPE = "deferred_route"
DEFERRED_PICK_STATUS = "sent_to_batch"
_OPEN_STATUSES = ("Created", "Active", "Paused")


class DeferredBatchError(Exception):
    """Raised when an item cannot be sent to a deferred batch."""

    def __init__(self, message, code="error"):
        super().__init__(message)
        self.message = message
        self.code = code


def _deferred_name(route_id):
    return f"DEFERRED-ROUTE-{route_id}"


def _next_batch_number():
    """Best-effort batch number; falls back to a timestamp if the helper
    is unavailable so the migration order between modules doesn't matter.
    """
    try:
        from batch_utils import generate_batch_number
        return generate_batch_number()
    except Exception:
        return f"DEF-{get_utc_now().strftime('%Y%m%d%H%M%S')}"


def get_or_create_deferred_session(route_id, created_by):
    """Return the open ``DEFERRED-ROUTE-<route_id>`` session, creating
    it inside a SAVEPOINT if it doesn't exist yet.

    Race-safe: two concurrent pickers calling this for the same route at
    the same time both end up returning the same row — the loser's
    INSERT raises ``IntegrityError`` (when a unique-by-name index is in
    place) or simply re-queries on the duplicate-detection re-read.
    The SAVEPOINT only wraps the INSERT so the outer transaction (the
    one writing the item lock + queue row) is preserved on conflict.
    """
    if route_id is None:
        raise DeferredBatchError(
            "Invoice has no route assigned; cannot create deferred batch.",
            code="no_route",
        )

    name = _deferred_name(route_id)

    existing = (
        BatchPickingSession.query
        .filter(BatchPickingSession.session_type == DEFERRED_SESSION_TYPE)
        .filter(BatchPickingSession.route_id == route_id)
        .filter(BatchPickingSession.status.in_(_OPEN_STATUSES))
        .order_by(BatchPickingSession.id.desc())
        .first()
    )
    if existing:
        return existing

    # Race window: try to insert; on IntegrityError (or any DB-level
    # uniqueness conflict) the SAVEPOINT rolls back and we re-query.
    try:
        with db.session.begin_nested():
            session = BatchPickingSession(
                name=name,
                batch_number=_next_batch_number(),
                zones="DEFERRED",
                created_by=created_by,
                assigned_to=None,
                picking_mode="Sequential",
                status="Created",
                session_type=DEFERRED_SESSION_TYPE,
                route_id=route_id,
            )
            try:
                session.last_activity_at = get_utc_now()
            except Exception:
                pass
            db.session.add(session)
            db.session.flush()
        logger.info(
            "Created deferred batch session #%s (%s) for route %s by %s",
            session.id, name, route_id, created_by,
        )
        return session
    except IntegrityError:
        # Lost the race — re-read the winner.
        winner = (
            BatchPickingSession.query
            .filter(BatchPickingSession.session_type == DEFERRED_SESSION_TYPE)
            .filter(BatchPickingSession.route_id == route_id)
            .filter(BatchPickingSession.status.in_(_OPEN_STATUSES))
            .order_by(BatchPickingSession.id.desc())
            .first()
        )
        if winner:
            return winner
        raise


def send_item_to_batch(invoice_no, item_code, picker_username):
    """Move a single item from regular picking to the per-route deferred
    batch. Returns the deferred ``BatchPickingSession``.

    Validates:
      * Item exists.
      * Item is not already picked.
      * Item is not already locked by another batch (cooler / standard /
        a different deferred session).
      * The invoice has a ``route_id``.

    On success (single transaction):
      1. Get-or-create the deferred session for the invoice's route.
      2. Stamp ``locked_by_batch_id`` + ``pick_status = 'sent_to_batch'``
         on the item.
      3. Insert a ``batch_session_invoices`` row if this invoice isn't
         already attached.
      4. Insert a ``batch_pick_queue`` row (``pick_zone_type = 'normal'``).
      5. Activity log.
      6. Recompute order status.

    Raises ``DeferredBatchError`` on validation failures.
    """
    # Race guard: take a row-level lock on the InvoiceItem so concurrent
    # Send-to-Batch / picker actions on the same item serialise. SQLite
    # ignores ``with_for_update``; Postgres honours it. Wrapping in
    # try/except keeps the function dialect-agnostic.
    item_q = InvoiceItem.query.filter_by(
        invoice_no=invoice_no, item_code=item_code
    )
    try:
        item = item_q.with_for_update(of=InvoiceItem).first()
    except Exception:
        item = item_q.first()
    if not item:
        raise DeferredBatchError(
            f"Item {item_code} not found on invoice {invoice_no}.",
            code="not_found",
        )

    if item.is_picked or (item.pick_status or "") == "picked":
        raise DeferredBatchError(
            f"Item {item_code} is already picked.",
            code="already_picked",
        )

    if item.locked_by_batch_id is not None:
        raise DeferredBatchError(
            f"Item {item_code} is already locked by batch #{item.locked_by_batch_id}.",
            code="already_locked",
        )

    invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if not invoice:
        raise DeferredBatchError(
            f"Invoice {invoice_no} not found.", code="not_found",
        )

    route_id = getattr(invoice, "route_id", None)
    if route_id is None:
        raise DeferredBatchError(
            f"Invoice {invoice_no} has no route assigned. "
            "Send to Batch is only available once the order is on a route.",
            code="no_route",
        )

    session = get_or_create_deferred_session(route_id, picker_username)

    # 2. Lock + stamp the item.
    item.locked_by_batch_id = session.id
    item.pick_status = DEFERRED_PICK_STATUS

    # 3. Attach invoice to the session if not already attached.
    existing_link = BatchSessionInvoice.query.filter_by(
        batch_session_id=session.id, invoice_no=invoice_no,
    ).first()
    if not existing_link:
        db.session.add(BatchSessionInvoice(
            batch_session_id=session.id,
            invoice_no=invoice_no,
        ))

    # 4. Queue row. Pick the next sequence_no for this batch.
    next_seq = db.session.execute(
        text(
            "SELECT COALESCE(MAX(sequence_no), 0) + 1 "
            "FROM batch_pick_queue WHERE batch_session_id = :bid"
        ),
        {"bid": session.id},
    ).scalar() or 1

    qty_required = None
    try:
        if item.expected_pick_pieces is not None:
            qty_required = float(item.expected_pick_pieces)
        elif item.qty is not None:
            qty_required = float(item.qty)
    except (TypeError, ValueError):
        qty_required = None

    db.session.execute(
        text(
            """
            INSERT INTO batch_pick_queue (
                batch_session_id, invoice_no, item_code, pick_zone_type,
                sequence_no, status, qty_required, wms_zone
            ) VALUES (
                :sid, :inv, :code, 'normal',
                :seq, 'pending', :qty, :zone
            )
            """
        ),
        {
            "sid": session.id,
            "inv": invoice_no,
            "code": item_code,
            "seq": int(next_seq),
            "qty": qty_required,
            "zone": (item.zone or None),
        },
    )

    # 5. Activity log.
    db.session.add(ActivityLog(
        picker_username=picker_username,
        activity_type="batch.deferred",
        invoice_no=invoice_no,
        item_code=item_code,
        details=(
            f"Sent to deferred batch #{session.id} ({session.name}) "
            f"for route {route_id}"
        ),
    ))

    # Touch session activity.
    try:
        session.last_activity_at = get_utc_now()
    except Exception:
        pass

    db.session.commit()

    # 6. Recompute the invoice status now that an item is batch-locked.
    try:
        from batch_aware_order_status import update_order_status_batch_aware
        update_order_status_batch_aware(invoice_no)
    except Exception as exc:
        logger.warning(
            "Order status recompute after Send-to-Batch failed for %s: %s",
            invoice_no, exc,
        )

    return session


def list_open_deferred_batches():
    """Return all open ``deferred_route`` sessions with summary counts.
    Used by the picker dashboard's Batches section (warehouse_manager
    only).
    """
    sessions = (
        BatchPickingSession.query
        .filter(BatchPickingSession.session_type == DEFERRED_SESSION_TYPE)
        .filter(BatchPickingSession.status.in_(_OPEN_STATUSES))
        .order_by(BatchPickingSession.created_at.desc())
        .all()
    )
    if not sessions:
        return []

    sids = [s.id for s in sessions]

    # Counts via aggregate queries (avoids N+1).
    counts = db.session.execute(
        text(
            "SELECT batch_session_id, "
            "       COUNT(*) AS total, "
            "       SUM(CASE WHEN status = 'picked' THEN 1 ELSE 0 END) AS picked "
            "FROM batch_pick_queue "
            "WHERE batch_session_id IN :sids "
            "GROUP BY batch_session_id"
        ).bindparams(db.bindparam("sids", expanding=True)),
        {"sids": sids},
    ).fetchall()
    count_map = {
        r.batch_session_id: (int(r.total or 0), int(r.picked or 0))
        for r in counts
    }

    inv_counts = db.session.execute(
        text(
            "SELECT batch_session_id, COUNT(*) AS n "
            "FROM batch_session_invoices "
            "WHERE batch_session_id IN :sids "
            "GROUP BY batch_session_id"
        ).bindparams(db.bindparam("sids", expanding=True)),
        {"sids": sids},
    ).fetchall()
    inv_map = {r.batch_session_id: int(r.n or 0) for r in inv_counts}

    rows = []
    for s in sessions:
        total, picked = count_map.get(s.id, (0, 0))
        rows.append({
            "id": s.id,
            "batch_number": s.batch_number or s.name,
            "name": s.name,
            "route_id": s.route_id,
            "status": s.status,
            "assigned_to": s.assigned_to,
            "total_count": total,
            "picked_count": picked,
            "invoice_count": inv_map.get(s.id, 0),
            "created_at": s.created_at,
        })
    return rows
