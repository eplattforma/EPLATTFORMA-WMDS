"""
Supplier Returns blueprint.

Routes:
  GET  /supplier-returns          — main page (reads from cache)
  POST /supplier-returns/refresh  — force-refresh stock + POs from PS365
  POST /supplier-returns/create-po — send return PO to PS365
"""

import logging
import os
from datetime import datetime, timezone, timedelta

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

logger = logging.getLogger(__name__)

supplier_returns_bp = Blueprint(
    "supplier_returns",
    __name__,
    url_prefix="/supplier-returns",
    template_folder="../templates/supplier_returns",
)


# ---------------------------------------------------------------------------
# Main view — always reads from cache (fast)
# ---------------------------------------------------------------------------

@supplier_returns_bp.route("/")
@login_required
def index():
    from services.supplier_returns_service import get_returns_stock
    data = get_returns_stock(force_refresh=False)
    return render_template("index.html", data=data)


# ---------------------------------------------------------------------------
# Refresh — forces a live PS365 fetch then returns JSON success
# ---------------------------------------------------------------------------

@supplier_returns_bp.route("/refresh", methods=["POST"])
@login_required
def refresh():
    """Force-refresh stock + pending POs from PS365."""
    from services.supplier_returns_service import get_returns_stock
    try:
        get_returns_stock(force_refresh=True)
        return jsonify({"success": True})
    except Exception as e:
        logger.exception("[Returns] Refresh failed")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Create Purchase Order — sends a return PO to PS365
# ---------------------------------------------------------------------------

@supplier_returns_bp.route("/create-po", methods=["POST"])
@login_required
def api_create_po():
    """
    Send a return PO to PS365.
    Quantities are in CASES (raw decimals — bypasses _normalize_order_lines
    which would truncate 0.07 to 0).
    Uses _build_po_lines for proper calculated fields (totals, VAT, etc.).

    order_status_code_365 = "RETURN"
    store_code_365        = "100"
    """
    payload       = request.get_json(silent=True) or {}
    supplier_code = (payload.get("supplier_code_365") or "").strip()
    lines         = payload.get("lines") or []
    comments      = (payload.get("comments") or "").strip()

    if not supplier_code:
        return jsonify({"success": False, "error": "supplier_code_365 required"}), 400

    valid_lines = [
        {
            "item_code_365": (ln.get("item_code_365") or "").strip(),
            "line_quantity":  str(ln.get("line_quantity") or 0),
            **({"cost_price": ln["cost_price"]} if ln.get("cost_price") else {}),
        }
        for ln in lines
        if (ln.get("item_code_365") or "").strip()
        and float(ln.get("line_quantity") or 0) > 0
    ]
    if not valid_lines:
        return jsonify({"success": False, "error": "No lines with quantity > 0"}), 400

    base_url = os.getenv("PS365_BASE_URL", "").rstrip("/")
    token    = os.getenv("PS365_TOKEN", "")
    if not base_url or not token:
        return jsonify({"success": False, "error": "PS365 credentials not configured"}), 503

    # Enrich with VAT data (same path as all other POs in the system)
    try:
        from services.ps365_purchase_order_service import _enrich_pricing
        enriched_lines = _enrich_pricing(valid_lines)
    except Exception:
        logger.warning("[Returns PO] VAT enrichment failed — proceeding without VAT")
        enriched_lines = valid_lines

    from routes_reports import _build_po_lines
    detail_lines, h_totals = _build_po_lines(enriched_lines)

    user_code  = getattr(current_user, "username", "system")
    now_utc    = datetime.now(timezone.utc).replace(microsecond=0)
    deliver_by = (now_utc + timedelta(days=7)).replace(microsecond=0)
    cart_code  = f"RET-{now_utc.strftime('%Y%m%d-%H%M%S')}-{supplier_code}"

    po_payload = {
        "api_credentials": {"token": token},
        "order": {
            "purchase_order_header": {
                "shopping_cart_code":         cart_code,
                "order_date_local":           now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "order_date_utc0":            now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "order_date_deliverby_utc0":  deliver_by.strftime("%Y-%m-%d %H:%M:%S"),
                "supplier_code_365":          supplier_code,
                "store_code_365":             "100",
                "store_name":                 "RETURNS",
                "agent_code_365":             "",
                "user_code_365":              user_code,
                "comments":                   comments or f"Supplier return — {len(detail_lines)} line(s)",
                "search_additional_barcodes": False,
                "order_status_code_365":      "RETURN",
                "order_status_name":          "RETURN",
                **h_totals,
            },
            "list_purchase_order_details": detail_lines,
        },
    }

    try:
        import requests as http_req
        resp = http_req.post(f"{base_url}/purchaseorder", json=po_payload, timeout=45)
        resp.raise_for_status()
        result   = resp.json()
        api_resp = result.get("api_response", {})

        if api_resp.get("response_code") not in ("1", 1):
            raw_msg = (api_resp.get("response_msg")
                       or api_resp.get("response_message")
                       or "Unknown PS365 error")
            # Trim .NET stack traces — keep only the first meaningful line
            msg = raw_msg.split("\n")[0].split(" ---> ")[0].strip()[:200]
            logger.error("[Returns PO] PS365 rejected: %s", raw_msg)
            return jsonify({"success": False, "error": msg}), 422

        po_code = api_resp.get("response_id") or cart_code
        logger.info("[Returns PO] Created PO %s for supplier %s (%d lines)",
                    po_code, supplier_code, len(detail_lines))

        # Persist cart code so the Refresh can do targeted lookups (any age)
        try:
            from app import db
            from models import SupplierReturnPoTracking
            user      = getattr(current_user, "username", None)
            now_local = datetime.now(timezone.utc).replace(tzinfo=None)
            tracking = SupplierReturnPoTracking(
                cart_code         = cart_code,
                po_id_365         = str(po_code),
                supplier_code_365 = supplier_code,
                supplier_name     = payload.get("supplier_name", ""),
                sent_by           = user,
                collected_at      = now_local,
                collected_by      = user,
            )
            db.session.merge(tracking)
            db.session.commit()
        except Exception:
            logger.warning("[Returns PO] Could not save tracking row for %s", cart_code)

        return jsonify({"success": True, "po_code": po_code,
                        "lines_sent": len(detail_lines)})

    except Exception as e:
        logger.exception("[Returns PO] Failed for supplier %s", supplier_code)
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Mark as Collected — local record only, does NOT update PS365
# ---------------------------------------------------------------------------

