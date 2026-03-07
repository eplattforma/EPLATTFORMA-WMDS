"""
Schema migration for Replenishment MVP tables.
Safe to run multiple times - uses CREATE TABLE IF NOT EXISTS.
"""
import logging
from sqlalchemy import text
from app import db

logger = logging.getLogger(__name__)


def update_replenishment_schema():
    try:
        with db.engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS replenishment_suppliers (
                    id SERIAL PRIMARY KEY,
                    supplier_code VARCHAR(50) NOT NULL UNIQUE,
                    supplier_name VARCHAR(255) NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    sort_order INTEGER NULL,
                    notes TEXT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS replenishment_item_settings (
                    id SERIAL PRIMARY KEY,
                    item_code_365 VARCHAR(64) NOT NULL UNIQUE,
                    case_qty_units NUMERIC(12,2) NULL,
                    safety_days_override NUMERIC(8,2) NULL,
                    min_order_cases NUMERIC(12,2) NULL DEFAULT 1,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    sort_order INTEGER NULL,
                    notes TEXT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS replenishment_runs (
                    id SERIAL PRIMARY KEY,
                    supplier_code VARCHAR(50) NOT NULL,
                    supplier_name VARCHAR(255) NOT NULL,
                    run_date DATE NOT NULL,
                    run_type VARCHAR(20) NOT NULL,
                    receipt_date DATE NOT NULL,
                    include_today_demand BOOLEAN NOT NULL DEFAULT TRUE,
                    status VARCHAR(20) NOT NULL DEFAULT 'draft',
                    created_by VARCHAR(100) NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    notes TEXT NULL
                )
            """))

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS replenishment_run_lines (
                    id SERIAL PRIMARY KEY,
                    run_id BIGINT NOT NULL REFERENCES replenishment_runs(id) ON DELETE CASCADE,
                    item_code_365 VARCHAR(64) NOT NULL,
                    item_name VARCHAR(255) NULL,
                    case_qty_units NUMERIC(12,2) NOT NULL DEFAULT 0,
                    stock_now_units NUMERIC(12,2) NOT NULL DEFAULT 0,
                    reserved_now_units NUMERIC(12,2) NOT NULL DEFAULT 0,
                    ordered_now_units NUMERIC(12,2) NOT NULL DEFAULT 0,
                    on_transfer_now_units NUMERIC(12,2) NOT NULL DEFAULT 0,
                    available_base_units NUMERIC(12,2) NOT NULL DEFAULT 0,
                    pre_receipt_forecast_units NUMERIC(12,2) NOT NULL DEFAULT 0,
                    projected_units_at_receipt NUMERIC(12,2) NOT NULL DEFAULT 0,
                    cover_forecast_units NUMERIC(12,2) NOT NULL DEFAULT 0,
                    safety_stock_units NUMERIC(12,2) NOT NULL DEFAULT 0,
                    raw_needed_units NUMERIC(12,2) NOT NULL DEFAULT 0,
                    suggested_cases NUMERIC(12,2) NOT NULL DEFAULT 0,
                    suggested_units NUMERIC(12,2) NOT NULL DEFAULT 0,
                    final_cases NUMERIC(12,2) NULL,
                    final_units NUMERIC(12,2) NULL,
                    earliest_expiry_date DATE NULL,
                    qty_at_earliest_expiry NUMERIC(12,2) NULL,
                    expiring_within_30_days_units NUMERIC(12,2) NULL,
                    warning_code VARCHAR(50) NULL,
                    warning_text TEXT NULL,
                    explanation_text TEXT NULL,
                    calc_json JSONB NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """))

            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_replenishment_runs_supplier_code
                ON replenishment_runs(supplier_code)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_replenishment_runs_run_date
                ON replenishment_runs(run_date)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_replenishment_run_lines_run_id
                ON replenishment_run_lines(run_id)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_replenishment_run_lines_item_code
                ON replenishment_run_lines(item_code_365)
            """))

            existing = conn.execute(text(
                "SELECT id FROM replenishment_suppliers WHERE supplier_code = 'CORINA_SNACKS'"
            )).fetchone()
            if not existing:
                conn.execute(text("""
                    INSERT INTO replenishment_suppliers (supplier_code, supplier_name, is_active, sort_order, notes, created_at, updated_at)
                    VALUES ('CORINA_SNACKS', 'Corina Snacks Ltd', TRUE, 1,
                            'Placeholder supplier code - update with actual PS365 supplier code', NOW(), NOW())
                """))
                logger.info("Seeded Corina Snacks Ltd into replenishment_suppliers")

            conn.commit()
            logger.info("Replenishment schema update completed successfully")

    except Exception as e:
        logger.error(f"Replenishment schema update failed: {e}")
        raise
