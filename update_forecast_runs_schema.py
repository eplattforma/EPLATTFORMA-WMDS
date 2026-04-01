"""
Schema migration for ForecastRun validation metadata columns.
Safe to run multiple times - uses ADD COLUMN IF NOT EXISTS.
"""
import logging
from sqlalchemy import text
from app import db

logger = logging.getLogger(__name__)


def update_forecast_runs_schema():
    try:
        with db.engine.connect() as conn:
            cols = [
                ("sales_period_start", "DATE"),
                ("sales_period_end", "DATE"),
                ("sales_total_qty", "NUMERIC(18,2)"),
                ("sales_total_value_ex_vat", "NUMERIC(18,2)"),
            ]
            for col_name, col_type in cols:
                conn.execute(text(
                    f"ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                ))
                logger.info(f"✅ {col_name} column ensured on forecast_runs")
            conn.commit()
        
        from sqlalchemy import inspect
        insp = inspect(db.engine)
        existing_cols = {c["name"] for c in insp.get_columns("forecast_runs")}
        required = {"sales_period_start", "sales_period_end", "sales_total_qty", "sales_total_value_ex_vat"}
        missing = required - existing_cols
        if missing:
            raise RuntimeError(f"forecast_runs is missing columns: {sorted(missing)}")
        
        logger.info("Forecast runs schema update completed successfully")
    except Exception as e:
        logger.error(f"Forecast runs schema update failed: {e}")
        raise
