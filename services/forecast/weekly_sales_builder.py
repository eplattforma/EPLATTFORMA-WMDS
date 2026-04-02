import logging
from datetime import date, timedelta, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from timezone_utils import get_utc_now
from services.forecast.week_utils import monday_of, get_completed_week_cutoff

logger = logging.getLogger(__name__)

RETURN_TYPES = ["RETURN", "CREDIT_NOTE", "SALES_RETURN", "CREDIT", "REFUND"]


def build_weekly_sales(session: Session, weeks_back: int = 52, progress_callback=None):
    cutoff = monday_of(date.today()) - timedelta(weeks=weeks_back)
    logger.info(f"Building weekly sales from {cutoff} (last {weeks_back} weeks)")
    _aggregate_and_upsert(session, cutoff, progress_callback=progress_callback)


def update_weekly_sales(session: Session, since_date: date = None):
    if since_date is None:
        since_date = monday_of(date.today()) - timedelta(weeks=4)
    else:
        since_date = monday_of(since_date)

    logger.info(f"Incremental weekly sales update from {since_date}")
    _aggregate_and_upsert(session, since_date)


def _aggregate_and_upsert(session: Session, cutoff: date, progress_callback=None):
    logger.info("=" * 60)
    logger.info("WEEKLY_SALES: Starting aggregate_and_upsert")
    logger.info(f"Cutoff date: {cutoff}")
    logger.info("=" * 60)

    return_cases = " OR ".join(
        f"h.invoice_type ILIKE '%{rt}%'" for rt in RETURN_TYPES
    )

    agg_sql = text(f"""
        SELECT
            date_trunc('week', h.invoice_date_utc0)::date  AS week_start,
            l.item_code_365,
            COALESCE(SUM(CASE WHEN NOT ({return_cases}) THEN ABS(l.quantity) ELSE 0 END), 0)  AS gross_qty,
            COALESCE(SUM(CASE WHEN ({return_cases}) THEN ABS(l.quantity) ELSE 0 END), 0)      AS return_qty,
            COALESCE(SUM(CASE WHEN NOT ({return_cases}) THEN ABS(COALESCE(l.line_total_excl, 0)) ELSE 0 END), 0) AS sales_ex_vat,
            COALESCE(SUM(ABS(COALESCE(l.line_total_discount, 0))), 0)                         AS discount_value,
            COUNT(DISTINCT l.invoice_no_365)                                                   AS invoice_count,
            COUNT(DISTINCT h.customer_code_365)                                                AS customer_count
        FROM dw_invoice_line l
        JOIN dw_invoice_header h ON l.invoice_no_365 = h.invoice_no_365
        WHERE h.invoice_date_utc0 >= :cutoff
          AND l.item_code_365 IS NOT NULL
          AND l.item_code_365 != ''
        GROUP BY date_trunc('week', h.invoice_date_utc0)::date, l.item_code_365
    """)

    upsert_sql = text("""
        INSERT INTO fact_sales_weekly_item
            (week_start, item_code_365, gross_qty, return_qty, net_qty,
             invoice_count, customer_count, sales_ex_vat, discount_value, updated_at)
        VALUES
            (:ws, :item, :gross, :ret, :net, :inv_cnt, :cust_cnt, :sales, :disc, :upd)
        ON CONFLICT (week_start, item_code_365) DO UPDATE SET
            gross_qty = EXCLUDED.gross_qty,
            return_qty = EXCLUDED.return_qty,
            net_qty = EXCLUDED.net_qty,
            invoice_count = EXCLUDED.invoice_count,
            customer_count = EXCLUDED.customer_count,
            sales_ex_vat = EXCLUDED.sales_ex_vat,
            discount_value = EXCLUDED.discount_value,
            updated_at = EXCLUDED.updated_at
    """)

    query_start = datetime.utcnow()
    logger.info("[WEEKLY_SALES] Running aggregate query via raw SQL...")

    conn = session.connection()
    result = conn.execute(agg_sql, {"cutoff": cutoff})

    query_time = (datetime.utcnow() - query_start).total_seconds()
    logger.info(f"[WEEKLY_SALES] Aggregate query finished in {query_time:.2f}s")

    upserted = 0
    now = get_utc_now()
    batch_size = 500
    batch_params = []

    for row in result:
        ws = row.week_start
        if isinstance(ws, str):
            ws = date.fromisoformat(ws[:10])
        elif hasattr(ws, 'date'):
            ws = ws.date()

        gross = Decimal(str(row.gross_qty or 0))
        ret = Decimal(str(row.return_qty or 0))

        batch_params.append({
            "ws": ws,
            "item": row.item_code_365,
            "gross": gross,
            "ret": ret,
            "net": gross - ret,
            "inv_cnt": row.invoice_count or 0,
            "cust_cnt": row.customer_count or 0,
            "sales": Decimal(str(row.sales_ex_vat or 0)),
            "disc": Decimal(str(row.discount_value or 0)),
            "upd": now,
        })

        if len(batch_params) >= batch_size:
            for p in batch_params:
                conn.execute(upsert_sql, p)
            upserted += len(batch_params)
            batch_params.clear()
            session.flush()

            if progress_callback:
                progress_callback(f"Upserted {upserted} weekly sales rows")

    if batch_params:
        for p in batch_params:
            conn.execute(upsert_sql, p)
        upserted += len(batch_params)
        batch_params.clear()
        session.flush()

    logger.info("=" * 60)
    logger.info(f"[WEEKLY_SALES] COMPLETED: upserted {upserted} rows total")
    logger.info("=" * 60)
    return upserted
