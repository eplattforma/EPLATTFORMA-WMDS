"""
Update database schema to add batch picking tables
"""
from sqlalchemy import inspect, Column, String, Integer, DateTime, ForeignKey, Boolean, Text, Float
from sqlalchemy.sql import text
import logging

from app import app, db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def update_database_schema():
    """
    Add batch picking tables to the database schema
    """
    with app.app_context():
        inspector = inspect(db.engine)
        
        # Check if batch_picking_sessions table exists
        if not inspector.has_table('batch_picking_sessions'):
            # Create batch_picking_sessions table
            logger.info("Creating batch_picking_sessions table")
            with db.engine.begin() as conn:
                conn.execute(text("""
                    CREATE TABLE batch_picking_sessions (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(100) NOT NULL,
                        zones VARCHAR(500) NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        created_by VARCHAR(64) NOT NULL,
                        assigned_to VARCHAR(64),
                        status VARCHAR(20) DEFAULT 'Created',
                        picking_mode VARCHAR(20) DEFAULT 'Sequential',
                        current_invoice_index INTEGER DEFAULT 0,
                        current_item_index INTEGER DEFAULT 0,
                        FOREIGN KEY (created_by) REFERENCES users(username),
                        FOREIGN KEY (assigned_to) REFERENCES users(username)
                    )
                """))
            logger.info("Created batch_picking_sessions table")
        else:
            # Check if columns exist and add missing ones
            columns = inspector.get_columns('batch_picking_sessions')
            column_names = [column['name'] for column in columns]
            
            missing_columns = []
            if 'picking_mode' not in column_names:
                missing_columns.append(("picking_mode", "VARCHAR(20) DEFAULT 'Sequential'"))
            
            if 'current_invoice_index' not in column_names:
                missing_columns.append(("current_invoice_index", "INTEGER DEFAULT 0"))
                
            if 'current_item_index' not in column_names:
                missing_columns.append(("current_item_index", "INTEGER DEFAULT 0"))
                
            # Add missing columns
            for column_name, column_type in missing_columns:
                logger.info(f"Adding {column_name} column to batch_picking_sessions")
                with db.engine.begin() as conn:
                    conn.execute(text(f"""
                        ALTER TABLE batch_picking_sessions
                        ADD COLUMN {column_name} {column_type}
                    """))
                logger.info(f"Added {column_name} column to batch_picking_sessions")
        
        # Check if batch_session_invoices table exists
        if not inspector.has_table('batch_session_invoices'):
            # Create batch_session_invoices table
            logger.info("Creating batch_session_invoices table")
            with db.engine.begin() as conn:
                conn.execute(text("""
                    CREATE TABLE batch_session_invoices (
                        batch_session_id INTEGER NOT NULL,
                        invoice_no VARCHAR(50) NOT NULL,
                        PRIMARY KEY (batch_session_id, invoice_no),
                        FOREIGN KEY (batch_session_id) REFERENCES batch_picking_sessions(id),
                        FOREIGN KEY (invoice_no) REFERENCES invoices(invoice_no)
                    )
                """))
            logger.info("Created batch_session_invoices table")
            
            # Add is_completed column if it doesn't exist
            with db.engine.begin() as conn:
                conn.execute(text("""
                    ALTER TABLE batch_session_invoices
                    ADD COLUMN is_completed BOOLEAN DEFAULT FALSE
                """))
            logger.info("Added is_completed column to batch_session_invoices table")
        
        # Check if batch_picked_items table exists
        if not inspector.has_table('batch_picked_items'):
            # Create batch_picked_items table
            logger.info("Creating batch_picked_items table")
            with db.engine.begin() as conn:
                conn.execute(text("""
                    CREATE TABLE batch_picked_items (
                        id SERIAL PRIMARY KEY,
                        batch_session_id INTEGER NOT NULL,
                        invoice_no VARCHAR(50) NOT NULL,
                        item_code VARCHAR(50) NOT NULL,
                        picked_qty INTEGER NOT NULL,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (batch_session_id) REFERENCES batch_picking_sessions(id),
                        FOREIGN KEY (invoice_no) REFERENCES invoices(invoice_no)
                    )
                """))
            logger.info("Created batch_picked_items table")
        
        logger.info("Batch picking schema updates completed")

if __name__ == "__main__":
    update_database_schema()