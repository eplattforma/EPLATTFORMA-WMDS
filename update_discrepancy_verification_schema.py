"""
Database schema update for Discrepancy Verification and Credit Note workflow
Adds warehouse verification columns to delivery_discrepancies and indexes for route_return_handover
"""

from app import app, db
from sqlalchemy import text
import logging

def update_discrepancy_verification_schema():
    """Add warehouse verification and credit note columns to delivery_discrepancies"""
    with app.app_context():
        try:
            columns_to_add = [
                ('warehouse_checked_by', 'VARCHAR(64) REFERENCES users(username)'),
                ('warehouse_checked_at', 'TIMESTAMP'),
                ('warehouse_result', 'VARCHAR(30)'),
                ('warehouse_note', 'TEXT'),
                ('credit_note_required', 'BOOLEAN DEFAULT FALSE'),
                ('credit_note_no', 'VARCHAR(50)'),
                ('credit_note_amount', 'NUMERIC(12, 2)'),
                ('credit_note_created_at', 'TIMESTAMP')
            ]
            
            for column_name, column_type in columns_to_add:
                result = db.session.execute(text(f"""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'delivery_discrepancies' AND column_name = '{column_name}'
                """))
                
                if result.fetchone() is None:
                    db.session.execute(text(f"""
                        ALTER TABLE delivery_discrepancies 
                        ADD COLUMN {column_name} {column_type}
                    """))
                    logging.info(f"Added {column_name} column to delivery_discrepancies table")
                else:
                    logging.info(f"{column_name} column already exists in delivery_discrepancies table")
            
            result = db.session.execute(text("""
                SELECT indexname FROM pg_indexes 
                WHERE indexname = 'ux_return_handover_route_invoice'
            """))
            
            if result.fetchone() is None:
                db.session.execute(text("""
                    CREATE UNIQUE INDEX ux_return_handover_route_invoice
                    ON route_return_handover(route_id, invoice_no)
                """))
                logging.info("Created unique index ux_return_handover_route_invoice")
            else:
                logging.info("Index ux_return_handover_route_invoice already exists")
            
            result = db.session.execute(text("""
                SELECT indexname FROM pg_indexes 
                WHERE indexname = 'ix_return_handover_driver_pending'
            """))
            
            if result.fetchone() is None:
                db.session.execute(text("""
                    CREATE INDEX ix_return_handover_driver_pending
                    ON route_return_handover(route_id, driver_confirmed_at, warehouse_received_at)
                """))
                logging.info("Created index ix_return_handover_driver_pending")
            else:
                logging.info("Index ix_return_handover_driver_pending already exists")
            
            result = db.session.execute(text("""
                SELECT indexname FROM pg_indexes 
                WHERE indexname = 'ix_dd_invoice_status'
            """))
            
            if result.fetchone() is None:
                db.session.execute(text("""
                    CREATE INDEX ix_dd_invoice_status
                    ON delivery_discrepancies(invoice_no, status, is_validated, is_resolved)
                """))
                logging.info("Created index ix_dd_invoice_status")
            else:
                logging.info("Index ix_dd_invoice_status already exists")
            
            db.session.commit()
            logging.info("✅ Discrepancy verification schema update completed successfully")
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating discrepancy verification schema: {str(e)}")
            raise

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    update_discrepancy_verification_schema()
