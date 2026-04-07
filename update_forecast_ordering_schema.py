import logging
from sqlalchemy import text
from app import app, db


def update_forecast_ordering_schema():
    with app.app_context():
        with db.engine.begin() as conn:

            def add_column_if_missing(table, column, ddl):
                result = conn.execute(text(f"""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name='{table}'
                      AND column_name='{column}'
                """))
                if not result.fetchone():
                    conn.execute(text(ddl))
                    logging.info(f"Added {table}.{column}")

            add_column_if_missing(
                "sku_forecast_profile",
                "target_weeks_of_stock",
                "ALTER TABLE sku_forecast_profile ADD COLUMN target_weeks_of_stock numeric(12,4) NOT NULL DEFAULT 4",
            )

            add_column_if_missing(
                "sku_forecast_profile",
                "target_weeks_updated_at",
                "ALTER TABLE sku_forecast_profile ADD COLUMN target_weeks_updated_at timestamp null",
            )

            add_column_if_missing(
                "sku_forecast_profile",
                "target_weeks_updated_by",
                "ALTER TABLE sku_forecast_profile ADD COLUMN target_weeks_updated_by varchar(100) null",
            )

            add_column_if_missing(
                "sku_forecast_profile",
                "seeded_cap_applied",
                "ALTER TABLE sku_forecast_profile ADD COLUMN seeded_cap_applied boolean NOT NULL DEFAULT false",
            )

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS sku_ordering_snapshot (
                    id bigserial primary key,
                    item_code_365 varchar(64) not null,
                    snapshot_type varchar(20) not null default 'manual',
                    snapshot_at timestamp not null default now(),
                    created_by varchar(100) null,
                    forecast_run_id bigint null,
                    forecast_calculated_at timestamp null,
                    target_weeks_of_stock numeric(12,4) not null default 4,
                    lead_time_days numeric(12,4) not null default 0,
                    review_cycle_days numeric(12,4) not null default 1,
                    buffer_days numeric(12,4) not null default 0,
                    base_forecast_weekly_qty numeric(18,6) not null default 0,
                    trend_adjusted_weekly_qty numeric(18,6) not null default 0,
                    final_forecast_weekly_qty numeric(18,6) not null default 0,
                    final_forecast_daily_qty numeric(18,6) not null default 0,
                    on_hand_qty numeric(18,6) not null default 0,
                    incoming_qty numeric(18,6) not null default 0,
                    reserved_qty numeric(18,6) not null default 0,
                    net_available_qty numeric(18,6) not null default 0,
                    target_stock_qty numeric(18,6) not null default 0,
                    raw_recommended_order_qty numeric(18,6) not null default 0,
                    rounded_order_qty numeric(18,6) not null default 0,
                    supplier_code varchar(50) null,
                    order_multiple numeric(12,4) null,
                    min_order_qty numeric(12,4) null,
                    explanation_json jsonb null
                )
            """))

            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_sku_ordering_snapshot_item_time
                ON sku_ordering_snapshot (item_code_365, snapshot_at)
            """))

            logging.info("Forecast ordering schema update completed")