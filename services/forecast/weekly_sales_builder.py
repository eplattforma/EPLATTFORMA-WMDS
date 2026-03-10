import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, and_, case, distinct
from sqlalchemy.orm import Session

from models import DwInvoiceHeader, DwInvoiceLine, FactSalesWeeklyItem
from timezone_utils import get_utc_now

logger = logging.getLogger(__name__)

RETURN_TYPES = ["RETURN", "CREDIT_NOTE", "SALES_RETURN", "CREDIT", "REFUND"]


def _is_return_type(invoice_type: str) -> bool:
    if not invoice_type:
        return False
    upper = invoice_type.upper().strip()
    return any(rt in upper for rt in RETURN_TYPES)


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def build_weekly_sales(session: Session, weeks_back: int = 52):
    cutoff = _monday_of(date.today()) - timedelta(weeks=weeks_back)
    logger.info(f"Building weekly sales from {cutoff} (last {weeks_back} weeks)")

    session.query(FactSalesWeeklyItem).filter(
        FactSalesWeeklyItem.week_start >= cutoff
    ).delete(synchronize_session=False)
    session.flush()

    _aggregate_and_insert(session, cutoff)


def update_weekly_sales(session: Session, since_date: date = None):
    if since_date is None:
        since_date = _monday_of(date.today()) - timedelta(weeks=4)
    else:
        since_date = _monday_of(since_date)

    logger.info(f"Incremental weekly sales update from {since_date}")

    session.query(FactSalesWeeklyItem).filter(
        FactSalesWeeklyItem.week_start >= since_date
    ).delete(synchronize_session=False)
    session.flush()

    _aggregate_and_insert(session, since_date)


def _aggregate_and_insert(session: Session, cutoff: date):
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

    inserted = 0
    now = get_utc_now()
    for r in rows:
        ws = r.week_start
        if isinstance(ws, str):
            ws = date.fromisoformat(ws[:10])
        elif hasattr(ws, 'date'):
            ws = ws.date()

        gross = Decimal(str(r.gross_qty or 0))
        ret = Decimal(str(r.return_qty or 0))

        fact = FactSalesWeeklyItem(
            week_start=ws,
            item_code_365=r.item_code_365,
            gross_qty=gross,
            return_qty=ret,
            net_qty=gross - ret,
            invoice_count=r.invoice_count or 0,
            customer_count=r.customer_count or 0,
            sales_ex_vat=Decimal(str(r.sales_ex_vat or 0)),
            discount_value=Decimal(str(r.discount_value or 0)),
            updated_at=now,
        )
        session.add(fact)
        inserted += 1

    session.flush()
    logger.info(f"Inserted {inserted} weekly sales rows")
    return inserted
