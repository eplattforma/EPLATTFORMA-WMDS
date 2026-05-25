"""
Supplier Returns Service — store 100 (RETURNS)

Cache strategy
--------------
PS365 is called only when:
  - force_refresh=True  (Refresh button)
  - cache is empty or older than CACHE_TTL_MINUTES

Two PS365 calls on each refresh, cached as a unit:
  1. list_stock_items_store  → current stock in store 100
  2. list_purchase_orders (stores_selection=100) → outstanding return PO quantities

Displayed quantity = PS365 stock − outstanding PO qty
  where outstanding = line_quantity − line_quantity_received

When PS365 processes the Purchase Return:
  - line_quantity_received fills in automatically
  - is_pending flips to false
  - Items reappear as available on next Refresh, with no manual action needed
"""

import logging
import threading
from datetime import datetime, date, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from ps365_client import call_ps365

logger = logging.getLogger(__name__)

RETURNS_STORE_CODE = "100"
PAGE_SIZE          = 100
MAX_PAGES          = 500
CACHE_TTL_MINUTES  = 30

# Statuses that mean the PO is still open / items not yet fully returned
PO_OPEN_STATUSES = {"RETURN", "PROC", "PROCESSING", "PENDING", "ORDERED",
                    "APPROVED", "OPEN", "RET"}

# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------
_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {"rows": None, "pending_pos": None, "fetched_at": None}


def _cache_age_minutes() -> Optional[float]:
    t = _cache.get("fetched_at")
    if t is None:
        return None
    return (datetime.now(timezone.utc) - t).total_seconds() / 60


def _cache_is_valid() -> bool:
    age = _cache_age_minutes()
    return age is not None and age < CACHE_TTL_MINUTES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dec(v) -> Decimal:
    try:
        if v is None or v == "":
            return Decimal("0")
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _stock_display(v: Decimal) -> str:
    f = float(v)
    if f == int(f):
        return str(int(f))
    return f"{f:.4g}"


# ---------------------------------------------------------------------------
# PS365 — stock fetch
# ---------------------------------------------------------------------------

def _fetch_store_100_stock() -> List[Dict[str, Any]]:
    items: Dict[str, Dict[str, Any]] = {}
    page = 1
    while page <= MAX_PAGES:
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
                "stock_ps365": stock,
            }
        if len(rows) < PAGE_SIZE:
            break
        page += 1
    return list(items.values())


# ---------------------------------------------------------------------------
# PS365 — pending return POs for store 100
# ---------------------------------------------------------------------------

