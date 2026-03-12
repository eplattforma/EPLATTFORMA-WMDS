"""
Replenishment MVP - Planner

Main orchestration: generate_replenishment_run()
Uses precomputed weekly-demand-based forecasts from SkuForecastResult.
Target: cover_days + lead_time + review_cycle + buffer stock.
"""
import logging
import math
from datetime import date
from decimal import Decimal

from app import db
from models import (
    ReplenishmentRun, ReplenishmentRunLine,
    SkuForecastResult, SkuForecastProfile, DwItem,
    ForecastItemSupplierMap, Setting,
)

from services.replenishment_mvp.ps365_client import fetch_supplier_stock, REPLENISHMENT_WAREHOUSE_STORE
from services.replenishment_mvp.repositories import (
    get_supplier_by_code, get_item_master_for_codes,
    get_item_settings_for_codes, get_expiry_summary,
)

logger = logging.getLogger(__name__)

WARNING_PRIORITY = [
    "CASE_QTY_MISSING",
    "MANUAL_REVIEW_REQUIRED",
    "NEGATIVE_PROJECTED_STOCK",
    "NO_RECENT_SALES",
    "EXPIRY_SOON",
    "HAS_ORDERED_STOCK",
    "HAS_CURRENT_RESERVED",
    "ZERO_ORDER",
]

WARNING_TEXT_MAP = {
    "CASE_QTY_MISSING": "No valid case quantity found - cannot calculate order",
    "MANUAL_REVIEW_REQUIRED": "Stock is fully reserved but no valid forecast history was found",
    "NEGATIVE_PROJECTED_STOCK": "Projected stock at receipt is negative - potential stockout",
    "NO_RECENT_SALES": "No sales in any lookback window - forecast is zero",
    "EXPIRY_SOON": "Stock expiring within 30 days",
    "HAS_ORDERED_STOCK": "Has open purchase orders",
    "HAS_CURRENT_RESERVED": "Has currently reserved stock",
    "ZERO_ORDER": "No order needed",
}