@supplier_returns_bp.route("/mark-collected", methods=["POST"])
@login_required
def mark_collected():
    """
    Record that a supplier physically collected the items on a return PO.
    This is a local record only — does NOT update PS365.
    The Purchase Return must still be processed in PS365 to deduct stock.
    """
    payload   = request.get_json(silent=True) or {}
    po_id_365 = (payload.get("po_id_365") or "").strip()
    cart_code = (payload.get("cart_code") or "").strip()

    if not po_id_365 and not cart_code:
        return jsonify({"success": False, "error": "po_id_365 or cart_code required"}), 400

    try:
        from app import db
        from models import SupplierReturnPoTracking
        from datetime import datetime, timezone

        now  = datetime.now(timezone.utc).replace(tzinfo=None)
        user = getattr(current_user, "username", "system")

        row = None
        if po_id_365:
            row = SupplierReturnPoTracking.query.filter_by(po_id_365=po_id_365).first()
        if not row and cart_code:
            row = SupplierReturnPoTracking.query.filter_by(cart_code=cart_code).first()

        if not row:
            if cart_code:
                row = SupplierReturnPoTracking(
                    cart_code         = cart_code,
                    po_id_365         = po_id_365 or cart_code,
                    supplier_code_365 = payload.get("supplier_code_365", ""),
                    supplier_name     = payload.get("supplier_name", ""),
                    collected_at      = now,
                    collected_by      = user,
                )
                db.session.add(row)
            else:
                return jsonify({"success": False, "error": "PO not found in tracking table"}), 404
        else:
            row.collected_at = now
            row.collected_by = user

        db.session.commit()
        logger.info("[Returns] PO %s marked as collected by %s", po_id_365 or cart_code, user)
        return jsonify({"success": True, "collected_at": now.strftime("%d/%m/%Y %H:%M")})

    except Exception as e:
        logger.exception("[Returns] mark-collected failed for %s", po_id_365 or cart_code)
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Print slip — acknowledgement slip for a single supplier
# ---------------------------------------------------------------------------

@supplier_returns_bp.route("/print/<supplier_code>")
@login_required
def print_slip(supplier_code):
    """Print-friendly acknowledgement slip for a single supplier."""
    from services.supplier_returns_service import get_returns_stock
    from models import DwItem
    from flask import abort
    result = get_returns_stock(force_refresh=False)
    groups = result.get("groups", [])

    group = next(
        (g for g in groups if g["supplier_code_365"] == supplier_code),
        None
    )
    if group is None:
        abort(404)

    item_codes = [item["item_code_365"] for item in group.get("item_rows", [])]
    if item_codes:
        dw_items = DwItem.query.filter(DwItem.item_code_365.in_(item_codes)).all()
        dw_map   = {d.item_code_365: d for d in dw_items}
    else:
        dw_map = {}

    for item in group.get("item_rows", []):
        dw = dw_map.get(item["item_code_365"])
        item["barcode"]            = (dw.barcode            or "") if dw else ""
        item["supplier_item_code"] = (dw.supplier_item_code or "") if dw else ""

    po_number = request.args.get("po_number", "").strip()

    return render_template(
        "supplier_returns/print_slip.html",
        group=group,
        print_date=datetime.utcnow().strftime("%d/%m/%Y %H:%M"),
        po_number=po_number,
    )
