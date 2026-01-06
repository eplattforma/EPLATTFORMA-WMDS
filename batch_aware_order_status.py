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
    
    # PERFORMANCE FIX: Batch query all batch sessions at once to avoid N+1 queries
    # Collect all unique batch IDs first
    batch_ids = set()
    for item in all_items:
        if not (item.is_picked and item.pick_status == 'picked') and item.locked_by_batch_id is not None:
            batch_ids.add(item.locked_by_batch_id)
    
    # Single query to get all batch statuses
    batch_status_map = {}
    if batch_ids:
        from models import BatchPickingSession
        batches = BatchPickingSession.query.filter(BatchPickingSession.id.in_(batch_ids)).all()
        batch_status_map = {b.id: b.status for b in batches}
    
    # Now iterate items with cached batch statuses
    for item in all_items:
        if item.is_picked and item.pick_status == 'picked':
            picked_items += 1
        else:
            unpicked_items += 1
            # Check if item is locked by an ACTIVE batch
            if item.locked_by_batch_id is not None:
                batch_status = batch_status_map.get(item.locked_by_batch_id)
                if batch_status and batch_status != 'Completed':
                    batch_locked_items += 1
                elif batch_status == 'Completed':
                    # Unlock item from completed batch
                    item.locked_by_batch_id = None
    
    # Determine the correct status
    old_status = invoice.status
    
    if picked_items == total_items:
        # All items picked - order is now awaiting packing
        invoice.status = 'awaiting_packing'
        
    elif unpicked_items > 0 and batch_locked_items == unpicked_items:
        # All remaining unpicked items are locked by batches
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


def update_all_orders_after_batch_completion(batch_id):
    """
    Update order statuses after a batch is completed
    
    Args:
        batch_id: ID of the completed batch
        
    Returns:
        list: Summary of updated orders
    """
    from models import BatchSessionInvoice
    
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
    
    total_items = len(all_items)
    picked_items = sum(1 for item in all_items if item.is_picked and item.pick_status == 'picked')
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