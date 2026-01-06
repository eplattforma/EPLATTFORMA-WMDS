"""
Update database schema to support 'Skip and Collect Later' functionality
"""
from sqlalchemy import Column, String, DateTime, Text
from sqlalchemy.sql import text
from app import app, db
from models import InvoiceItem
import logging

def update_database_schema():
    """
    Add new columns to support the 'Skip and Collect Later' functionality
    """
    with app.app_context():
        try:
            # Check if skip_reason column exists
            inspector = db.inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('invoice_items')]
            
            changes_made = False
            
            # Add skip_reason column if it doesn't exist
            if 'skip_reason' not in columns:
                db.session.execute(text(
                    "ALTER TABLE invoice_items ADD COLUMN skip_reason TEXT"
                ))
                changes_made = True
                logging.info("Added skip_reason column to invoice_items table")
            
            # Add skip_timestamp column if it doesn't exist
            if 'skip_timestamp' not in columns:
                db.session.execute(text(
                    "ALTER TABLE invoice_items ADD COLUMN skip_timestamp TIMESTAMP"
                ))
                changes_made = True
                logging.info("Added skip_timestamp column to invoice_items table")
                
            # Add skip_count column if it doesn't exist
            if 'skip_count' not in columns:
                db.session.execute(text(
                    "ALTER TABLE invoice_items ADD COLUMN skip_count INTEGER DEFAULT 0"
                ))
                changes_made = True
                logging.info("Added skip_count column to invoice_items table")
            
            db.session.commit()
            
            if changes_made:
                logging.info("Database schema updated for skip and collect functionality")
            else:
                logging.info("Skip and collect schema updates already applied")
                
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating schema for skip functionality: {str(e)}")
            raise

if __name__ == "__main__":
    update_database_schema()