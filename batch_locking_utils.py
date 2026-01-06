"""
Batch locking utility functions to prevent picking conflicts
"""
from app import db
from models import InvoiceItem, BatchPickingSession
from sqlalchemy import and_
import logging

logger = logging.getLogger(__name__)

def lock_items_for_batch(batch_id, zones_list, corridors_list=None, unit_types_list=None, invoice_nos=None):
    """
    Lock all items that match the batch criteria
    
    Args:
        batch_id: ID of the batch picking session
        zones_list: List of zones to lock items in
        corridors_list: Optional list of corridors to filter by
        unit_types_list: Optional list of unit types to filter by
        invoice_nos: Optional list of specific invoice numbers
    """
    try:
        # Build filter conditions
        filter_conditions = [
            InvoiceItem.zone.in_(zones_list),
            InvoiceItem.is_picked == False,
            InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending']),
            InvoiceItem.locked_by_batch_id == None  # Only lock unlocked items
        ]
        
        # Add corridor filter if specified
        if corridors_list:
            filter_conditions.append(InvoiceItem.corridor.in_(corridors_list))
        
        # Add unit type filter if specified
        if unit_types_list:
            filter_conditions.append(InvoiceItem.unit_type.in_(unit_types_list))
        
        # Add invoice filter if specified
        if invoice_nos:
            filter_conditions.append(InvoiceItem.invoice_no.in_(invoice_nos))
        
        # Lock the items
        locked_count = db.session.query(InvoiceItem).filter(
            and_(*filter_conditions)
        ).update({
            InvoiceItem.locked_by_batch_id: batch_id
        }, synchronize_session=False)
        
        db.session.commit()
        logger.info(f"🔒 Locked {locked_count} items for batch {batch_id}")
        return locked_count
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error locking items for batch {batch_id}: {str(e)}")
        raise

def unlock_items_for_batch(batch_id, preserve_picked=True):
    """
    Unlock items when a batch is deleted or completed
    
    Args:
        batch_id: ID of the batch picking session
        preserve_picked: If True, keep picked items locked
    """
    try:
        filter_conditions = [
            InvoiceItem.locked_by_batch_id == batch_id
        ]
        
        # If preserving picked items, only unlock unpicked items
        if preserve_picked:
            filter_conditions.extend([
                InvoiceItem.is_picked == False,
                InvoiceItem.picked_qty.is_(None) | (InvoiceItem.picked_qty == 0)
            ])
        
        # Unlock the items
        unlocked_count = db.session.query(InvoiceItem).filter(
            and_(*filter_conditions)
        ).update({
            InvoiceItem.locked_by_batch_id: None
        }, synchronize_session=False)
        
        db.session.commit()
        logger.info(f"🔓 Unlocked {unlocked_count} items from batch {batch_id}")
        return unlocked_count
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error unlocking items for batch {batch_id}: {str(e)}")
        raise

def check_item_lock_status(invoice_no, item_code, current_batch_id=None):
    """
    Check if an item is locked by a different batch
    
    Args:
        invoice_no: Invoice number
        item_code: Item code
        current_batch_id: Current batch ID (to allow picking from own batch)
    
    Returns:
        Dict with lock status information
    """
    try:
        item = db.session.query(InvoiceItem).filter(
            InvoiceItem.invoice_no == invoice_no,
            InvoiceItem.item_code == item_code
        ).first()
        
        if not item:
            return {'locked': False, 'message': 'Item not found'}
        
        if not item.locked_by_batch_id:
            return {'locked': False, 'message': 'Item is not locked'}
        
        if current_batch_id and item.locked_by_batch_id == current_batch_id:
            return {'locked': False, 'message': 'Item is locked by current batch'}
        
        # Item is locked by a different batch
        batch = db.session.get(BatchPickingSession, item.locked_by_batch_id)
        batch_name = batch.name if batch else f"Batch #{item.locked_by_batch_id}"
        
        return {
            'locked': True,
            'batch_id': item.locked_by_batch_id,
            'batch_name': batch_name,
            'message': f'Item is locked by {batch_name}'
        }
        
    except Exception as e:
        logger.error(f"❌ Error checking lock status for {invoice_no}-{item_code}: {str(e)}")
        return {'locked': False, 'message': 'Error checking lock status'}

def update_batch_locks_on_edit(batch_id, new_zones_list, new_corridors_list=None, new_unit_types_list=None, new_invoice_nos=None):
    """
    Update item locks when a batch is edited
    
    Args:
        batch_id: ID of the batch being edited
        new_zones_list: New list of zones
        new_corridors_list: New list of corridors (optional)
        new_invoice_nos: New list of invoice numbers (optional)
    """
    try:
        # First, unlock all items currently locked by this batch (except picked ones)
        unlock_items_for_batch(batch_id, preserve_picked=True)
        
        # Then lock items matching the new criteria
        locked_count = lock_items_for_batch(batch_id, new_zones_list, new_corridors_list, new_unit_types_list, new_invoice_nos)
        
        logger.info(f"🔄 Updated locks for batch {batch_id}: {locked_count} items locked")
        return locked_count
        
    except Exception as e:
        logger.error(f"❌ Error updating batch locks for batch {batch_id}: {str(e)}")
        raise

