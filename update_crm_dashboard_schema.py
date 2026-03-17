import logging
from sqlalchemy import text
from app import db

logger = logging.getLogger(__name__)


def update_crm_dashboard_schema():
    with db.engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS crm_customer_profile (
                customer_code_365 VARCHAR(64) PRIMARY KEY,
                classification VARCHAR(50) NULL,
                district VARCHAR(100) NULL,
                area VARCHAR(100) NULL,
                notes TEXT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_by VARCHAR(100) NULL
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_crm_customer_profile_classification ON crm_customer_profile(classification)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_crm_customer_profile_district ON crm_customer_profile(district)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_crm_customer_profile_area ON crm_customer_profile(area)"))

        result = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='crm_customer_profile' AND column_name='assisted_ordering'"
        ))
        if not result.fetchone():
            conn.execute(text(
                "ALTER TABLE crm_customer_profile ADD COLUMN assisted_ordering BOOLEAN NOT NULL DEFAULT false"
            ))
            logger.info("Added assisted_ordering column to crm_customer_profile")

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS crm_task (
                id SERIAL PRIMARY KEY,
                customer_code_365 VARCHAR(64) NOT NULL,
                task_type VARCHAR(30) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
                due_at TIMESTAMPTZ NULL,
                priority VARCHAR(10) NULL,
                notes TEXT NULL,
                assigned_to VARCHAR(100) NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_crm_task_customer ON crm_task(customer_code_365)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_crm_task_status ON crm_task(status)"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS crm_interaction_log (
                id SERIAL PRIMARY KEY,
                customer_code_365 VARCHAR(64) NOT NULL,
                channel VARCHAR(20) NOT NULL,
                outcome VARCHAR(50) NULL,
                message_text TEXT NULL,
                meta_json TEXT NULL,
                created_by VARCHAR(100) NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_crm_interaction_log_customer ON crm_interaction_log(customer_code_365)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_crm_interaction_log_channel ON crm_interaction_log(channel)"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS crm_ordering_review (
                id SERIAL PRIMARY KEY,
                customer_code_365 VARCHAR(64) NOT NULL,
                delivery_date DATE NOT NULL,
                review_state VARCHAR(20) NOT NULL DEFAULT 'waiting',
                outcome_reason VARCHAR(50) NULL,
                expected_this_cycle BOOLEAN NOT NULL DEFAULT false,
                manual_follow_up_flag BOOLEAN NOT NULL DEFAULT false,
                cart_mode VARCHAR(20) NULL,
                review_note TEXT NULL,
                done_at TIMESTAMPTZ NULL,
                done_by VARCHAR(100) NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_crm_ordering_review_cust_date UNIQUE (customer_code_365, delivery_date)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_crm_ordering_review_customer ON crm_ordering_review(customer_code_365)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_crm_ordering_review_state ON crm_ordering_review(review_state)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_crm_ordering_review_delivery ON crm_ordering_review(delivery_date)"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS crm_customer_price_offer_import (
                id SERIAL PRIMARY KEY,
                import_batch_id VARCHAR(64) NOT NULL,
                snapshot_at TIMESTAMPTZ,
                magento_customer_id INTEGER,
                customer_email VARCHAR(255),
                sku VARCHAR(100),
                product_name TEXT,
                rule_code VARCHAR(100),
                rule_name VARCHAR(255),
                rule_description TEXT,
                origin_price NUMERIC(12,4),
                customer_final_price NUMERIC(12,4),
                imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_cpo_import_batch ON crm_customer_price_offer_import(import_batch_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_cpo_import_cust ON crm_customer_price_offer_import(magento_customer_id)"
        ))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS crm_customer_price_offer (
                id SERIAL PRIMARY KEY,
                snapshot_at TIMESTAMPTZ,
                magento_customer_id INTEGER,
                customer_email VARCHAR(255),
                ps_customer_code VARCHAR(64),
                ps_customer_name TEXT,
                sku VARCHAR(100),
                item_code_365 VARCHAR(64),
                item_name VARCHAR(255),
                rule_code VARCHAR(100),
                rule_name VARCHAR(255),
                rule_description TEXT,
                origin_price NUMERIC(12,4),
                customer_final_price NUMERIC(12,4),
                discount_amount NUMERIC(12,4),
                discount_percent NUMERIC(8,2),
                is_linked_customer BOOLEAN NOT NULL DEFAULT false,
                is_linked_item BOOLEAN NOT NULL DEFAULT false,
                import_batch_id VARCHAR(64),
                imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_cpo_cust_sku UNIQUE (magento_customer_id, sku)
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_cpo_ps_customer ON crm_customer_price_offer(ps_customer_code)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_cpo_sku ON crm_customer_price_offer(sku)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_cpo_rule ON crm_customer_price_offer(rule_code)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_cpo_magento ON crm_customer_price_offer(magento_customer_id)"
        ))

        result = conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='crm_customer_price_offer' AND column_name='product_name'"
        ))
        if not result.fetchone():
            conn.execute(text(
                "ALTER TABLE crm_customer_price_offer ADD COLUMN product_name TEXT"
            ))
            logger.info("Added product_name column to crm_customer_price_offer")

        conn.commit()
        logger.info("CRM dashboard schema ensured (crm_customer_profile, crm_task, crm_interaction_log, crm_ordering_review, crm_customer_price_offer)")
