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

        conn.commit()
        logger.info("CRM dashboard schema ensured (crm_customer_profile, crm_task, crm_interaction_log)")
