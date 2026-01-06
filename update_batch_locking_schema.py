#!/usr/bin/env python3
"""
Add batch locking column to invoice_items table for preventing picking conflicts
"""
import logging
from sqlalchemy import text
from main import app
from app import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def update_batch_locking_schema():
    """Add locked_by_batch_id column to invoice_items table"""
    with app.app_context():
        try:
            # Check if column already exists
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'invoice_items' 
                AND column_name = 'locked_by_batch_id'
            """))
            
            if result.fetchone():
                logger.info("locked_by_batch_id column already exists in invoice_items")
                return
            
            # Add the new column
            db.session.execute(text("""
                ALTER TABLE invoice_items 
                ADD COLUMN locked_by_batch_id INTEGER DEFAULT NULL
            """))
            
            # Add foreign key constraint
            db.session.execute(text("""
                ALTER TABLE invoice_items 
                ADD CONSTRAINT fk_locked_by_batch_id 
                FOREIGN KEY (locked_by_batch_id) 
                REFERENCES batch_picking_sessions(id) 
                ON DELETE SET NULL
            """))
            
            db.session.commit()
            logger.info("✅ Added locked_by_batch_id column to invoice_items table")
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"❌ Error updating batch locking schema: {str(e)}")
            raise

if __name__ == "__main__":
    update_batch_locking_schema()