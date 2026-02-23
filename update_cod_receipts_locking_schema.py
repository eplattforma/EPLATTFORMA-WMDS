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

            idx_exists = db.session.execute(text(
                "SELECT 1 FROM pg_indexes WHERE indexname = 'uq_cod_receipts_stop_non_voided'"
            )).fetchone()
            if not idx_exists:
                db.session.execute(text("""
                    UPDATE cod_receipts SET status = 'VOIDED', void_reason = 'auto-dedup migration'
                    WHERE id IN (
                        SELECT id FROM (
                            SELECT id, ROW_NUMBER() OVER (
                                PARTITION BY route_stop_id
                                ORDER BY created_at DESC NULLS LAST, id DESC
                            ) AS rn
                            FROM cod_receipts
                            WHERE (status IS NULL OR status <> 'VOIDED')
                        ) sub WHERE rn > 1
                    )
                """))
                db.session.execute(text("""
                    UPDATE cod_receipts SET status = 'DRAFT'
                    WHERE status IS NULL
                """))
                db.session.execute(text("""
                    CREATE UNIQUE INDEX uq_cod_receipts_stop_non_voided
                    ON cod_receipts(route_stop_id)
                    WHERE status <> 'VOIDED'
                """))

            db.session.commit()
            logging.info("✅ COD receipts locking schema update completed successfully")

        except Exception as e:
            db.session.rollback()
            logging.error(f"Error updating COD receipts locking schema: {str(e)}")
            raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    update_cod_receipts_locking_schema()
