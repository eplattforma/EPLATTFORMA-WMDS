import logging
from sqlalchemy import text
from app import db

logger = logging.getLogger(__name__)


def update_magento_last_login_current_schema():
    with db.engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS magento_customer_last_login_current (
                customer_code_365 VARCHAR(50) PRIMARY KEY,
                magento_customer_id INTEGER NULL,
                last_login_at TIMESTAMPTZ NULL,
                last_logout_at TIMESTAMPTZ NULL,
                email VARCHAR(255) NULL,
                first_name VARCHAR(100) NULL,
                last_name VARCHAR(100) NULL,
                imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                source_filename VARCHAR(255) NULL
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_mcllc_magento_customer_id
            ON magento_customer_last_login_current(magento_customer_id)
        """))
        conn.commit()
        logger.info("Magento last login CURRENT schema ensured")
