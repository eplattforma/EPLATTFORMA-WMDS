import logging
import math
from decimal import Decimal

from sqlalchemy.orm import Session

from models import (
    SkuForecastProfile,
    SkuForecastResult,
    ForecastItemSupplierMap,
    DwItem,
    Setting,
)
from timezone_utils import get_utc_now

logger = logging.getLogger(__name__)


def _to_float(v):
    if v is None:
        return 0.0
    return float(v)


def _ceil_to_multiple(qty, multiple):
    if multiple <= 0:
        return qty
    if qty <= 0:
        return 0.0
    return math.ceil(qty / multiple) * multiple


def _enforce_moq(qty, moq):
    if moq <= 0 or qty <= 0:
        return qty
    return max(qty, moq)


def _get_stock_for_item(item_code, stock_cache=None):
    if stock_cache and item_code in stock_cache:
        stock = stock_cache[item_code]
        return (
            _to_float(stock.get("stock_now_units", 0)),
            _to_float(stock.get("ordered_now_units", 0)),
            _to_float(stock.get("reserved_now_units", 0)),
        )
    return 0.0, 0.0, 0.0


def _build_review_reasons(profile, result, supplier_map, dw_item, old_final, raw_order, rounded_order):
    reasons = []

    if profile.demand_class in ("erratic", "lumpy", "new_sparse"):
        reasons.append(f"{profile.demand_class} demand class")

    if dw_item and not dw_item.active and profile.weeks_non_zero_26 > 0:
        reasons.append("inactive item with recent sales")

    if profile.trend_flag in ("up", "down"):
        pct = _to_float(profile.trend_pct)
        if abs(pct) > 10:
            reasons.append(f"trend {profile.trend_flag} ({pct:+.1f}%)")

    if profile.seasonality_confidence == "low" and profile.seasonality_source != "none":
        reasons.append("low seasonality confidence")

    forecast_change = _to_float(result.forecast_change_pct)
    if abs(forecast_change) > 20:
        reasons.append(f"forecast changed {forecast_change:+.1f}%")

    if raw_order > 0 and rounded_order > 0:
        distortion = abs(rounded_order - raw_order) / raw_order if raw_order > 0 else 0
        if distortion > 0.30:
            reasons.append(f"order rounding distortion {distortion:.0%}")

    has_supplier = (dw_item and dw_item.supplier_code_365) or supplier_map
    if not has_supplier:
        reasons.append("missing supplier mapping")

    if dw_item:
        if not dw_item.category_code_365 and not dw_item.brand_code_365:
            reasons.append("missing brand/category")

    if profile.demand_class == "no_demand":
        on_hand = _to_float(result.on_hand_qty)
        if on_hand > 0:
            reasons.append("no demand but stock exists")

    return reasons


def compute_replenishment(session: Session, run_id=None):
    STOCK_DAYS = 7

    results = (
        session.query(SkuForecastResult)
        .join(DwItem, DwItem.item_code_365 == SkuForecastResult.item_code_365)
        .filter(DwItem.active == True)
        .all()
    )
    logger.info(f"Computing replenishment for {len(results)} active items (target: {STOCK_DAYS} days)")

    now = get_utc_now()
    count = 0

    supplier_map_cache = {}
    maps = session.query(ForecastItemSupplierMap).filter_by(is_active=True).all()
    for m in maps:
        supplier_map_cache[m.item_code_365] = m

    dw_item_cache = {}
    
    stock_cache_by_supplier = {}
    def _get_stock_cache(supplier_code):
        if supplier_code not in stock_cache_by_supplier:
            try:
                from services.replenishment_mvp.ps365_client import fetch_supplier_stock
                stock_cache_by_supplier[supplier_code] = fetch_supplier_stock(supplier_code)
            except Exception as e:
                logger.warning(f"Failed to fetch stock for supplier {supplier_code}: {e}")
                stock_cache_by_supplier[supplier_code] = {}
        return stock_cache_by_supplier[supplier_code]

    for result in results:
        item_code = result.item_code_365

        profile = session.query(SkuForecastProfile).filter_by(item_code_365=item_code).first()
        if not profile:
            continue

        supplier_map = supplier_map_cache.get(item_code)

        if item_code not in dw_item_cache:
            dw_item_cache[item_code] = session.query(DwItem).filter_by(item_code_365=item_code).first()
        dw_item = dw_item_cache[item_code]

        result.cover_days = Decimal(str(STOCK_DAYS))
        result.lead_time_days = Decimal("0")
        result.review_cycle_days = Decimal("0")

        final_daily = _to_float(result.final_forecast_daily_qty)
        safety_stock = 0.0
        result.safety_stock_qty = Decimal(str(safety_stock))

        target_stock = final_daily * STOCK_DAYS
        result.target_stock_qty = Decimal(str(round(target_stock, 6)))

        supplier_code = dw_item.supplier_code_365 if dw_item else None
        stock_cache = _get_stock_cache(supplier_code) if supplier_code else {}
        on_hand, incoming, reserved = _get_stock_for_item(item_code, stock_cache)
        result.on_hand_qty = Decimal(str(round(on_hand, 6)))
        result.incoming_qty = Decimal(str(round(incoming, 6)))
        result.reserved_qty = Decimal(str(round(reserved, 6)))

        net_available = on_hand + incoming - reserved
        result.net_available_qty = Decimal(str(round(net_available, 6)))

        raw_order = max(0.0, target_stock - net_available)
        result.raw_recommended_order_qty = Decimal(str(round(raw_order, 6)))

        order_multiple = 1.0
        moq = 0.0

        if supplier_map and supplier_map.order_multiple:
            order_multiple = _to_float(supplier_map.order_multiple)
        elif dw_item and dw_item.case_qty and dw_item.case_qty > 1:
            order_multiple = float(dw_item.case_qty)

        if supplier_map and supplier_map.min_order_qty_override:
            moq = _to_float(supplier_map.min_order_qty_override)
        elif dw_item and dw_item.min_order_qty and dw_item.min_order_qty > 0:
            moq = float(dw_item.min_order_qty)

        rounded = raw_order
        if raw_order > 0:
            if order_multiple > 1:
                rounded = _ceil_to_multiple(raw_order, order_multiple)
            rounded = _enforce_moq(rounded, moq)

        result.rounded_order_qty = Decimal(str(round(rounded, 6)))

        if run_id is not None:
            result.run_id = run_id
        result.calculated_at = now

        old_final = _to_float(result.final_forecast_weekly_qty)
        review_reasons = _build_review_reasons(
            profile, result, supplier_map, dw_item, old_final, raw_order, rounded
        )

        if review_reasons:
            profile.review_flag = True
            profile.review_reason = "; ".join(review_reasons)
        profile.updated_at = now

        count += 1
        if count % 500 == 0:
            session.flush()
            logger.info(f"Processed replenishment for {count} items...")

    session.flush()
    logger.info(f"Completed replenishment for {count} items")
    return count
