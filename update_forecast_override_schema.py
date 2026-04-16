import logging
from app import db
from sqlalchemy import text

logger = logging.getLogger(__name__)


def update_forecast_override_schema():
    try:
        try:
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS sku_forecast_override (
                    id BIGSERIAL PRIMARY KEY,
                    item_code_365 VARCHAR(64) NOT NULL,
                    override_weekly_qty NUMERIC(18,6) NOT NULL,
                    reason_code VARCHAR(50),
                    reason_note TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    created_by VARCHAR(100),
                    review_due_at TIMESTAMP,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    cleared_at TIMESTAMP,
                    cleared_by VARCHAR(100),
                    last_reviewed_at TIMESTAMP,
                    last_reviewed_by VARCHAR(100)
                )
            """))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            if "already exists" in str(e):
                logger.info("sku_forecast_override table already exists")
            else:
                raise

        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_sku_forecast_override_item_active
            ON sku_forecast_override (item_code_365, is_active)
        """))
        db.session.commit()

        for col, col_type in [
            ("system_forecast_weekly_qty", "NUMERIC(18,6)"),
            ("override_forecast_weekly_qty", "NUMERIC(18,6)"),
            ("final_forecast_source", "VARCHAR(20)"),
        ]:
            try:
                db.session.execute(text(
                    f"ALTER TABLE sku_ordering_snapshot ADD COLUMN IF NOT EXISTS {col} {col_type}"
                ))
                logger.info(f"{col} column ensured on sku_ordering_snapshot")
            except Exception:
                pass

        db.session.commit()
        logger.info("✅ Forecast override schema update completed successfully")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating forecast override schema: {e}")
        raise
