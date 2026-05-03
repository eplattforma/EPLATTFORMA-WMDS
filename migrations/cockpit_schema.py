"""Cockpit Ticket 1: schema migration.

Idempotent and dialect-aware (Postgres in production, SQLite in tests).
Creates two tables and (Postgres only) one read-only SQL view.

Usage as a script::

    python -m migrations.cockpit_schema

Or call ``ensure_cockpit_schema()`` on app boot — the function is wired
into ``main.py`` after the Phase 5 schema runner.
"""
import logging

from sqlalchemy import inspect, text

from app import db

logger = logging.getLogger(__name__)


_TARGETS_DDL_PG = """
CREATE TABLE IF NOT EXISTS customer_spend_target (
    customer_code_365 VARCHAR(50) PRIMARY KEY
        REFERENCES ps_customers(customer_code_365),
    weekly_ambition NUMERIC(14,2),
    monthly         NUMERIC(14,2),
    quarterly       NUMERIC(14,2),
    annual          NUMERIC(14,2),
    status          VARCHAR(20) NOT NULL DEFAULT 'active',
    proposed_by         VARCHAR(64),
    proposed_at         TIMESTAMP,
    proposed_notes      TEXT,
    proposed_weekly     NUMERIC(14,2),
    proposed_monthly    NUMERIC(14,2),
    proposed_quarterly  NUMERIC(14,2),
    proposed_annual     NUMERIC(14,2),
    approved_by         VARCHAR(64),
    approved_at         TIMESTAMP,
    last_modified_by    VARCHAR(64),
    last_modified_at    TIMESTAMP DEFAULT NOW()
);
"""

_HISTORY_DDL_PG = """
CREATE TABLE IF NOT EXISTS customer_spend_target_history (
    id BIGSERIAL PRIMARY KEY,
    customer_code_365 VARCHAR(50) NOT NULL,
    event_type VARCHAR(40) NOT NULL,
    actor_username VARCHAR(64) NOT NULL,
    occurred_at TIMESTAMP NOT NULL DEFAULT NOW(),
    weekly_ambition NUMERIC(14,2),
    monthly   NUMERIC(14,2),
    quarterly NUMERIC(14,2),
    annual    NUMERIC(14,2),
    notes TEXT,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_cst_hist_customer_at
    ON customer_spend_target_history(customer_code_365, occurred_at DESC);
"""

_TARGETS_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS customer_spend_target (
    customer_code_365 VARCHAR(50) PRIMARY KEY,
    weekly_ambition NUMERIC,
    monthly         NUMERIC,
    quarterly       NUMERIC,
    annual          NUMERIC,
    status          VARCHAR(20) NOT NULL DEFAULT 'active',
    proposed_by         VARCHAR(64),
    proposed_at         TIMESTAMP,
    proposed_notes      TEXT,
    proposed_weekly     NUMERIC,
    proposed_monthly    NUMERIC,
    proposed_quarterly  NUMERIC,
    proposed_annual     NUMERIC,
    approved_by         VARCHAR(64),
    approved_at         TIMESTAMP,
    last_modified_by    VARCHAR(64),
    last_modified_at    TIMESTAMP
);
"""

_HISTORY_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS customer_spend_target_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_code_365 VARCHAR(50) NOT NULL,
    event_type VARCHAR(40) NOT NULL,
    actor_username VARCHAR(64) NOT NULL,
    occurred_at TIMESTAMP NOT NULL,
    weekly_ambition NUMERIC,
    monthly   NUMERIC,
    quarterly NUMERIC,
    annual    NUMERIC,
    notes TEXT,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_cst_hist_customer_at
    ON customer_spend_target_history(customer_code_365, occurred_at);
"""


