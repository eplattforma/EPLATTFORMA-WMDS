"""Add confirmed_unprinted_at / confirmed_unprinted_at_route_submit to CODReceipt."""
import logging
logger = logging.getLogger(__name__)

def ensure_confirmed_unprinted_schema(db):
    from sqlalchemy import text
    with db.engine.connect() as conn:
        for col, typ in [
            ('confirmed_unprinted_at', 'TIMESTAMP'),
            ('confirmed_unprinted_by', 'VARCHAR(64)'),
        ]:
            try:
                conn.execute(text(
                    f"ALTER TABLE cod_receipts ADD COLUMN IF NOT EXISTS {col} {typ}"))
                conn.commit()
            except Exception:
                conn.rollback()
    logger.info("confirmed_unprinted_at schema ensured")
