import logging
from sqlalchemy import text
from app import db

logger = logging.getLogger(__name__)


def update_magento_login_log_schema():
    with db.engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS magento_customer_login_log (
                log_id INTEGER PRIMARY KEY,
                magento_customer_id INTEGER NOT NULL,
                customer_code_365 VARCHAR(50) NULL,
                email VARCHAR(255) NULL,
                first_name VARCHAR(100) NULL,
                last_name VARCHAR(100) NULL,
                last_login_at TIMESTAMPTZ NULL,
                last_logout_at TIMESTAMPTZ NULL,
                imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                source_filename VARCHAR(255) NULL
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_mcll_customer_code_365 ON magento_customer_login_log(customer_code_365)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_mcll_magento_customer_id ON magento_customer_login_log(magento_customer_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_mcll_last_login_at ON magento_customer_login_log(last_login_at)"))
        conn.commit()
        logger.info("Magento login log schema ensured")