def get_locked_items_count(batch_id):
    """
    Get count of items locked by a specific batch
    
    Args:
        batch_id: ID of the batch picking session
    
    Returns:
        Count of locked items
    """
    try:
        count = db.session.query(InvoiceItem).filter(
            InvoiceItem.locked_by_batch_id == batch_id
        ).count()
        
        return count
        
    except Exception as e:
        logger.error(f"❌ Error getting locked items count for batch {batch_id}: {str(e)}")
        return 0

def get_available_items_count(zones_list, corridors_list=None, invoice_nos=None, unit_types_list=None):
    """
    Get count of available (unlocked) items matching the criteria
    
    Args:
        zones_list: List of zones
        corridors_list: Optional list of corridors
        invoice_nos: Optional list of invoice numbers
        unit_types_list: Optional list of unit types
    
    Returns:
        Count of available items
    """
    try:
        # Build filter conditions for available items
        filter_conditions = [
            InvoiceItem.zone.in_(zones_list),
            InvoiceItem.is_picked == False,
            InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending']),
            InvoiceItem.locked_by_batch_id == None  # Only unlocked items
        ]
        
        # Add corridor filter if specified
        if corridors_list:
            filter_conditions.append(InvoiceItem.corridor.in_(corridors_list))
        
        # Add unit type filter if specified
        if unit_types_list:
            filter_conditions.append(InvoiceItem.unit_type.in_(unit_types_list))
        
        # Add invoice filter if specified
        if invoice_nos:
            filter_conditions.append(InvoiceItem.invoice_no.in_(invoice_nos))
        
        # Count available items
        available_count = db.session.query(InvoiceItem).filter(
            and_(*filter_conditions)
        ).count()
        
        return available_count
        
    except Exception as e:
        logger.error(f"❌ Error counting available items: {str(e)}")
        return 0

def check_batch_conflicts(zones_list, corridors_list=None, invoice_nos=None, unit_types_list=None):
    """
    Check if items matching the criteria are already locked by other active batches
    
    Args:
        zones_list: List of zones
        corridors_list: Optional list of corridors
        invoice_nos: Optional list of invoice numbers
        unit_types_list: Optional list of unit types
    
    Returns:
        Dict with conflict information
    """
    try:
        # Build filter conditions for potential items
        filter_conditions = [
            InvoiceItem.zone.in_(zones_list),
            InvoiceItem.is_picked == False,
            InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending']),
            InvoiceItem.locked_by_batch_id != None  # Only check locked items
        ]
        
        # Add corridor filter if specified
        if corridors_list:
            filter_conditions.append(InvoiceItem.corridor.in_(corridors_list))
        
        # Add unit type filter if specified
        if unit_types_list:
            filter_conditions.append(InvoiceItem.unit_type.in_(unit_types_list))
        
        # Add invoice filter if specified
        if invoice_nos:
            filter_conditions.append(InvoiceItem.invoice_no.in_(invoice_nos))
        
        # Find conflicting items - only consider items locked by active batches
        conflicting_items = db.session.query(InvoiceItem).join(
            BatchPickingSession, InvoiceItem.locked_by_batch_id == BatchPickingSession.id
        ).filter(
            and_(*filter_conditions),
            BatchPickingSession.status.in_(['Created', 'In Progress', 'Assigned'])  # Active batch statuses
        ).all()
        
        if not conflicting_items:
            return {'has_conflicts': False, 'conflicts': []}
        
        # Group conflicts by batch
        conflicts_by_batch = {}
        for item in conflicting_items:
            batch_id = item.locked_by_batch_id
            if batch_id not in conflicts_by_batch:
                batch = db.session.get(BatchPickingSession, batch_id)
                batch_name = batch.name if batch else f"Batch #{batch_id}"
                conflicts_by_batch[batch_id] = {
                    'batch_id': batch_id,
                    'batch_name': batch_name,
                    'items': []
                }
            
            conflicts_by_batch[batch_id]['items'].append({
                'invoice_no': item.invoice_no,
                'item_code': item.item_code,
                'item_name': item.item_name,
                'location': item.location
            })
        
        return {
            'has_conflicts': True,
            'conflicts': list(conflicts_by_batch.values()),
            'total_conflicting_items': len(conflicting_items)
        }
        
    except Exception as e:
        logger.error(f"❌ Error checking batch conflicts: {str(e)}")
        return {'has_conflicts': False, 'conflicts': [], 'error': str(e)}