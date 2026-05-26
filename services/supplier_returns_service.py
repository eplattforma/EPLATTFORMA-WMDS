"""
Supplier Returns Service — store 100 (RETURNS)

Architecture
------------
Stock is stored in `supplier_returns_stock_cache` DB table.
PS365 is called ONLY on force_refresh=True (manual Refresh button).
All page loads read from DB — instant, no API call, survives restarts.

PS365 docs warn: repeatedly calling list_stock_items_store will result
in the token being disconnected. DB cache eliminates this risk.

On each Refresh:
  1. Fetch items with stock > 0 in store 100 from PS365
     (available_stock_type=withStock — faster, skips zero-stock items)
  2. Upsert into supplier_returns_stock_cache
  3. Delete rows for items no longer in store 100
  4. Fetch open return POs (two-stage strategy — unchanged)

Page loads: read from DB, build groups, return. No PS365 call.
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

PO_OPEN_STATUSES = {"RETURN"}

# 60-second in-memory layer — avoids repeated DB hits on rapid page loads
# within the same worker. DB is the real source of truth.
_mem_lock  = threading.Lock()
_mem_cache: Dict[str, Any] = {"data": None, "at": None}
MEM_TTL_SECONDS = 60


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
# PS365 — fetch stock (called ONLY on manual Refresh)
# ---------------------------------------------------------------------------

def _fetch_store_100_stock_from_ps365() -> List[Dict[str, Any]]:
    """
    Fetches all items with stock > 0 in store 100.
    available_stock_type=withStock skips zero-stock items — much faster.
    """
    items: Dict[str, Dict[str, Any]] = {}
    page = 1
    while page <= MAX_PAGES:
        data = call_ps365(
            "list_stock_items_store",
            {
                "store_code_365":       RETURNS_STORE_CODE,
                "available_stock_type": "withStock",
                "active_type":          "all",
                "ecommerce_type":       "all",
                "page_number":          page,
                "page_size":            PAGE_SIZE,
            },
            method="GET",
        )

        if page == 1:
            logger.info("[Returns] PS365 stock response keys: %s",
                        list(data.keys()) if data else "None")

        rows = (
            data.get("list_stock_stores_item")
            or data.get("list_stock_items_store")
            or []
        )
        if not rows:
            break
        for r in rows:
            code  = (r.get("item_code_365") or "").strip()
            stock = _dec(r.get("stock"))
            if not code or stock <= 0:
                continue
            items[code] = {
                "item_code_365": code,
                "item_name":     (r.get("item_name") or "").strip(),
                "stock_cases":   stock,
            }
        if len(rows) < PAGE_SIZE:
            break
        page += 1

    logger.info("[Returns] PS365 returned %d items with stock in store 100", len(items))
    return list(items.values())


# ---------------------------------------------------------------------------
# DB — write to cache table
# ---------------------------------------------------------------------------

def _write_stock_to_db(ps365_rows: List[Dict[str, Any]]) -> None:
    from app import db
    from models import DwItem
    from sqlalchemy import text

    if not ps365_rows:
        db.session.execute(text("DELETE FROM supplier_returns_stock_cache"))
        db.session.commit()
        logger.info("[Returns] Store 100 empty — cache cleared")
        return

    codes    = [r["item_code_365"] for r in ps365_rows]
    dw_items = DwItem.query.filter(DwItem.item_code_365.in_(codes)).all()
    dw_map   = {d.item_code_365: d for d in dw_items}
    now      = datetime.now(timezone.utc).replace(tzinfo=None)

    for r in ps365_rows:
        code = r["item_code_365"]
        dw   = dw_map.get(code)

        db.session.execute(text("""
            INSERT INTO supplier_returns_stock_cache
                (item_code_365, item_name, stock_cases, supplier_code_365,
                 supplier_name, selling_qty, cost_price, barcode, last_synced_at)
            VALUES
                (:code, :name, :stock, :sup_code,
                 :sup_name, :selling_qty, :cost_price, :barcode, :now)
            ON CONFLICT (item_code_365) DO UPDATE SET
                item_name         = EXCLUDED.item_name,
                stock_cases       = EXCLUDED.stock_cases,
                supplier_code_365 = EXCLUDED.supplier_code_365,
                supplier_name     = EXCLUDED.supplier_name,
                selling_qty       = EXCLUDED.selling_qty,
                cost_price        = EXCLUDED.cost_price,
                barcode           = EXCLUDED.barcode,
                last_synced_at    = EXCLUDED.last_synced_at
        """), {
            "code":        code,
            "name":        (dw.item_name or r["item_name"]) if dw else r["item_name"],
            "stock":       float(r["stock_cases"]),
            "sup_code":    (dw.supplier_code_365 or "").strip() if dw else "",
            "sup_name":    (dw.supplier_name     or "").strip() if dw else "",
            "selling_qty": float(dw.selling_qty) if dw and dw.selling_qty is not None else None,
            "cost_price":  float(dw.cost_price)  if dw and dw.cost_price  is not None else None,
            "barcode":     (dw.barcode or "").strip() if dw else "",
            "now":         now,
        })

    # Remove items no longer in store 100
    db.session.execute(text("""
        DELETE FROM supplier_returns_stock_cache
        WHERE item_code_365 NOT IN :codes
    """), {"codes": tuple(codes)})

    db.session.commit()
    logger.info("[Returns] DB cache updated: %d items", len(ps365_rows))


# ---------------------------------------------------------------------------
# DB — read from cache table
# ---------------------------------------------------------------------------

def _read_stock_from_db() -> List[Dict[str, Any]]:
    from app import db
    from sqlalchemy import text

    rows = db.session.execute(text("""
        SELECT item_code_365, item_name, stock_cases,
               supplier_code_365, supplier_name,
               selling_qty, cost_price, barcode, last_synced_at
        FROM   supplier_returns_stock_cache
        WHERE  stock_cases > 0
        ORDER  BY item_code_365
    """)).fetchall()

    result = []
    for r in rows:
        stock       = _dec(r.stock_cases)
        selling_qty = _dec(r.selling_qty) if r.selling_qty is not None else None
        cost_price  = _dec(r.cost_price)  if r.cost_price  is not None else None

        pieces = None
        if selling_qty and selling_qty > 0:
            pieces = int((stock * selling_qty).to_integral_value(rounding=ROUND_HALF_UP))

        result.append({
            "item_code_365":     r.item_code_365,
            "item_name":         r.item_name or "",
            "stock_ps365":       stock,
            "selling_qty":       float(selling_qty) if selling_qty else None,
            "pieces_ps365":      pieces,
            "cost_price":        float(cost_price) if cost_price else None,
            "supplier_code_365": (r.supplier_code_365 or "").strip(),
            "supplier_name":     (r.supplier_name     or "").strip(),
            "barcode":           (r.barcode or "").strip() if r.barcode else "",
            "last_synced_at":    r.last_synced_at,
        })
    return result


def _get_last_synced_at() -> Optional[datetime]:
    from app import db
    from sqlalchemy import text
    row = db.session.execute(text(
        "SELECT MAX(last_synced_at) FROM supplier_returns_stock_cache"
    )).fetchone()
    return row[0] if row and row[0] else None


# ---------------------------------------------------------------------------
# PS365 — pending return POs (two-stage, same logic as before)
# ---------------------------------------------------------------------------

def _lookup_po_by_cart_code(cart_code: str) -> Optional[Dict[str, Any]]:
    try:
        today = date.today()
        resp = call_ps365("list_purchase_orders", method="POST", payload={
            "filter_define": {
                "page_number":                  1,
                "page_size":                    10,
                "only_counted":                 "N",
                "orders_supplier_selection":    "",
                "order_status_selection":       "",
                "from_date":                    "2020-01-01",
                "to_date":                      (today + timedelta(days=365)).isoformat(),
                "items_selection":              "",
                "stores_selection":             "",
                "orders_type":                  "all",
                "shopping_cart_code_selection": cart_code,
            }
        })
        if not resp or resp.get("api_response", {}).get("response_code") != "1":
            return None
        pos = resp.get("list_purchase_orders") or []
        return pos[0] if pos else None
    except Exception as e:
        logger.warning("[Returns] Cart lookup failed %s: %s", cart_code, e)
        return None


def _fetch_pending_return_pos() -> tuple:
    today            = date.today()
    cutoff_30        = today - timedelta(days=30)
    from_date_recent = cutoff_30.isoformat()
    to_date          = (today + timedelta(days=365)).isoformat()

    raw_pos: List[Dict[str, Any]] = []
    seen_cart_codes: set = set()

    # Stage 1: 30-day query filtered to store 100
    page = 1
    while page <= 10:
        try:
            resp = call_ps365("list_purchase_orders", method="POST", payload={
                "filter_define": {
                    "page_number":                  page,
                    "page_size":                    100,
                    "only_counted":                 "N",
                    "orders_supplier_selection":    "",
                    "order_status_selection":       "",
                    "from_date":                    from_date_recent,
                    "to_date":                      to_date,
                    "items_selection":              "",
                    "stores_selection":             "100",
                    "orders_type":                  "all",
                    "shopping_cart_code_selection": "",
                }
            })
        except Exception as e:
            logger.warning("[Returns] Stage-1 page %d failed: %s", page, e)
            if page == 1:
                return {}, []
            break

        if not resp or resp.get("api_response", {}).get("response_code") != "1":
            if page == 1:
                return {}, []
            break

        page_pos = resp.get("list_purchase_orders") or []
        raw_pos.extend(page_pos)
        for po in page_pos:
            cc = str(po.get("purchase_order_header", {}).get("shopping_cart_code") or "")
            if cc:
                seen_cart_codes.add(cc)
        if len(page_pos) < 100:
            break
        page += 1

    logger.info("[Returns] Stage-1: %d POs from store 100 (last 30 days)", len(raw_pos))

    # Stage 2: targeted lookup for tracked codes older than 30 days
    try:
        from models import SupplierReturnPoTracking
        from datetime import datetime as _dt
        older_tracking = SupplierReturnPoTracking.query.filter(
            SupplierReturnPoTracking.sent_at < _dt.combine(cutoff_30, _dt.min.time())
        ).all()
    except Exception as e:
        logger.warning("[Returns] Tracking table read failed: %s", e)
        older_tracking = []

    for row in older_tracking:
        if row.cart_code in seen_cart_codes:
            continue
        po = _lookup_po_by_cart_code(row.cart_code)
        if po:
            raw_pos.append(po)
            seen_cart_codes.add(row.cart_code)

    # Process POs into pending_qty and po_list
    pending_qty: Dict[str, Decimal] = {}
    po_list: List[Dict[str, Any]] = []

    for po in raw_pos:
        header      = po.get("purchase_order_header", {})
        status_code = (header.get("order_status_code") or "").upper().strip()
        is_pending  = header.get("is_pending", False)
        cart_code   = str(header.get("shopping_cart_code") or "")
        po_id       = header.get("purchase_order_id") or header.get("purchase_order_code") or "?"

        if "RET-" not in cart_code:
            continue
        # Only net against POs that PS365 actually assigned to store 100.
        # PS365 sometimes saves return POs as store 777 even when we send
        # store_code_365="100" — those must not block store 100 stock.
        po_store = str(header.get("store_code_365") or "").strip()
        if po_store != RETURNS_STORE_CODE:
            logger.info("[Returns] PO %s cart=%s skipped — store=%s (not 100)",
                        po_id, cart_code, po_store)
            continue
        # Use status as the authoritative signal.
        if status_code not in PO_OPEN_STATUSES:
            continue

        lines              = po.get("list_purchase_order_details") or []
        line_summaries     = []
        po_has_outstanding = False

        for ln in lines:
            code        = (ln.get("item_code_365") or "").strip()
            qty         = _dec(ln.get("line_quantity", 0))
            received    = _dec(ln.get("line_quantity_received", 0))
            outstanding = max(qty - received, Decimal("0"))
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
            "supplier_code_365": header.get("supplier_code_365", ""),
            "supplier_name":     header.get("supplier_name", ""),
            "status_code":       status_code,
            "is_pending":        is_pending,
            "order_date":        (header.get("order_date_local") or "")[:16],
            "comments":          header.get("comments", ""),
            "has_outstanding":   po_has_outstanding,
            "lines":             line_summaries,
        })

    logger.info("[Returns] %d open return POs; %d items with outstanding qty",
                len(po_list), len(pending_qty))
    return pending_qty, po_list


# ---------------------------------------------------------------------------
# Group and net against pending POs
# ---------------------------------------------------------------------------

def _apply_pending_and_group(
    db_rows: List[Dict[str, Any]],
    pending_qty: Dict[str, Decimal],
) -> List[Dict[str, Any]]:

    display_rows = []
    for r in db_rows:
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
            # Aliases for print slip template
            "stock_cases":       float(r["stock_ps365"]),
            "pieces":            r.get("pieces_ps365"),
        })

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
    force_refresh=True  → fetch from PS365, write to DB, return fresh data
    force_refresh=False → read from DB only (no API call)
    """
    # 60-second in-memory layer
    with _mem_lock:
        if not force_refresh and _mem_cache.get("data") and _mem_cache.get("at"):
            age = (datetime.now(timezone.utc) - _mem_cache["at"]).total_seconds()
            if age < MEM_TTL_SECONDS:
                return _mem_cache["data"]

    error_msg   = None
    po_list: List[Dict[str, Any]] = []
    pending_qty: Dict[str, Decimal] = {}

    if force_refresh:
        try:
            ps365_rows = _fetch_store_100_stock_from_ps365()
            _write_stock_to_db(ps365_rows)
        except Exception as e:
            logger.exception("[Returns] PS365 refresh failed")
            error_msg = str(e)

        try:
            pending_qty, po_list = _fetch_pending_return_pos()
        except Exception as e:
            logger.warning("[Returns] PO fetch failed: %s", e)
    else:
        # On page load, reuse po_list from mem cache to avoid redundant PO queries
        cached = _mem_cache.get("data") or {}
        po_list = cached.get("pending_pos") or []
        for po in po_list:
            if po["has_outstanding"]:
                for ln in po["lines"]:
                    code = ln["item_code_365"]
                    out  = _dec(ln["outstanding"])
                    if code and out > 0:
                        pending_qty[code] = pending_qty.get(code, Decimal("0")) + out

    try:
        db_rows = _read_stock_from_db()
    except Exception as e:
        logger.exception("[Returns] DB read failed")
        db_rows   = []
        error_msg = error_msg or str(e)

    groups        = _apply_pending_and_group(db_rows, pending_qty)
    last_synced   = _get_last_synced_at()
    fetched_str   = last_synced.strftime("%d/%m/%Y %H:%M") if last_synced else None
    total_value   = round(sum(g["total_value"] for g in groups), 2)
    total_items   = sum(len(g["item_rows"]) for g in groups)
    unknown_count = sum(len(g["item_rows"]) for g in groups if not g["supplier_code_365"])

    result = {
        "groups":                 groups,
        "total_items":            total_items,
        "total_value":            total_value,
        "unknown_supplier_count": unknown_count,
        "pending_pos":            po_list,
        "fetched_at":             fetched_str,
        "cache_age_minutes":      None,
        "error":                  error_msg,
    }

    with _mem_lock:
        _mem_cache["data"] = result
        _mem_cache["at"]   = datetime.now(timezone.utc)

    return result
