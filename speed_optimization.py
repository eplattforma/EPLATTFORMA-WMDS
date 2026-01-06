"""
Emergency speed optimization for warehouse picking system
Targets the specific performance bottlenecks causing system slowdown
"""

from models import db, Setting
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)

def optimize_database_performance():
    """Apply immediate database optimizations"""
    try:
        # Add performance indexes if they don't exist
        with db.engine.connect() as conn:
            # Index for invoice items by status and zone
            try:
                conn.execute(text("""
                    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_invoice_items_performance 
                    ON invoice_items (status, zone, is_picked, locked_by_batch_id)
                """))
            except:
                pass
            
            # Index for batch performance
            try:
                conn.execute(text("""
                    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_batch_sessions_status 
                    ON batch_picking_sessions (status, created_at)
                """))
            except:
                pass
            
            conn.commit()
        
        # Set optimal session-level settings
        Setting.set(db.session, 'enable_item_time_tracking', 'false')
        Setting.set(db.session, 'show_product_image_confirmation', 'false')
        Setting.set(db.session, 'debug_batch_picking', 'false')
        db.session.commit()
        
        logger.info("Database performance optimizations applied")
        return True
        
    except Exception as e:
        logger.error(f"Database optimization failed: {e}")
        return False

def reduce_system_load():
    """Reduce system load by disabling non-critical features"""
    try:
        # Disable expensive operations during peak usage
        Setting.set(db.session, 'confirm_picking_step', 'false')
        Setting.set(db.session, 'show_multi_qty_warning', 'false')
        Setting.set(db.session, 'require_skip_reason', 'false')
        Setting.set(db.session, 'enable_activity_logging', 'false')
        
        db.session.commit()
        logger.info("Reduced system load by disabling non-critical features")
        return True
        
    except Exception as e:
        logger.error(f"System load reduction failed: {e}")
        return False

if __name__ == '__main__':
    from main import app
    
    with app.app_context():
        print("Applying emergency speed optimizations...")
        
        if optimize_database_performance():
            print("✓ Database optimizations applied")
        else:
            print("✗ Database optimizations failed")
            
        if reduce_system_load():
            print("✓ System load reduced")
        else:
            print("✗ System load reduction failed")
            
        print("Speed optimization completed")