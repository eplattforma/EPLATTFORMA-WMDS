"""Phase 5: order readiness rule.

An order with both normal and cooler items must not be marked fully
ready until **both** queues complete (or are exceptioned) AND every
cooler box that contains items for the invoice is closed (or beyond).

Terminal queue statuses (per Phase 4 audit semantics):

  picked, skipped, exception, cancelled

Terminal cooler-box statuses (per Phase 5 §5.7):

  closed, loaded, delivered

When ``summer_cooler_mode_enabled = false``, no cooler queue rows exist
for the invoice, so the cooler check is a no-op and behaviour reduces
to the normal-queue check alone (pre-Phase-5 behaviour preserved).

This module is the single source of truth for "is this invoice ready
for shipment". Existing call sites in routes/services that previously
asked ``all(item.is_picked for item in invoice.items)`` should call
``is_order_ready(invoice_no)`` instead so the cooler dimension is
honoured everywhere.
"""
import logging

from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)

QUEUE_TERMINAL_STATUSES = ("picked", "skipped", "exception", "cancelled")
COOLER_BOX_TERMINAL_STATUSES = ("closed", "loaded", "delivered")


def _table_exists(name):
    try:
        from sqlalchemy import inspect
        return name in inspect(db.engine).get_table_names()
    except Exception:
        return False


def is_order_ready(invoice_no):
    """Return True iff the order ``invoice_no`` is fully ready for shipment.

    The check is composed of three sub-checks:

    1. Normal queue — every ``batch_pick_queue`` row for the invoice with
       ``pick_zone_type = 'normal'`` is in a terminal status.
    2. Cooler queue — every ``batch_pick_queue`` row for the invoice with
       ``pick_zone_type = 'cooler'`` is in a terminal status.
    3. Cooler boxes — every ``cooler_box`` that has at least one
       ``cooler_box_items`` row for the invoice is in a terminal status.

    When the queue table is empty for the invoice (no batch refactor
    rows ever written, e.g. during the Phase 4 rollout window where the
    legacy session path is still in use), the queue checks pass
    vacuously and readiness reduces to the legacy ``InvoiceItem``
    ``is_picked`` check. This preserves pre-Phase-5 behaviour for
    invoices that never went through the new pipeline.
    """
    if not invoice_no:
        return False

    queue_terminal = "(" + ",".join(f"'{s}'" for s in QUEUE_TERMINAL_STATUSES) + ")"
    box_terminal = "(" + ",".join(f"'{s}'" for s in COOLER_BOX_TERMINAL_STATUSES) + ")"

    if _table_exists("batch_pick_queue"):
        # Total queue rows for this invoice (any zone).
        total = db.session.execute(
            text(
                "SELECT COUNT(*) FROM batch_pick_queue "
                "WHERE invoice_no = :inv"
            ),
            {"inv": invoice_no},
        ).scalar() or 0

        if total > 0:
            # Pending rows (any zone) block readiness.
            pending = db.session.execute(
                text(
                    "SELECT COUNT(*) FROM batch_pick_queue "
                    "WHERE invoice_no = :inv "
                    f"  AND status NOT IN {queue_terminal}"
                ),
                {"inv": invoice_no},
            ).scalar() or 0
            if pending > 0:
                return False

            if _table_exists("cooler_boxes") and _table_exists("cooler_box_items"):
                # Any cooler box holding items for this invoice that
                # has not yet reached a terminal status blocks readiness.
                open_boxes = db.session.execute(
                    text(
                        "SELECT COUNT(DISTINCT cb.id) "
                        "FROM cooler_boxes cb "
                        "JOIN cooler_box_items cbi "
                        "  ON cbi.cooler_box_id = cb.id "
                        "WHERE cbi.invoice_no = :inv "
                        f"  AND cb.status NOT IN {box_terminal}"
                    ),
                    {"inv": invoice_no},
                ).scalar() or 0
                if open_boxes > 0:
                    return False
            return True

    # Fallback: legacy session-path invoice — readiness from InvoiceItem.
    try:
        from models import Invoice
        inv = db.session.query(Invoice).filter_by(invoice_no=invoice_no).first()
        if inv is None:
            return False
        items = list(getattr(inv, "items", []) or [])
        if not items:
            return False
        return all(bool(getattr(i, "is_picked", False)) for i in items)
    except Exception as e:
        logger.debug("is_order_ready legacy fallback failed for %s: %s", invoice_no, e)
        return False