# Postgres-only view. Adapted from cockpit-brief Section 10.2 to actual
# schema (see ASSUMPTION-034):
#   - dw_invoice_lines  -> dw_invoice_line  (singular)
#   - revenue_excl_vat  -> line_total_excl
#   - invoice_date      -> dw_invoice_header.invoice_date_utc0 (JOIN)
#   - dw_customers      -> ps_customers
#   - reporting_group_code -> reporting_group (TEXT on ps_customers)
_OFFER_OPPORTUNITY_VIEW_PG = """
CREATE OR REPLACE VIEW vw_customer_offer_opportunity AS
WITH customer_sku_revenue AS (
    SELECT
        h.customer_code_365,
        l.item_code_365 AS sku,
        SUM(l.line_total_excl)   AS revenue_90d,
        SUM(l.gross_profit)      AS gp_90d,
        AVG(l.gross_margin_pct)  AS gm_pct_avg,
        MAX(h.invoice_date_utc0) AS last_bought
    FROM dw_invoice_line l
    JOIN dw_invoice_header h ON h.invoice_no_365 = l.invoice_no_365
    WHERE h.invoice_date_utc0 >= (CURRENT_DATE - INTERVAL '90 days')
      AND h.customer_code_365 IS NOT NULL
      AND l.item_code_365 IS NOT NULL
      AND l.line_total_excl > 0
    GROUP BY h.customer_code_365, l.item_code_365
    HAVING SUM(l.line_total_excl) >= 100
),
customer_active_offers AS (
    SELECT DISTINCT customer_code_365, item_code_365 AS sku
    FROM crm_customer_offer_current
    WHERE is_active = true
      AND customer_code_365 IS NOT NULL
      AND item_code_365 IS NOT NULL
),
customer_group AS (
    SELECT customer_code_365, reporting_group
    FROM ps_customers
    WHERE reporting_group IS NOT NULL
      AND deleted_at IS NULL
),
peer_offer_penetration AS (
    SELECT
        cg.reporting_group,
        o.item_code_365 AS sku,
        COUNT(DISTINCT o.customer_code_365) AS peer_offered_count
    FROM crm_customer_offer_current o
    JOIN customer_group cg ON cg.customer_code_365 = o.customer_code_365
    WHERE o.is_active = true
    GROUP BY cg.reporting_group, o.item_code_365
)
SELECT
    csr.customer_code_365,
    csr.sku,
    cg.reporting_group,
    csr.revenue_90d,
    csr.gp_90d,
    csr.gm_pct_avg,
    csr.last_bought,
    COALESCE(pop.peer_offered_count, 0) AS peer_offered_count
FROM customer_sku_revenue csr
JOIN customer_group cg
    ON cg.customer_code_365 = csr.customer_code_365
LEFT JOIN customer_active_offers cao
    ON cao.customer_code_365 = csr.customer_code_365 AND cao.sku = csr.sku
LEFT JOIN peer_offer_penetration pop
    ON pop.reporting_group = cg.reporting_group AND pop.sku = csr.sku
WHERE cao.sku IS NULL
  AND COALESCE(pop.peer_offered_count, 0) >= 3
;
"""


def _exec_each(conn, sql_block):
    for stmt in [s.strip() for s in sql_block.strip().split(";") if s.strip()]:
        conn.execute(text(stmt))


def _build_offer_view_sqlite() -> str:
    """SQLite-compatible variant of vw_customer_offer_opportunity. Uses
    the same column shape as the Postgres view so the cockpit service
    selects against the same names on both dialects. SQLite supports CTEs
    and CREATE VIEW; the only swap needed is the date arithmetic
    (`date('now', '-90 days')` instead of `CURRENT_DATE - INTERVAL '90 days'`).
    """
    return _OFFER_OPPORTUNITY_VIEW_PG \
        .replace("CREATE OR REPLACE VIEW", "CREATE VIEW IF NOT EXISTS") \
        .replace("(CURRENT_DATE - INTERVAL '90 days')",
                 "date('now', '-90 days')") \
        .replace("is_active = true", "is_active = 1")


def ensure_cockpit_schema():
    """Idempotent boot-time schema ensure. Safe under parallel workers.

    Cross-dialect: tables and view are created on both PostgreSQL and
    SQLite. If the underlying fact tables (dw_invoice_line, etc.) are
    missing on a given backend (e.g. a fresh in-memory test DB), the view
    creation is skipped non-fatally — the cockpit service degrades to
    ``[]`` for offer opportunities rather than raising.
    """
    try:
        dialect = db.engine.dialect.name
        with db.engine.begin() as conn:
            if dialect == "postgresql":
                _exec_each(conn, _TARGETS_DDL_PG)
                _exec_each(conn, _HISTORY_DDL_PG)
                view_sql = _OFFER_OPPORTUNITY_VIEW_PG
            else:
                _exec_each(conn, _TARGETS_DDL_SQLITE)
                _exec_each(conn, _HISTORY_DDL_SQLITE)
                view_sql = _build_offer_view_sqlite()
            try:
                conn.execute(text(view_sql))
            except Exception as ve:
                logger.warning(
                    "Cockpit: vw_customer_offer_opportunity not created "
                    "(dialect=%s, likely missing fact tables): %s",
                    dialect, ve,
                )
        logger.info("Cockpit: schema ensured (dialect=%s)", dialect)
    except Exception:
        logger.exception("Cockpit: ensure_cockpit_schema failed")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ensure_cockpit_schema()
