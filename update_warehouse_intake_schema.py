"""
Database schema update for Warehouse Intake System
Creates tables for post-delivery case management, invoice routing history, and reroute requests
"""

from app import app, db
from sqlalchemy import text
import logging

def update_warehouse_intake_schema():
    """Create warehouse intake system tables"""
    with app.app_context():
        try:
            # 1. Create invoice_post_delivery_cases table
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS invoice_post_delivery_cases (
                    id BIGSERIAL PRIMARY KEY,
                    invoice_no VARCHAR(50) NOT NULL REFERENCES invoices(invoice_no) ON DELETE CASCADE,
                    route_id BIGINT REFERENCES shipments(id) ON DELETE SET NULL,
                    route_stop_id BIGINT REFERENCES route_stop(route_stop_id) ON DELETE SET NULL,
                    status VARCHAR(50) NOT NULL DEFAULT 'OPEN',
                    reason TEXT,
                    notes TEXT,
                    created_by VARCHAR(100),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            logging.info("Created invoice_post_delivery_cases table")

            # Create indexes for post_delivery_cases
            db.session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_ipdc_status 
                ON invoice_post_delivery_cases(status)
            """))
            
            db.session.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_ipdc_invoice_open
                ON invoice_post_delivery_cases(invoice_no)
                WHERE status IN ('OPEN','INTAKE_RECEIVED','REROUTE_QUEUED')
            """))
            logging.info("Created indexes for invoice_post_delivery_cases")

            # 2. Create invoice_route_history table
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS invoice_route_history (
                    id BIGSERIAL PRIMARY KEY,
                    invoice_no VARCHAR(50) NOT NULL REFERENCES invoices(invoice_no) ON DELETE CASCADE,
                    route_id BIGINT REFERENCES shipments(id) ON DELETE SET NULL,
                    route_stop_id BIGINT REFERENCES route_stop(route_stop_id) ON DELETE SET NULL,
                    action VARCHAR(100) NOT NULL,
                    reason TEXT,
                    notes TEXT,
                    actor_username VARCHAR(100),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            logging.info("Created invoice_route_history table")

            # Create index for route_history
            db.session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_irh_invoice 
                ON invoice_route_history(invoice_no, created_at DESC)
            """))
            logging.info("Created index for invoice_route_history")

            # 3. Create reroute_requests table
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS reroute_requests (
                    id BIGSERIAL PRIMARY KEY,
                    invoice_no VARCHAR(50) NOT NULL REFERENCES invoices(invoice_no) ON DELETE CASCADE,
                    requested_by VARCHAR(100),
                    status VARCHAR(50) NOT NULL DEFAULT 'OPEN',
                    notes TEXT,
                    assigned_route_id BIGINT REFERENCES shipments(id) ON DELETE SET NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ
                )
            """))
            logging.info("Created reroute_requests table")

            # Create index for reroute_requests
            db.session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_rr_status 
                ON reroute_requests(status)
            """))
            logging.info("Created index for reroute_requests")

            db.session.commit()
            logging.info("✅ Warehouse intake schema update completed successfully")
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating warehouse intake schema: {str(e)}")
            raise

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    update_warehouse_intake_schema()
