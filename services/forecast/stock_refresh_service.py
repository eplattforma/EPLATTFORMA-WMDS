"""Stock Refresh Service — updates Ps365Stock777Current only.

This service is responsible for fetching live stock from PS365 and persisting
it to the local ``Ps365Stock777Current`` snapshot table.

It never touches ordering snapshots, forecast results, or manual order
overrides.  Ordering decisions are handled separately by
``ordering_refresh_service.py``.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from models import DwItem, ForecastItemSupplierMap, Ps365Stock777Current

logger = logging.getLogger(__name__)


def _get_utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _supplier_codes_for_scope(session: Session, supplier_code: str = None,
                              item_codes: list = None) -> set:
    """Return the set of supplier codes that cover the requested scope."""
    codes = set()

    if supplier_code:
        codes.add(supplier_code)
        return codes

    q = session.query(DwItem.supplier_code_365).filter(
        DwItem.active == True,
        DwItem.supplier_code_365.isnot(None),
        DwItem.supplier_code_365 != '',
    )
    if item_codes:
        q = q.filter(DwItem.item_code_365.in_(item_codes))
    for row in q.distinct().all():
        if row.supplier_code_365:
            codes.add(row.supplier_code_365)

    map_q = session.query(ForecastItemSupplierMap.supplier_code).filter(
        ForecastItemSupplierMap.is_active == True,
        ForecastItemSupplierMap.supplier_code.isnot(None),
    )
    if item_codes:
        map_q = map_q.filter(ForecastItemSupplierMap.item_code_365.in_(item_codes))
    for row in map_q.distinct().all():
        if row.supplier_code:
            codes.add(row.supplier_code)

    return codes


def _persist_stock_to_db(session: Session, sup_code: str, items_dict: dict,
                         now_utc: datetime) -> int:
    """Write live stock data to ps365_stock_777_current. Returns count of rows written."""
    written = 0
    for item_code, d in items_dict.items():
        stock_val = float(d.get("stock_now_units", 0))
        ordered_val = float(d.get("ordered_now_units", 0))
        reserved_val = float(d.get("reserved_now_units", 0))
        transfer_val = float(d.get("on_transfer_now_units", 0))
        avail_val = stock_val + ordered_val - reserved_val

        row = session.get(Ps365Stock777Current, item_code)
        if row is None:
            row = Ps365Stock777Current(item_code_365=item_code)
            session.add(row)
        row.supplier_code_365 = sup_code
        row.store_code_365 = "777"
        row.item_name = d.get("item_name", "")
        row.stock = Decimal(str(round(stock_val, 4)))
        row.stock_reserved = Decimal(str(round(reserved_val, 4)))
        row.stock_on_transfer = Decimal(str(round(transfer_val, 4)))
        row.stock_ordered = Decimal(str(round(ordered_val, 4)))
        row.available_qty = Decimal(str(round(avail_val, 4)))
        row.is_oos = stock_val <= 0
        row.is_available = stock_val > 0
        row.is_low_stock = 0 < stock_val < 5
        row.updated_at = now_utc
        written += 1
    return written


def refresh_stock_snapshot(
    session: Session,
    supplier_code: str = None,
    item_codes: list = None,
    progress_callback=None,
) -> dict:
    """Fetch live PS365 stock and persist to Ps365Stock777Current.

    Args:
        session: SQLAlchemy session.
        supplier_code: If given, refresh only this supplier's items.
        item_codes: If given (without supplier_code), limit to these items.
        progress_callback: Optional callable(str) for progress messages.

    Returns:
        Audit dict with action_type, scope, suppliers_refreshed,
        items_updated, manual_overrides_reset (always False), stock_source.
    """
    from services.replenishment_mvp.ps365_client import fetch_supplier_stock

    now_utc = _get_utc_now()
    scope_label = supplier_code if supplier_code else "global"

    supplier_codes = _supplier_codes_for_scope(session, supplier_code=supplier_code,
                                               item_codes=item_codes)
    if not supplier_codes:
        logger.warning("stock_refresh: no supplier codes found for scope=%s", scope_label)
        return {
            "action_type": "stock_refresh",
            "scope": scope_label,
            "suppliers_refreshed": 0,
            "items_updated": 0,
            "manual_overrides_reset": False,
            "stock_source": "none",
        }

    logger.info(
        "stock_refresh START: scope=%s suppliers=%d",
        scope_label, len(supplier_codes),
    )
    if progress_callback:
        progress_callback(f"Stock refresh: fetching {len(supplier_codes)} suppliers…")

    suppliers_ok = 0
    suppliers_failed = 0
    items_total = 0

    for sup in sorted(supplier_codes):
        if progress_callback:
            progress_callback(f"Stock refresh: fetching supplier {sup}…")
        try:
            live = fetch_supplier_stock(sup)
            written = _persist_stock_to_db(session, sup, live, now_utc)
            session.flush()
            items_total += written
            suppliers_ok += 1
            logger.info(
                "stock_refresh: supplier=%s items=%d (live)",
                sup, written,
            )
        except Exception as exc:
            suppliers_failed += 1
            logger.warning(
                "stock_refresh: supplier=%s FAILED (%s) — skipped",
                sup, exc,
            )

    logger.info(
        "stock_refresh DONE: scope=%s suppliers_ok=%d suppliers_failed=%d items_updated=%d "
        "manual_overrides_reset=False stock_source=live_ps365",
        scope_label, suppliers_ok, suppliers_failed, items_total,
    )
    return {
        "action_type": "stock_refresh",
        "scope": scope_label,
        "suppliers_refreshed": suppliers_ok,
        "suppliers_failed": suppliers_failed,
        "items_updated": items_total,
        "manual_overrides_reset": False,
        "stock_source": "live_ps365",
    }


def refresh_supplier_stock_snapshot(
    session: Session,
    supplier_code: str,
    progress_callback=None,
) -> dict:
    """Convenience wrapper: refresh stock for exactly one supplier."""
    if not supplier_code:
        raise ValueError("supplier_code is required")
    return refresh_stock_snapshot(
        session=session,
        supplier_code=supplier_code,
        progress_callback=progress_callback,
    )
