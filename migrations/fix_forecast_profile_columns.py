from sqlalchemy import text
from app import db

def run():
    with db.engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE sku_forecast_profile 
            ALTER COLUMN forecast_method TYPE VARCHAR(128);
        """))

        conn.execute(text("""
            ALTER TABLE sku_forecast_profile 
            ALTER COLUMN seasonality_source TYPE VARCHAR(128);
        """))

        conn.execute(text("""
            ALTER TABLE sku_forecast_profile 
            ALTER COLUMN seed_source TYPE VARCHAR(128);
        """))

        conn.execute(text("""
            ALTER TABLE sku_forecast_profile 
            ALTER COLUMN analogue_level TYPE VARCHAR(128);
        """))

        conn.execute(text("""
            ALTER TABLE sku_forecast_profile 
            ALTER COLUMN baseline_source TYPE VARCHAR(128);
        """))

        conn.commit()