def generate_replenishment_run(
    supplier_code: str,
    run_date: date,
    current_user=None,
) -> int:
    supplier = get_supplier_by_code(supplier_code)
    if not supplier:
        raise ValueError(f"Supplier not found: {supplier_code}")
    if not supplier.is_active:
        raise ValueError(f"Supplier is inactive: {supplier_code}")

    cover_days = int(Setting.get(db.session, "forecast_default_cover_days", "7"))
    buffer_days = float(Setting.get(db.session, "forecast_buffer_stock_days", "1"))
    default_review_cycle = float(Setting.get(db.session, "forecast_review_cycle_days", "1"))

    logger.info(f"Run on {run_date}: cover={cover_days}d, buffer={buffer_days}d, review_cycle={default_review_cycle}d")

    stock_snapshot = fetch_supplier_stock(supplier_code, REPLENISHMENT_WAREHOUSE_STORE)
    if not stock_snapshot:
        raise ValueError(f"No items returned from PS365 for supplier {supplier_code}")

    item_codes = list(stock_snapshot.keys())
    item_master = get_item_master_for_codes(item_codes)
    item_settings = get_item_settings_for_codes(item_codes)
    expiry_data = get_expiry_summary(item_codes, REPLENISHMENT_WAREHOUSE_STORE)

    forecast_results = {}
    forecast_profiles = {}
    supplier_maps = {}

    fr_rows = db.session.query(SkuForecastResult).filter(
        SkuForecastResult.item_code_365.in_(item_codes)
    ).all()
    for fr in fr_rows:
        forecast_results[fr.item_code_365] = fr

    fp_rows = db.session.query(SkuForecastProfile).filter(
        SkuForecastProfile.item_code_365.in_(item_codes)
    ).all()
    for fp in fp_rows:
        forecast_profiles[fp.item_code_365] = fp

    sm_rows = db.session.query(ForecastItemSupplierMap).filter(
        ForecastItemSupplierMap.item_code_365.in_(item_codes),
        ForecastItemSupplierMap.is_active == True,
    ).all()
    for sm in sm_rows:
        supplier_maps[sm.item_code_365] = sm

    items_with_forecast = sum(1 for ic in item_codes if ic in forecast_results)
    items_no_forecast = len(item_codes) - items_with_forecast
    logger.info(f"Forecast data: {items_with_forecast} items have forecasts, "
                f"{items_no_forecast} items without forecast (total {len(item_codes)})")

    run = ReplenishmentRun(
        supplier_code=supplier_code,
        supplier_name=supplier.supplier_name,
        run_date=run_date,
        run_type='weekly',
        receipt_date=run_date,
        include_today_demand=True,
        status='draft',
        created_by=current_user.username if current_user else None,
    )
    db.session.add(run)
    db.session.flush()

    for item_code in item_codes:
        api_data = stock_snapshot[item_code]
        master = item_master.get(item_code, {})
        settings = item_settings.get(item_code, {})
        expiry = expiry_data.get(item_code, {})
        fcst_result = forecast_results.get(item_code)
        fcst_profile = forecast_profiles.get(item_code)
        smap = supplier_maps.get(item_code)

        item_name = master.get("item_name") or api_data.get("item_name", item_code)
        stock_now = api_data["stock_now_units"]
        reserved_now = api_data["reserved_now_units"]
        ordered_now = api_data["ordered_now_units"]
        on_transfer_now = api_data["on_transfer_now_units"]

        net_available = stock_now - reserved_now + ordered_now

        lead_time = 0.0
        review_cycle = default_review_cycle
        if smap:
            if smap.lead_time_days is not None:
                lead_time = float(smap.lead_time_days)
            if smap.review_cycle_days is not None:
                review_cycle = float(smap.review_cycle_days)

        if fcst_result:
            daily_forecast = float(fcst_result.final_forecast_daily_qty or 0)
            weekly_forecast = float(fcst_result.final_forecast_weekly_qty or 0)
            base_weekly = float(fcst_result.base_forecast_weekly_qty or 0)
            trend_adjusted_weekly = float(fcst_result.trend_adjusted_weekly_qty or 0)
        else:
            daily_forecast = 0.0
            weekly_forecast = 0.0
            base_weekly = 0.0
            trend_adjusted_weekly = 0.0

        demand_class = fcst_profile.demand_class if fcst_profile else "no_data"
        forecast_method = fcst_profile.forecast_method if fcst_profile else "NONE"
        trend_flag = fcst_profile.trend_flag if fcst_profile else "flat"
        forecast_confidence = fcst_profile.forecast_confidence if fcst_profile else "none"
        forecast_source = f"{forecast_method}" if fcst_profile else "none"

        buffer_stock = daily_forecast * buffer_days
        total_cover = cover_days + lead_time + review_cycle
        cover_fcst = daily_forecast * cover_days
        target_stock = daily_forecast * total_cover + buffer_stock

        raw_needed = max(0, target_stock - net_available)

        case_qty, case_qty_source = _resolve_case_qty(master, settings)
        min_order_cases = float(settings.get("min_order_cases") or 1)

        if case_qty and case_qty > 0 and raw_needed > 0:
            suggested_cases = math.ceil(raw_needed / case_qty)
            suggested_cases = max(suggested_cases, min_order_cases)
            suggested_units = suggested_cases * case_qty
        else:
            suggested_cases = 0
            suggested_units = 0

        warnings = _build_warnings(
            case_qty, net_available - cover_fcst, reserved_now, ordered_now,
            expiry, suggested_cases, 0, cover_fcst,
            net_available, forecast_source
        )
        warning_code = warnings[0][0] if warnings else None
        warning_text = warnings[0][1] if warnings else None

        explanation = (
            f"Demand class: {demand_class}. Method: {forecast_method}. "
            f"Weekly forecast: {weekly_forecast:.2f}. Daily forecast: {daily_forecast:.2f}. "
            f"Cover {cover_days}d + LT {lead_time:.0f}d + RC {review_cycle:.0f}d = {total_cover:.0f}d. "
            f"Buffer stock ({buffer_days:.0f}d): {buffer_stock:.2f}. "
            f"Target: {target_stock:.1f}. "
            f"Net available = stock {stock_now:.0f} - reserved {reserved_now:.0f} "
            f"+ ordered {ordered_now:.0f} = {net_available:.0f}. "
            f"Raw need: {raw_needed:.1f}."
        )

        calc_json = {
            "demand_class": demand_class,
            "forecast_method": forecast_method,
            "forecast_confidence": forecast_confidence,
            "trend_flag": trend_flag,
            "base_weekly_forecast": round(base_weekly, 4),
            "trend_adjusted_weekly": round(trend_adjusted_weekly, 4),
            "final_weekly_forecast": round(weekly_forecast, 4),
            "final_daily_forecast": round(daily_forecast, 4),
            "cover_days": cover_days,
            "lead_time_days": lead_time,
            "review_cycle_days": review_cycle,
            "total_cover_days": total_cover,
            "buffer_days": buffer_days,
            "buffer_stock_qty": round(buffer_stock, 4),
            "target_stock_qty": round(target_stock, 4),
            "stock_now": stock_now,
            "reserved_now": reserved_now,
            "ordered_now": ordered_now,
            "net_available": net_available,
            "cover_forecast_units": round(cover_fcst, 4),
            "raw_needed_units": round(raw_needed, 4),
            "case_qty_units": case_qty or 0,
            "case_qty_source": case_qty_source,
            "min_order_cases": min_order_cases,
        }

        if fcst_profile:
            calc_json["weeks_non_zero_26"] = fcst_profile.weeks_non_zero_26
            calc_json["adi_26"] = float(fcst_profile.adi_26) if fcst_profile.adi_26 else None
            calc_json["cv2_26"] = float(fcst_profile.cv2_26) if fcst_profile.cv2_26 else None
            calc_json["seed_source"] = fcst_profile.seed_source
            calc_json["analogue_level"] = fcst_profile.analogue_level
            calc_json["review_flag"] = fcst_profile.review_flag
            calc_json["review_reason"] = fcst_profile.review_reason

        line = ReplenishmentRunLine(
            run_id=run.id,
            item_code_365=item_code,
            item_name=item_name,
            case_qty_units=Decimal(str(case_qty or 0)),
            stock_now_units=Decimal(str(stock_now)),
            reserved_now_units=Decimal(str(reserved_now)),
            ordered_now_units=Decimal(str(ordered_now)),
            on_transfer_now_units=Decimal(str(on_transfer_now)),
            available_base_units=Decimal(str(net_available)),
            pre_receipt_forecast_units=Decimal("0"),
            projected_units_at_receipt=Decimal(str(round(net_available, 2))),
            cover_forecast_units=Decimal(str(round(cover_fcst, 2))),
            safety_stock_units=Decimal(str(round(buffer_stock, 2))),
            raw_needed_units=Decimal(str(round(raw_needed, 2))),
            suggested_cases=Decimal(str(round(suggested_cases, 2))),
            suggested_units=Decimal(str(round(suggested_units, 2))),
            final_cases=Decimal(str(round(suggested_cases, 2))),
            final_units=Decimal(str(round(suggested_units, 2))),
            earliest_expiry_date=expiry.get("earliest_expiry_date"),
            qty_at_earliest_expiry=Decimal(str(expiry.get("qty_at_earliest_expiry", 0))),
            warning_code=warning_code,
            explanation_text=explanation,
            calc_json=calc_json,
        )
        db.session.add(line)

    db.session.commit()
    logger.info(f"Run {run.id} created with {len(item_codes)} lines")
    return run.id


