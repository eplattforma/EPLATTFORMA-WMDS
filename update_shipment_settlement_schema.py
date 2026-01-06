"""
Database schema update for Shipment settlement fields
Adds settlement tracking columns to shipments table
"""

from app import app, db
from sqlalchemy import text
import logging

def update_shipment_settlement_schema():
    """Add settlement fields to shipments table"""
    with app.app_context():
        try:
            # List of columns to add
            columns_to_add = [
                ('settlement_status', 'VARCHAR(20) DEFAULT \'PENDING\''),
                ('driver_submitted_at', 'TIMESTAMP'),
                ('cash_expected', 'NUMERIC(12, 2)'),
                ('cash_handed_in', 'NUMERIC(12, 2)'),
                ('cash_variance', 'NUMERIC(12, 2)'),
                ('cash_variance_note', 'TEXT'),
                ('returns_count', 'INTEGER DEFAULT 0'),
                ('returns_weight', 'FLOAT'),
                ('settlement_notes', 'TEXT'),
                ('completion_reason', 'VARCHAR(50)')
            ]
            
            for column_name, column_type in columns_to_add:
                # Check if column exists
                result = db.session.execute(text(f"""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'shipments' AND column_name = '{column_name}'
                """))
                
                if result.fetchone() is None:
                    # Add column
                    db.session.execute(text(f"""
                        ALTER TABLE shipments 
                        ADD COLUMN {column_name} {column_type}
                    """))
                    logging.info(f"Added {column_name} column to shipments table")
                else:
                    logging.info(f"{column_name} column already exists in shipments table")
            
            db.session.commit()
            logging.info("✅ Shipment settlement schema update completed successfully")
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating Shipment settlement schema: {str(e)}")
            raise

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    update_shipment_settlement_schema()
