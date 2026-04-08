import logging
import time
from datetime import date, timedelta, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from timezone_utils import get_utc_now
from services.forecast.week_utils import monday_of, get_completed_week_cutoff

logger = logging.getLogger(__name__)

RETURN_TYPES = ["RETURN", "CREDIT_NOTE", "SALES_RETURN", "CREDIT", "REFUND"]


VALID_MODES = ("incremental", "full_26", "full_52", "full_rebuild", "rebuild_365")

MIN_COVERAGE_WEEKS = 20
COVERAGE_CHECK_WINDOW = 26


def build_weekly_sales(session: Session, weeks_back: int = 52, mode: str = "incremental", progress_callback=None):
    if mode == "full_rebuild" or mode == "rebuild_365":
        mode = "full_52"
    if mode not in VALID_MODES:
        mode = "incremental"

    if mode == "full_52":
        since_weeks = 52
    elif mode == "full_26":
        since_weeks = 26
    else:
        since_weeks = 8

    original_mode = mode
    auto_switched = False

    if mode == "incremental":
        coverage = _check_weekly_coverage(session)
        if coverage < MIN_COVERAGE_WEEKS:
            mode = "full_26"
            since_weeks = 26
            auto_switched = True
            logger.warning(
                f"[weekly_sales] Insufficient historical coverage: {coverage}/{MIN_COVERAGE_WEEKS} weeks. "
                f"Auto-switching from incremental to full_26"
            )
            if progress_callback:
                progress_callback(f"Low history coverage ({coverage} weeks) — switching to 26-week rebuild")

    cutoff = monday_of(date.today()) - timedelta(weeks=since_weeks)
    logger.info(f"[weekly_sales] mode={mode} (original={original_mode}, auto_switched={auto_switched}), cutoff={cutoff}, weeks_back={since_weeks}")

    t0 = time.time()
    upserted = _aggregate_and_upsert_sql(session, cutoff, progress_callback=progress_callback)
    elapsed = time.time() - t0

    logger.info(f"[weekly_sales] completed in {elapsed:.2f}s; mode={mode} upserted={upserted}")
    return {"upserted": upserted, "mode": mode, "auto_switched": auto_switched, "elapsed": round(elapsed, 2)}


def _check_weekly_coverage(session: Session):
    window_start = monday_of(date.today()) - timedelta(weeks=COVERAGE_CHECK_WINDOW)
    result = session.execute(
        text("""
            SELECT COUNT(DISTINCT week_start) AS covered_weeks
            FROM fact_sales_weekly_item
            WHERE week_start >= :window_start
              AND net_qty > 0
        """),
        {"window_start": window_start}
    )
    row = result.fetchone()
    return row[0] if row else 0


def update_weekly_sales(session: Session, since_date: date = None):
    if since_date is None:
        since_date = monday_of(date.today()) - timedelta(weeks=4)
    else:
        since_date = monday_of(since_date)

    logger.info(f"[weekly_sales] incremental update from {since_date}")
    t0 = time.time()
    upserted = _aggregate_and_upsert_sql(session, since_date)
    elapsed = time.time() - t0
    logger.info(f"[weekly_sales] incremental completed in {elapsed:.2f}s; upserted={upserted}")
    return upserted


def _aggregate_and_upsert_sql(session: Session, cutoff: date, progress_callback=None):
    return_cases = " OR ".join(
        f"h.invoice_type ILIKE '%{rt}%'" for rt in RETURN_TYPES
    )

    upsert_sql = text(f"""
        INSERT INTO fact_sales_weekly_item
            (week_start, item_code_365, gross_qty, return_qty, net_qty,
             invoice_count, customer_count, sales_ex_vat, discount_value, updated_at)
        SELECT
            date_trunc('week', h.invoice_date_utc0)::date  AS week_start,
            l.item_code_365,
            COALESCE(SUM(CASE WHEN NOT ({return_cases}) THEN ABS(l.quantity) ELSE 0 END), 0)  AS gross_qty,
            COALESCE(SUM(CASE WHEN ({return_cases}) THEN ABS(l.quantity) ELSE 0 END), 0)      AS return_qty,
            COALESCE(SUM(CASE WHEN NOT ({return_cases}) THEN ABS(l.quantity) ELSE 0 END), 0)
              - COALESCE(SUM(CASE WHEN ({return_cases}) THEN ABS(l.quantity) ELSE 0 END), 0)  AS net_qty,
            COUNT(DISTINCT l.invoice_no_365)     AS invoice_count,
            COUNT(DISTINCT h.customer_code_365)  AS customer_count,
            COALESCE(SUM(CASE WHEN NOT ({return_cases}) THEN ABS(COALESCE(l.line_total_excl, 0)) ELSE 0 END), 0) AS sales_ex_vat,
            COALESCE(SUM(ABS(COALESCE(l.line_total_discount, 0))), 0) AS discount_value,
            now() AS updated_at
        FROM dw_invoice_line l
        JOIN dw_invoice_header h ON l.invoice_no_365 = h.invoice_no_365
        WHERE h.invoice_date_utc0 >= :cutoff
          AND l.item_code_365 IS NOT NULL
          AND l.item_code_365 != ''
        GROUP BY date_trunc('week', h.invoice_date_utc0)::date, l.item_code_365
        ON CONFLICT (week_start, item_code_365) DO UPDATE SET
            gross_qty      = EXCLUDED.gross_qty,
            return_qty     = EXCLUDED.return_qty,
            net_qty        = EXCLUDED.net_qty,
            invoice_count  = EXCLUDED.invoice_count,
            customer_count = EXCLUDED.customer_count,
            sales_ex_vat   = EXCLUDED.sales_ex_vat,
            discount_value = EXCLUDED.discount_value,
            updated_at     = EXCLUDED.updated_at
    """)

    if progress_callback:
        progress_callback("Running single-SQL weekly sales upsert...")

    conn = session.connection()
    result = conn.execute(upsert_sql, {"cutoff": cutoff})

    upserted = result.rowcount if result.rowcount and result.rowcount >= 0 else 0
    session.flush()

    if progress_callback:
        progress_callback(f"Upserted {upserted} weekly sales rows")

    return upserted
