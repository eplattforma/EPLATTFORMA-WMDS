import logging
from app import app, db
from sqlalchemy import text

logger = logging.getLogger(__name__)

def update_receiving_desktop_schema():
    with app.app_context():
        with db.engine.connect() as conn:
            for col_name, col_def in [
                ('input_qty', 'NUMERIC(12,3)'),
                ('input_unit_type', 'VARCHAR(30)'),
                ('conversion_factor', 'NUMERIC(12,4)'),
            ]:
                exists = conn.execute(text("""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'receiving_lines'
                      AND column_name = :col
                """), {"col": col_name}).fetchone()
                if not exists:
                    conn.execute(text(f"ALTER TABLE receiving_lines ADD COLUMN {col_name} {col_def}"))
                    logger.info("Added %s column to receiving_lines", col_name)
                else:
                    logger.info("%s column already exists in receiving_lines", col_name)
            conn.commit()
        logger.info("Receiving desktop schema update completed")
