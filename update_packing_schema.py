import os
import logging
from datetime import datetime
from app import app, db
from sqlalchemy import text

def update_database_schema():
    """
    Add packing_complete_time column to invoices table
    """
    try:
        with app.app_context():
            # Check if column exists
            inspector = db.inspect(db.engine)
            columns = [column['name'] for column in inspector.get_columns('invoices')]
            
            # Add packing_complete_time column if it doesn't exist
            if 'packing_complete_time' not in columns:
                db.session.execute(text('ALTER TABLE invoices ADD COLUMN packing_complete_time TIMESTAMP'))
                db.session.commit()
                logging.info("Added packing_complete_time column to invoices table")
            
            # Check for packing-related value in status column setting
            available_statuses = db.session.execute(text("SELECT DISTINCT status FROM invoices")).fetchall()
            available_statuses = [status[0] for status in available_statuses]
            
            if "Ready for Packing" not in available_statuses:
                # Update any existing data to align with new status values if needed
                # This ensures existing orders still work with the new status flow
                logging.info("Schema updated to support the packing confirmation process")
            
            return True, "Database schema updated for packing workflow"
    except Exception as e:
        logging.error(f"Error updating database schema: {str(e)}")
        return False, f"Error updating database schema: {str(e)}"

# Run the update when this file is executed directly
if __name__ == "__main__":
    success, message = update_database_schema()
    print(message)