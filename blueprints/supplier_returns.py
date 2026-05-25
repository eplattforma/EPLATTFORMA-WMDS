"""
Supplier Returns blueprint.

Routes:
  GET  /supplier-returns          — main page
  GET  /api/supplier-returns/data — JSON refresh
  POST /api/supplier-returns/create-po — fire a PO to PS365
"""

import logging
import math

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
            {"item_code_365": "JUI-0016", "line_quantity": 0.33},
            ...
        ],
        "comments": "Optional note"
    }

    Uses the shared create_ps365_purchase_order service (same as forecasting /
    replenishment) so payload shape, VAT/cost enrichment and error handling are
    identical.  Fractional case quantities are ceiling-rounded to the nearest
    whole case (min 1) before being passed to the normaliser.
    """
    from services.ps365_purchase_order_service import create_ps365_purchase_order

    body = request.get_json(silent=True) or {}
    supplier_code = (body.get("supplier_code_365") or "").strip()
    lines = body.get("lines") or []
    comments = (body.get("comments") or "").strip()

    if not supplier_code:
        return jsonify({"success": False, "error": "supplier_code_365 is required"}), 400
    if not lines:
        return jsonify({"success": False, "error": "No lines provided"}), 400

    # Ceiling-round fractional case quantities so nothing is lost.
    # create_ps365_purchase_order's normaliser does int(), which would drop < 1.
    order_lines = []
    for ln in lines:
        code = (ln.get("item_code_365") or "").strip()
        raw_qty = float(ln.get("line_quantity") or 0)
        if not code or raw_qty <= 0:
            continue
        order_lines.append({
            "item_code_365": code,
            "line_quantity": max(1, math.ceil(raw_qty)),
        })

    if not order_lines:
        return jsonify({"success": False, "error": "All quantities are zero"}), 400

    user_code = getattr(current_user, "username", "system")
    auto_comment = comments or f"Supplier return — {len(order_lines)} line(s)"

    result = create_ps365_purchase_order(
        supplier_code=supplier_code,
        order_lines=order_lines,
        user_code=user_code,
        comments=auto_comment,
        cart_prefix="WMDS-RET",
    )

    if result["success"]:
        logger.info("[Returns PO] Created PO %s for supplier %s (%d lines)",
                    result["po_code"], supplier_code, result["lines_count"])
        return jsonify({
            "success": True,
            "po_code": result["po_code"],
            "lines_sent": result["lines_count"],
        })

    logger.error("[Returns PO] Failed for supplier %s: %s", supplier_code, result["error"])
    return jsonify({"success": False, "error": result["error"]}), 422
