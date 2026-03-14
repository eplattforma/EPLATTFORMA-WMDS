import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from app import db
from models import PSCustomer, CrmAbandonedCartState
from integrations.magento_rest_oauth import magento_rest_get
from sqlalchemy import text

logger = logging.getLogger(__name__)


def get_all_customer_carts_batch() -> dict:
    """
    Fetch abandoned carts for all active customers from Magento in a single batch operation.
    Returns {customer_code_365: {has_cart, amount, items}, ...}
    """
    try:
        customers = PSCustomer.query.filter(
            PSCustomer.active == True,
            PSCustomer.deleted_at.is_(None)
        ).all()
        
        results = {}
        for cust in customers:
            magento_id_str = (cust.customer_code_secondary or "").strip()
            if not magento_id_str:
                results[cust.customer_code_365] = {"has_cart": False, "amount": 0, "items": 0, "error": "No Magento ID"}
                continue
            
            try:
                magento_id = int(magento_id_str)
            except (ValueError, TypeError):
                results[cust.customer_code_365] = {"has_cart": False, "amount": 0, "items": 0, "error": "Invalid Magento ID"}
                continue
            
            try:
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
                
                status_code, response_text = magento_rest_get("/rest/V1/carts/search", params=params)
                
                if status_code != 200:
                    results[cust.customer_code_365] = {"has_cart": False, "amount": 0, "items": 0, "error": f"HTTP {status_code}"}
                    continue
                
                data = json.loads(response_text)
                carts = data.get("items", [])
                carts_with_items = [c for c in carts if c.get("items_count") or len(c.get("items", []))]
                
                if not carts_with_items:
                    results[cust.customer_code_365] = {"has_cart": False, "amount": 0, "items": 0}
                    continue
                
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
                
                results[cust.customer_code_365] = {
                    "has_cart": True,
                    "amount": float(total_amount),
                    "items": total_items,
                }
            except Exception as e:
                logger.error(f"Error fetching cart for customer {cust.customer_code_365}: {str(e)}")
                results[cust.customer_code_365] = {"has_cart": False, "amount": 0, "items": 0, "error": str(e)[:100]}
        
        return results
    except Exception as e:
        logger.error(f"Batch cart fetch failed: {str(e)}")
        raise


def sync_abandoned_carts_batch(triggered_by: str = "manual") -> dict:
    """
    Perform batch refresh of all abandoned carts from Magento.
    Stores all results in database in a single transaction.
    """
    try:
        logger.info("Starting batch abandoned carts sync...")
        carts_data = get_all_customer_carts_batch()
        
        # Clear all old cart state and insert new data
        db.session.execute(text("DELETE FROM crm_abandoned_cart_state"))
        db.session.flush()
        
        now = datetime.now(timezone.utc)
        inserted_count = 0
        
        for customer_code_365, cart_info in carts_data.items():
            row = CrmAbandonedCartState(
                customer_code_365=customer_code_365,
                has_abandoned_cart=cart_info.get("has_cart", False),
                abandoned_cart_amount=Decimal(str(cart_info.get("amount", 0))),
                abandoned_cart_items=cart_info.get("items", 0),
                sync_status="OK" if not cart_info.get("error") else "FAIL",
                last_error=cart_info.get("error"),
                last_synced_at=now,
            )
            db.session.add(row)
            if cart_info.get("has_cart"):
                inserted_count += 1
        
        db.session.commit()
        
        result = {
            "success": True,
            "message": "Abandoned carts synced successfully",
            "customers_processed": len(carts_data),
            "customers_with_carts": inserted_count,
            "triggered_by": triggered_by,
        }
        
        logger.info(f"Batch sync complete: {result}")
        return result
    except Exception as e:
        db.session.rollback()
        logger.error(f"Batch sync failed: {str(e)}")
        return {
            "success": False,
            "message": f"Sync failed: {str(e)}",
            "error": str(e),
        }


def get_abandoned_cart_state(customer_code_365: str) -> dict | None:
    """Get abandoned cart state for a specific customer from database."""
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
