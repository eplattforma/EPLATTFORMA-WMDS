"""Shared PS365 Purchase Order sender.

Single source of truth for building and posting purchase orders to the PS365
/purchaseorder endpoint.

Both replenishment and forecasting supplier-ordering go through
``create_ps365_purchase_order`` so that payload shape, pricing/VAT enrichment
and error handling stay consistent.
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta

import requests as http_requests

logger = logging.getLogger(__name__)


def _ps365_config():
    base_url = os.getenv("PS365_BASE_URL", "").rstrip("/")
    token = os.getenv("PS365_TOKEN", "")
    return base_url, token


def _normalize_order_lines(order_lines):
    """Validate / coerce caller-supplied order_lines.

    Each line must contain at least item_code_365 and a positive line_quantity.
    Optional fields cost_price, vat_code_365, vat_percent are passed through
    if already present (caller may pre-enrich); otherwise they are filled in
    by ``_enrich_pricing``.
    """
    cleaned = []
    for ln in order_lines or []:
        code = (ln.get("item_code_365") or "").strip()
        try:
            qty = int(float(ln.get("line_quantity") or 0))
        except (TypeError, ValueError):
            qty = 0
        if not code or qty <= 0:
            continue
        out = {
            "item_code_365": code,
            "line_quantity": str(qty),
        }
        if ln.get("cost_price") is not None:
            out["cost_price"] = ln["cost_price"]
        if ln.get("vat_code_365"):
            out["vat_code_365"] = ln["vat_code_365"]
        if ln.get("vat_percent") is not None:
            out["vat_percent"] = ln["vat_percent"]
        cleaned.append(out)
    return cleaned


def _enrich_pricing(order_lines):
    """Fill in cost_price / vat_code_365 / vat_percent from DwItem and PS365.

    Mirrors the pricing-enrichment flow used historically by the replenishment
    PO sender so behavior stays identical.
    """
    from models import DwItem
    from routes_reports import _fetch_item_pricing_from_ps365

    item_codes = [l["item_code_365"] for l in order_lines]
    if not item_codes:
        return order_lines

    dw_items = DwItem.query.filter(DwItem.item_code_365.in_(item_codes)).all()
    dw_map = {d.item_code_365: d for d in dw_items}

    missing_pricing_codes = [
        code for code in item_codes
        if not dw_map.get(code)
        or dw_map[code].cost_price is None
        or not dw_map[code].vat_code_365
        or dw_map[code].vat_percent is None
    ]
    ps365_pricing = _fetch_item_pricing_from_ps365(missing_pricing_codes) if missing_pricing_codes else {}
    if ps365_pricing:
        dw_items = DwItem.query.filter(DwItem.item_code_365.in_(item_codes)).all()
        dw_map = {d.item_code_365: d for d in dw_items}

    enriched = []
    for line in order_lines:
        code = line["item_code_365"]
        dw = dw_map.get(code)
        ps_price = ps365_pricing.get(code, {})

        cost = line.get("cost_price")
        if cost is None:
            cost = (float(dw.cost_price) if dw and dw.cost_price is not None and float(dw.cost_price) > 0 else None)
        if cost is None:
            ps_cost = ps_price.get("cost_price")
            if ps_cost is not None and ps_cost > 0:
                cost = ps_cost

        vat = line.get("vat_code_365") or (dw.vat_code_365 if dw and dw.vat_code_365 else None) or ps_price.get("vat_code_365")
        vat_pct = line.get("vat_percent")
        if vat_pct is None:
            vat_pct = (float(dw.vat_percent) if dw and dw.vat_percent is not None else None)
        if vat_pct is None:
            vat_pct = ps_price.get("vat_percent")

        out = {
            "item_code_365": code,
            "line_quantity": line["line_quantity"],
        }
        if cost is not None:
            out["cost_price"] = cost
        if vat:
            out["vat_code_365"] = vat
        if vat_pct is not None:
            out["vat_percent"] = vat_pct
        enriched.append(out)
    return enriched


def build_ps365_po_payload(supplier_code, order_lines, user_code, comments=None,
                           cart_prefix="WMDS", deliver_in_days=7):
    """Build the full PS365 /purchaseorder payload from enriched order_lines."""
    from routes_reports import _build_po_lines

    _, ps365_token = _ps365_config()
    detail_lines, h_totals = _build_po_lines(order_lines)

    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    deliver_by_utc = (now_utc + timedelta(days=deliver_in_days)).replace(microsecond=0)
    shopping_cart_code = f"{cart_prefix}-{now_utc.strftime('%Y%m%d-%H%M%S')}-{supplier_code}"

    payload = {
        "api_credentials": {"token": ps365_token},
        "order": {
            "purchase_order_header": {
                "shopping_cart_code": shopping_cart_code,
                "order_date_local": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "order_date_utc0": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "order_date_deliverby_utc0": deliver_by_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "supplier_code_365": supplier_code,
                "agent_code_365": "",
                "user_code_365": user_code,
                "comments": comments or f"PO {supplier_code} - {len(order_lines)} items",
                "search_additional_barcodes": False,
                "order_status_code_365": "PROC",
                "order_status_name": "PROCESSING",
                **h_totals,
            },
            "list_purchase_order_details": detail_lines,
        }
    }
    return payload, shopping_cart_code, now_utc


def send_ps365_po(payload, timeout=120):
    """POST a built payload to PS365 /purchaseorder. Returns parsed JSON."""
    base_url, _ = _ps365_config()
    url = f"{base_url}/purchaseorder"
    resp = http_requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def create_ps365_purchase_order(supplier_code, order_lines, user_code,
                                comments=None, cart_prefix="WMDS",
                                deliver_in_days=7):
    """High-level entry point: enrich, build payload, post to PS365.

    Args:
        supplier_code: PS365 supplier_code_365 (string).
        order_lines: list of dicts with at least
            ``{"item_code_365": str, "line_quantity": int}``.
            Optional pre-filled ``cost_price`` / ``vat_code_365`` / ``vat_percent``
            are honored; missing fields are enriched from DwItem then PS365.
        user_code: user identifier stored on the PS365 PO header.
        comments: header comments text (defaults to a generic summary).
        cart_prefix: prefix of the generated shopping_cart_code, e.g.
            ``"WMDS-RPL"`` for replenishment, ``"WMDS-FCT"`` for forecasting.

    Returns:
        dict with keys:
            success (bool)
            po_code (str|None)
            error (str|None)
            lines_count (int)
            shopping_cart_code (str|None)
            api_response (dict): raw PS365 api_response block when available
    """
    base_url, token = _ps365_config()
    if not base_url or not token:
        return {
            "success": False, "po_code": None, "error": "PS365 API not configured.",
            "lines_count": 0, "shopping_cart_code": None, "api_response": {},
        }
    if not supplier_code:
        return {
            "success": False, "po_code": None, "error": "Supplier code is required.",
            "lines_count": 0, "shopping_cart_code": None, "api_response": {},
        }

    cleaned = _normalize_order_lines(order_lines)
    if not cleaned:
        return {
            "success": False, "po_code": None,
            "error": "No order lines with positive quantity.",
            "lines_count": 0, "shopping_cart_code": None, "api_response": {},
        }

    enriched = _enrich_pricing(cleaned)
    payload, cart_code, _ = build_ps365_po_payload(
        supplier_code=supplier_code,
        order_lines=enriched,
        user_code=user_code,
        comments=comments,
        cart_prefix=cart_prefix,
    )

    try:
        logger.info(
            "PS365 PO send: supplier=%s lines=%d cart=%s prefix=%s",
            supplier_code, len(enriched), cart_code, cart_prefix,
        )
        for ln in enriched:
            logger.info("  line: %s qty=%s", ln["item_code_365"], ln["line_quantity"])
        logger.debug("PS365 PO detail sample: %s",
                     json.dumps(payload["order"]["list_purchase_order_details"][:3], indent=2))

        result = send_ps365_po(payload)
        api_response = result.get("api_response", {}) or {}
        if api_response.get("response_code") == "1":
            po_code = api_response.get("response_id", "Unknown")
            logger.info("PS365 PO created: %s for supplier %s (%d lines)",
                        po_code, supplier_code, len(enriched))
            return {
                "success": True, "po_code": po_code, "error": None,
                "lines_count": len(enriched), "shopping_cart_code": cart_code,
                "api_response": api_response,
            }
        error_msg = api_response.get("response_msg", "Unknown error")
        logger.error("PS365 PO creation failed for supplier %s: %s",
                     supplier_code, api_response)
        return {
            "success": False, "po_code": None, "error": error_msg,
            "lines_count": len(enriched), "shopping_cart_code": cart_code,
            "api_response": api_response,
        }
    except Exception as e:
        logger.exception("Failed to POST PS365 PO for supplier %s", supplier_code)
        return {
            "success": False, "po_code": None, "error": str(e),
            "lines_count": len(enriched), "shopping_cart_code": cart_code,
            "api_response": {},
        }
