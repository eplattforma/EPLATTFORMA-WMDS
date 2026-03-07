"""
Replenishment MVP - Planner

Main orchestration: generate_replenishment_run()
Pulls all data sources together and produces a saved run with lines.

V1 limitations:
- Uses current reserved stock only (not future reserved by delivery date)
- Uses current ordered stock only
- Historical weekday sales averages only (no seasonality)
- No expiry-based ordering math
- No auto PO creation
"""
import logging
import math
from datetime import date
from decimal import Decimal

from app import db
from models import ReplenishmentRun, ReplenishmentRunLine

from services.replenishment_mvp.calendar import (
    get_receipt_date, get_pre_receipt_dates, get_cover_dates_after_receipt
)
from services.replenishment_mvp.ps365_client import fetch_supplier_stock, REPLENISHMENT_WAREHOUSE_STORE
from services.replenishment_mvp.repositories import (
    get_supplier_by_code, get_item_master_for_codes,
    get_item_settings_for_codes, get_same_weekday_sales_averages,
    get_expiry_summary
)
from services.replenishment_mvp.forecast import get_forecast_for_dates

logger = logging.getLogger(__name__)

WARNING_PRIORITY = [
    "CASE_QTY_MISSING",
    "NEGATIVE_PROJECTED_STOCK",
    "NO_RECENT_SALES",
    "EXPIRY_SOON",
    "HAS_ORDERED_STOCK",
    "HAS_CURRENT_RESERVED",
    "ZERO_ORDER",
]

WARNING_TEXT_MAP = {
    "CASE_QTY_MISSING": "No valid case quantity found - cannot calculate order",
    "NEGATIVE_PROJECTED_STOCK": "Projected stock at receipt is negative - potential stockout",
    "NO_RECENT_SALES": "No sales in lookback window - forecast is zero",
    "EXPIRY_SOON": "Stock expiring within 30 days",
    "HAS_ORDERED_STOCK": "Has open purchase orders",
    "HAS_CURRENT_RESERVED": "Has currently reserved stock",
    "ZERO_ORDER": "No order needed",
}


