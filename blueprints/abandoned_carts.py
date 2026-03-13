import json
import logging
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from integrations.magento_rest_oauth import magento_rest_get

logger = logging.getLogger(__name__)

abandoned_bp = Blueprint("abandoned_carts", __name__, url_prefix="/abandoned-carts")


@abandoned_bp.route("", methods=["GET"])
@login_required
def index():
    """Render the Abandoned Carts page"""
    if current_user.role not in ["admin", "warehouse_manager"]:
        return "Access denied", 403
    return render_template("customers/abandoned_carts.html")


@abandoned_bp.route("/api/abandoned-carts", methods=["GET"])
@login_required
def api_abandoned_carts():
    """Fetch abandoned carts from Magento"""
    if current_user.role not in ["admin", "warehouse_manager"]:
        return jsonify({"ok": False, "error": "Access denied"}), 403
    
    days = request.args.get("days", 30, type=int)
    limit = request.args.get("limit", 50, type=int)
    search_term = request.args.get("search", "", type=str)
    
    try:
        utc_now = datetime.utcnow()
        cutoff_date = utc_now - timedelta(days=days)
        cutoff_str = cutoff_date.strftime("%Y-%m-%d %H:%M:%S")

        params = {
            "searchCriteria[filterGroups][0][filters][0][field]": "is_active",
            "searchCriteria[filterGroups][0][filters][0][value]": "1",
            "searchCriteria[filterGroups][0][filters][0][conditionType]": "eq",
            "searchCriteria[filterGroups][1][filters][0][field]": "updated_at",
            "searchCriteria[filterGroups][1][filters][0][value]": cutoff_str,
            "searchCriteria[filterGroups][1][filters][0][conditionType]": "gteq",
            "searchCriteria[filterGroups][2][filters][0][field]": "customer_id",
            "searchCriteria[filterGroups][2][filters][0][value]": "0",
            "searchCriteria[filterGroups][2][filters][0][conditionType]": "gt",
            "searchCriteria[pageSize]": str(limit),
            "searchCriteria[currentPage]": "1",
            "searchCriteria[sortOrders][0][field]": "updated_at",
            "searchCriteria[sortOrders][0][direction]": "DESC",
        }

        status_code, response_text = magento_rest_get("/rest/V1/carts/search", params=params)
        
        if status_code != 200:
            return jsonify({
                "ok": False,
                "status": status_code,
                "error": f"Magento returned {status_code}"
            }), status_code
        
        data = json.loads(response_text)
        raw_items = data.get("items", [])

        raw_items = [c for c in raw_items if c.get("items_count") or len(c.get("items", []))]

        if search_term:
            s = search_term.lower()
            def _match(c):
                cust = c.get("customer", {}) or {}
                email = (cust.get("email") or c.get("customer_email") or "").lower()
                fname = (cust.get("firstname") or c.get("customer_firstname") or "").lower()
                lname = (cust.get("lastname") or c.get("customer_lastname") or "").lower()
                return s in email or s in fname or s in lname
            raw_items = [c for c in raw_items if _match(c)]

        carts = []
        total_value = 0

        for cart in raw_items:
            cust = cart.get("customer", {}) or {}
            items_list = cart.get("items", [])
            item_count = cart.get("items_count") or len(items_list)

            subtotal = 0
            for ci in items_list:
                price = float(ci.get("price", 0) or 0)
                qty = float(ci.get("qty", 0) or 0)
                subtotal += price * qty
            grand_total = float(cart.get("grand_total") or subtotal or 0)
            total_value += grand_total

            currency_info = cart.get("currency", {})
            currency_code = "EUR"
            if isinstance(currency_info, dict):
                currency_code = currency_info.get("quote_currency_code") or currency_info.get("base_currency_code") or "EUR"
            elif isinstance(currency_info, str):
                currency_code = currency_info

            cart_data = {
                "entity_id": cart.get("id") or cart.get("entity_id"),
                "customer_id": cust.get("id") or cart.get("customer_id"),
                "customer_email": cust.get("email") or cart.get("customer_email", ""),
                "customer_firstname": cust.get("firstname") or cart.get("customer_firstname", ""),
                "customer_lastname": cust.get("lastname") or cart.get("customer_lastname", ""),
                "customer_group_id": cust.get("group_id") or cart.get("customer_group_id"),
                "item_count": item_count,
                "items": [
                    {
                        "sku": ci.get("sku", ""),
                        "name": ci.get("name", ""),
                        "qty": ci.get("qty", 0),
                        "price": ci.get("price", 0),
                    }
                    for ci in items_list
                ],
                "grand_total": grand_total,
                "currency": currency_code,
                "created_at": cart.get("created_at", ""),
                "updated_at": cart.get("updated_at", ""),
                "store_id": cart.get("store_id"),
            }
            carts.append(cart_data)
        
        return jsonify({
            "ok": True,
            "total_abandoned": len(carts),
            "showing": len(carts),
            "filter_days": days,
            "carts": carts,
            "total_value": round(total_value, 2),
            "average_value": round(total_value / len(carts), 2) if carts else 0
        })
    
    except ValueError as e:
        return jsonify({
            "ok": False,
            "error": f"Configuration error: {str(e)}"
        }), 400
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON from Magento: {e}")
        return jsonify({
            "ok": False,
            "error": "Invalid response from Magento"
        }), 502
    except Exception as e:
        logger.error(f"Abandoned carts API error: {e}")
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500
