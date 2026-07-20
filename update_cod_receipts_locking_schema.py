"""
Database schema update for COD Receipts locking and document types.
Adds columns for receipt lifecycle: doc_type, status, locking, printing, voiding, reissue tracking.
"""

from app import app, db
from sqlalchemy import text
import logging


def update_cod_receipts_locking_schema():
    """Add locking, doc_type, and lifecycle columns to cod_receipts"""
    with app.app_context():
        try:
            columns_to_add = [
                ('doc_type', "VARCHAR(30) NOT NULL DEFAULT 'official'"),
                ('status', "VARCHAR(20) NOT NULL DEFAULT 'DRAFT'"),
                ('locked_at', 'TIMESTAMPTZ'),
                ('locked_by', 'VARCHAR(64) REFERENCES users(username)'),
                ('print_count', 'INTEGER NOT NULL DEFAULT 0'),
                ('first_printed_at', 'TIMESTAMPTZ'),
                ('last_printed_at', 'TIMESTAMPTZ'),
                ('voided_at', 'TIMESTAMPTZ'),
                ('voided_by', 'VARCHAR(64) REFERENCES users(username)'),
                ('void_reason', 'TEXT'),
                ('replaced_by_cod_receipt_id', 'INTEGER REFERENCES cod_receipts(id)'),
                ('client_request_id', 'VARCHAR(128)'),
                ('ps365_reference_number', 'VARCHAR(128)'),
                ('variance_reason', 'VARCHAR(50)'),
                ('slips_recovered', 'INTEGER'),
                ('ps365_reversed_by', 'VARCHAR(64) REFERENCES users(username)'),
                ('ps365_reversed_at', 'TIMESTAMPTZ'),
                ('ps365_reversal_ref', 'VARCHAR(128)'),
                ('cancellation_requested_at', 'TIMESTAMPTZ'),
                ('cancellation_requested_by', 'VARCHAR(64)'),
            ]

            for column_name, column_type in columns_to_add:
                try:
                    result = db.session.execute(text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'cod_receipts' AND column_name = :col"
                    ), {'col': column_name})

                    if result.fetchone() is None:
                        db.session.execute(text(
                            f"ALTER TABLE cod_receipts ADD COLUMN {column_name} {column_type}"
                        ))
                        logging.info(f"Added {column_name} column to cod_receipts table")
                    else:
                        logging.info(f"{column_name} column already exists in cod_receipts table")
                except Exception as col_err:
                    db.session.rollback()
                    if 'already exists' in str(col_err):
                        logging.info(f"{column_name} column already exists in cod_receipts table")
                    else:
                        raise

            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_cod_receipts_status ON cod_receipts(status)"
            ))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_cod_receipts_doc_type ON cod_receipts(doc_type)"
            ))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_cod_receipts_client_request_id ON cod_receipts(client_request_id)"
            ))

            db.session.execute(text("""
                UPDATE cod_receipts SET status = 'DRAFT'
                WHERE status IS NULL
            """))
            db.session.execute(text("""
                UPDATE cod_receipts SET status = 'VOIDED', void_reason = 'auto-dedup migration'
                WHERE id IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY route_stop_id
                            ORDER BY created_at DESC NULLS LAST, id DESC
                        ) AS rn
                        FROM cod_receipts
                        WHERE status <> 'VOIDED'
                    ) sub WHERE rn > 1
                )
            """))

            db.session.execute(text(
                "DROP INDEX IF EXISTS uq_cod_receipts_stop_non_voided"
            ))

            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS manual_receipt_log (
                    id SERIAL PRIMARY KEY,
                    manual_book_number VARCHAR(50) NOT NULL,
                    driver_username VARCHAR(64) NOT NULL REFERENCES users(username),
                    route_id INTEGER REFERENCES shipments(id),
                    route_stop_id INTEGER REFERENCES route_stop(route_stop_id),
                    customer_code VARCHAR(50),
                    amount NUMERIC(12,2) NOT NULL,
                    reason VARCHAR(50) NOT NULL DEFAULT 'other',
                    note TEXT,
                    matched_cod_receipt_id INTEGER REFERENCES cod_receipts(id),
                    logged_by VARCHAR(64) NOT NULL REFERENCES users(username),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_manual_receipt_driver ON manual_receipt_log(driver_username)"
            ))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_manual_receipt_route ON manual_receipt_log(route_id)"
            ))

            db.session.commit()
            logging.info("✅ COD receipts locking schema update completed successfully")

        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating COD receipts locking schema: {str(e)}")
            raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    update_cod_receipts_locking_schema()
