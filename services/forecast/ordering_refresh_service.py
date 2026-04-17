import logging
import math
from decimal import Decimal
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy import desc

from models import (
    SkuForecastProfile,
    SkuForecastResult,
    SkuOrderingSnapshot,
    SkuForecastOverride,
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


def _safe_num(session, key, default, cast=float):
    try:
        return cast(Setting.get(session, key, str(default)))
    except (ValueError, TypeError):
        return default


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
    context = {
        "supplier_code": None,
        "supplier_source": None,
        "lead_time_days": 0.0,
        "review_cycle_days": 1.0,
        "min_order_qty": 0.0,
        "fallback_used": False,
    }

    if supplier_map:
        context["supplier_code"] = supplier_map.supplier_code or None
        context["supplier_source"] = "supplier_map"
        context["lead_time_days"] = _to_float(supplier_map.lead_time_days)
        context["review_cycle_days"] = _to_float(supplier_map.review_cycle_days) or 1.0
        context["min_order_qty"] = _to_float(supplier_map.min_order_qty_override)

    if not context["supplier_code"] and dw_item and dw_item.supplier_code_365:
        context["supplier_code"] = dw_item.supplier_code_365
        context["supplier_source"] = "dw_item"
        context["fallback_used"] = True

    return context


def _build_override_cache(session, item_codes=None):
    q = session.query(SkuForecastOverride).filter(
        SkuForecastOverride.is_active == True,
    )
    if item_codes:
        q = q.filter(SkuForecastOverride.item_code_365.in_(item_codes))

    cache = {}
    for o in q.all():
        code = o.item_code_365
        if code not in cache or o.created_at > cache[code].created_at:
            cache[code] = o
    return cache


def refresh_ordering_snapshot(
    session: Session,
    supplier_code: str = None,
    item_codes: list = None,
    created_by: str = None,
    progress_callback=None,
):
    now = get_utc_now()
    buffer_days = _safe_num(session, "forecast_buffer_stock_days", 1.0, float)
    default_review_cycle = _safe_num(session, "forecast_review_cycle_days", 1.0, float)

    supplier_map_cache = {}
    maps = session.query(ForecastItemSupplierMap).filter_by(is_active=True).all()
    for m in maps:
        supplier_map_cache[m.item_code_365] = m

    supplier_map_items_for_supplier = set()
    if supplier_code:
        for code, smap in supplier_map_cache.items():
            if smap.supplier_code == supplier_code:
                supplier_map_items_for_supplier.add(code)

    results_q = session.query(SkuForecastResult).join(
        DwItem, DwItem.item_code_365 == SkuForecastResult.item_code_365
    ).filter(DwItem.active == True)

    if supplier_code:
        results_q = results_q.filter(DwItem.supplier_code_365 == supplier_code)
    if item_codes:
        results_q = results_q.filter(SkuForecastResult.item_code_365.in_(item_codes))

    results = results_q.all()
    result_map = {r.item_code_365: r for r in results}

    if supplier_code and supplier_map_items_for_supplier:
        missing_codes = supplier_map_items_for_supplier - set(result_map.keys())
        if missing_codes:
            extra_q = session.query(SkuForecastResult).filter(
                SkuForecastResult.item_code_365.in_(list(missing_codes))
            )
            for r in extra_q.all():
                if r.item_code_365 not in result_map:
                    result_map[r.item_code_365] = r
                    results.append(r)
            added = len([c for c in missing_codes if c in result_map])
            logger.info(f"Ordering refresh: found {len(missing_codes)} supplier-map items"
                        f" not in DwItem query, {added} had forecast results"
                        f" (supplier={supplier_code})")

    logger.info(f"Ordering refresh: processing {len(results)} items"
                f" (supplier={supplier_code}, buffer={buffer_days}d)")

    profile_cache = {}
    profile_q = session.query(SkuForecastProfile)
    if item_codes:
        profile_q = profile_q.filter(SkuForecastProfile.item_code_365.in_(item_codes))
    for p in profile_q.all():
        profile_cache[p.item_code_365] = p

    all_item_codes = [r.item_code_365 for r in results]

    override_cache = _build_override_cache(session, all_item_codes)

    dw_item_cache = {}
    dw_q = session.query(DwItem)
    if item_codes:
        dw_q = dw_q.filter(DwItem.item_code_365.in_(item_codes))
    for item in dw_q.all():
        dw_item_cache[item.item_code_365] = item

    stock_cache_by_supplier = {}

    def _get_stock_cache(sup_code):
        if sup_code not in stock_cache_by_supplier:
            try:
                if progress_callback:
                    progress_callback(f"Fetching stock from PS365 for supplier {sup_code}...")
                from services.replenishment_mvp.ps365_client import fetch_supplier_stock
                stock_cache_by_supplier[sup_code] = fetch_supplier_stock(sup_code)
            except Exception as e:
                logger.warning(f"Failed to fetch stock for supplier {sup_code}: {e}")
                stock_cache_by_supplier[sup_code] = {}
        return stock_cache_by_supplier[sup_code]

    last_run = (
        session.query(SkuForecastResult.run_id, SkuForecastResult.calculated_at)
        .order_by(desc(SkuForecastResult.calculated_at))
        .first()
    )
    forecast_run_id = last_run.run_id if last_run else None
    forecast_calculated_at = last_run.calculated_at if last_run else None

    count = 0
    override_count = 0
    snapshots = []

    for result in results:
        item_code = result.item_code_365
        profile = profile_cache.get(item_code)
        dw_item = dw_item_cache.get(item_code)
        smap = supplier_map_cache.get(item_code)

        sup_ctx = _resolve_supplier_context(item_code, smap, dw_item)
        lead_time = sup_ctx["lead_time_days"]
        review_cycle = sup_ctx["review_cycle_days"] or default_review_cycle

        target_weeks = 4.0
        if profile and profile.target_weeks_of_stock is not None:
            target_weeks = _to_float(profile.target_weeks_of_stock)

        system_weekly = _to_float(result.final_forecast_weekly_qty)
        system_daily = _to_float(result.final_forecast_daily_qty)

        override = override_cache.get(item_code)
        if override:
            override_weekly = float(override.override_weekly_qty)
            effective_weekly = override_weekly
            effective_daily = override_weekly / 7.0
            forecast_source = "override"
            override_count += 1
        else:
            override_weekly = None
            effective_weekly = system_weekly
            effective_daily = system_daily
            forecast_source = "system"

        base_target_stock = effective_weekly * target_weeks
        lead_time_cover = effective_daily * lead_time
        review_cycle_cover = effective_daily * review_cycle
        buffer_stock = effective_daily * buffer_days
        target_stock = base_target_stock + lead_time_cover + review_cycle_cover + buffer_stock

        stock_cache = _get_stock_cache(sup_ctx["supplier_code"]) if sup_ctx["supplier_code"] else {}
        on_hand, incoming, reserved = _get_stock_for_item(item_code, stock_cache)
        net_available = on_hand + incoming - reserved

        raw_order = max(0.0, target_stock - net_available)

        moq = sup_ctx["min_order_qty"]
        if moq <= 0 and dw_item and dw_item.min_order_qty and dw_item.min_order_qty > 0:
            moq = float(dw_item.min_order_qty)

        order_multiple = _to_float(smap.order_multiple) if smap and smap.order_multiple else 0.0

        rounded = raw_order
        rounding_step = order_multiple if order_multiple > 0 else (moq if moq > 0 else 0.0)
        if raw_order > 0:
            if rounding_step > 0:
                rounded = _ceil_to_multiple(raw_order, rounding_step)
            else:
                rounded = math.ceil(raw_order)
            rounded = _enforce_moq(rounded, moq)

        explanation = {
            "forecast_source": forecast_source,
            "system_forecast_weekly_qty": round(system_weekly, 6),
            "effective_weekly_qty": round(effective_weekly, 6),
            "target_weeks_of_stock": target_weeks,
            "base_target_stock": round(base_target_stock, 4),
            "lead_time_cover": round(lead_time_cover, 4),
            "review_cycle_cover": round(review_cycle_cover, 4),
            "buffer_stock": round(buffer_stock, 4),
            "buffer_days": buffer_days,
            "target_stock": round(target_stock, 4),
            "on_hand": round(on_hand, 4),
            "incoming": round(incoming, 4),
            "reserved": round(reserved, 4),
            "net_available": round(net_available, 4),
            "raw_order": round(raw_order, 4),
            "order_multiple": order_multiple if order_multiple > 0 else None,
            "min_order_qty": moq,
            "rounded_order": round(rounded, 4),
            "supplier_code": sup_ctx["supplier_code"],
            "supplier_source": sup_ctx["supplier_source"],
        }
        if override:
            explanation["override_weekly_qty"] = round(override_weekly, 6)
            explanation["override_reason_code"] = override.reason_code
            explanation["override_created_at"] = override.created_at.isoformat() if override.created_at else None
            explanation["override_created_by"] = override.created_by
            explanation["override_review_due_at"] = override.review_due_at.isoformat() if override.review_due_at else None

        snap = SkuOrderingSnapshot(
            item_code_365=item_code,
            snapshot_type="manual",
            snapshot_at=now,
            created_by=created_by,
            forecast_run_id=forecast_run_id,
            forecast_calculated_at=forecast_calculated_at,
            target_weeks_of_stock=Decimal(str(round(target_weeks, 4))),
            lead_time_days=Decimal(str(round(lead_time, 4))),
            review_cycle_days=Decimal(str(round(review_cycle, 4))),
            buffer_days=Decimal(str(round(buffer_days, 4))),
            base_forecast_weekly_qty=Decimal(str(round(_to_float(result.base_forecast_weekly_qty), 6))),
            trend_adjusted_weekly_qty=Decimal(str(round(_to_float(result.trend_adjusted_weekly_qty), 6))),
            final_forecast_weekly_qty=Decimal(str(round(effective_weekly, 6))),
            final_forecast_daily_qty=Decimal(str(round(effective_daily, 6))),
            system_forecast_weekly_qty=Decimal(str(round(system_weekly, 6))),
            override_forecast_weekly_qty=Decimal(str(round(override_weekly, 6))) if override_weekly is not None else None,
            final_forecast_source=forecast_source,
            on_hand_qty=Decimal(str(round(on_hand, 6))),
            incoming_qty=Decimal(str(round(incoming, 6))),
            reserved_qty=Decimal(str(round(reserved, 6))),
            net_available_qty=Decimal(str(round(net_available, 6))),
            target_stock_qty=Decimal(str(round(target_stock, 6))),
            raw_recommended_order_qty=Decimal(str(round(raw_order, 6))),
            rounded_order_qty=Decimal(str(round(rounded, 6))),
            supplier_code=sup_ctx["supplier_code"],
            order_multiple=Decimal(str(round(order_multiple, 4))) if order_multiple > 0 else None,
            min_order_qty=Decimal(str(round(moq, 4))) if moq else None,
            explanation_json=explanation,
        )
        session.add(snap)

        # Clear any manual order quantity — the new snapshot supersedes it
        if profile is not None and getattr(profile, 'manual_order_qty', None) is not None:
            profile.manual_order_qty = None
            profile.manual_order_qty_updated_at = get_utc_now()
            profile.manual_order_qty_updated_by = f'system:ordering_refresh'

        snapshots.append(snap)
        count += 1

        if count % 500 == 0:
            session.flush()
            logger.info(f"Ordering refresh: processed {count}/{len(results)} items")
            if progress_callback:
                progress_callback(f"Processed {count}/{len(results)} ordering items")

    session.flush()
    logger.info(f"Ordering refresh completed: {count} snapshots created"
                f" ({override_count} using overrides)")

    return {
        "status": "completed",
        "snapshot_count": count,
        "override_count": override_count,
        "snapshot_at": now.isoformat() if now else None,
        "created_by": created_by,
    }


def get_latest_snapshots(session: Session, item_codes: list = None, supplier_code: str = None):
    from sqlalchemy import func as sqla_func

    latest_sub = (
        session.query(
            SkuOrderingSnapshot.item_code_365,
            sqla_func.max(SkuOrderingSnapshot.snapshot_at).label("max_at"),
        )
        .group_by(SkuOrderingSnapshot.item_code_365)
        .subquery()
    )

    q = (
        session.query(SkuOrderingSnapshot)
        .join(
            latest_sub,
            (SkuOrderingSnapshot.item_code_365 == latest_sub.c.item_code_365)
            & (SkuOrderingSnapshot.snapshot_at == latest_sub.c.max_at),
        )
    )

    if item_codes:
        q = q.filter(SkuOrderingSnapshot.item_code_365.in_(item_codes))
    if supplier_code:
        q = q.filter(SkuOrderingSnapshot.supplier_code == supplier_code)

    snapshots = q.all()
    return {s.item_code_365: s for s in snapshots}
