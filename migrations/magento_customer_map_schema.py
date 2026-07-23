"""
Additive migration: magento_customer_map table + vw_customer_magento view.

Maps Magento customer IDs to PS365 customer codes so the rest of the
system can join web-shop data to the PS365 customer master in one place.
Idempotent — safe to run at every boot (dev AND prod).
"""
import logging

from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS magento_customer_map (
  magento_customer_id integer PRIMARY KEY,
  customer_code_365   text NOT NULL,
  magento_group_id    integer,
  magento_group_name  text,
  source_filename     text,
  imported_at         timestamptz NOT NULL DEFAULT now()
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_mcm_code ON magento_customer_map (customer_code_365);
"""

VIEW_SQL = """
CREATE OR REPLACE VIEW vw_customer_magento AS
SELECT c.customer_code_365, c.company_name,
       m.magento_customer_id, m.magento_group_id, m.magento_group_name
FROM ps_customers c
LEFT JOIN magento_customer_map m ON m.customer_code_365 = c.customer_code_365;
"""


def ensure_magento_customer_map_schema():
    """Create magento_customer_map table, index and vw_customer_magento view."""
    db.session.execute(text(TABLE_SQL))
    db.session.execute(text(INDEX_SQL))
    db.session.execute(text(VIEW_SQL))
    db.session.commit()
    logger.info("magento_customer_map table + vw_customer_magento view ensured")
