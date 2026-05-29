"""
Adds 'planned' as a valid status in cooler_box_items.
Also ensures the id surrogate PK column exists.
Run once on startup via main.py.
"""
import logging
logger = logging.getLogger(__name__)

def update_cooler_schema():
    try:
        from app import db
        from sqlalchemy import text

        try:
            db.session.execute(text(
                "ALTER TABLE cooler_box_items "
                "DROP CONSTRAINT IF EXISTS cooler_box_items_status_check"
            ))
            db.session.execute(text(
                "ALTER TABLE cooler_box_items "
                "ADD CONSTRAINT cooler_box_items_status_check "
                "CHECK (status IN ('planned', 'picked', 'exception'))"
            ))
            db.session.commit()
            logger.info("cooler_box_items status constraint updated to include 'planned'")
        except Exception as e:
            db.session.rollback()
            logger.warning("Could not update cooler_box_items constraint (may not exist): %s", e)

        try:
            db.session.execute(text(
                "ALTER TABLE cooler_box_items "
                "ADD COLUMN IF NOT EXISTS id SERIAL"
            ))
            db.session.commit()
            logger.info("cooler_box_items.id column ensured")
        except Exception as e:
            db.session.rollback()
            logger.warning("cooler_box_items id column: %s", e)

        logger.info("update_cooler_schema complete")
    except Exception as e:
        logger.error("update_cooler_schema failed: %s", e)