def _fetch_pending_return_pos() -> tuple:
    """
    Query list_purchase_orders filtered by stores_selection="100".

    Returns:
        pending_qty:  {item_code_365: outstanding_cases}   (for netting)
        po_list:      list of PO summaries for the Pending POs tab
    """
    today     = date.today()
    from_date = (today - timedelta(days=180)).isoformat()
    to_date   = (today + timedelta(days=365)).isoformat()

    try:
        resp = call_ps365("list_purchase_orders", method="POST", payload={
            "filter_define": {
                "page_number": 1,
                "page_size":   100,
                "only_counted": "N",
                "orders_supplier_selection":    "",
                "order_status_selection":       "",
                "from_date":                    from_date,
                "to_date":                      to_date,
                "items_selection":              "",
                "stores_selection":             "",
                "orders_type":                  "all",
                "shopping_cart_code_selection": "",
            }
        })
    except Exception as e:
        logger.warning("[Returns] Could not fetch pending POs from PS365: %s", e)
        return {}, []

    if not resp or resp.get("api_response", {}).get("response_code") != "1":
        logger.warning("[Returns] list_purchase_orders returned non-1: %s",
                       resp.get("api_response", {}) if resp else "no response")
        return {}, []

    raw_pos = resp.get("list_purchase_orders") or []
    logger.info("[Returns] list_purchase_orders raw count: %d", len(raw_pos))

    pending_qty: Dict[str, Decimal] = {}
    po_list: List[Dict[str, Any]] = []

    for po in raw_pos:
        header      = po.get("purchase_order_header", {})
        status_code = (header.get("order_status_code") or "").upper().strip()
        is_pending  = header.get("is_pending", False)
        po_store    = str(header.get("store_code_365") or "").strip()
        po_id       = header.get("purchase_order_id") or header.get("purchase_order_code") or "?"
        supplier    = header.get("supplier_code_365", "")
        sup_name    = header.get("supplier_name", "")
        comments    = header.get("comments", "")
        order_date  = (header.get("order_date_local") or "")[:16]

        # Client-side filter: keep POs for the RETURNS store OR with RETURN status
        is_return_store  = (po_store == RETURNS_STORE_CODE)
        is_return_status = (status_code == "RETURN")
        if not is_return_store and not is_return_status:
            logger.debug("[Returns] PO %s store=%s status=%s — skipping",
                         po_id, po_store, status_code)
            continue

        logger.info("[Returns] PO %s store=%s status=%s is_pending=%s — evaluating",
                    po_id, po_store, status_code, is_pending)

        if not is_pending and status_code not in PO_OPEN_STATUSES:
            logger.info("[Returns] PO %s skipped: status=%s not open and not pending",
                        po_id, status_code)
            continue

        lines          = po.get("list_purchase_order_details") or []
        line_summaries = []
        po_has_outstanding = False

        for ln in lines:
            code        = (ln.get("item_code_365") or "").strip()
            qty         = _dec(ln.get("line_quantity", 0))
            received    = _dec(ln.get("line_quantity_received", 0))
            outstanding = qty - received
            if outstanding < 0:
                outstanding = Decimal("0")

            line_summaries.append({
                "item_code_365": code,
                "qty":           float(qty),
                "received":      float(received),
                "outstanding":   float(outstanding),
            })

            if code and outstanding > 0:
                pending_qty[code] = pending_qty.get(code, Decimal("0")) + outstanding
                po_has_outstanding = True

        po_list.append({
            "po_id":             po_id,
            "supplier_code_365": supplier,
            "supplier_name":     sup_name,
            "status_code":       status_code,
            "is_pending":        is_pending,
            "order_date":        order_date,
            "comments":          comments,
            "has_outstanding":   po_has_outstanding,
            "lines":             line_summaries,
        })

    logger.info("[Returns] %d open POs for store 100; %d items with outstanding qty",
                len(po_list), len(pending_qty))
    return pending_qty, po_list


# ---------------------------------------------------------------------------
# DwItem enrichment
# ---------------------------------------------------------------------------

