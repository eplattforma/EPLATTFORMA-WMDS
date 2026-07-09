"""
Additive migration: create dw_credit_note table and update pbi_fact_sales view
to UNION credit notes as negative rows so net_sales = SUM(line_total_excl) is correct.

Safe to run multiple times (fully idempotent).
"""
import logging

logger = logging.getLogger(__name__)


_PBI_FACT_SALES_VIEW = """
CREATE OR REPLACE VIEW pbi_fact_sales AS
-- ── Regular sale / return lines from the loyalty POS ─────────────────
SELECT
    l.id AS line_id,
    h.invoice_no_365 AS invoice_no,
    h.invoice_type,
    COALESCE(h.invoice_date_local, h.invoice_date_utc0) AS invoice_date,
    h.customer_code_365 AS customer_code,
    h.store_code_365 AS store_code,
    h.user_code_365 AS salesperson_code,
    l.item_code_365 AS item_code,
    l.line_number,
    l.quantity,
    l.price_excl,
    l.price_incl,
    l.discount_percent,
    l.vat_percent,
    l.line_total_excl,
    l.line_total_discount,
    l.line_total_vat,
    l.line_total_incl,
    (COALESCE(l.line_total_incl, 0) - COALESCE(l.line_total_vat, 0)) AS line_net_value,
    EXTRACT(YEAR  FROM COALESCE(h.invoice_date_local, h.invoice_date_utc0)) AS year,
    EXTRACT(MONTH FROM COALESCE(h.invoice_date_local, h.invoice_date_utc0)) AS month,
    EXTRACT(QUARTER FROM COALESCE(h.invoice_date_local, h.invoice_date_utc0)) AS quarter,
    TO_CHAR(COALESCE(h.invoice_date_local, h.invoice_date_utc0), 'YYYY-MM') AS year_month,
    TO_CHAR(COALESCE(h.invoice_date_local, h.invoice_date_utc0), 'Day') AS day_of_week,
    EXTRACT(DOW  FROM COALESCE(h.invoice_date_local, h.invoice_date_utc0)) AS day_of_week_no
FROM dw_invoice_line l
JOIN dw_invoice_header h ON h.invoice_no_365 = l.invoice_no_365

UNION ALL

-- ── Header/line reconciliation adjustments ───────────────────────────
-- Some invoices have missing lines or line totals that do not include the
-- invoice-level discount. This arm adds one pseudo-line per invoice with the
-- difference (header net − sum of lines) so SUM(line_total_excl) always ties
-- out to Powersoft's header figures.
SELECT
    NULL                                             AS line_id,
    h.invoice_no_365                                 AS invoice_no,
    h.invoice_type,
    COALESCE(h.invoice_date_local, h.invoice_date_utc0) AS invoice_date,
    h.customer_code_365                              AS customer_code,
    h.store_code_365                                 AS store_code,
    h.user_code_365                                  AS salesperson_code,
    NULL                                             AS item_code,
    0                                                AS line_number,
    0                                                AS quantity,
    NULL                                             AS price_excl,
    NULL                                             AS price_incl,
    NULL                                             AS discount_percent,
    NULL                                             AS vat_percent,
    (COALESCE(h.total_sub,0) - COALESCE(h.total_discount,0)) - COALESCE(la.lines_excl, 0) AS line_total_excl,
    NULL                                             AS line_total_discount,
    0                                                AS line_total_vat,
    (COALESCE(h.total_sub,0) - COALESCE(h.total_discount,0)) - COALESCE(la.lines_excl, 0) AS line_total_incl,
    (COALESCE(h.total_sub,0) - COALESCE(h.total_discount,0)) - COALESCE(la.lines_excl, 0) AS line_net_value,
    EXTRACT(YEAR  FROM COALESCE(h.invoice_date_local, h.invoice_date_utc0)) AS year,
    EXTRACT(MONTH FROM COALESCE(h.invoice_date_local, h.invoice_date_utc0)) AS month,
    EXTRACT(QUARTER FROM COALESCE(h.invoice_date_local, h.invoice_date_utc0)) AS quarter,
    TO_CHAR(COALESCE(h.invoice_date_local, h.invoice_date_utc0), 'YYYY-MM') AS year_month,
    TO_CHAR(COALESCE(h.invoice_date_local, h.invoice_date_utc0), 'Day') AS day_of_week,
    EXTRACT(DOW  FROM COALESCE(h.invoice_date_local, h.invoice_date_utc0)) AS day_of_week_no
FROM dw_invoice_header h
LEFT JOIN (
    SELECT invoice_no_365, SUM(line_total_excl) AS lines_excl
    FROM dw_invoice_line
    GROUP BY invoice_no_365
) la ON la.invoice_no_365 = h.invoice_no_365
WHERE ABS((COALESCE(h.total_sub,0) - COALESCE(h.total_discount,0)) - COALESCE(la.lines_excl, 0)) > 0.005

UNION ALL

-- ── Credit notes from the accounting module (imported separately) ─────
-- Amounts are stored positive in dw_credit_note; negated here so that
-- plain SUM(line_total_excl) = true net sales ex-VAT.
SELECT
    -cn.id                                           AS line_id,
    cn.cn_no                                         AS invoice_no,
    'CREDIT NOTE'                                    AS invoice_type,
    cn.cn_date                                       AS invoice_date,
    cn.customer_code                                 AS customer_code,
    cn.store_code                                    AS store_code,
    NULL                                             AS salesperson_code,
    'CREDIT_NOTE_ADJ'                                AS item_code,
    1                                                AS line_number,
    -1                                               AS quantity,
    NULL                                             AS price_excl,
    NULL                                             AS price_incl,
    NULL                                             AS discount_percent,
    NULL                                             AS vat_percent,
    -cn.amount_excl                                  AS line_total_excl,
    NULL                                             AS line_total_discount,
    -COALESCE(cn.amount_vat, 0)                      AS line_total_vat,
    -(cn.amount_excl + COALESCE(cn.amount_vat, 0))   AS line_total_incl,
    -cn.amount_excl                                  AS line_net_value,
    EXTRACT(YEAR  FROM cn.cn_date)                   AS year,
    EXTRACT(MONTH FROM cn.cn_date)                   AS month,
    EXTRACT(QUARTER FROM cn.cn_date)                 AS quarter,
    TO_CHAR(cn.cn_date, 'YYYY-MM')                   AS year_month,
    TO_CHAR(cn.cn_date, 'Day')                       AS day_of_week,
    EXTRACT(DOW  FROM cn.cn_date)                    AS day_of_week_no
FROM dw_credit_note cn
"""


