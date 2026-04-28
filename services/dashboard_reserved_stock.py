"""
Helper for refreshing the per-item reserved/ordered-stock snapshot used by
the Stock Dashboard.

Triggered by /api/refresh-reserved-stock right after the "Fetch from ERP"
flow on /stock-dashboard finishes. Calls the PS365 stock API for every
item currently in StockPosition and overwrites the StockDashboardReserved
table.
"""
import logging
from decimal import Decimal
from typing import Dict

from sqlalchemy import text

from app import db
from models import StockPosition, StockDashboardReserved
from services_ps365_stock import (
    fetch_items_stock_for_store,
    POWERSOFT_BASE,
    POWERSOFT_TOKEN,
)
from timezone_utils import get_utc_now

logger = logging.getLogger(__name__)

# Warehouse store used everywhere else for stock lookups (see routes_po_receiving).
DASHBOARD_STORE_CODE = "777"


def _to_decimal(v) -> Decimal:
    try:
        if v is None or v == "":
            return Decimal("0")
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


def refresh_dashboard_reserved_stock() -> dict:
    """
    Fetch reserved/ordered stock for every item code currently in
    StockPosition and overwrite the StockDashboardReserved table.

    Returns a dict with keys: success, items_requested, items_returned,
    items_saved, synced_at, error.
    """
    if not POWERSOFT_BASE or not POWERSOFT_TOKEN:
        return {
            "success": False,
            "error": "PS365 stock API is not configured (POWERSOFT_BASE/POWERSOFT_TOKEN missing).",
            "items_requested": 0,
            "items_returned": 0,
            "items_saved": 0,
        }

    item_codes = [
        code for (code,) in db.session.query(StockPosition.item_code).distinct().all()
        if code
    ]

    if not item_codes:
        # Nothing in the dashboard yet — clear the table so stale rows don't show.
        db.session.execute(text("DELETE FROM stock_dashboard_reserved"))
        db.session.commit()
        return {
            "success": True,
            "items_requested": 0,
            "items_returned": 0,
            "items_saved": 0,
            "synced_at": get_utc_now().isoformat(),
        }

    logger.info(
        f"[DashboardReserved] Fetching reserved stock for {len(item_codes)} items "
        f"in store {DASHBOARD_STORE_CODE}"
    )

    stock_map: Dict[str, dict] = fetch_items_stock_for_store(
        DASHBOARD_STORE_CODE, item_codes
    )

    now = get_utc_now()
    rows = []
    for code in item_codes:
        info = stock_map.get(code)
        if info is None:
            # Item not returned by PS365 — store zeros so dashboard shows 0
            # rather than stale data from a prior refresh.
            rows.append({
                "item_code": code,
                "store_code": DASHBOARD_STORE_CODE,
                "stock_reserved": Decimal("0"),
                "stock_ordered": Decimal("0"),
                "synced_at": now,
            })
            continue
        rows.append({
            "item_code": code,
            "store_code": DASHBOARD_STORE_CODE,
            "stock_reserved": _to_decimal(info.get("stock_reserved")),
            "stock_ordered": _to_decimal(info.get("stock_ordered")),
            "synced_at": now,
        })

    try:
        db.session.execute(text("DELETE FROM stock_dashboard_reserved"))
        if rows:
            db.session.bulk_insert_mappings(StockDashboardReserved, rows)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"[DashboardReserved] DB write failed: {e}", exc_info=True)
        return {
            "success": False,
            "error": f"Database write failed: {e}",
            "items_requested": len(item_codes),
            "items_returned": len(stock_map),
            "items_saved": 0,
        }

    logger.info(
        f"[DashboardReserved] Saved {len(rows)} rows "
        f"(returned by PS365: {len(stock_map)}/{len(item_codes)})"
    )

    return {
        "success": True,
        "items_requested": len(item_codes),
        "items_returned": len(stock_map),
        "items_saved": len(rows),
        "synced_at": now.isoformat(),
    }
