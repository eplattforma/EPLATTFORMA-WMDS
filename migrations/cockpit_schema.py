"""Cockpit Ticket 1: schema migration.

Idempotent and dialect-aware (Postgres in production, SQLite in tests).
Creates three tables and one read-only SQL view per cockpit-brief §6.1,
§6.7 and §10.2.

Schema follows brief §6.1 column naming exactly: ``target_weekly_ambition``,
``target_monthly``, ``target_quarterly``, ``target_annual``; history uses
``event``/``created_at`` with ``previous_*`` columns. The brief's main
table has no ``proposed_*`` numeric columns — pending-proposal values
live as the latest ``event='proposed'`` row in the history table.

Audit events (§6.7) are written to ``cockpit_audit_log`` because the
operational batch's audit-event API is not yet in place — see
ASSUMPTION-038.

Usage as a script::

    python -m migrations.cockpit_schema
"""
import logging

from sqlalchemy import text

from app import db

logger = logging.getLogger(__name__)


# ─── PostgreSQL DDL ─────────────────────────────────────────────────────

_TARGETS_DDL_PG = """
CREATE TABLE IF NOT EXISTS customer_spend_target (
    customer_code_365      VARCHAR(50) PRIMARY KEY
        REFERENCES ps_customers(customer_code_365),
    target_weekly_ambition NUMERIC(12,2),
    target_monthly         NUMERIC(12,2),
    target_quarterly       NUMERIC(12,2),
    target_annual          NUMERIC(12,2),
    status                 VARCHAR(20) NOT NULL DEFAULT 'active',
    proposed_by            VARCHAR(64),
    proposed_at            TIMESTAMP WITH TIME ZONE,
    proposed_notes         TEXT,
    approved_by            VARCHAR(64),
    approved_at            TIMESTAMP WITH TIME ZONE,
    last_modified_at       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    last_modified_by       VARCHAR(64) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_customer_spend_target_status
    ON customer_spend_target(status);
"""

_HISTORY_DDL_PG = """
CREATE TABLE IF NOT EXISTS customer_spend_target_history (
    id                       BIGSERIAL PRIMARY KEY,
    customer_code_365        VARCHAR(50) NOT NULL,
    event                    VARCHAR(30) NOT NULL,
    target_weekly_ambition   NUMERIC(12,2),
    target_monthly           NUMERIC(12,2),
    target_quarterly         NUMERIC(12,2),
    target_annual            NUMERIC(12,2),
    previous_weekly_ambition NUMERIC(12,2),
    previous_monthly         NUMERIC(12,2),
    previous_quarterly       NUMERIC(12,2),
    previous_annual          NUMERIC(12,2),
    actor_username           VARCHAR(64) NOT NULL,
    notes                    TEXT,
    created_at               TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_customer_spend_target_history_customer
    ON customer_spend_target_history(customer_code_365, created_at DESC);
"""

_AUDIT_LOG_DDL_PG = """
CREATE TABLE IF NOT EXISTS cockpit_audit_log (
    id              BIGSERIAL PRIMARY KEY,
    event_name      VARCHAR(80) NOT NULL,
    actor_username  VARCHAR(64) NOT NULL,
    customer_code_365 VARCHAR(50),
    payload_json    TEXT,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cockpit_audit_log_event_at
    ON cockpit_audit_log(event_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cockpit_audit_log_customer
    ON cockpit_audit_log(customer_code_365, created_at DESC);
"""


# ─── SQLite DDL (test envs) ─────────────────────────────────────────────

