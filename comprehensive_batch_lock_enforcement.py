"""
Comprehensive batch lock enforcement system
Prevents any item assigned to a batch from being picked through regular order picking
"""
from app import db
from models import InvoiceItem, BatchPickingSession, BatchSessionInvoice
from sqlalchemy import and_
import logging

logger = logging.getLogger(__name__)

def get_items_in_active_batches():
    """
    Get all items that are currently assigned to active batches
    Returns set of (invoice_no, item_code) tuples
    """
    try:
        # Get all active batch sessions
        active_batches = BatchPickingSession.query.filter(
            BatchPickingSession.status.in_(['Created', 'picking'])
        ).all()
        
        locked_items = set()
        
        for batch in active_batches:
            # Get invoices in this batch
            batch_invoices = BatchSessionInvoice.query.filter_by(
                batch_session_id=batch.id
            ).all()
            
            if not batch_invoices:
                continue
                
            invoice_nos = [bi.invoice_no for bi in batch_invoices]
            zones_list = batch.zones.split(',') if batch.zones else []
            corridors_list = batch.corridors.split(',') if batch.corridors and batch.corridors.strip() else []
            
            if not zones_list:
                continue
            
            # Build filter conditions
            filter_conditions = [
                InvoiceItem.invoice_no.in_(invoice_nos),
                InvoiceItem.zone.in_(zones_list),
                InvoiceItem.is_picked == False,
                InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending'])
            ]
            
            if corridors_list:
                filter_conditions.append(InvoiceItem.corridor.in_(corridors_list))
            
            # Get items in this batch
            batch_items = db.session.query(InvoiceItem).filter(
                and_(*filter_conditions)
            ).all()
            
            for item in batch_items:
                locked_items.add((item.invoice_no, item.item_code))
        
        return locked_items
        
    except Exception as e:
        logger.error(f"Error getting items in active batches: {str(e)}")
        return set()

def is_item_in_active_batch(invoice_no, item_code):
    """
    Quick check if a specific item is in an active batch
    """
    try:
        # Check if item is directly locked
        item = db.session.query(InvoiceItem).filter(
            InvoiceItem.invoice_no == invoice_no,
            InvoiceItem.item_code == item_code
        ).first()
        
        if not item:
            return False
            
        if item.locked_by_batch_id:
            # Check if the batch is still active
            batch = db.session.get(BatchPickingSession, item.locked_by_batch_id)
            if batch and batch.status in ['Created', 'picking']:
                return True
        
        # Also check if item would be included in any active batch
        locked_items = get_items_in_active_batches()
        return (invoice_no, item_code) in locked_items
        
    except Exception as e:
        logger.error(f"Error checking if item {invoice_no}-{item_code} is in active batch: {str(e)}")
        return False

def enforce_batch_locks_on_invoice(invoice_no):
    """
    Apply comprehensive batch lock enforcement for a specific invoice
    Returns list of items that are locked by batches
    """
    try:
        locked_items_in_batches = get_items_in_active_batches()
        
        # Get all items for this invoice
        invoice_items = InvoiceItem.query.filter_by(
            invoice_no=invoice_no,
            is_picked=False
        ).all()
        
        locked_items = []
        for item in invoice_items:
            if (item.invoice_no, item.item_code) in locked_items_in_batches:
                locked_items.append(item)
        
        return locked_items
        
    except Exception as e:
        logger.error(f"Error enforcing batch locks on invoice {invoice_no}: {str(e)}")
        return []

def update_all_batch_locks():
    """
    Ensure all active batches have proper item locks applied
    """
    try:
        active_batches = BatchPickingSession.query.filter(
            BatchPickingSession.status.in_(['Created', 'picking'])
        ).all()
        
        for batch in active_batches:
            # Get invoices in this batch
            batch_invoices = BatchSessionInvoice.query.filter_by(
                batch_session_id=batch.id
            ).all()
            
            if not batch_invoices:
                continue
                
            invoice_nos = [bi.invoice_no for bi in batch_invoices]
            zones_list = batch.zones.split(',') if batch.zones else []
            corridors_list = batch.corridors.split(',') if batch.corridors and batch.corridors.strip() else []
            
            if not zones_list:
                continue
            
            # Apply locks using the existing utility
            from batch_locking_utils import lock_items_for_batch
            unit_types_list = batch.unit_types.split(',') if batch.unit_types else []
            locked_count = lock_items_for_batch(
                batch.id, 
                zones_list, 
                corridors_list if corridors_list else None,
                unit_types_list if unit_types_list else None, 
                invoice_nos
            )
            
            logger.info(f"Applied/verified {locked_count} locks for batch {batch.id}")
        
    except Exception as e:
        logger.error(f"Error updating all batch locks: {str(e)}")
        raise