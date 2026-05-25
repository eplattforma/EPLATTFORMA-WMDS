"""
Supplier Returns blueprint.

Routes:
  GET  /supplier-returns          — main page
  GET  /api/supplier-returns/data — JSON refresh
  POST /api/supplier-returns/create-po — fire a PO to PS365
"""

import logging
from datetime import datetime, timezone, timedelta

from flask import (
    Blueprint, jsonify, redirect, render_template,
    request, url_for,
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
    if "refresh" in request.args:
        get_returns_stock(force=True)
        return redirect(url_for("supplier_returns.index"))
    data = get_returns_stock(force=False)
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
            {"item_code_365": "JUI-0016", "line_quantity": 0.33, "cost_price": 11.55},
            ...
        ],
        "comments": "Optional note"
    }

    Builds the PS365 PO directly (bypassing create_ps365_purchase_order's
    int()-normaliser) so that:
      - line_quantity is sent as raw cases (fractional, e.g. 0.33)
      - store_code_365 is forced to "100" (RETURNS store)
      - order_status_code_365 / order_status_name are set to "RETURN"
    VAT enrichment is handled by the shared _enrich_pricing helper.
    """
    from services.ps365_purchase_order_service import (
        _enrich_pricing, _ps365_config, send_ps365_po,
    )
    from routes_reports import _build_po_lines

    body = request.get_json(silent=True) or {}
    supplier_code = (body.get("supplier_code_365") or "").strip()
    lines = body.get("lines") or []
    comments = (body.get("comments") or "").strip()

    if not supplier_code:
        return jsonify({"success": False, "error": "supplier_code_365 is required"}), 400
    if not lines:
        return jsonify({"success": False, "error": "No lines provided"}), 400

    # Keep raw case quantities (fractional). Pass cost_price through so
    # _enrich_pricing only needs to resolve VAT fields from DwItem / PS365.
    order_lines = []
    for ln in lines:
        code = (ln.get("item_code_365") or "").strip()
        try:
            raw_qty = float(ln.get("line_quantity") or 0)
        except (TypeError, ValueError):
            raw_qty = 0.0
        if not code or raw_qty <= 0:
            continue
        entry = {"item_code_365": code, "line_quantity": raw_qty}
        if ln.get("cost_price") is not None:
            try:
                entry["cost_price"] = float(ln["cost_price"])
            except (TypeError, ValueError):
                pass
        order_lines.append(entry)

    if not order_lines:
        return jsonify({"success": False, "error": "All quantities are zero"}), 400

    # Enrich with vat_code_365 / vat_percent from DwItem (and PS365 fallback)
    enriched = _enrich_pricing(order_lines)

    # Build line-level detail + header totals using the shared calculator
    detail_lines, h_totals = _build_po_lines(enriched)

    # Assemble the full payload with returns-specific overrides
    _, ps365_token = _ps365_config()
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    deliver_by = (now_utc + timedelta(days=7)).replace(microsecond=0)
    cart_code = f"WMDS-RET-{now_utc.strftime('%Y%m%d-%H%M%S')}-{supplier_code}"
    user_code = getattr(current_user, "username", "system")
    auto_comment = comments or f"Supplier return — {len(enriched)} line(s)"

    payload = {
        "api_credentials": {"token": ps365_token},
        "order": {
            "purchase_order_header": {
                "shopping_cart_code": cart_code,
                "order_date_local": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "order_date_utc0": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "order_date_deliverby_utc0": deliver_by.strftime("%Y-%m-%d %H:%M:%S"),
                "supplier_code_365": supplier_code,
                "store_code_365": "100",
                "agent_code_365": "",
                "user_code_365": user_code,
                "comments": auto_comment,
                "search_additional_barcodes": False,
                "order_status_code_365": "RETURN",
                "order_status_name": "RETURN",
                **h_totals,
            },
            "list_purchase_order_details": detail_lines,
        },
    }

    try:
        result = send_ps365_po(payload)
        api_response = result.get("api_response", {}) or {}
        if api_response.get("response_code") == "1":
            po_code = api_response.get("response_id", cart_code)
            logger.info("[Returns PO] Created PO %s for supplier %s (%d lines)",
                        po_code, supplier_code, len(enriched))
            return jsonify({"success": True, "po_code": po_code,
                            "lines_sent": len(enriched)})
        error_msg = api_response.get("response_msg") or "Unknown PS365 error"
        logger.error("[Returns PO] PS365 rejected for %s: %s", supplier_code, api_response)
        return jsonify({"success": False, "error": error_msg}), 422
    except Exception as exc:
        logger.exception("[Returns PO] Failed for supplier %s", supplier_code)
        return jsonify({"success": False, "error": str(exc)}), 500
