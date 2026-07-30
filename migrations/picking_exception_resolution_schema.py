"""Add resolution fields to picking_exceptions (credit note / picked correction)."""
import logging
logger = logging.getLogger(__name__)

def ensure_picking_exception_resolution_schema(db):
    from sqlalchemy import text
    with db.engine.connect() as conn:
        for col, typ in [
            ('resolution_action', 'VARCHAR(20)'),
            ('resolution_qty', 'INTEGER'),
            ('resolved_by', 'VARCHAR(64)'),
            ('resolved_at', 'TIMESTAMPTZ'),
        ]:
            try:
                conn.execute(text(
                    f"ALTER TABLE picking_exceptions ADD COLUMN IF NOT EXISTS {col} {typ}"))
                conn.commit()
            except Exception:
                conn.rollback()
    logger.info("picking_exceptions resolution schema ensured")
