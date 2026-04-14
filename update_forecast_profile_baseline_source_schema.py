import logging
from app import app, db
from sqlalchemy import text

logger = logging.getLogger(__name__)

def update_forecast_profile_baseline_source_schema():
    with app.app_context():
        with db.engine.connect() as conn:
            for column_name in ['forecast_method', 'seasonality_source', 'seed_source', 'analogue_level', 'baseline_source']:
                exists = conn.execute(text("""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'sku_forecast_profile'
                      AND column_name = :column_name
                """), {'column_name': column_name}).fetchone()
                if exists:
                    conn.execute(text(f"ALTER TABLE sku_forecast_profile ALTER COLUMN {column_name} TYPE VARCHAR(64)"))
                    logger.info(f"Updated {column_name} column type on sku_forecast_profile")
            conn.commit()
        logger.info("Forecast profile schema update completed")