_TARGETS_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS customer_spend_target (
    customer_code_365      VARCHAR(50) PRIMARY KEY,
    target_weekly_ambition NUMERIC,
    target_monthly         NUMERIC,
    target_quarterly       NUMERIC,
    target_annual          NUMERIC,
    status                 VARCHAR(20) NOT NULL DEFAULT 'active',
    proposed_by            VARCHAR(64),
    proposed_at            TIMESTAMP,
    proposed_notes         TEXT,
    approved_by            VARCHAR(64),
    approved_at            TIMESTAMP,
    last_modified_at       TIMESTAMP NOT NULL,
    last_modified_by       VARCHAR(64) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_customer_spend_target_status
    ON customer_spend_target(status);
"""

_HISTORY_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS customer_spend_target_history (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_code_365        VARCHAR(50) NOT NULL,
    event                    VARCHAR(30) NOT NULL,
    target_weekly_ambition   NUMERIC,
    target_monthly           NUMERIC,
    target_quarterly         NUMERIC,
    target_annual            NUMERIC,
    previous_weekly_ambition NUMERIC,
    previous_monthly         NUMERIC,
    previous_quarterly       NUMERIC,
    previous_annual          NUMERIC,
    actor_username           VARCHAR(64) NOT NULL,
    notes                    TEXT,
    created_at               TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_customer_spend_target_history_customer
    ON customer_spend_target_history(customer_code_365, created_at);
"""

_AUDIT_LOG_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS cockpit_audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_name      VARCHAR(80) NOT NULL,
    actor_username  VARCHAR(64) NOT NULL,
    customer_code_365 VARCHAR(50),
    payload_json    TEXT,
    created_at      TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cockpit_audit_log_event_at
    ON cockpit_audit_log(event_name, created_at);
CREATE INDEX IF NOT EXISTS idx_cockpit_audit_log_customer
    ON cockpit_audit_log(customer_code_365, created_at);
"""


# ─── offer-opportunity view (brief §10.2) ───────────────────────────────
# Adapted from cockpit-brief Section 10.2 to actual schema (ASSUMPTION-034):
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
    """SQLite-compatible variant of vw_customer_offer_opportunity. Same
    column shape as the Postgres view; only date arithmetic and boolean
    literal change."""
    return _OFFER_OPPORTUNITY_VIEW_PG \
        .replace("CREATE OR REPLACE VIEW", "CREATE VIEW IF NOT EXISTS") \
        .replace("(CURRENT_DATE - INTERVAL '90 days')",
                 "date('now', '-90 days')") \
        .replace("is_active = true", "is_active = 1")


def _migrate_old_schema_if_present(conn, dialect: str):
    """One-shot rename of pre-§6.1 column names.

    Previous in-progress draft used ``weekly_ambition``/``monthly``/...
    and ``event_type``/``occurred_at``. Brief §6.1 specifies
    ``target_weekly_ambition``/...`` and ``event``/``created_at`` with
    ``previous_*`` columns. Since cockpit was never enabled in prod
    (cockpit_enabled=false everywhere), the safe operation is DROP +
    recreate when the old column shape is detected.
    """
    from sqlalchemy import inspect
    insp = inspect(conn)
    if not insp.has_table("customer_spend_target"):
        return
    cols = {c["name"] for c in insp.get_columns("customer_spend_target")}
    if "target_weekly_ambition" in cols:
        return  # already on the new schema
    logger.warning(
        "Cockpit: detected old (pre-brief §6.1) schema — dropping "
        "customer_spend_target / customer_spend_target_history for "
        "rebuild. Safe because cockpit_enabled is false."
    )
    conn.execute(text("DROP TABLE IF EXISTS customer_spend_target_history"))
    conn.execute(text("DROP TABLE IF EXISTS customer_spend_target"))


def ensure_cockpit_schema():
    """Idempotent boot-time schema ensure. Safe under parallel workers.

    Cross-dialect: tables and view created on both PostgreSQL and SQLite.
    If the underlying fact tables (dw_invoice_line, etc.) are missing the
    view creation is skipped non-fatally — the cockpit service degrades
    to ``[]`` for offer opportunities rather than raising.
    """
    try:
        dialect = db.engine.dialect.name
        with db.engine.begin() as conn:
            _migrate_old_schema_if_present(conn, dialect)
            if dialect == "postgresql":
                _exec_each(conn, _TARGETS_DDL_PG)
                _exec_each(conn, _HISTORY_DDL_PG)
                _exec_each(conn, _AUDIT_LOG_DDL_PG)
                view_sql = _OFFER_OPPORTUNITY_VIEW_PG
            else:
                _exec_each(conn, _TARGETS_DDL_SQLITE)
                _exec_each(conn, _HISTORY_DDL_SQLITE)
                _exec_each(conn, _AUDIT_LOG_DDL_SQLITE)
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
