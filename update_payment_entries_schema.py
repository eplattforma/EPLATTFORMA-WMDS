"""
Schema migration for payment_entries table.
Tracks individual payment entries per route stop with PS365 commit status.
"""
import logging
from app import db
from sqlalchemy import text

logger = logging.getLogger(__name__)


def update_payment_entries_schema():
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS payment_entries (
                id SERIAL PRIMARY KEY,
                route_stop_id INTEGER NOT NULL REFERENCES route_stop(route_stop_id) ON DELETE CASCADE,
                method VARCHAR(20) NOT NULL,
                amount NUMERIC(18,2) NOT NULL DEFAULT 0,
                cheque_no VARCHAR(64),
                cheque_date DATE,
                commit_mode VARCHAR(20) NOT NULL,
                doc_type VARCHAR(20) NOT NULL,
                ps_status VARCHAR(20) NOT NULL DEFAULT 'NEW',
                ps_reference VARCHAR(64),
                ps_error TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TIMESTAMP,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """))

        db.session.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_entries_active
            ON payment_entries(route_stop_id)
            WHERE is_active = TRUE
        """))

        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_payment_entries_stop
            ON payment_entries(route_stop_id)
        """))

        db.session.commit()
        logger.info("✅ payment_entries schema update completed successfully")
    except Exception as e:
        db.session.rollback()
        logger.error(f"payment_entries schema update failed: {e}")
        raise
