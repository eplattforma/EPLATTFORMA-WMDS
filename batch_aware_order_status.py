"""
Batch-Aware Order Status Logic
Updates order status correctly when items are locked in batches
"""

from models import Invoice, InvoiceItem, db
from flask import current_app


def update_order_status_batch_aware(invoice_no):
    """
    Update order status considering batch-locked items
    
    Args:
        invoice_no: Invoice number to update
        
    Returns:
        dict: Status update summary
    """
    invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if not invoice:
        return {'error': 'Invoice not found'}
    
    # Get all items for this invoice
    all_items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
    
    if not all_items:
        return {'error': 'No items found for invoice'}
    
    total_items = len(all_items)
    picked_items = 0
    unpicked_items = 0
    batch_locked_items = 0
    
    # Items with these pick_status values count as "done" for routing/dispatch
    # purposes (mirrors services.order_readiness.QUEUE_TERMINAL_STATUSES so the
    # batch flow agrees with the non-batch flow on what "finished" means).
    TERMINAL_PICK_STATUSES = ('picked', 'exception', 'skipped', 'cancelled')

    # PERFORMANCE FIX: Batch query all batch sessions at once to avoid N+1 queries
    # Collect all unique batch IDs first
    batch_ids = set()
    for item in all_items:
        if not (item.is_picked and item.pick_status in TERMINAL_PICK_STATUSES) and item.locked_by_batch_id is not None:
            batch_ids.add(item.locked_by_batch_id)

    # Single query to get all batch statuses
    batch_status_map = {}
    if batch_ids:
        from models import BatchPickingSession
        batches = BatchPickingSession.query.filter(BatchPickingSession.id.in_(batch_ids)).all()
        batch_status_map = {b.id: b.status for b in batches}

    # Batches in these statuses no longer hold their locks. Cancelled and
    # Archived batches must NOT count as blocking, otherwise stale locks
    # (e.g. exception/skipped rows from a later-cancelled batch) park the
    # invoice at awaiting_batch_items forever.
    RELEASED_BATCH_STATUSES = ('Completed', 'Cancelled', 'Archived')

    # Now iterate items with cached batch statuses
    for item in all_items:
        if item.is_picked and item.pick_status in TERMINAL_PICK_STATUSES:
            picked_items += 1
        else:
            unpicked_items += 1
            # Check if item is locked by an ACTIVE batch
            if item.locked_by_batch_id is not None:
                batch_status = batch_status_map.get(item.locked_by_batch_id)
                if batch_status and batch_status not in RELEASED_BATCH_STATUSES:
                    batch_locked_items += 1
                elif batch_status in RELEASED_BATCH_STATUSES:
                    # Unlock item from a completed/cancelled/archived batch
                    item.locked_by_batch_id = None
    
    # Determine the correct status
    old_status = invoice.status

    # Check batch_pick_queue for cooler items still pending.
    # InvoiceItem.is_picked can be True for cooler items that were picked
    # before summer_cooler_mode was enabled, but still need cooler processing.
    from sqlalchemy import text as _text
    cooler_pending = db.session.execute(_text(
        "SELECT COUNT(*) FROM batch_pick_queue "
        "WHERE invoice_no = :inv "
        "  AND pick_zone_type = 'cooler' "
        "  AND status = 'pending'"
    ), {"inv": invoice_no}).scalar() or 0

    if picked_items == total_items and cooler_pending == 0:
        # All items picked. If the order had already been packed earlier
        # and was sitting in 'awaiting_batch_items' waiting on this batch
        # (Phase-5 cooler/batch integration), promote it straight to
        # ready_for_dispatch — packing is already done; the only thing
        # that was holding it back was the batch queue. Otherwise fall
        # back to the legacy behaviour of routing through awaiting_packing.
        try:
            from services.order_readiness import is_order_ready
            ready = is_order_ready(invoice_no)
        except Exception:
            ready = False
        if old_status in ('awaiting_batch_items', 'ready_for_dispatch') and ready:
            invoice.status = 'ready_for_dispatch'
        else:
            invoice.status = 'awaiting_packing'

    elif picked_items == total_items and cooler_pending > 0:
        # All InvoiceItems look picked but cooler queue still has
        # pending items — hold until cooler batch completes
        invoice.status = 'awaiting_batch_items'

    elif unpicked_items > 0 and batch_locked_items == unpicked_items:
        # All remaining unpicked items are locked by batches (cooler,
        # standard, or deferred-route via Send-to-Batch). The order
        # parks at ``awaiting_batch_items`` until the batch flow picks
        # them, at which point ``update_all_orders_after_batch_completion``
        # promotes it to ``awaiting_packing`` / ``ready_for_dispatch``.
        invoice.status = 'awaiting_batch_items'
        
    elif picked_items > 0:
        # Some items picked, some regular unpicked items remain
        invoice.status = 'picking'
        
    else:
        # No items picked yet
        invoice.status = 'not_started'
    
    # Update status timestamp if status changed
    if old_status != invoice.status:
        from datetime import datetime
        invoice.status_updated_at = datetime.utcnow()
    
    # Commit the status change
    try:
        db.session.commit()
        
        summary = {
            'invoice_no': invoice_no,
            'old_status': old_status,
            'new_status': invoice.status,
            'total_items': total_items,
            'picked_items': picked_items,
            'unpicked_items': unpicked_items,
            'batch_locked_items': batch_locked_items,
            'status_changed': old_status != invoice.status
        }
        
        if summary['status_changed']:
            current_app.logger.info(f"📦 Order {invoice_no} status: {old_status} → {invoice.status} "
                                  f"(Picked: {picked_items}/{total_items}, Batch-locked: {batch_locked_items})")
        
        return summary
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"❌ Failed to update status for {invoice_no}: {str(e)}")
        return {'error': f'Failed to update status: {str(e)}'}


