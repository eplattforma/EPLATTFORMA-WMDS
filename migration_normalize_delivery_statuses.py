"""
Migration script to normalize delivery statuses in the database.

This script updates:
1. Invoice.status - normalize 'returned' to 'returned_to_warehouse'
2. RouteStopInvoice.status - normalize all uppercase values to lowercase

Run this ONCE after deploying the code changes.
"""

from app import app, db
from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def normalize_invoice_statuses():
    """Normalize Invoice.status values"""
    updates = [
        ("returned", "returned_to_warehouse"),
        ("DELIVERED", "delivered"),
        ("FAILED", "delivery_failed"),
        ("DELIVERY_FAILED", "delivery_failed"),
        ("OUT_FOR_DELIVERY", "out_for_delivery"),
        ("SHIPPED", "shipped"),
        ("RETURNED", "returned_to_warehouse"),
        ("RETURNED_TO_WAREHOUSE", "returned_to_warehouse"),
    ]
    
    total_updated = 0
    for old_status, new_status in updates:
        result = db.session.execute(
            text("UPDATE invoices SET status = :new_status WHERE status = :old_status"),
            {"old_status": old_status, "new_status": new_status}
        )
        if result.rowcount > 0:
            logger.info(f"Invoice: Updated {result.rowcount} rows from '{old_status}' to '{new_status}'")
            total_updated += result.rowcount
    
    return total_updated


def normalize_route_stop_invoice_statuses():
    """Normalize RouteStopInvoice.status values"""
    updates = [
        ("OUT_FOR_DELIVERY", "out_for_delivery"),
        ("DELIVERED", "delivered"),
        ("FAILED", "delivery_failed"),
        ("DELIVERY_FAILED", "delivery_failed"),
        ("SHIPPED", "shipped"),
        ("RETURNED", "returned_to_warehouse"),
        ("RETURNED_TO_WAREHOUSE", "returned_to_warehouse"),
    ]
    
    total_updated = 0
    for old_status, new_status in updates:
        result = db.session.execute(
            text("UPDATE route_stop_invoice SET status = :new_status WHERE status = :old_status"),
            {"old_status": old_status, "new_status": new_status}
        )
        if result.rowcount > 0:
            logger.info(f"RouteStopInvoice: Updated {result.rowcount} rows from '{old_status}' to '{new_status}'")
            total_updated += result.rowcount
    
    return total_updated


def add_missing_columns():
    """Add missing columns to shipments table if they don't exist"""
    columns_to_add = [
        ("cash_collected", "NUMERIC(12, 2)"),
        ("settlement_cleared_at", "TIMESTAMP"),
        ("settlement_cleared_by", "VARCHAR(64)"),
    ]
    
    for col_name, col_type in columns_to_add:
        try:
            db.session.execute(text(f"SELECT {col_name} FROM shipments LIMIT 1"))
            logger.info(f"Column 'shipments.{col_name}' already exists")
        except Exception:
            logger.info(f"Adding column 'shipments.{col_name}'")
            db.session.execute(text(f"ALTER TABLE shipments ADD COLUMN {col_name} {col_type}"))
            db.session.commit()


def verify_status_consistency():
    """Verify that all statuses are now consistent (lowercase)"""
    bad_invoice_statuses = db.session.execute(
        text("""
            SELECT DISTINCT status FROM invoices 
            WHERE status ~ '[A-Z]'
            AND status IS NOT NULL
        """)
    ).fetchall()
    
    bad_rsi_statuses = db.session.execute(
        text("""
            SELECT DISTINCT status FROM route_stop_invoice 
            WHERE status ~ '[A-Z]'
            AND status IS NOT NULL
        """)
    ).fetchall()
    
    if bad_invoice_statuses:
        logger.warning(f"Found uppercase Invoice statuses: {[r[0] for r in bad_invoice_statuses]}")
    else:
        logger.info("All Invoice statuses are lowercase")
    
    if bad_rsi_statuses:
        logger.warning(f"Found uppercase RouteStopInvoice statuses: {[r[0] for r in bad_rsi_statuses]}")
    else:
        logger.info("All RouteStopInvoice statuses are lowercase")
    
    return len(bad_invoice_statuses) == 0 and len(bad_rsi_statuses) == 0


def run_migration():
    """Run the full migration"""
    with app.app_context():
        logger.info("=" * 60)
        logger.info("Starting Delivery Status Normalization Migration")
        logger.info("=" * 60)
        
        logger.info("\n1. Adding missing columns to shipments table...")
        add_missing_columns()
        
        logger.info("\n2. Normalizing Invoice statuses...")
        inv_updated = normalize_invoice_statuses()
        logger.info(f"   Total Invoice rows updated: {inv_updated}")
        
        logger.info("\n3. Normalizing RouteStopInvoice statuses...")
        rsi_updated = normalize_route_stop_invoice_statuses()
        logger.info(f"   Total RouteStopInvoice rows updated: {rsi_updated}")
        
        db.session.commit()
        logger.info("\n4. Committed all changes")
        
        logger.info("\n5. Verifying consistency...")
        all_consistent = verify_status_consistency()
        
        logger.info("\n" + "=" * 60)
        if all_consistent:
            logger.info("Migration completed successfully!")
        else:
            logger.warning("Migration completed with warnings - some statuses may still need manual review")
        logger.info("=" * 60)
        
        return all_consistent


if __name__ == "__main__":
    run_migration()