def _enrich_from_dw(stock_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not stock_rows:
        return []
    from models import DwItem
    codes    = [r["item_code_365"] for r in stock_rows]
    dw_items = DwItem.query.filter(DwItem.item_code_365.in_(codes)).all()
    dw_map   = {d.item_code_365: d for d in dw_items}

    enriched = []
    for r in stock_rows:
        code  = r["item_code_365"]
        dw    = dw_map.get(code)
        stock = r["stock_ps365"]

        selling_qty = _dec(dw.selling_qty) if dw and dw.selling_qty else None
        cost_price  = _dec(dw.cost_price)  if dw and dw.cost_price is not None else None

        pieces_ps365 = None
        if selling_qty and selling_qty > 0:
            pieces_ps365 = int(
                (stock * selling_qty).to_integral_value(rounding=ROUND_HALF_UP)
            )

        enriched.append({
            "item_code_365":     code,
            "item_name":         dw.item_name if dw and dw.item_name else r["item_name"],
            "stock_ps365":       stock,
            "selling_qty":       float(selling_qty) if selling_qty else None,
            "pieces_ps365":      pieces_ps365,
            "cost_price":        float(cost_price) if cost_price else None,
            "supplier_code_365": (dw.supplier_code_365 or "").strip() if dw else "",
            "supplier_name":     (dw.supplier_name or "").strip()     if dw else "",
        })
    return enriched


# ---------------------------------------------------------------------------
# Net against PO quantities + group
# ---------------------------------------------------------------------------

def _apply_pending_and_group(
    enriched: List[Dict[str, Any]],
    pending_qty: Dict[str, Decimal],
) -> List[Dict[str, Any]]:

    display_rows = []
    for r in enriched:
        code        = r["item_code_365"]
        stock_ps365 = r["stock_ps365"]
        on_po       = pending_qty.get(code, Decimal("0"))
        available   = max(stock_ps365 - on_po, Decimal("0"))

        sq   = Decimal(str(r["selling_qty"])) if r["selling_qty"] else None
        cost = Decimal(str(r["cost_price"]))  if r["cost_price"]  else None

        pieces_available = None
        if sq and sq > 0 and available > 0:
            pieces_available = int(
                (available * sq).to_integral_value(rounding=ROUND_HALF_UP)
            )

        value_available = None
        if cost and available > 0:
            value_available = float((available * cost).quantize(Decimal("0.01")))

        display_rows.append({
            **r,
            "on_po_cases":       float(on_po),
            "available_cases":   available,
            "available_display": _stock_display(available),
            "pieces_available":  pieces_available,
            "value_available":   value_available,
            "has_pending_po":    on_po > 0,
            "fully_committed":   available <= 0 and on_po > 0,
        })

    # Group by supplier
    buckets: Dict[str, Dict[str, Any]] = {}
    for r in display_rows:
        key = r["supplier_code_365"] or ""
        if key not in buckets:
            buckets[key] = {
                "supplier_code_365": key,
                "supplier_name": r["supplier_name"] or ("Unknown Supplier" if not key else key),
                "total_value": 0.0,
                "item_rows": [],
            }
        buckets[key]["item_rows"].append(r)
        if r["value_available"]:
            buckets[key]["total_value"] = round(
                buckets[key]["total_value"] + r["value_available"], 2
            )


    return sorted(
        buckets.values(),
        key=lambda g: ("\xff" if not g["supplier_code_365"] else "", g["supplier_name"].lower()),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_returns_stock(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Returns the full data payload. PS365 is only called when the cache is
    stale or force_refresh=True. Outstanding return PO quantities are fetched
    from PS365 on the same Refresh and netted against stock automatically.
    """
    with _cache_lock:
        if force_refresh or not _cache_is_valid():
            try:
                raw_rows             = _fetch_store_100_stock()
                enriched             = _enrich_from_dw(raw_rows)
                pending_qty, po_list = _fetch_pending_return_pos()

                _cache["rows"]        = enriched
                _cache["pending_pos"] = po_list
                _cache["fetched_at"]  = datetime.now(timezone.utc)
                logger.info("[Returns] Cache refreshed: %d stock items, %d open POs",
                            len(enriched), len(po_list))
            except Exception as e:
                logger.exception("[Returns] Refresh failed")
                if not _cache.get("rows"):
                    return {
                        "groups": [], "total_items": 0, "total_value": 0.0,
                        "unknown_supplier_count": 0, "pending_pos": [],
                        "fetched_at": None, "cache_age_minutes": None,
                        "error": str(e),
                    }
                # Serve stale cache with a warning

    enriched = _cache["rows"]        or []
    po_list  = _cache["pending_pos"] or []

    # Rebuild pending_qty from cached po_list
    pending_qty: Dict[str, Decimal] = {}
    for po in po_list:
        if po["has_outstanding"]:
            for ln in po["lines"]:
                code = ln["item_code_365"]
                out  = _dec(ln["outstanding"])
                if code and out > 0:
                    pending_qty[code] = pending_qty.get(code, Decimal("0")) + out

    groups = _apply_pending_and_group(enriched, pending_qty)

    total_value   = round(sum(g["total_value"] for g in groups), 2)
    total_items   = sum(len(g["item_rows"]) for g in groups)
    unknown_count = sum(len(g["item_rows"]) for g in groups if not g["supplier_code_365"])
    age           = _cache_age_minutes()
    fetched_at    = _cache["fetched_at"]

    return {
        "groups":                 groups,
        "total_items":            total_items,
        "total_value":            total_value,
        "unknown_supplier_count": unknown_count,
        "pending_pos":            po_list,
        "fetched_at":             fetched_at.strftime("%d/%m/%Y %H:%M") if fetched_at else None,
        "cache_age_minutes":      round(age, 1) if age is not None else None,
        "error":                  None,
    }
