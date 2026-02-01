"""
Route Reconciliation Migration
- Add versioning columns to route_stop_invoice for reroute-safe history
- Add partial unique index for active invoice mappings
- Standardize statuses with CHECK constraint
- Prevent invalid stop completion timestamps
- Create view for active mappings
"""

import logging
from app import db
from sqlalchemy import text

logger = logging.getLogger(__name__)


def run_migration():
    """Execute the route reconciliation database migration"""
    
    logger.info("Starting route reconciliation migration...")
    
    try:
        # A1) Add versioning columns to route_stop_invoice
        logger.info("Adding versioning columns to route_stop_invoice...")
        
        versioning_columns = [
            ("is_active", "boolean NOT NULL DEFAULT true"),
            ("effective_from", "timestamptz NOT NULL DEFAULT now()"),
            ("effective_to", "timestamptz"),
            ("changed_by", "varchar(64)"),
        ]
        
        for col_name, col_def in versioning_columns:
            try:
                db.session.execute(text(f"""
                    ALTER TABLE route_stop_invoice 
                    ADD COLUMN IF NOT EXISTS {col_name} {col_def}
                """))
                logger.info(f"  Added/verified column: {col_name}")
            except Exception as e:
                if "already exists" in str(e).lower():
                    logger.info(f"  Column {col_name} already exists")
                else:
                    raise
        
        db.session.commit()
        
        # A1.2) Drop redundant constraints if they exist
        logger.info("Dropping redundant constraints...")
        
        constraints_to_drop = [
            "route_stop_invoice_route_stop_id_invoice_no_key",
            "route_stop_invoice_invoice_no_unique",
        ]
        
        for constraint in constraints_to_drop:
            try:
                db.session.execute(text(f"""
                    ALTER TABLE route_stop_invoice 
                    DROP CONSTRAINT IF EXISTS {constraint}
                """))
            except Exception:
                pass
        
        # Drop old unique index if exists
        db.session.execute(text("""
            DROP INDEX IF EXISTS route_stop_invoice_invoice_no_unique
        """))
        
        db.session.commit()
        
        # A1.3) Create partial unique index for active mappings
        logger.info("Creating partial unique index for active invoice mappings...")
        
        db.session.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_rsi_active_invoice
            ON route_stop_invoice (invoice_no)
            WHERE is_active = true
        """))
        
        # A1.4) Create useful indexes for reconciliation queries
        logger.info("Creating reconciliation indexes...")
        
        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_rsi_active_stop
            ON route_stop_invoice (route_stop_id)
            WHERE is_active = true
        """))
        
        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_rsi_active_status
            ON route_stop_invoice (status)
            WHERE is_active = true
        """))
        
        db.session.commit()
        
        # A2) Standardize route_stop_invoice.status with CHECK constraint
        logger.info("Adding status CHECK constraint...")
        
        # First, normalize any existing non-standard statuses
        db.session.execute(text("""
            UPDATE route_stop_invoice 
            SET status = UPPER(status)
            WHERE status IS NOT NULL AND status != UPPER(status)
        """))
        
        # Map legacy statuses to new standard
        status_mappings = [
            ("ready_for_dispatch", "PENDING"),
            ("shipped", "OUT_FOR_DELIVERY"),
            ("out_for_delivery", "OUT_FOR_DELIVERY"),
            ("delivered", "DELIVERED"),
            ("delivery_failed", "FAILED"),
            ("returned_to_warehouse", "RETURNED"),
        ]
        
        for old_status, new_status in status_mappings:
            db.session.execute(text(f"""
                UPDATE route_stop_invoice 
                SET status = :new_status
                WHERE LOWER(status) = :old_status
            """), {"old_status": old_status, "new_status": new_status})
        
        db.session.commit()
        
        # Add CHECK constraint
        db.session.execute(text("""
            ALTER TABLE route_stop_invoice
            DROP CONSTRAINT IF EXISTS chk_rsi_status
        """))
        
        db.session.execute(text("""
            ALTER TABLE route_stop_invoice
            ADD CONSTRAINT chk_rsi_status
            CHECK (
                status IS NULL OR
                status IN ('PENDING','OUT_FOR_DELIVERY','DELIVERED','FAILED','PARTIAL','SKIPPED','RETURNED')
            )
        """))
        
        db.session.commit()
        
        # A3) Prevent invalid stop completion timestamps
        logger.info("Adding stop completion constraint...")
        
        db.session.execute(text("""
            ALTER TABLE route_stop
            DROP CONSTRAINT IF EXISTS chk_route_stop_completion
        """))
        
        db.session.execute(text("""
            ALTER TABLE route_stop
            ADD CONSTRAINT chk_route_stop_completion
            CHECK (NOT (delivered_at IS NOT NULL AND failed_at IS NOT NULL))
        """))
        
        db.session.commit()
        
        # A4) Create VIEW for active mappings
        logger.info("Creating active mappings view...")
        
        db.session.execute(text("""
            CREATE OR REPLACE VIEW v_route_stop_invoice_active AS
            SELECT *
            FROM route_stop_invoice
            WHERE is_active = true
        """))
        
        # E2) Create backward-compatible view for shipment_orders
        logger.info("Creating shipment orders compatibility view...")
        
        db.session.execute(text("""
            CREATE OR REPLACE VIEW v_shipment_orders AS
            SELECT
                rs.shipment_id,
                rsi.invoice_no
            FROM route_stop rs
            JOIN route_stop_invoice rsi ON rsi.route_stop_id = rs.route_stop_id
            WHERE rsi.is_active = true
        """))
        
        db.session.commit()
        
        logger.info("Route reconciliation migration completed successfully!")
        return True
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Migration failed: {str(e)}", exc_info=True)
        raise


def check_migration_status():
    """Check if migration has been applied"""
    try:
        result = db.session.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'route_stop_invoice' 
            AND column_name = 'is_active'
        """))
        return result.fetchone() is not None
    except Exception:
        return False


if __name__ == "__main__":
    from app import app
    with app.app_context():
        if not check_migration_status():
            run_migration()
        else:
            print("Migration already applied")