def sync_cooler_invoice_items(batch_id):
    """For a completed cooler_route batch, ensure every InvoiceItem that was
    picked via ``batch_pick_queue`` (queue_pick endpoint) has its
    ``is_picked`` flag set to True.

    When items are picked from the cooler route page the
    ``queue_pick`` endpoint only writes to ``batch_pick_queue``; it
    bypasses the normal ``batch_picking_item`` code-path that also sets
    ``invoice_items.is_picked = True``.  This leaves the InvoiceItem in
    an inconsistent state and causes ``update_order_status_batch_aware``
    to see the item as still unpicked, keeping the invoice at 'picking'.
    """
    from sqlalchemy import text as _text
    try:
        rows = db.session.execute(
            _text(
                "UPDATE invoice_items ii "
                "SET is_picked = TRUE, "
                "    pick_status = CASE "
                "        WHEN bpq.status = 'exception' THEN 'exception' "
                "        ELSE 'picked' END "
                "FROM batch_pick_queue bpq "
                "WHERE bpq.batch_session_id = :bid "
                "  AND bpq.status IN ('picked', 'exception') "
                "  AND bpq.pick_zone_type = 'cooler' "
                "  AND ii.invoice_no = bpq.invoice_no "
                "  AND ii.item_code  = bpq.item_code "
                "  AND ii.is_picked  = FALSE"
            ),
            {"bid": batch_id},
        )
        synced = rows.rowcount if rows.rowcount is not None else 0
        if synced:
            current_app.logger.info(
                "sync_cooler_invoice_items: batch %s — synced %d invoice_item row(s)",
                batch_id, synced,
            )
    except Exception as exc:
        current_app.logger.warning(
            "sync_cooler_invoice_items: batch %s failed: %s", batch_id, exc
        )


def update_all_orders_after_batch_completion(batch_id):
    """
    Update order statuses after a batch is completed
    
    Args:
        batch_id: ID of the completed batch
        
    Returns:
        list: Summary of updated orders
    """
    from models import BatchSessionInvoice, BatchPickingSession

    # For cooler_route sessions items may have been picked via the cooler
    # route page (queue_pick), which only writes to batch_pick_queue and
    # never sets InvoiceItem.is_picked.  Sync the gap before running the
    # status logic so invoice statuses advance correctly.
    try:
        batch = BatchPickingSession.query.get(batch_id)
        if batch and getattr(batch, 'session_type', None) == 'cooler_route':
            sync_cooler_invoice_items(batch_id)
    except Exception as _se:
        current_app.logger.warning(
            "update_all_orders_after_batch_completion: cooler sync check failed: %s", _se
        )

    # Get all invoices that were in this batch
    batch_invoices = BatchSessionInvoice.query.filter_by(batch_session_id=batch_id).all()
    
    updated_orders = []
    for batch_invoice in batch_invoices:
        summary = update_order_status_batch_aware(batch_invoice.invoice_no)
        updated_orders.append(summary)
    
    current_app.logger.info(f"📋 Updated {len(updated_orders)} orders after batch {batch_id} completion")
    return updated_orders


def get_order_status_summary(invoice_no):
    """
    Get detailed status summary for an order
    
    Args:
        invoice_no: Invoice number
        
    Returns:
        dict: Detailed status information
    """
    invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if not invoice:
        return {'error': 'Invoice not found'}
    
    all_items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
    
    TERMINAL_PICK_STATUSES = ('picked', 'exception', 'skipped', 'cancelled')
    total_items = len(all_items)
    picked_items = sum(1 for item in all_items if item.is_picked and item.pick_status in TERMINAL_PICK_STATUSES)
    batch_locked_items = sum(1 for item in all_items if not item.is_picked and item.locked_by_batch_id is not None)
    unpicked_free_items = total_items - picked_items - batch_locked_items
    
    return {
        'invoice_no': invoice_no,
        'status': invoice.status,
        'total_items': total_items,
        'picked_items': picked_items,
        'batch_locked_items': batch_locked_items,
        'unpicked_free_items': unpicked_free_items,
        'summary': f"Picked: {picked_items}/{total_items}. Batch-locked: {batch_locked_items}. Available: {unpicked_free_items}"
    }