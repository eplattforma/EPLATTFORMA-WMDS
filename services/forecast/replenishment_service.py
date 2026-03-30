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

BUFFER_STOCK_DAYS_KEY = "forecast_buffer_stock_days"


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


def _resolve_supplier_context(item_code, supplier_map, dw_item):
    """
    Resolve supplier from one source of truth, prioritizing supplier_map then dw_item.
    Returns dict with supplier_code and planning parameters.
    """
    context = {
        "supplier_code": None,
        "supplier_source": None,
        "lead_time_days": 0.0,
        "review_cycle_days": 1.0,
        "min_order_qty": 0.0,
        "order_multiple": 1.0,
        "fallback_used": False,
        "issues": [],
    }
    
    if supplier_map:
        context["supplier_code"] = supplier_map.supplier_code or None
        context["supplier_source"] = "supplier_map"
        context["lead_time_days"] = _to_float(supplier_map.lead_time_days)
        context["review_cycle_days"] = _to_float(supplier_map.review_cycle_days) or 1.0
        context["min_order_qty"] = _to_float(supplier_map.min_order_qty_override)
        context["order_multiple"] = _to_float(supplier_map.order_multiple) or 1.0
    
    if not context["supplier_code"] and dw_item and dw_item.supplier_code_365:
        context["supplier_code"] = dw_item.supplier_code_365
        context["supplier_source"] = "dw_item"
        context["fallback_used"] = True
        context["issues"].append("fallback to DwItem.supplier_code_365")
    
    if not context["supplier_code"]:
        context["issues"].append("no supplier source found")
    
    if context["issues"]:
        logger.warning(f"Item {item_code}: {'; '.join(context['issues'])}")
    
    return context


def _build_review_reasons(profile, result, supplier_context, dw_item, old_final, raw_order, rounded_order, session=None):
    reasons = []

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

    if not supplier_context["supplier_code"]:
        reasons.append("missing supplier mapping")

    if dw_item:
        if not dw_item.category_code_365 and not dw_item.brand_code_365:
            reasons.append("missing brand/category")

    if profile.demand_class == "no_demand":
        on_hand = _to_float(result.on_hand_qty)
        if on_hand > 0:
            reasons.append("no demand but stock exists")

    if profile.forecast_method == "SEEDED":
        if getattr(profile, "seeded_cap_applied", False):
            reasons.append("seeded forecast capped")
        elif raw_order > 0:
            seeded_threshold = 0.0
            if session:
                seeded_threshold = _safe_num(session, "forecast_seeded_review_min_qty", 0.0, float)
            if seeded_threshold > 0 and raw_order >= seeded_threshold:
                reasons.append(f"seeded forecast with significant order ({raw_order:.0f} units)")

    if supplier_context.get("issues"):
        if "fallback to DwItem.supplier_code_365" in supplier_context["issues"]:
            if supplier_context.get("fallback_used") and not supplier_context.get("supplier_code"):
                reasons.append("fallback supplier failed; no supplier available")

    return reasons


def _safe_num(session, key, default, cast=float):
    try:
        return cast(Setting.get(session, key, str(default)))
    except (ValueError, TypeError):
        return default


def compute_replenishment(session: Session, run_id=None):
    cover_days = _safe_num(session, "forecast_default_cover_days", 7, int)
    buffer_days = _safe_num(session, BUFFER_STOCK_DAYS_KEY, 1.0, float)
    default_review_cycle = _safe_num(session, "forecast_review_cycle_days", 1.0, float)

    results = (
        session.query(SkuForecastResult)
        .join(DwItem, DwItem.item_code_365 == SkuForecastResult.item_code_365)
        .filter(DwItem.active == True)
        .all()
    )
    logger.info(f"Computing replenishment for {len(results)} active items "
                f"(cover={cover_days}d, buffer={buffer_days}d, review_cycle={default_review_cycle}d)")

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

        supplier_context = _resolve_supplier_context(item_code, supplier_map, dw_item)
        
        lead_time = supplier_context["lead_time_days"]
        review_cycle = supplier_context["review_cycle_days"] or default_review_cycle

        result.cover_days = Decimal(str(cover_days))
        result.lead_time_days = Decimal(str(lead_time))
        result.review_cycle_days = Decimal(str(review_cycle))

        final_daily = _to_float(result.final_forecast_daily_qty)

        buffer_stock = final_daily * buffer_days
        result.buffer_stock_qty = Decimal(str(round(buffer_stock, 6)))
        result.safety_stock_qty = Decimal(str(round(buffer_stock, 6)))

        total_cover = cover_days + lead_time + review_cycle
        target_stock = final_daily * total_cover + buffer_stock
        result.target_stock_qty = Decimal(str(round(target_stock, 6)))

        stock_cache = _get_stock_cache(supplier_context["supplier_code"]) if supplier_context["supplier_code"] else {}
        on_hand, incoming, reserved = _get_stock_for_item(item_code, stock_cache)
        result.on_hand_qty = Decimal(str(round(on_hand, 6)))
        result.incoming_qty = Decimal(str(round(incoming, 6)))
        result.reserved_qty = Decimal(str(round(reserved, 6)))

        net_available = on_hand + incoming - reserved
        result.net_available_qty = Decimal(str(round(net_available, 6)))

        raw_order = max(0.0, target_stock - net_available)
        result.raw_recommended_order_qty = Decimal(str(round(raw_order, 6)))

        order_multiple = supplier_context["order_multiple"]
        if order_multiple <= 1 and dw_item and dw_item.case_qty and dw_item.case_qty > 1:
            order_multiple = float(dw_item.case_qty)

        moq = supplier_context["min_order_qty"]
        if moq <= 0 and dw_item and dw_item.min_order_qty and dw_item.min_order_qty > 0:
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
            profile, result, supplier_context, dw_item, old_final, raw_order, rounded, session
        )

        profile.review_flag = bool(review_reasons)
        profile.review_reason = "; ".join(review_reasons) if review_reasons else None
        profile.updated_at = now

        count += 1
        if count % 500 == 0:
            session.flush()
            logger.info(f"Processed replenishment for {count} items...")

    session.flush()
    logger.info(f"Completed replenishment for {count} items")
    return count
