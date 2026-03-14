import math
import logging
from datetime import datetime
from decimal import Decimal
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app import db
from models import PSPendingOrderHeader, CRMCustomerOpenOrders, SyncJobLog
from ps365_client import call_ps365

logger = logging.getLogger(__name__)

JOB_NAME = "ps365_pending_orders_sync"


def get_pending_orders_total_count():
    data = call_ps365("list_pending_orders_header", {"display_all": "true"}, method="GET")
    api_response = data.get("api_response") or {}
    if str(api_response.get("response_code")) != "1":
        raise RuntimeError(f"PS365 count call failed: {api_response}")
    return int(data.get("total_count_list_pending_orders") or 0)


def get_pending_orders_page(page_number: int, page_size: int = 100):
    params = {
        "page_number": page_number,
        "page_size": page_size,
        "display_all": "true",
    }
    data = call_ps365("list_pending_orders_header", params, method="GET")
    api_response = data.get("api_response") or {}
    if str(api_response.get("response_code")) != "1":
        raise RuntimeError(f"PS365 page call failed on page {page_number}: {api_response}")
    return data.get("list_pending_orders") or []


def fetch_all_pending_orders(page_size: int = 100):
    total_count = get_pending_orders_total_count()
    logger.info(f"PS365 pending orders: total_count={total_count}")
    if total_count == 0:
        return []

    total_pages = math.ceil(total_count / page_size)
    rows = []

    for page_number in range(1, total_pages + 1):
        logger.info(f"Fetching pending orders page {page_number}/{total_pages}")
        page_rows = get_pending_orders_page(page_number, page_size)
        rows.extend(page_rows)

    logger.info(f"Fetched {len(rows)} pending order rows total")
    return rows


def aggregate_pending_orders(rows):
    aggregated = {}
    for row in rows:
        customer_code = (row.get("customer_code_365") or "").strip()
        if not customer_code:
            continue
        total_grand = Decimal(str(row.get("total_grand") or 0))
        if customer_code not in aggregated:
            aggregated[customer_code] = {
                "open_order_amount": Decimal("0.00"),
                "open_order_count": 0,
            }
        aggregated[customer_code]["open_order_amount"] += total_grand
        aggregated[customer_code]["open_order_count"] += 1
    return aggregated


def _safe_parse_datetime(val):
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(str(val), fmt)
        except (ValueError, TypeError):
            continue
    return None


def _safe_parse_date(val):
    if not val:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(val), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def sync_pending_order_totals_from_ps365(triggered_by="system"):
    log = SyncJobLog(job_name=JOB_NAME, triggered_by=triggered_by, status="running")
    db.session.add(log)
    db.session.flush()

    try:
        rows = fetch_all_pending_orders(page_size=100)
        aggregated = aggregate_pending_orders(rows)
        now = datetime.utcnow()

        db.session.execute(text("DELETE FROM ps_pending_orders_header"))

        seen_cart_codes = set()
        for row in rows:
            cart_code = (row.get("shopping_cart_code") or "").strip()
            if not cart_code or cart_code in seen_cart_codes:
                continue
            seen_cart_codes.add(cart_code)
            db.session.add(PSPendingOrderHeader(
                shopping_cart_code=cart_code,
                customer_code_365=(row.get("customer_code_365") or "").strip(),
                customer_email=row.get("customer_email"),
                customer_name=row.get("customer_name"),
                order_date_local=_safe_parse_datetime(row.get("order_date_local")),
                order_date_utc0=_safe_parse_datetime(row.get("order_date_utc0")),
                order_date_deliverby_utc0=_safe_parse_date(row.get("order_date_deliverby_utc0")),
                total_sub=Decimal(str(row.get("total_sub") or 0)),
                total_discount=Decimal(str(row.get("total_discount") or 0)),
                total_vat=Decimal(str(row.get("total_vat") or 0)),
                total_grand=Decimal(str(row.get("total_grand") or 0)),
                comments=row.get("comments"),
                delivery_address_line_1=row.get("delivery_address_line_1"),
                delivery_address_line_2=row.get("delivery_address_line_2"),
                delivery_address_line_3=row.get("delivery_address_line_3"),
                delivery_postal_code=row.get("delivery_postal_code"),
                delivery_town=row.get("delivery_town"),
                delivery_country_code_iso2=row.get("delivery_country_code_iso2"),
                delivery_country_name=row.get("delivery_country_name"),
                payment_term_code_365=row.get("payment_term_code_365"),
                delivery_term_code_365=row.get("delivery_term_code_365"),
                order_status_code_365=row.get("order_status_code_365"),
                order_status_name=row.get("order_status_name"),
                synced_at=now,
            ))

        db.session.execute(text("""
            UPDATE crm_customer_open_orders
            SET open_order_amount = 0,
                open_order_count = 0,
                last_synced_at = :now,
                updated_at = :now
        """), {"now": now})

        for customer_code, payload in aggregated.items():
            existing = db.session.get(CRMCustomerOpenOrders, customer_code)
            if existing:
                existing.open_order_amount = payload["open_order_amount"]
                existing.open_order_count = payload["open_order_count"]
                existing.last_synced_at = now
                existing.updated_at = now
            else:
                db.session.add(CRMCustomerOpenOrders(
                    customer_code_365=customer_code,
                    open_order_amount=payload["open_order_amount"],
                    open_order_count=payload["open_order_count"],
                    last_synced_at=now,
                ))

        log.status = "success"
        log.finished_at = now
        log.rows_processed = len(rows)
        log.customers_updated = len(aggregated)
        log.message = "Pending orders sync completed successfully"

        db.session.commit()

        return {
            "success": True,
            "message": "Open orders refreshed successfully",
            "orders_processed": len(rows),
            "customers_updated": len(aggregated),
            "last_sync_at": now.isoformat(),
        }

    except Exception as e:
        db.session.rollback()
        logger.error(f"Pending orders sync failed: {e}", exc_info=True)
        try:
            log.status = "failed"
            log.finished_at = datetime.utcnow()
            log.message = str(e)[:500]
            db.session.add(log)
            db.session.commit()
        except Exception:
            db.session.rollback()

        return {
            "success": False,
            "message": f"Refresh failed: {str(e)}",
        }


def acquire_sync_lock(job_name, locked_by):
    try:
        db.session.execute(text("""
            DELETE FROM sync_job_lock
            WHERE job_name = :job_name AND locked_at < NOW() - INTERVAL '15 minutes'
        """), {"job_name": job_name})
        db.session.execute(text("""
            INSERT INTO sync_job_lock (job_name, locked_at, locked_by)
            VALUES (:job_name, NOW(), :locked_by)
        """), {"job_name": job_name, "locked_by": locked_by})
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        return False


def release_sync_lock(job_name):
    db.session.execute(text("DELETE FROM sync_job_lock WHERE job_name = :job_name"), {"job_name": job_name})
    db.session.commit()


def get_open_orders_status():
    last_success = (
        SyncJobLog.query
        .filter_by(job_name=JOB_NAME, status="success")
        .order_by(SyncJobLog.finished_at.desc())
        .first()
    )
    lock = db.session.execute(
        text("SELECT locked_at, locked_by FROM sync_job_lock WHERE job_name = :jn"),
        {"jn": JOB_NAME}
    ).first()

    return {
        "last_sync_at": last_success.finished_at.isoformat() if last_success and last_success.finished_at else None,
        "last_message": last_success.message if last_success else None,
        "orders_processed": last_success.rows_processed if last_success else 0,
        "customers_updated": last_success.customers_updated if last_success else 0,
        "is_running": lock is not None,
    }
