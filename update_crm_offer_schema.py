import logging
from sqlalchemy import text
from app import db

logger = logging.getLogger(__name__)


def ensure_crm_offer_schema():
    with db.engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS crm_customer_offer_import_batch (
                id SERIAL PRIMARY KEY,
                source_name VARCHAR(100) NOT NULL DEFAULT 'magento_customer_price_master',
                snapshot_at TIMESTAMPTZ,
                row_count INTEGER NOT NULL DEFAULT 0,
                imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                imported_by VARCHAR(100),
                status VARCHAR(30) NOT NULL DEFAULT 'done',
                notes TEXT
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_offer_import_batch_snapshot_at ON crm_customer_offer_import_batch(snapshot_at)"
        ))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS crm_customer_offer_raw (
                id SERIAL PRIMARY KEY,
                import_batch_id INTEGER NOT NULL REFERENCES crm_customer_offer_import_batch(id),
                snapshot_at TIMESTAMPTZ,
                customer_id_magento INTEGER,
                customer_email VARCHAR(255),
                sku VARCHAR(100) NOT NULL,
                product_name VARCHAR(255),
                rule_code VARCHAR(100),
                rule_name VARCHAR(255),
                rule_description TEXT,
                origin_price NUMERIC(12,4),
                offer_price NUMERIC(12,4),
                imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_offer_raw_snapshot ON crm_customer_offer_raw(snapshot_at)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_offer_raw_magento_customer ON crm_customer_offer_raw(customer_id_magento)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_offer_raw_email ON crm_customer_offer_raw(customer_email)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_offer_raw_sku ON crm_customer_offer_raw(sku)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_offer_raw_rule ON crm_customer_offer_raw(rule_code)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_offer_raw_batch ON crm_customer_offer_raw(import_batch_id)"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS crm_offer_rule_dim (
                id SERIAL PRIMARY KEY,
                rule_code VARCHAR(100) NOT NULL UNIQUE,
                rule_name VARCHAR(255),
                rule_description TEXT,
                is_active BOOLEAN NOT NULL DEFAULT true,
                first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS crm_customer_offer_current (
                id SERIAL PRIMARY KEY,
                snapshot_at TIMESTAMPTZ NOT NULL,
                customer_id_magento INTEGER,
                customer_email VARCHAR(255),
                customer_code_365 VARCHAR(50),
                sku VARCHAR(100) NOT NULL,
                item_code_365 VARCHAR(100),
                product_name VARCHAR(255),
                brand_name VARCHAR(255),
                supplier_code VARCHAR(100),
                supplier_name VARCHAR(255),
                category_name VARCHAR(255),
                rule_code VARCHAR(100) NOT NULL DEFAULT '__NO_RULE__',
                rule_id INTEGER REFERENCES crm_offer_rule_dim(id),
                rule_name VARCHAR(255),
                origin_price NUMERIC(12,4),
                offer_price NUMERIC(12,4),
                discount_value NUMERIC(12,4),
                discount_percent NUMERIC(12,4),
                cost NUMERIC(12,4),
                gross_profit NUMERIC(12,4),
                gross_margin_percent NUMERIC(12,4),
                margin_status VARCHAR(30),
                sold_qty_4w NUMERIC(12,3) NOT NULL DEFAULT 0,
                sold_value_4w NUMERIC(12,2) NOT NULL DEFAULT 0,
                sold_qty_90d NUMERIC(12,3) NOT NULL DEFAULT 0,
                sold_value_90d NUMERIC(12,2) NOT NULL DEFAULT 0,
                last_sold_at DATE,
                line_status VARCHAR(40),
                is_active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        for col_add in [
            ("crm_customer_offer_current", "brand_name", "VARCHAR(255)"),
            ("crm_customer_offer_current", "supplier_code", "VARCHAR(100)"),
            ("crm_customer_offer_current", "supplier_name", "VARCHAR(255)"),
            ("crm_customer_offer_current", "category_name", "VARCHAR(255)"),
            ("crm_customer_offer_current", "rule_id", "INTEGER REFERENCES crm_offer_rule_dim(id)"),
            ("crm_customer_offer_current", "discount_value", "NUMERIC(12,4)"),
            ("crm_customer_offer_current", "discount_percent", "NUMERIC(12,4)"),
            ("crm_customer_offer_current", "cost", "NUMERIC(12,4)"),
            ("crm_customer_offer_current", "gross_profit", "NUMERIC(12,4)"),
            ("crm_customer_offer_current", "gross_margin_percent", "NUMERIC(12,4)"),
            ("crm_customer_offer_current", "margin_status", "VARCHAR(30)"),
            ("crm_customer_offer_current", "sold_qty_4w", "NUMERIC(12,3) NOT NULL DEFAULT 0"),
            ("crm_customer_offer_current", "sold_value_4w", "NUMERIC(12,2) NOT NULL DEFAULT 0"),
            ("crm_customer_offer_current", "sold_qty_90d", "NUMERIC(12,3) NOT NULL DEFAULT 0"),
            ("crm_customer_offer_current", "sold_value_90d", "NUMERIC(12,2) NOT NULL DEFAULT 0"),
            ("crm_customer_offer_current", "last_sold_at", "DATE"),
            ("crm_customer_offer_current", "line_status", "VARCHAR(40)"),
        ]:
            conn.execute(text(f"""
                DO $$ BEGIN
                    ALTER TABLE {col_add[0]} ADD COLUMN {col_add[1]} {col_add[2]};
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$
            """))

        conn.execute(text("""
            DO $$ BEGIN
                ALTER TABLE crm_customer_offer_current
                    ADD CONSTRAINT uq_offer_current_customer_sku_rule
                    UNIQUE (customer_code_365, sku, rule_code);
            EXCEPTION WHEN duplicate_table OR duplicate_object THEN NULL;
            END $$
        """))
        for widen in [
            ("crm_customer_offer_current", "discount_percent", "NUMERIC(12,4)"),
            ("crm_customer_offer_current", "gross_margin_percent", "NUMERIC(12,4)"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE {widen[0]} ALTER COLUMN {widen[1]} TYPE {widen[2]}"))
            except Exception:
                pass

        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_offer_current_customer ON crm_customer_offer_current(customer_code_365)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_offer_current_item ON crm_customer_offer_current(item_code_365)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_offer_current_rule ON crm_customer_offer_current(rule_code)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_offer_current_snapshot ON crm_customer_offer_current(snapshot_at)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_offer_current_margin ON crm_customer_offer_current(margin_status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_offer_current_line_status ON crm_customer_offer_current(line_status)"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS crm_customer_offer_summary_current (
                customer_code_365 VARCHAR(50) PRIMARY KEY,
                snapshot_at TIMESTAMPTZ,
                has_special_pricing BOOLEAN NOT NULL DEFAULT false,
                active_offer_skus INTEGER NOT NULL DEFAULT 0,
                active_offer_rules INTEGER NOT NULL DEFAULT 0,
                avg_discount_percent NUMERIC(12,4),
                max_discount_percent NUMERIC(12,4),
                avg_gross_margin_percent NUMERIC(12,4),
                margin_risk_skus INTEGER NOT NULL DEFAULT 0,
                negative_margin_skus INTEGER NOT NULL DEFAULT 0,
                offered_skus_bought_4w INTEGER NOT NULL DEFAULT 0,
                offered_skus_bought_90d INTEGER NOT NULL DEFAULT 0,
                offered_skus_not_bought INTEGER NOT NULL DEFAULT 0,
                offer_sales_4w NUMERIC(12,2) NOT NULL DEFAULT 0,
                offer_sales_90d NUMERIC(12,2) NOT NULL DEFAULT 0,
                offer_utilisation_pct NUMERIC(8,4),
                high_discount_unused_skus INTEGER NOT NULL DEFAULT 0,
                top_rule_name VARCHAR(255),
                top_opportunity_count INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        for col_add in [
            ("crm_customer_offer_summary_current", "max_discount_percent", "NUMERIC(12,4)"),
            ("crm_customer_offer_summary_current", "avg_gross_margin_percent", "NUMERIC(12,4)"),
            ("crm_customer_offer_summary_current", "negative_margin_skus", "INTEGER NOT NULL DEFAULT 0"),
            ("crm_customer_offer_summary_current", "offered_skus_bought_90d", "INTEGER NOT NULL DEFAULT 0"),
            ("crm_customer_offer_summary_current", "offer_sales_90d", "NUMERIC(12,2) NOT NULL DEFAULT 0"),
            ("crm_customer_offer_summary_current", "offer_utilisation_pct", "NUMERIC(8,4)"),
            ("crm_customer_offer_summary_current", "high_discount_unused_skus", "INTEGER NOT NULL DEFAULT 0"),
            ("crm_customer_offer_summary_current", "top_rule_name", "VARCHAR(255)"),
            ("crm_customer_offer_summary_current", "top_opportunity_count", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            conn.execute(text(f"""
                DO $$ BEGIN
                    ALTER TABLE {col_add[0]} ADD COLUMN {col_add[1]} {col_add[2]};
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$
            """))

        for widen_sum in [
            ("crm_customer_offer_summary_current", "avg_discount_percent", "NUMERIC(12,4)"),
            ("crm_customer_offer_summary_current", "max_discount_percent", "NUMERIC(12,4)"),
            ("crm_customer_offer_summary_current", "avg_gross_margin_percent", "NUMERIC(12,4)"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE {widen_sum[0]} ALTER COLUMN {widen_sum[1]} TYPE {widen_sum[2]}"))
            except Exception:
                pass

        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_offer_summary_has_special ON crm_customer_offer_summary_current(has_special_pricing)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_offer_summary_risk ON crm_customer_offer_summary_current(margin_risk_skus)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_offer_summary_unused ON crm_customer_offer_summary_current(offered_skus_not_bought)"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS crm_customer_offer_unresolved (
                id SERIAL PRIMARY KEY,
                import_batch_id INTEGER,
                snapshot_at TIMESTAMPTZ,
                customer_id_magento INTEGER,
                customer_email VARCHAR(255),
                customer_code_365 VARCHAR(50),
                sku VARCHAR(100),
                item_code_365 VARCHAR(100),
                rule_code VARCHAR(100),
                issue_type VARCHAR(50) NOT NULL,
                issue_detail TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))

        for col_add in [
            ("crm_customer_offer_unresolved", "import_batch_id", "INTEGER"),
            ("crm_customer_offer_unresolved", "customer_code_365", "VARCHAR(50)"),
            ("crm_customer_offer_unresolved", "item_code_365", "VARCHAR(100)"),
        ]:
            conn.execute(text(f"""
                DO $$ BEGIN
                    ALTER TABLE {col_add[0]} ADD COLUMN {col_add[1]} {col_add[2]};
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$
            """))

        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_offer_unresolved_type ON crm_customer_offer_unresolved(issue_type)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_offer_unresolved_batch ON crm_customer_offer_unresolved(import_batch_id)"))

        conn.commit()

    logger.info("CRM offer intelligence schema ensured")


def bootstrap_offer_settings():
    defaults = {
        "crm_offer_low_margin_pct_threshold": "12",
        "crm_offer_negative_margin_pct_threshold": "0",
        "crm_offer_strong_discount_pct_threshold": "15",
        "crm_offer_cost_source": "cost_price",
        "crm_offer_import_latest_csv_path": "",
        "crm_offer_show_on_dashboard": "1",
        "crm_offer_show_on_review_ordering": "1",
    }
    for key, default_val in defaults.items():
        try:
            existing = db.session.execute(
                text("SELECT 1 FROM settings WHERE key = :k"), {"k": key}
            ).fetchone()
            if not existing:
                db.session.execute(
                    text("INSERT INTO settings (key, value) VALUES (:k, :v)"),
                    {"k": key, "v": default_val},
                )
        except Exception:
            pass
    db.session.commit()
    logger.info("Offer intelligence settings bootstrapped")
