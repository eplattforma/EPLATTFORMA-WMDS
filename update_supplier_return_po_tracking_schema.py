import logging
from app import db
from sqlalchemy import text

logger = logging.getLogger(__name__)


def update_supplier_return_po_tracking_schema():
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS supplier_return_po_tracking (
                id                SERIAL PRIMARY KEY,
                cart_code         VARCHAR(128) NOT NULL,
                po_id_365         VARCHAR(64),
                supplier_code_365 VARCHAR(64)  NOT NULL,
                supplier_name     VARCHAR(255),
                sent_at           TIMESTAMP    NOT NULL DEFAULT NOW(),
                sent_by           VARCHAR(64),
                CONSTRAINT uq_srpt_cart_code UNIQUE (cart_code)
            )
        """))
        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_srpt_supplier
                ON supplier_return_po_tracking (supplier_code_365)
        """))
        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_srpt_sent_at
                ON supplier_return_po_tracking (sent_at)
        """))
        db.session.commit()
        logger.info("supplier_return_po_tracking schema ensured")
    except Exception as e:
        db.session.rollback()
        logger.warning("supplier_return_po_tracking schema update failed (non-fatal): %s", e)
