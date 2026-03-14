import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from app import db
from models import PSCustomer, CrmAbandonedCartState
from integrations.magento_rest_oauth import magento_rest_get
from sqlalchemy import text

logger = logging.getLogger(__name__)


def fetch_all_active_carts_from_magento() -> list:
    params = {
        "searchCriteria[filterGroups][0][filters][0][field]": "is_active",
        "searchCriteria[filterGroups][0][filters][0][value]": "1",
        "searchCriteria[filterGroups][0][filters][0][conditionType]": "eq",
        "searchCriteria[filterGroups][1][filters][0][field]": "customer_id",
        "searchCriteria[filterGroups][1][filters][0][value]": "0",
        "searchCriteria[filterGroups][1][filters][0][conditionType]": "gt",
        "searchCriteria[pageSize]": "500",
        "searchCriteria[currentPage]": "1",
        "searchCriteria[sortOrders][0][field]": "updated_at",
        "searchCriteria[sortOrders][0][direction]": "DESC",
    }

    status_code, response_text = magento_rest_get("/rest/V1/carts/search", params=params)

    if status_code != 200:
        raise RuntimeError(f"Magento returned {status_code}")

    data = json.loads(response_text)
    raw_items = data.get("items", [])
    return [c for c in raw_items if c.get("items_count") or len(c.get("items", []))]


def sync_abandoned_carts_batch(triggered_by: str = "manual") -> dict:
    try:
        logger.info("Starting batch abandoned carts sync (single Magento call)...")

        carts = fetch_all_active_carts_from_magento()
        logger.info(f"Fetched {len(carts)} active carts from Magento")

        magento_id_to_customer = {}
        customers = PSCustomer.query.filter(
            PSCustomer.active == True,
            PSCustomer.deleted_at.is_(None),
        ).all()
        for cust in customers:
            mid = (cust.customer_code_secondary or "").strip()
            if mid:
                try:
                    magento_id_to_customer[int(mid)] = cust.customer_code_365
                except (ValueError, TypeError):
                    pass

        cart_by_customer = {}
        for cart in carts:
            cust_obj = cart.get("customer", {}) or {}
            magento_cust_id = cust_obj.get("id") or cart.get("customer_id")
            if not magento_cust_id:
                continue

            customer_code = magento_id_to_customer.get(int(magento_cust_id))
            if not customer_code:
                continue

            items_list = cart.get("items", [])
            total_amount = Decimal("0")
            for ci in items_list:
                try:
                    price = Decimal(str(ci.get("price", 0) or 0))
                    qty = Decimal(str(ci.get("qty", 0) or 0))
                except Exception:
                    price, qty = Decimal("0"), Decimal("0")
                total_amount += price * qty

            grand_total = Decimal(str(cart.get("grand_total") or total_amount or 0))
            item_count = cart.get("items_count") or len(items_list)

            if customer_code in cart_by_customer:
                cart_by_customer[customer_code]["amount"] += grand_total
                cart_by_customer[customer_code]["items"] += item_count
            else:
                cart_by_customer[customer_code] = {
                    "amount": grand_total,
                    "items": item_count,
                    "magento_id": int(magento_cust_id),
                }

        db.session.execute(text("DELETE FROM crm_abandoned_cart_state"))
        db.session.flush()

        now = datetime.now(timezone.utc)
        customers_with_carts = 0

        for cust in customers:
            cart_info = cart_by_customer.get(cust.customer_code_365)
            has_cart = cart_info is not None
            row = CrmAbandonedCartState(
                customer_code_365=cust.customer_code_365,
                has_abandoned_cart=has_cart,
                abandoned_cart_amount=cart_info["amount"] if cart_info else Decimal("0"),
                abandoned_cart_items=cart_info["items"] if cart_info else 0,
                magento_customer_id=cart_info["magento_id"] if cart_info else None,
                sync_status="OK",
                last_synced_at=now,
            )
            db.session.add(row)
            if has_cart:
                customers_with_carts += 1

        db.session.commit()

        result = {
            "success": True,
            "message": "Abandoned carts synced successfully",
            "customers_processed": len(customers),
            "customers_with_carts": customers_with_carts,
            "triggered_by": triggered_by,
        }
        logger.info(f"Batch sync complete: {result}")
        return result

    except Exception as e:
        db.session.rollback()
        logger.error(f"Batch sync failed: {str(e)}", exc_info=True)
        return {
            "success": False,
            "message": f"Sync failed: {str(e)}",
        }


def get_abandoned_cart_state(customer_code_365: str) -> dict | None:
    row = CrmAbandonedCartState.query.filter_by(customer_code_365=customer_code_365).first()
    if not row:
        return None
    return {
        "has_abandoned_cart": row.has_abandoned_cart,
        "abandoned_cart_amount": float(row.abandoned_cart_amount) if row.abandoned_cart_amount else 0,
        "abandoned_cart_items": row.abandoned_cart_items or 0,
        "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
        "sync_status": row.sync_status,
        "last_error": row.last_error,
    }
