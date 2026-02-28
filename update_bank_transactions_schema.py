"""Migration: Create bank_transactions table for bank statement import matching."""
import logging
from sqlalchemy import text
from app import db

logger = logging.getLogger(__name__)

def update_bank_transactions_schema():
    conn = db.session.connection()
    result = conn.execute(text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'bank_transactions'"
    )).fetchone()
    if result:
        logger.info("bank_transactions table already exists")
        return

    conn.execute(text("""
        CREATE TABLE bank_transactions (
            id SERIAL PRIMARY KEY,
            batch_id VARCHAR(36) NOT NULL,
            txn_date DATE,
            description TEXT,
            reference VARCHAR(200),
            credit NUMERIC(12,2),
            debit NUMERIC(12,2),
            balance NUMERIC(14,2),
            raw_row TEXT,
            matched_allocation_id INTEGER REFERENCES cod_invoice_allocations(id),
            match_status VARCHAR(20) NOT NULL DEFAULT 'UNMATCHED',
            match_confidence VARCHAR(20),
            match_reason VARCHAR(200),
            dismissed BOOLEAN NOT NULL DEFAULT FALSE,
            uploaded_by VARCHAR(64),
            uploaded_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
        )
    """))
    conn.execute(text("CREATE INDEX ix_bank_transactions_batch_id ON bank_transactions(batch_id)"))
    conn.execute(text("CREATE INDEX ix_bank_transactions_matched_alloc ON bank_transactions(matched_allocation_id) WHERE matched_allocation_id IS NOT NULL"))
    db.session.commit()
    logger.info("bank_transactions table created successfully")
