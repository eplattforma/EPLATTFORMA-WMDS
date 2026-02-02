"""
Database schema update for Route Reconciliation feature
Adds manifest locking fields to route_stop_invoice and creates route_return_handover table
"""

from app import app, db
from sqlalchemy import text
import logging

def update_route_reconciliation_schema():
    """Add manifest locking fields and return handover tracking"""
    with app.app_context():
        try:
            columns_to_add = [
                ('expected_payment_method', 'VARCHAR(20)'),
                ('expected_amount', 'NUMERIC(12, 2)'),
                ('manifest_locked_at', 'TIMESTAMP'),
                ('manifest_locked_by', 'VARCHAR(64) REFERENCES users(username)')
            ]
            
            for column_name, column_type in columns_to_add:
                result = db.session.execute(text(f"""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'route_stop_invoice' AND column_name = '{column_name}'
                """))
                
                if result.fetchone() is None:
                    db.session.execute(text(f"""
                        ALTER TABLE route_stop_invoice 
                        ADD COLUMN {column_name} {column_type}
                    """))
                    logging.info(f"Added {column_name} column to route_stop_invoice table")
                else:
                    logging.info(f"{column_name} column already exists in route_stop_invoice table")
            
            result = db.session.execute(text("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_name = 'route_return_handover'
            """))
            
            if result.fetchone() is None:
                db.session.execute(text("""
                    CREATE TABLE route_return_handover (
                        id SERIAL PRIMARY KEY,
                        route_id INTEGER NOT NULL REFERENCES shipments(id),
                        route_stop_id INTEGER REFERENCES route_stop(route_stop_id),
                        invoice_no VARCHAR(50) NOT NULL REFERENCES invoices(invoice_no),
                        driver_confirmed_at TIMESTAMP,
                        driver_username VARCHAR(64) REFERENCES users(username),
                        warehouse_received_at TIMESTAMP,
                        received_by VARCHAR(64) REFERENCES users(username),
                        packages_count INTEGER,
                        notes TEXT,
                        photo_paths JSONB,
                        created_at TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                """))
                logging.info("Created route_return_handover table")
                
                db.session.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_route_return_handover_route 
                    ON route_return_handover(route_id)
                """))
                db.session.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_route_return_handover_invoice 
                    ON route_return_handover(invoice_no)
                """))
                logging.info("Created indexes for route_return_handover table")
            else:
                logging.info("route_return_handover table already exists")
            
            db.session.commit()
            logging.info("✅ Route reconciliation schema update completed successfully")
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating route reconciliation schema: {str(e)}")
            raise

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    update_route_reconciliation_schema()
