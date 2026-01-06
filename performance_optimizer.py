"""
Emergency performance optimizer for critical system slowdown
Applies immediate fixes to restore system responsiveness
"""

from sqlalchemy import text
from models import db, Setting
import logging

logger = logging.getLogger(__name__)

def emergency_cleanup():
    """Remove performance-killing data and optimize queries"""
    try:
        with db.engine.connect() as conn:
            # Clean up old logs aggressively
            result1 = conn.execute(text("DELETE FROM activity_logs WHERE timestamp < NOW() - INTERVAL '12 hours'"))
            
            # Clean up old item tracking records
            result2 = conn.execute(text("DELETE FROM item_time_tracking WHERE created_at < NOW() - INTERVAL '2 days'"))
            
            conn.commit()
            
        logger.info(f"Cleaned {result1.rowcount} old activity logs, {result2.rowcount} old tracking records")
        return True
        
    except Exception as e:
        logger.error(f"Emergency cleanup failed: {e}")
        return False

def disable_performance_killers():
    """Disable features that are causing the system slowdown"""
    try:
        # Disable all non-essential features immediately
        critical_settings = {
            'enable_item_time_tracking': 'false',
            'show_product_image_confirmation': 'false', 
            'debug_batch_picking': 'false',
            'confirm_picking_step': 'false',
            'show_multi_qty_warning': 'false',
            'require_skip_reason': 'false',
            'enable_activity_logging': 'false',
            'show_picker_performance_stats': 'false',
            'enable_location_validation': 'false'
        }
        
        for key, value in critical_settings.items():
            Setting.set(db.session, key, value)
        
        db.session.commit()
        logger.info("Disabled performance-killing features")
        return True
        
    except Exception as e:
        logger.error(f"Failed to disable features: {e}")
        return False

def optimize_queries():
    """Add indexes and optimize database performance"""
    try:
        with db.engine.connect() as conn:
            # Critical indexes for picking operations
            conn.execute(text("""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_emergency_picking 
                ON invoice_items (status, is_picked, zone, corridor) 
                WHERE is_picked = false
            """))
            
            # Batch operation index
            conn.execute(text("""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_emergency_batch 
                ON batch_picking_sessions (status, created_at) 
                WHERE status IN ('Active', 'Paused')
            """))
            
            conn.commit()
            
        logger.info("Applied emergency database indexes")
        return True
        
    except Exception as e:
        logger.error(f"Query optimization failed: {e}")
        return False

if __name__ == '__main__':
    from main import app
    
    with app.app_context():
        print("🚨 EMERGENCY PERFORMANCE OPTIMIZATION")
        print("Applying critical fixes for system slowdown...")
        
        success_count = 0
        
        if emergency_cleanup():
            print("✓ Database cleanup completed")
            success_count += 1
        
        if disable_performance_killers():
            print("✓ Performance-killing features disabled")
            success_count += 1
            
        if optimize_queries():
            print("✓ Database queries optimized")
            success_count += 1
            
        print(f"Applied {success_count}/3 optimizations")
        print("System should be significantly faster now")