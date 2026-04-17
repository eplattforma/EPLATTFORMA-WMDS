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
                    review_due_at TIMESTAMP DEFAULT (NOW() + INTERVAL '28 days'),
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

        try:
            db.session.execute(text("""
                ALTER TABLE sku_forecast_override
                ALTER COLUMN review_due_at SET DEFAULT (NOW() + INTERVAL '28 days')
            """))
            db.session.commit()
            logger.info("review_due_at default ensured on sku_forecast_override")
        except Exception as e:
            db.session.rollback()
            logger.warning(f"Could not set review_due_at default: {e}")

        for col, col_type in [
            ("system_forecast_weekly_qty", "NUMERIC(18,6)"),
            ("override_forecast_weekly_qty", "NUMERIC(18,6)"),
            ("final_forecast_source", "VARCHAR(20)"),
        ]:
            try:
                db.session.execute(text(
                    f"ALTER TABLE sku_ordering_snapshot ADD COLUMN IF NOT EXISTS {col} {col_type}"
                ))
                db.session.commit()
                logger.info(f"{col} column ensured on sku_ordering_snapshot")
            except Exception as e:
                db.session.rollback()
                logger.warning(f"Could not add {col} to sku_ordering_snapshot: {e}")

        try:
            db.session.execute(text("""
                ALTER TABLE sku_ordering_snapshot
                ADD CONSTRAINT ck_final_forecast_source
                CHECK (final_forecast_source IN ('system', 'override'))
            """))
            db.session.commit()
            logger.info("ck_final_forecast_source constraint added to sku_ordering_snapshot")
        except Exception as e:
            db.session.rollback()
            if "already exists" in str(e):
                logger.info("ck_final_forecast_source constraint already exists")
            else:
                logger.warning(f"Could not add ck_final_forecast_source constraint: {e}")

        dupes = db.session.execute(text("""
            SELECT item_code_365
            FROM sku_forecast_override
            WHERE is_active = true
            GROUP BY item_code_365
            HAVING COUNT(*) > 1
        """)).fetchall()
        if dupes:
            logger.warning(f"Found {len(dupes)} item(s) with duplicate active overrides, deactivating older rows")
            for (item_code,) in dupes:
                db.session.execute(text("""
                    UPDATE sku_forecast_override
                    SET is_active = false, cleared_at = NOW(), cleared_by = 'system-migration'
                    WHERE item_code_365 = :item_code
                      AND is_active = true
                      AND id != (
                          SELECT id FROM sku_forecast_override
                          WHERE item_code_365 = :item_code AND is_active = true
                          ORDER BY created_at DESC
                          LIMIT 1
                      )
                """), {'item_code': item_code})
            db.session.commit()
            logger.info("Duplicate active overrides cleaned up")

        db.session.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_active_override_per_item
            ON sku_forecast_override (item_code_365)
            WHERE is_active = true
        """))
        db.session.commit()
        logger.info("ux_active_override_per_item partial unique index ensured")

        logger.info("✅ Forecast override schema update completed successfully")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating forecast override schema: {e}")
        raise
