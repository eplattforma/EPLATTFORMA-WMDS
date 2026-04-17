import logging
from app import db
from sqlalchemy import text

logger = logging.getLogger(__name__)


def update_forecast_profile_manual_order_schema():
    try:
        for col, col_type in [
            ("manual_order_qty", "NUMERIC(18,6)"),
            ("manual_order_qty_updated_at", "TIMESTAMP"),
            ("manual_order_qty_updated_by", "VARCHAR(100)"),
        ]:
            try:
                db.session.execute(text(
                    f"ALTER TABLE sku_forecast_profile ADD COLUMN IF NOT EXISTS {col} {col_type}"
                ))
                db.session.commit()
                logger.info(f"{col} column ensured on sku_forecast_profile")
            except Exception as e:
                db.session.rollback()
                logger.warning(f"Could not add {col} to sku_forecast_profile: {e}")

        logger.info("✅ Forecast profile manual order schema update completed successfully")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating forecast profile manual order schema: {e}")
        raise