def generate_replenishment_run(
    supplier_code: str,
    run_date: date,
    run_type: str,
    include_today_demand: bool,
    current_user=None,
) -> int:
    supplier = get_supplier_by_code(supplier_code)
    if not supplier:
        raise ValueError(f"Supplier not found: {supplier_code}")
    if not supplier.is_active:
        raise ValueError(f"Supplier is inactive: {supplier_code}")

    receipt_date = get_receipt_date(run_date, run_type)
    pre_receipt_dates = get_pre_receipt_dates(run_date, run_type, include_today_demand)
    cover_dates = get_cover_dates_after_receipt(receipt_date, run_type)

    all_needed_weekdays = list(set(d.weekday() for d in pre_receipt_dates + cover_dates))

    logger.info(f"Run {run_type} on {run_date}: receipt={receipt_date}, "
                f"pre_receipt={[str(d) for d in pre_receipt_dates]}, "
                f"cover={[str(d) for d in cover_dates]}")

    stock_snapshot = fetch_supplier_stock(supplier_code, REPLENISHMENT_WAREHOUSE_STORE)
    if not stock_snapshot:
        raise ValueError(f"No items returned from PS365 for supplier {supplier_code}")

    item_codes = list(stock_snapshot.keys())
    item_master = get_item_master_for_codes(item_codes)
    item_settings = get_item_settings_for_codes(item_codes)
    weekday_avgs = get_same_weekday_sales_averages(item_codes, all_needed_weekdays, reference_date=run_date)
    expiry_data = get_expiry_summary(item_codes, REPLENISHMENT_WAREHOUSE_STORE)

    pre_forecast = get_forecast_for_dates(item_codes, pre_receipt_dates, weekday_avgs)
    cover_forecast = get_forecast_for_dates(item_codes, cover_dates, weekday_avgs)

    items_with_sales = sum(1 for ic in item_codes if any(weekday_avgs.get(ic, {}).get(wd, 0) > 0 for wd in all_needed_weekdays))
    logger.info(f"Forecast diagnostics: {len(item_codes)} items, "
                f"{items_with_sales} with non-zero weekday averages, "
                f"weekdays={all_needed_weekdays}")
    if items_with_sales == 0:
        logger.warning("ALL items have zero weekday averages - check sales data in dw_invoice_header/dw_invoice_line")

    run = ReplenishmentRun(
        supplier_code=supplier_code,
        supplier_name=supplier.supplier_name,
        run_date=run_date,
        run_type=run_type,
        receipt_date=receipt_date,
        include_today_demand=include_today_demand,
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

        item_name = master.get("item_name") or api_data.get("item_name", item_code)
        stock_now = api_data["stock_now_units"]
        reserved_now = api_data["reserved_now_units"]
        ordered_now = api_data["ordered_now_units"]
        on_transfer_now = api_data["on_transfer_now_units"]

        available_base = stock_now - reserved_now + ordered_now

        pre_receipt_fcst = sum(pre_forecast.get(item_code, {}).get(d, 0) for d in pre_receipt_dates)
        projected_at_receipt = available_base - pre_receipt_fcst

        cover_fcst = sum(cover_forecast.get(item_code, {}).get(d, 0) for d in cover_dates)

        case_qty, case_qty_source = _resolve_case_qty(master, settings)
        min_order_cases = float(settings.get("min_order_cases") or 1)
        safety_days = float(settings.get("safety_days_override") or 1.0)

        num_cover_dates = len(cover_dates)
        avg_daily_cover = cover_fcst / num_cover_dates if num_cover_dates > 0 else 0
        safety_stock = avg_daily_cover * safety_days

        raw_needed = max(0, cover_fcst + safety_stock - projected_at_receipt)

        if case_qty and case_qty > 0 and raw_needed > 0:
            suggested_cases = math.ceil(raw_needed / case_qty)
            suggested_cases = max(suggested_cases, min_order_cases)
            suggested_units = suggested_cases * case_qty
        else:
            suggested_cases = 0
            suggested_units = 0

        warnings = _build_warnings(
            case_qty, projected_at_receipt, reserved_now, ordered_now,
            expiry, suggested_cases, pre_receipt_fcst, cover_fcst
        )
        warning_code = warnings[0][0] if warnings else None
        warning_text = warnings[0][1] if warnings else None

        explanation = (
            f"Available base = stock {stock_now:.0f} - reserved {reserved_now:.0f} "
            f"+ ordered {ordered_now:.0f} = {available_base:.0f}. "
            f"Pre-receipt forecast {pre_receipt_fcst:.0f} => "
            f"projected at receipt {projected_at_receipt:.0f}. "
            f"Cover forecast {cover_fcst:.0f} + safety {safety_stock:.0f} "
            f"=> raw need {raw_needed:.0f}."
        )

        weekday_avgs_used = {}
        for wd in all_needed_weekdays:
            weekday_avgs_used[str(wd)] = weekday_avgs.get(item_code, {}).get(wd, 0)

        calc_json = {
            "pre_receipt_dates": [str(d) for d in pre_receipt_dates],
            "cover_dates": [str(d) for d in cover_dates],
            "weekday_averages": weekday_avgs_used,
            "available_base_units": available_base,
            "pre_receipt_forecast_units": pre_receipt_fcst,
            "projected_units_at_receipt": projected_at_receipt,
            "cover_forecast_units": cover_fcst,
            "safety_days": safety_days,
            "safety_stock_units": safety_stock,
            "raw_needed_units": raw_needed,
            "case_qty_units": case_qty or 0,
            "case_qty_source": case_qty_source,
            "min_order_cases": min_order_cases,
            "sales_date_field": "invoice_date_utc0",
        }

        line = ReplenishmentRunLine(
            run_id=run.id,
            item_code_365=item_code,
            item_name=item_name,
            case_qty_units=Decimal(str(case_qty or 0)),
            stock_now_units=Decimal(str(stock_now)),
            reserved_now_units=Decimal(str(reserved_now)),
            ordered_now_units=Decimal(str(ordered_now)),
            on_transfer_now_units=Decimal(str(on_transfer_now)),
            available_base_units=Decimal(str(available_base)),
            pre_receipt_forecast_units=Decimal(str(round(pre_receipt_fcst, 2))),
            projected_units_at_receipt=Decimal(str(round(projected_at_receipt, 2))),
            cover_forecast_units=Decimal(str(round(cover_fcst, 2))),
            safety_stock_units=Decimal(str(round(safety_stock, 2))),
            raw_needed_units=Decimal(str(round(raw_needed, 2))),
            suggested_cases=Decimal(str(round(suggested_cases, 2))),
            suggested_units=Decimal(str(round(suggested_units, 2))),
            final_cases=Decimal(str(round(suggested_cases, 2))),
            final_units=Decimal(str(round(suggested_units, 2))),
            earliest_expiry_date=expiry.get("earliest_expiry_date"),
            qty_at_earliest_expiry=Decimal(str(expiry.get("qty_at_earliest_expiry", 0))),
            expiring_within_30_days_units=Decimal(str(expiry.get("expiring_within_30_days_units", 0))),
            warning_code=warning_code,
            warning_text=warning_text,
            explanation_text=explanation,
            calc_json=calc_json,
        )
        db.session.add(line)

    db.session.commit()
    logger.info(f"Replenishment run {run.id} created with {len(item_codes)} lines")
    return run.id


def _resolve_case_qty(master: dict, settings: dict) -> tuple[float | None, str]:
    moq = master.get("min_order_qty")
    if moq and moq > 0:
        return float(moq), "min_order_qty"
    override = settings.get("case_qty_units")
    if override and override > 0:
        return float(override), "item_settings_override"
    return None, "missing"


def _build_warnings(case_qty, projected_at_receipt, reserved_now, ordered_now, expiry, suggested_cases, pre_receipt_fcst=0, cover_fcst=0) -> list:
    warnings = []

    if not case_qty or case_qty <= 0:
        warnings.append(("CASE_QTY_MISSING", WARNING_TEXT_MAP["CASE_QTY_MISSING"]))

    if projected_at_receipt < 0:
        warnings.append(("NEGATIVE_PROJECTED_STOCK", WARNING_TEXT_MAP["NEGATIVE_PROJECTED_STOCK"]))

    if pre_receipt_fcst == 0 and cover_fcst == 0:
        warnings.append(("NO_RECENT_SALES", WARNING_TEXT_MAP["NO_RECENT_SALES"]))

    earliest_exp = expiry.get("earliest_expiry_date")
    exp_qty = expiry.get("expiring_within_30_days_units", 0)
    if earliest_exp and exp_qty > 0:
        from datetime import date as date_type, timedelta
        if earliest_exp <= date_type.today() + timedelta(days=30):
            warnings.append(("EXPIRY_SOON", WARNING_TEXT_MAP["EXPIRY_SOON"]))

    if ordered_now > 0:
        warnings.append(("HAS_ORDERED_STOCK", WARNING_TEXT_MAP["HAS_ORDERED_STOCK"]))

    if reserved_now > 0:
        warnings.append(("HAS_CURRENT_RESERVED", WARNING_TEXT_MAP["HAS_CURRENT_RESERVED"]))

    if suggested_cases == 0:
        warnings.append(("ZERO_ORDER", WARNING_TEXT_MAP["ZERO_ORDER"]))

    warnings.sort(key=lambda w: WARNING_PRIORITY.index(w[0]) if w[0] in WARNING_PRIORITY else 99)
    return warnings
