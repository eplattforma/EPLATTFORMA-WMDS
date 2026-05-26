import logging
from app import db
from sqlalchemy import text

logger = logging.getLogger(__name__)


def update_supplier_returns_stock_cache_schema():
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS supplier_returns_stock_cache (
                item_code_365     VARCHAR(64)   NOT NULL,
                item_name         VARCHAR(255)  NOT NULL DEFAULT '',
                stock_cases       NUMERIC(12,4) NOT NULL DEFAULT 0,
                supplier_code_365 VARCHAR(64)   NOT NULL DEFAULT '',
                supplier_name     VARCHAR(255)  NOT NULL DEFAULT '',
                selling_qty       NUMERIC(10,3),
                cost_price        NUMERIC(12,4),
                last_synced_at    TIMESTAMP     NOT NULL DEFAULT NOW(),
                CONSTRAINT pk_srsc PRIMARY KEY (item_code_365)
            )
        """))
        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_srsc_supplier
                ON supplier_returns_stock_cache (supplier_code_365)
        """))
        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_srsc_stock
                ON supplier_returns_stock_cache (stock_cases)
        """))
        # V8 — barcode for print slip
        db.session.execute(text("""
            ALTER TABLE supplier_returns_stock_cache
            ADD COLUMN IF NOT EXISTS barcode VARCHAR(64)
        """))
        db.session.commit()
        logger.info("supplier_returns_stock_cache schema ensured")
    except Exception as e:
        db.session.rollback()
        logger.warning("supplier_returns_stock_cache schema update failed (non-fatal): %s", e)