def ensure_dw_credit_note_schema():
    """Create dw_credit_note table (if absent) and refresh pbi_fact_sales view."""
    from app import db
    from sqlalchemy import text

    with db.engine.begin() as conn:
        # 0. invoice_date_local on dw_invoice_header (PS365 value date used by
        #    Powersoft's own reports; utc0 can differ by days or even months).
        conn.execute(text("""
            ALTER TABLE dw_invoice_header
            ADD COLUMN IF NOT EXISTS invoice_date_local DATE
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_dw_invoice_header_date_local
            ON dw_invoice_header (invoice_date_local)
        """))

        # 1. Table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dw_credit_note (
                id                 SERIAL PRIMARY KEY,
                cn_no              VARCHAR(64) NOT NULL,
                cn_date            DATE        NOT NULL,
                customer_code      VARCHAR(64),
                store_code         VARCHAR(64),
                amount_excl        NUMERIC(18,4) NOT NULL,
                amount_vat         NUMERIC(18,4) DEFAULT 0,
                related_invoice_no VARCHAR(64),
                notes              TEXT,
                source             VARCHAR(20) NOT NULL DEFAULT 'csv',
                last_sync_at       TIMESTAMP NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_dw_credit_note_cn_no UNIQUE (cn_no)
            )
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_dw_credit_note_date_customer
            ON dw_credit_note (cn_date, customer_code)
        """))

        # 2. View — DROP first to avoid "cannot change column type" when the
        #    UNION widens a varchar(64) column to varchar (no length).
        #    No other view depends on pbi_fact_sales, so CASCADE is safe.
        conn.execute(text("DROP VIEW IF EXISTS pbi_fact_sales CASCADE"))
        conn.execute(text(_PBI_FACT_SALES_VIEW))

    logger.info("dw_credit_note table + pbi_fact_sales view (UNION) ensured")
