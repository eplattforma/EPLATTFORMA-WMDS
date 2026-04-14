import logging
from app import app, db
from sqlalchemy import text

logger = logging.getLogger(__name__)

def update_forecast_profile_baseline_source_schema():
    with app.app_context():
        with db.engine.connect() as conn:
            exists = conn.execute(text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'sku_forecast_profile'
                  AND column_name = 'baseline_source'
            """)).fetchone()
            if exists:
                conn.execute(text("ALTER TABLE sku_forecast_profile ALTER COLUMN baseline_source TYPE VARCHAR(64)"))
                logger.info("Updated baseline_source column type on sku_forecast_profile")
            conn.commit()
        logger.info("Forecast profile baseline_source schema update completed")
