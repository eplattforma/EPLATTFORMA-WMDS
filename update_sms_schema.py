import logging
from app import db
from sqlalchemy import text

logger = logging.getLogger(__name__)

def update_sms_schema():
    try:
        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS sms_template (
                id BIGSERIAL PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                sender_title TEXT,
                body TEXT NOT NULL,
                force_unicode BOOLEAN NOT NULL DEFAULT FALSE,
                is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                allowed_roles TEXT[] NOT NULL DEFAULT ARRAY['admin','warehouse_manager','crm_admin'],
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        db.session.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_sms_template_enabled ON sms_template(is_enabled)
        """))

        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS sms_log (
                id BIGSERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_by_username TEXT,
                context_type TEXT,
                context_id TEXT,
                template_code TEXT,
                customer_code_365 TEXT,
                customer_name TEXT,
                mobile_number TEXT NOT NULL,
                sender_title TEXT,
                batch_id TEXT,
                unicode_mode BOOLEAN NOT NULL DEFAULT FALSE,
                message_text TEXT NOT NULL,
                provider_status TEXT NOT NULL,
                provider_message_id TEXT,
                provider_error_code INT,
                provider_raw_response TEXT,
                dlr_status TEXT,
                dlr_received_at TIMESTAMPTZ,
                dlr_raw_xml TEXT
            )
        """))
        db.session.execute(text("CREATE INDEX IF NOT EXISTS ix_sms_log_created_at ON sms_log(created_at DESC)"))
        db.session.execute(text("CREATE INDEX IF NOT EXISTS ix_sms_log_mobile ON sms_log(mobile_number)"))
        db.session.execute(text("CREATE INDEX IF NOT EXISTS ix_sms_log_batch ON sms_log(batch_id)"))

        db.session.execute(text("""
            INSERT INTO sms_template (code, title, body, force_unicode)
            VALUES
            ('DELIVERY_TODAY', 'Delivery Today',
             E'Καλημέρα {{customer_name}}, η παράδοση σας είναι προγραμματισμένη για {{delivery_date}}. \u2014 EPLATTFORMA',
             TRUE),
            ('PAYMENT_DUE', 'Payment Reminder',
             E'Reminder: Invoice {{invoice_no}} amount \u20ac{{amount_due}} due {{due_date}}. \u2014 EPLATTFORMA',
             FALSE)
            ON CONFLICT (code) DO NOTHING
        """))

        db.session.commit()
        logger.info("✅ SMS schema update completed successfully")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating SMS schema: {e}")
        raise
