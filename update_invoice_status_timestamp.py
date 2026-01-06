#!/usr/bin/env python3
"""
Add status_updated_at column to invoices table and populate with current timestamp
"""

import os
import logging
from datetime import datetime
from sqlalchemy import create_engine, text, MetaData, Table, Column, DateTime
from sqlalchemy.exc import OperationalError

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger(__name__)

def add_status_timestamp_column():
    """Add status_updated_at column to invoices table"""
    try:
        # Get database URL from environment
        database_url = os.environ.get('DATABASE_URL')
        if not database_url:
            logger.error("DATABASE_URL environment variable not found")
            return False
        
        # Create engine
        engine = create_engine(database_url)
        
        # Check if column already exists
        with engine.connect() as conn:
            # Check if the column exists
            check_column_query = """
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'invoices' AND column_name = 'status_updated_at'
            """
            result = conn.execute(text(check_column_query))
            column_exists = result.fetchone() is not None
            
            if column_exists:
                logger.info("status_updated_at column already exists in invoices table")
                return True
            
            # Add the column
            logger.info("Adding status_updated_at column to invoices table...")
            add_column_query = """
                ALTER TABLE invoices 
                ADD COLUMN status_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            """
            conn.execute(text(add_column_query))
            
            # Update existing invoices with current timestamp
            logger.info("Updating existing invoices with current timestamp...")
            update_query = """
                UPDATE invoices 
                SET status_updated_at = CURRENT_TIMESTAMP 
                WHERE status_updated_at IS NULL
            """
            result = conn.execute(text(update_query))
            updated_count = result.rowcount
            
            # Commit the transaction
            conn.commit()
            
            logger.info(f"✅ Successfully added status_updated_at column and updated {updated_count} existing invoices")
            return True
            
    except OperationalError as e:
        logger.error(f"Database operation failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return False

if __name__ == "__main__":
    success = add_status_timestamp_column()
    if success:
        logger.info("Invoice status timestamp schema update completed successfully")
    else:
        logger.error("Invoice status timestamp schema update failed")
        exit(1)