"""
Supplier Returns Service — store 100 (RETURNS)

Fetches current stock for store 100 from PS365 via list_stock_items_store,
enriches each item from the local DwItem warehouse (selling_qty, cost_price,
supplier), computes piece count, and returns rows grouped by supplier.

Piece calculation:
  pieces = round(stock * selling_qty)
  where selling_qty is the number of individual pieces per selling unit
  (e.g. 15 for "1X15"). Items with no selling_qty show None for pieces.

PO quantities are passed through as raw decimals (case units) so the ERP
can convert to a Purchase Return correctly.
"""

import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from ps365_client import call_ps365

logger = logging.getLogger(__name__)

RETURNS_STORE_CODE = "100"
PAGE_SIZE = 100
MAX_PAGES = 500


# ---------------------------------------------------------------------------
# Decimal helpers
# ---------------------------------------------------------------------------

def _dec(v) -> Decimal:
    try:
        if v is None or v == "":
            return Decimal("0")
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _to_float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# PS365 fetch — store 100
# ---------------------------------------------------------------------------

def _fetch_store_100_stock() -> List[Dict[str, Any]]:
    """
    Fetch all items with stock > 0 in store 100 via list_stock_items_store.
    Uses the same GET pattern as the store 777 service.
    """
    items: Dict[str, Dict[str, Any]] = {}
    page = 1

    while page <= MAX_PAGES:
        try:
            data = call_ps365(
                "list_stock_items_store",
                {
                    "store_code_365": RETURNS_STORE_CODE,
                    "available_stock_type": "all",
                    "active_type": "all",
                    "ecommerce_type": "all",
                    "page_number": page,
                    "page_size": PAGE_SIZE,
                },
                method="GET",
            )
        except Exception as e:
            logger.error("[Returns] PS365 error on page %d: %s", page, e)
            break

        rows = (
            data.get("list_stock_stores_item")
            or data.get("list_stock_items_store")
            or []
        )

        if not rows:
            break

        for r in rows:
            code = (r.get("item_code_365") or "").strip()
            if not code:
                continue
            stock = _dec(r.get("stock"))
            if stock <= 0:
                continue
            items[code] = {
                "item_code_365": code,
                "item_name": (r.get("item_name") or "").strip(),
                "stock": stock,
            }

        if len(rows) < PAGE_SIZE:
            break
        page += 1

    return list(items.values())


# ---------------------------------------------------------------------------
# DwItem enrichment
# ---------------------------------------------------------------------------

def _enrich_from_dw(stock_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Join PS365 stock rows with DwItem to get:
      - selling_qty  (pieces per selling unit)
      - cost_price
      - supplier_code_365 / supplier_name
      - item_name override (DwItem name is usually cleaner)
    """
    if not stock_rows:
        return []

    from models import DwItem

    codes = [r["item_code_365"] for r in stock_rows]
    dw_items = DwItem.query.filter(DwItem.item_code_365.in_(codes)).all()
    dw_map = {d.item_code_365: d for d in dw_items}

    enriched = []
    for r in stock_rows:
        code = r["item_code_365"]
        dw = dw_map.get(code)
        stock = r["stock"]

        selling_qty = _dec(dw.selling_qty) if dw and dw.selling_qty else None
        cost_price = _dec(dw.cost_price) if dw and dw.cost_price is not None else None

        # Piece count: round(stock * selling_qty)
        if selling_qty and selling_qty > 0:
            pieces = int((stock * selling_qty).to_integral_value(rounding=ROUND_HALF_UP))
        else:
            pieces = None

        # Value = stock (cases) x cost_price
        value = (stock * cost_price) if cost_price else None

        enriched.append({
            "item_code_365": code,
            "item_name": (dw.item_name if dw and dw.item_name else r["item_name"]),
            "stock": stock,
            "stock_display": f"{float(stock):.4g}",
            "selling_qty": float(selling_qty) if selling_qty else None,
            "pieces": pieces,
            "cost_price": float(cost_price) if cost_price else None,
            "value": float(value.quantize(Decimal("0.001"))) if value else None,
            "supplier_code_365": (dw.supplier_code_365 or "").strip() if dw else "",
            "supplier_name": (dw.supplier_name or "").strip() if dw else "",
        })

    return enriched


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def _group_by_supplier(
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Returns a list of supplier groups, each:
    {
        "supplier_code_365": str,
        "supplier_name": str,
        "total_value": float,
        "items": [ {...row...}, ... ]
    }
    Rows with no supplier go into a group with supplier_code_365="" and
    supplier_name="Unknown Supplier" at the end of the list.
    """
    buckets: Dict[str, Dict[str, Any]] = {}

    for r in rows:
        key = r["supplier_code_365"] or ""
        if key not in buckets:
            buckets[key] = {
                "supplier_code_365": key,
                "supplier_name": r["supplier_name"] or ("Unknown Supplier" if not key else key),
                "total_pieces": 0,
                "total_cost": 0.0,
                "total_value": 0.0,
                "item_list": [],
            }
        buckets[key]["item_list"].append(r)
        if r["pieces"]:
            buckets[key]["total_pieces"] += r["pieces"]
        if r["value"]:
            buckets[key]["total_value"] = round(buckets[key]["total_value"] + r["value"], 2)
        if r["cost_price"] and r["stock"]:
            buckets[key]["total_cost"] = round(
                buckets[key]["total_cost"] + float(r["cost_price"]) * float(r["stock"]), 2
            )

    # Sort: known suppliers alphabetically, unknown last
    groups = sorted(
        buckets.values(),
        key=lambda g: ("" if g["supplier_code_365"] else "\xff", g["supplier_name"].lower()),
    )
    return groups


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_returns_stock() -> Dict[str, Any]:
    """
    Fetch and return all data needed for the Returns screen.

    Returns:
        {
            "groups": [ supplier_group, ... ],
            "total_items": int,
            "total_value": float,
            "unknown_supplier_count": int,
            "error": str | None,
        }
    """
    try:
        stock_rows = _fetch_store_100_stock()
    except Exception as e:
        logger.exception("[Returns] Failed to fetch store 100 stock")
        return {"groups": [], "total_items": 0, "total_value": 0.0,
                "unknown_supplier_count": 0, "error": str(e)}

    enriched = _enrich_from_dw(stock_rows)
    groups = _group_by_supplier(enriched)

    total_value = round(sum(g["total_value"] for g in groups), 3)
    total_items = sum(len(g["item_list"]) for g in groups)
    unknown_count = sum(
        len(g["item_list"]) for g in groups if not g["supplier_code_365"]
    )

    return {
        "groups": groups,
        "total_items": total_items,
        "total_value": total_value,
        "unknown_supplier_count": unknown_count,
        "error": None,
    }