def _resolve_case_qty(master, settings):
    settings_cq = settings.get("case_qty_units")
    if settings_cq and settings_cq > 0:
        return float(settings_cq), "item_settings"

    master_cq = master.get("case_qty") or master.get("number_field_2_value")
    if master_cq and master_cq > 0:
        return float(master_cq), "item_master"

    min_order = master.get("min_order_qty") or master.get("number_field_5_value")
    if min_order and min_order > 0:
        return float(min_order), "min_order_qty"

    return None, "missing"


def _build_warnings(case_qty, projected_stock, reserved, ordered, expiry_data,
                    suggested_cases, manual_review_check, cover_fcst,
                    net_available, fcst_source):
    warnings = []

    if not case_qty or case_qty <= 0:
        warnings.append(("CASE_QTY_MISSING", WARNING_TEXT_MAP["CASE_QTY_MISSING"]))

    if net_available <= 0 and fcst_source == "none":
        warnings.append(("MANUAL_REVIEW_REQUIRED", WARNING_TEXT_MAP["MANUAL_REVIEW_REQUIRED"]))

    if projected_stock < 0:
        warnings.append(("NEGATIVE_PROJECTED_STOCK", WARNING_TEXT_MAP["NEGATIVE_PROJECTED_STOCK"]))

    if cover_fcst <= 0:
        warnings.append(("NO_RECENT_SALES", WARNING_TEXT_MAP["NO_RECENT_SALES"]))

    if expiry_data and expiry_data.get("has_expiry_within_30d"):
        warnings.append(("EXPIRY_SOON", WARNING_TEXT_MAP["EXPIRY_SOON"]))

    if ordered > 0:
        warnings.append(("HAS_ORDERED_STOCK", WARNING_TEXT_MAP["HAS_ORDERED_STOCK"]))

    if reserved > 0:
        warnings.append(("HAS_CURRENT_RESERVED", WARNING_TEXT_MAP["HAS_CURRENT_RESERVED"]))

    if suggested_cases == 0:
        warnings.append(("ZERO_ORDER", WARNING_TEXT_MAP["ZERO_ORDER"]))

    warnings.sort(key=lambda x: WARNING_PRIORITY.index(x[0]) if x[0] in WARNING_PRIORITY else 99)
    return warnings
