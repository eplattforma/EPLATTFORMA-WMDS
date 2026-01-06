"""
Query optimization layer for picking confirmation
Implements caching and optimized queries to speed up picking operations
"""

from functools import lru_cache
from models import db, Setting, InvoiceItem
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)

class PickingQueryOptimizer:
    """Optimizes database queries for picking operations"""
    
    @staticmethod
    @lru_cache(maxsize=32)
    def get_cached_setting(key, default_value='true'):
        """Cache frequently accessed settings to reduce database hits"""
        try:
            return Setting.get(db.session, key, default_value)
        except Exception as e:
            logger.warning(f"Failed to get setting {key}: {e}")
            return default_value
    
    @staticmethod
    def get_optimized_invoice_items(invoice_no):
        """Get invoice items with batch lock status in a single optimized query"""
        query = text("""
            SELECT 
                ii.id, ii.invoice_no, ii.item_code, ii.item_name, ii.location, 
                ii.zone, ii.corridor, ii.qty, ii.unit_type, ii.pack,
                ii.is_picked, ii.picked_qty, ii.pick_status, ii.locked_by_batch_id,
                ii.barcode, ii.exp_time,
                CASE 
                    WHEN ii.locked_by_batch_id IS NOT NULL AND bps.status IN ('Active', 'Paused') THEN true
                    ELSE false
                END as is_batch_locked,
                bps.name as batch_name
            FROM invoice_items ii
            LEFT JOIN batch_picking_sessions bps ON ii.locked_by_batch_id = bps.id
            WHERE ii.invoice_no = :invoice_no
            ORDER BY 
                CASE 
                    WHEN ii.pick_status = 'skipped_pending' THEN 2
                    WHEN ii.is_picked = true THEN 3
                    ELSE 1
                END,
                ii.zone, ii.corridor, ii.location
        """)
        
        result = db.session.execute(query, {'invoice_no': invoice_no})
        return result.fetchall()
    
    @staticmethod
    def get_batch_items_optimized(batch_id):
        """Get batch items in a single optimized query"""
        query = text("""
            SELECT 
                ii.invoice_no, ii.item_code, ii.item_name, ii.location,
                ii.zone, ii.corridor, ii.qty, ii.unit_type, ii.pack,
                ii.is_picked, ii.picked_qty, ii.barcode,
                i.customer_name, i.total_items, i.total_weight, i.routing
            FROM invoice_items ii
            JOIN batch_session_invoices bsi ON ii.invoice_no = bsi.invoice_no
            JOIN invoices i ON ii.invoice_no = i.invoice_no
            WHERE bsi.batch_session_id = :batch_id
            AND (ii.locked_by_batch_id = :batch_id OR ii.locked_by_batch_id IS NULL)
            AND ii.is_picked = false
            ORDER BY ii.zone, ii.corridor, ii.location, ii.item_code
        """)
        
        result = db.session.execute(query, {'batch_id': batch_id})
        return result.fetchall()

def disable_expensive_operations():
    """Temporarily disable expensive operations during picking"""
    try:
        # Disable time tracking for performance during peak usage
        Setting.set(db.session, 'enable_item_time_tracking', 'false')
        
        # Disable product image loading during confirmation for speed
        Setting.set(db.session, 'show_product_image_confirmation', 'false')
        
        # Reduce logging verbosity
        Setting.set(db.session, 'debug_batch_picking', 'false')
        
        db.session.commit()
        logger.info("Disabled expensive operations for better picking performance")
        return True
        
    except Exception as e:
        logger.error(f"Failed to disable expensive operations: {e}")
        db.session.rollback()
        return False

def enable_performance_mode():
    """Enable performance mode for picking operations"""
    try:
        # Set optimized picking settings
        Setting.set(db.session, 'confirm_picking_step', 'false')  # Skip confirmation step
        Setting.set(db.session, 'show_multi_qty_warning', 'false')  # Reduce warnings
        Setting.set(db.session, 'require_skip_reason', 'false')  # Streamline skipping
        
        db.session.commit()
        logger.info("Performance mode enabled for picking operations")
        return True
        
    except Exception as e:
        logger.error(f"Failed to enable performance mode: {e}")
        db.session.rollback()
        return False