"""Print queue table for the office-PC print bridge.

Jobs are enqueued by the app (delivery slips on the Konica, box labels on
the Deli 750W) and drained by a small agent running on the office PC that
polls /print/agent/poll and sends the returned PDF to the right printer.
"""
import logging

logger = logging.getLogger(__name__)


def ensure_print_jobs_schema(db):
    from sqlalchemy import text
    with db.engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS print_jobs (
                id SERIAL PRIMARY KEY,
                invoice_no VARCHAR(50) NOT NULL,
                doc_type VARCHAR(12) NOT NULL DEFAULT 'slip',
                status VARCHAR(12) NOT NULL DEFAULT 'queued',
                requested_by VARCHAR(64),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                claimed_at TIMESTAMPTZ,
                done_at TIMESTAMPTZ,
                error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0
            )
        """))
        conn.execute(text(
            "ALTER TABLE print_jobs ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_print_jobs_status ON print_jobs (status, id)"
        ))
        conn.commit()
    logger.info("print_jobs schema ensured")
