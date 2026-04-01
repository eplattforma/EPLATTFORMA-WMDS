import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, and_, case, distinct, text
from sqlalchemy.orm import Session

from models import DwInvoiceHeader, DwInvoiceLine, FactSalesWeeklyItem
from timezone_utils import get_utc_now
from services.forecast.week_utils import monday_of, get_completed_week_cutoff

logger = logging.getLogger(__name__)

RETURN_TYPES = ["RETURN", "CREDIT_NOTE", "SALES_RETURN", "CREDIT", "REFUND"]


def build_weekly_sales(session: Session, weeks_back: int = 52):
    cutoff = monday_of(date.today()) - timedelta(weeks=weeks_back)
    logger.info(f"Building weekly sales from {cutoff} (last {weeks_back} weeks)")
    _aggregate_and_upsert(session, cutoff)


def update_weekly_sales(session: Session, since_date: date = None):
    if since_date is None:
        since_date = monday_of(date.today()) - timedelta(weeks=4)
    else:
        since_date = monday_of(since_date)

    logger.info(f"Incremental weekly sales update from {since_date}")
    _aggregate_and_upsert(session, since_date)


def _aggregate_and_upsert(session: Session, cutoff: date):
    is_return = case(
        *[(DwInvoiceHeader.invoice_type.ilike(f"%{rt}%"), True) for rt in RETURN_TYPES],
        else_=False,
    )

    gross_qty_expr = func.coalesce(
        func.sum(
            case(
                (is_return, Decimal(0)),
                else_=func.abs(DwInvoiceLine.quantity),
            )
        ),
        Decimal(0),
    )

    return_qty_expr = func.coalesce(
        func.sum(
            case(
                (is_return, func.abs(DwInvoiceLine.quantity)),
                else_=Decimal(0),
            )
        ),
        Decimal(0),
    )

    sales_ex_vat_expr = func.coalesce(
        func.sum(
            case(
                (is_return, Decimal(0)),
                else_=func.abs(func.coalesce(DwInvoiceLine.line_total_excl, Decimal(0))),
            )
        ),
        Decimal(0),
    )

    discount_expr = func.coalesce(
        func.sum(
            func.abs(func.coalesce(DwInvoiceLine.line_total_discount, Decimal(0)))
        ),
        Decimal(0),
    )

    invoice_count_expr = func.count(distinct(DwInvoiceLine.invoice_no_365))
    customer_count_expr = func.count(distinct(DwInvoiceHeader.customer_code_365))
    week_start_expr = func.date_trunc('week', DwInvoiceHeader.invoice_date_utc0)

    rows = (
        session.query(
            week_start_expr.label("week_start"),
            DwInvoiceLine.item_code_365,
            gross_qty_expr.label("gross_qty"),
            return_qty_expr.label("return_qty"),
            sales_ex_vat_expr.label("sales_ex_vat"),
            discount_expr.label("discount_value"),
            invoice_count_expr.label("invoice_count"),
            customer_count_expr.label("customer_count"),
        )
        .join(DwInvoiceHeader, DwInvoiceLine.invoice_no_365 == DwInvoiceHeader.invoice_no_365)
        .filter(
            DwInvoiceHeader.invoice_date_utc0 >= cutoff,
            DwInvoiceLine.item_code_365.isnot(None),
            DwInvoiceLine.item_code_365 != "",
        )
        .group_by(week_start_expr, DwInvoiceLine.item_code_365)
        .all()
    )

    logger.info(f"Aggregated {len(rows)} weekly-item rows, upserting in batches...")

    upserted = 0
    now = get_utc_now()
    batch_size = 500

    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        for r in batch:
            ws = r.week_start
            if isinstance(ws, str):
                ws = date.fromisoformat(ws[:10])
            elif hasattr(ws, 'date'):
                ws = ws.date()

            gross = Decimal(str(r.gross_qty or 0))
            ret = Decimal(str(r.return_qty or 0))

            session.execute(
                text("""
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
                """),
                {
                    "ws": ws,
                    "item": r.item_code_365,
                    "gross": gross,
                    "ret": ret,
                    "net": gross - ret,
                    "inv_cnt": r.invoice_count or 0,
                    "cust_cnt": r.customer_count or 0,
                    "sales": Decimal(str(r.sales_ex_vat or 0)),
                    "disc": Decimal(str(r.discount_value or 0)),
                    "upd": now,
                },
            )
            upserted += 1

        session.flush()
        if upserted % 500 == 0:
            logger.info(f"Upserted {upserted}/{len(rows)} rows...")

    session.commit()
    logger.info(f"Completed: upserted {upserted} weekly sales rows")
    return upserted
