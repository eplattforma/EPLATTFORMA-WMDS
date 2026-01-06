"""
Database Migration Script for New Order Status System
Converts existing order statuses to the new 8-status lifecycle
"""
import logging
from app import app, db
from models import Invoice, Setting
from order_status_constants import ORDER_STATUSES, get_sorted_statuses
import json

# Status mapping from old to new system
STATUS_MAPPING = {
    # Old statuses -> New status
    'Not Started': 'not_started',
    'In Progress': 'picking',
    'Partially Picked': 'picking',  # Convert partially picked to picking
    'Ready for Packing': 'awaiting_packing',
    'Completed': 'ready_for_dispatch',  # Completed orders become ready for dispatch
    'Shipped': 'shipped',
    'Out for delivery': 'shipped',  # Out for delivery becomes shipped
    'Delivered': 'delivered',
    'Cancelled': 'cancelled',
    'Returned': 'returned_to_warehouse'
}

def update_order_status_system():
    """Update the order status system to use new standardized statuses"""
    try:
        logging.info("Starting order status system migration...")
        
        # Get all invoices
        invoices = Invoice.query.all()
        updated_count = 0
        
        for invoice in invoices:
            old_status = invoice.status
            
            # Map old status to new status
            if old_status in STATUS_MAPPING:
                new_status = STATUS_MAPPING[old_status]
                invoice.status = new_status
                updated_count += 1
                logging.info(f"Updated invoice {invoice.invoice_no}: '{old_status}' -> '{new_status}'")
            else:
                # If status doesn't match, default to not_started
                if old_status not in ORDER_STATUSES:
                    logging.warning(f"Unknown status '{old_status}' for invoice {invoice.invoice_no}, setting to 'not_started'")
                    invoice.status = 'not_started'
                    updated_count += 1
        
        # Update the status sequence setting to use new statuses
        new_status_sequence = [status['value'] for status in get_sorted_statuses()]
        
        # Update global sorting configuration
        sorting_config = {
            'primarySort': 'status',
            'primaryDirection': 'asc',
            'secondarySort': 'routing',
            'secondaryDirection': 'asc',
            'statusSequence': new_status_sequence
        }
        
        Setting.set_json(db.session, 'admin_sorting_config', sorting_config)
        
        # Save simplified status information (skip the large definitions due to DB limit)
        
        # Commit all changes
        db.session.commit()
        
        logging.info(f"✅ Order status system migration completed successfully!")
        logging.info(f"Updated {updated_count} orders to new status system")
        logging.info(f"New status sequence: {new_status_sequence}")
        
        return True
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"❌ Error during order status migration: {str(e)}")
        return False

if __name__ == "__main__":
    with app.app_context():
        update_order_status_system()