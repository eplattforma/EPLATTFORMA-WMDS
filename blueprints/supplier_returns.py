"""
Supplier Returns blueprint.

Routes:
  GET  /supplier-returns          — main page
  GET  /api/supplier-returns/data — JSON refresh
  POST /api/supplier-returns/create-po — fire a PO to PS365
"""

import logging
import os
from datetime import datetime, timezone

from flask import (
    Blueprint, jsonify, render_template,
    request,
)
from flask_login import current_user, login_required

logger = logging.getLogger(__name__)

supplier_returns_bp = Blueprint(
    "supplier_returns",
    __name__,
    url_prefix="/supplier-returns",
    template_folder="../templates/supplier_returns",
)


# ---------------------------------------------------------------------------
# Main view
# ---------------------------------------------------------------------------

@supplier_returns_bp.route("/")
@login_required
def index():
    from services.supplier_returns_service import get_returns_stock
    data = get_returns_stock()
    return render_template("index.html", data=data)


# ---------------------------------------------------------------------------
# JSON refresh (called by the Refresh button — no full page reload)
# ---------------------------------------------------------------------------

@supplier_returns_bp.route("/data")
@login_required
def api_data():
    from services.supplier_returns_service import get_returns_stock
    data = get_returns_stock()
    return jsonify(data)


# ---------------------------------------------------------------------------
# Create Purchase Order
# ---------------------------------------------------------------------------

@supplier_returns_bp.route("/create-po", methods=["POST"])
@login_required
def api_create_po():
    """
    Accepts JSON:
    {
        "supplier_code_365": "SUP001",
        "lines": [
            {"item_code_365": "JUI-0016", "line_quantity": 0.07, "cost_price": 11.55},
            ...
        ],
        "comments": "Optional note"
    }

    Quantity is in CASES (raw decimal from PS365 stock). The existing
    create_ps365_purchase_order normalises quantities to int, which would
    drop fractional case values — so this route calls the PS365 API directly
    using the same endpoint and credential pattern, preserving decimals.
    """
    payload = request.get_json(silent=True) or {}
    supplier_code = (payload.get("supplier_code_365") or "").strip()
    lines = payload.get("lines") or []
    comments = payload.get("comments") or ""

    if not supplier_code:
        return jsonify({"success": False, "error": "supplier_code_365 is required"}), 400
    if not lines:
        return jsonify({"success": False, "error": "No lines provided"}), 400

    # Filter out zero quantities
    valid_lines = [
        ln for ln in lines
        if ln.get("item_code_365") and float(ln.get("line_quantity") or 0) > 0
    ]
    if not valid_lines:
        return jsonify({"success": False, "error": "All quantities are zero"}), 400

    base_url = os.getenv("PS365_BASE_URL", "").rstrip("/")
    token = os.getenv("PS365_TOKEN", "")
    if not base_url or not token:
        return jsonify({"success": False, "error": "PS365 credentials not configured"}), 503

    now_utc = datetime.now(timezone.utc)
    user_code = getattr(current_user, "username", "system")
    cart_code = f"RET-{now_utc.strftime('%Y%m%d-%H%M%S')}-{supplier_code}"

    detail_lines = []
    for ln in valid_lines:
        detail = {
            "item_code_365": ln["item_code_365"],
            "line_quantity": f"{float(ln['line_quantity']):.4f}".rstrip("0").rstrip("."),
        }
        if ln.get("cost_price") is not None:
            detail["cost_price"] = ln["cost_price"]
        detail_lines.append(detail)

    po_payload = {
        "api_credentials": {"token": token},
        "order": {
            "shopping_cart_code": cart_code,
            "purchase_order_header": {
                "supplier_code_365": supplier_code,
                "user_code_365": user_code,
                "delivery_date": now_utc.strftime("%Y-%m-%d"),
                "comments": comments or f"Supplier return — {len(detail_lines)} line(s)",
            },
            "list_purchase_order_details": detail_lines,
        },
    }

    try:
        import requests as http_req
        url = f"{base_url}/purchaseorder"
        resp = http_req.post(url, json=po_payload, timeout=60)
        resp.raise_for_status()
        result = resp.json()

        api_resp = result.get("api_response", {})
        if api_resp.get("response_code") not in ("1", "200", 1, 200):
            msg = api_resp.get("response_msg") or "Unknown PS365 error"
            logger.error("[Returns PO] PS365 rejected: %s", msg)
            return jsonify({"success": False, "error": msg}), 422

        po_code = (
            result.get("purchase_order_code")
            or result.get("po_code")
            or result.get("order_code")
            or cart_code
        )
        logger.info("[Returns PO] Created PO %s for supplier %s (%d lines)",
                    po_code, supplier_code, len(detail_lines))
        return jsonify({"success": True, "po_code": po_code,
                        "lines_sent": len(detail_lines)})

    except Exception as e:
        logger.exception("[Returns PO] Failed for supplier %s", supplier_code)
        return jsonify({"success": False, "error": str(e)}), 500
