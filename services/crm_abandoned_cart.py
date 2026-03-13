import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from app import db
from models import PSCustomer, CrmAbandonedCartState
from integrations.magento_rest_oauth import magento_rest_get

logger = logging.getLogger(__name__)


def upsert_abandoned_cart_state(customer_code_365: str, has_cart: bool,
                                amount: Decimal | None, item_count: int | None,
                                magento_id: int | None = None,
                                err: str | None = None):
    row = CrmAbandonedCartState.query.filter_by(customer_code_365=customer_code_365).first()
    if not row:
        row = CrmAbandonedCartState(customer_code_365=customer_code_365)
        db.session.add(row)

    if magento_id:
        row.magento_customer_id = magento_id

    if err:
        row.sync_status = "FAIL"
        row.last_error = err[:500]
        row.last_synced_at = datetime.now(timezone.utc)
        db.session.commit()
        return

    row.has_abandoned_cart = bool(has_cart)
    row.abandoned_cart_amount = amount
    row.abandoned_cart_items = item_count
    row.sync_status = "OK"
    row.last_error = None
    row.last_synced_at = datetime.now(timezone.utc)
    db.session.commit()


def refresh_abandoned_cart_for_customer(customer_code_365: str) -> dict:
    cust = PSCustomer.query.filter_by(customer_code_365=customer_code_365).first()
    if not cust:
        raise ValueError(f"Customer {customer_code_365} not found")

    magento_id_str = (cust.customer_code_secondary or "").strip()
    if not magento_id_str:
        upsert_abandoned_cart_state(customer_code_365, False, None, None, err="No Magento customer ID (customer_code_secondary)")
        return {"has_cart": False, "error": "No Magento customer ID"}

    try:
        magento_id = int(magento_id_str)
    except (ValueError, TypeError):
        upsert_abandoned_cart_state(customer_code_365, False, None, None, err=f"Invalid Magento ID: {magento_id_str}")
        return {"has_cart": False, "error": f"Invalid Magento ID: {magento_id_str}"}

    params = {
        "searchCriteria[filterGroups][0][filters][0][field]": "is_active",
        "searchCriteria[filterGroups][0][filters][0][value]": "1",
        "searchCriteria[filterGroups][0][filters][0][conditionType]": "eq",
        "searchCriteria[filterGroups][1][filters][0][field]": "customer_id",
        "searchCriteria[filterGroups][1][filters][0][value]": str(magento_id),
        "searchCriteria[filterGroups][1][filters][0][conditionType]": "eq",
        "searchCriteria[pageSize]": "10",
        "searchCriteria[currentPage]": "1",
    }

    try:
        status_code, response_text = magento_rest_get("/rest/V1/carts/search", params=params)
    except Exception as e:
        upsert_abandoned_cart_state(customer_code_365, False, None, None, magento_id=magento_id, err=str(e)[:200])
        raise

    if status_code != 200:
        err_msg = f"Magento returned {status_code}"
        upsert_abandoned_cart_state(customer_code_365, False, None, None, magento_id=magento_id, err=err_msg)
        raise RuntimeError(err_msg)

    try:
        data = json.loads(response_text)
    except (json.JSONDecodeError, TypeError) as e:
        upsert_abandoned_cart_state(customer_code_365, False, None, None, magento_id=magento_id, err="Invalid JSON from Magento")
        raise RuntimeError("Invalid JSON response from Magento") from e

    carts = data.get("items", [])
    carts_with_items = [c for c in carts if c.get("items_count") or len(c.get("items", []))]

    if not carts_with_items:
        upsert_abandoned_cart_state(customer_code_365, False, Decimal("0"), 0, magento_id=magento_id)
        return {"has_cart": False, "amount": 0, "items": 0}

    total_amount = Decimal("0")
    total_items = 0
    for cart in carts_with_items:
        items_list = cart.get("items", [])
        for ci in items_list:
            try:
                price = Decimal(str(ci.get("price", 0) or 0))
                qty = Decimal(str(ci.get("qty", 0) or 0))
            except Exception:
                price, qty = Decimal("0"), Decimal("0")
            total_amount += price * qty
        total_items += cart.get("items_count") or len(items_list)

    upsert_abandoned_cart_state(customer_code_365, True, total_amount, total_items, magento_id=magento_id)
    return {"has_cart": True, "amount": float(total_amount), "items": total_items}


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
