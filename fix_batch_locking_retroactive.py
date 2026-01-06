#!/usr/bin/env python3
"""
Retroactively fix batch locking for existing completed batches
and ensure proper locking for future batches
"""
import logging
from main import app
from app import db
from models import BatchPickingSession, BatchSessionInvoice, InvoiceItem
from batch_locking_utils import lock_items_for_batch
from sqlalchemy import and_

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_retroactive_batch_locking():
    """Fix batch locking for recently completed batches where items were picked outside the batch"""
    with app.app_context():
        try:
            # Get recently completed batches that might have locking issues
            recent_batches = BatchPickingSession.query.filter(
                BatchPickingSession.status == 'Completed',
                BatchPickingSession.id >= 100  # Recent batches
            ).order_by(BatchPickingSession.id.desc()).all()
            
            for batch in recent_batches:
                logger.info(f"Checking batch {batch.id}: {batch.name}")
                
                # Get invoices in this batch
                batch_invoices = BatchSessionInvoice.query.filter_by(
                    batch_session_id=batch.id
                ).all()
                
                invoice_nos = [bi.invoice_no for bi in batch_invoices]
                zones_list = batch.zones.split(',') if batch.zones else []
                corridors_list = batch.corridors.split(',') if batch.corridors and batch.corridors.strip() else []
                
                if not invoice_nos or not zones_list:
                    continue
                
                # Check for items that should have been locked but weren't
                filter_conditions = [
                    InvoiceItem.invoice_no.in_(invoice_nos),
                    InvoiceItem.zone.in_(zones_list),
                    InvoiceItem.is_picked == True,
                    InvoiceItem.locked_by_batch_id == None  # Items that weren't locked
                ]
                
                if corridors_list:
                    filter_conditions.append(InvoiceItem.corridor.in_(corridors_list))
                
                unlocked_picked_items = db.session.query(InvoiceItem).filter(
                    and_(*filter_conditions)
                ).all()
                
                if unlocked_picked_items:
                    logger.warning(f"Batch {batch.id} has {len(unlocked_picked_items)} items that were picked without proper locking:")
                    for item in unlocked_picked_items:
                        logger.warning(f"  - {item.invoice_no} / {item.item_code} in zone {item.zone}")
                        # Mark these items as having been picked by this batch
                        item.locked_by_batch_id = batch.id
                    
                    db.session.commit()
                    logger.info(f"Retroactively locked {len(unlocked_picked_items)} items for batch {batch.id}")
                else:
                    logger.info(f"Batch {batch.id} locking is correct")
            
            logger.info("Retroactive batch locking fix completed")
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error fixing retroactive batch locking: {str(e)}")
            raise

def test_current_locking_system():
    """Test the current locking system to ensure it works for new batches"""
    with app.app_context():
        try:
            # Check for any active batches
            active_batches = BatchPickingSession.query.filter(
                BatchPickingSession.status.in_(['Created', 'picking'])
            ).all()
            
            for batch in active_batches:
                logger.info(f"Testing locks for active batch {batch.id}: {batch.name}")
                
                # Check if items are properly locked
                locked_items = db.session.query(InvoiceItem).filter(
                    InvoiceItem.locked_by_batch_id == batch.id
                ).count()
                
                logger.info(f"Batch {batch.id} has {locked_items} locked items")
                
                if locked_items == 0:
                    logger.warning(f"Batch {batch.id} has no locked items - applying locks now")
                    
                    # Apply locks
                    zones_list = batch.zones.split(',') if batch.zones else []
                    corridors_list = batch.corridors.split(',') if batch.corridors and batch.corridors.strip() else []
                    
                    # Get invoices in this batch
                    batch_invoices = BatchSessionInvoice.query.filter_by(
                        batch_session_id=batch.id
                    ).all()
                    invoice_nos = [bi.invoice_no for bi in batch_invoices]
                    
                    if zones_list and invoice_nos:
                        unit_types_list = batch.unit_types.split(',') if batch.unit_types else []
                        locked_count = lock_items_for_batch(
                            batch.id, 
                            zones_list, 
                            corridors_list if corridors_list else None,
                            unit_types_list if unit_types_list else None, 
                            invoice_nos
                        )
                        logger.info(f"Applied locks to {locked_count} items for batch {batch.id}")
            
        except Exception as e:
            logger.error(f"Error testing locking system: {str(e)}")
            raise

if __name__ == "__main__":
    print("Fixing retroactive batch locking...")
    fix_retroactive_batch_locking()
    print("\nTesting current locking system...")
    test_current_locking_system()
    print("\nBatch locking fix completed!")