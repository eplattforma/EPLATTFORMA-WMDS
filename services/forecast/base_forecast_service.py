import logging
import math
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, desc, func
from sqlalchemy.orm import Session

from models import (
    SkuForecastProfile,
    SkuForecastResult,
    FactSalesWeeklyItem,
    DwItem,
    Setting,
    extract_item_prefix,
)
from timezone_utils import get_utc_now
from services.forecast.week_utils import get_completed_week_cutoff

logger = logging.getLogger(__name__)

TREND_UPLIFT_TRIGGER_KEY = "forecast_trend_uplift_trigger"
TREND_DOWN_TRIGGER_KEY = "forecast_trend_down_trigger"
TREND_UPLIFT_CAP_KEY = "forecast_trend_uplift_cap"
TREND_DOWN_FLOOR_KEY = "forecast_trend_down_floor"


def _to_float(v):
    if v is None:
        return 0.0
    return float(v)


def _get_recent_weekly_qtys(session: Session, item_code: str, n_weeks: int):
    completed_week_cutoff = get_completed_week_cutoff()
    cutoff = completed_week_cutoff - timedelta(weeks=n_weeks)

    rows = (
        session.query(FactSalesWeeklyItem.week_start, FactSalesWeeklyItem.gross_qty)
        .filter(
            FactSalesWeeklyItem.item_code_365 == item_code,
            FactSalesWeeklyItem.week_start >= cutoff,
            FactSalesWeeklyItem.week_start < completed_week_cutoff,
        )
        .order_by(desc(FactSalesWeeklyItem.week_start))
        .all()
    )

    existing = {r.week_start: _to_float(r.gross_qty) for r in rows}

    weeks = []
    for i in range(n_weeks):
        ws = completed_week_cutoff - timedelta(weeks=(i + 1))
        weeks.append(existing.get(ws, 0.0))

    return weeks


def _compute_ma8(weekly_qtys):
    last8 = weekly_qtys[:8]
    if not last8:
        return 0.0
    return sum(last8) / len(last8)


def _compute_median6(weekly_qtys):
    last6 = weekly_qtys[:6]
    if not last6:
        return 0.0
    sorted_vals = sorted(last6)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0
    return sorted_vals[mid]


def _percentile(values, pct):
    if not values:
        return None
    sorted_v = sorted(values)
    idx = int(len(sorted_v) * pct / 100)
    idx = min(idx, len(sorted_v) - 1)
    return sorted_v[idx]


def _build_analogue_floors(session, profiles, dw_item_cache):
    completed_week_cutoff = get_completed_week_cutoff()
    cutoff_26w = completed_week_cutoff - timedelta(weeks=26)

    eligible_profiles = {
        p.item_code_365: p for p in profiles
        if p.demand_class in ("smooth", "erratic")
        and (p.weeks_non_zero_26 or 0) >= 6
    }

    brand_groups = {}
    category_groups = {}

    for item_code, dw_item in dw_item_cache.items():
        if not dw_item.active or item_code not in eligible_profiles:
            continue
        if dw_item.brand_code_365:
            brand_groups.setdefault(dw_item.brand_code_365, []).append(item_code)
        if dw_item.category_code_365:
            category_groups.setdefault(dw_item.category_code_365, []).append(item_code)

    all_eligible_items = list(eligible_profiles.keys())

    item_weekly_avg = {}
    if all_eligible_items:
        batch_size = 500
        for i in range(0, len(all_eligible_items), batch_size):
            batch = all_eligible_items[i:i + batch_size]
            rows = (
                session.query(
                    FactSalesWeeklyItem.item_code_365,
                    func.sum(FactSalesWeeklyItem.gross_qty),
                )
                .filter(
                    FactSalesWeeklyItem.item_code_365.in_(batch),
                    FactSalesWeeklyItem.week_start >= cutoff_26w,
                    FactSalesWeeklyItem.week_start < completed_week_cutoff,
                )
                .group_by(FactSalesWeeklyItem.item_code_365)
                .all()
            )
            for item_code_r, total_qty in rows:
                avg = float(total_qty or 0) / 26.0
                if avg > 0:
                    item_weekly_avg[item_code_r] = avg

    cache = {}

    for brand, members in brand_groups.items():
        avgs = [item_weekly_avg[ic] for ic in members if ic in item_weekly_avg]
        if len(avgs) >= 2:
            cache[("brand", brand)] = _percentile(avgs, 25)
        elif len(avgs) == 1:
            cache[("brand", brand)] = avgs[0] * 0.5

    for cat, members in category_groups.items():
        avgs = [item_weekly_avg[ic] for ic in members if ic in item_weekly_avg]
        if len(avgs) >= 3:
            cache[("category", cat)] = _percentile(avgs, 20)

    logger.info(
        f"Analogue floors built: {sum(1 for k in cache if k[0] == 'brand')} brands, "
        f"{sum(1 for k in cache if k[0] == 'category')} categories"
    )
    return cache


def _get_analogue_floor(item_code, dw_item_cache, analogue_cache):
    dw_item = dw_item_cache.get(item_code)
    if not dw_item:
        return 0.0, "none"

    brand = dw_item.brand_code_365
    if brand and ("brand", brand) in analogue_cache:
        floor = analogue_cache[("brand", brand)]
        if floor is not None and floor > 0:
            return floor, "brand"

    category = dw_item.category_code_365
    if category and ("category", category) in analogue_cache:
        floor = analogue_cache[("category", category)]
        if floor is not None and floor > 0:
            return floor, "category"

    return 0.0, "none"


def _compute_seeded_new_forecast(item_code, dw_item_cache, analogue_cache):
    floor, level = _get_analogue_floor(item_code, dw_item_cache, analogue_cache)
    return floor, level, "low"


def _compute_rate_based_forecast(weekly_qtys, week_starts, oos_weeks):
    first_sale_idx = None
    for i, qty in enumerate(weekly_qtys):
        if qty > 0:
            first_sale_idx = i
    if first_sale_idx is None:
        return 0.0

    total_qty = 0.0
    active_weeks = 0
    for i in range(first_sale_idx + 1):
        if week_starts[i] in oos_weeks:
            continue
        total_qty += weekly_qtys[i]
        active_weeks += 1

    if active_weeks == 0:
        return 0.0

    return total_qty / active_weeks


def _compute_availability_distorted_forecast(
    item_code, weekly_qtys, week_starts, oos_weeks, dw_item_cache, analogue_cache
):
    review_note = None

    if oos_weeks:
        total_qty = sum(weekly_qtys)
        total_weeks = len(weekly_qtys)
        oos_count = sum(1 for ws in week_starts if ws in oos_weeks)
        active_weeks = total_weeks - oos_count

        if active_weeks >= 2:
            clean_total = sum(q for q, ws in zip(weekly_qtys, week_starts) if ws not in oos_weeks)
            forecast = clean_total / active_weeks
            return forecast, "exposure_adjusted", "medium", review_note

    floor, level = _get_analogue_floor(item_code, dw_item_cache, analogue_cache)
    if floor > 0:
        review_note = "Availability-distorted: analogue floor used, planner review recommended"
        return floor, level, "low", review_note

    return 0.0, "none", "low", "Availability-distorted: no analogue floor available"


def _compute_seeded_forecast(item_code, weekly_qtys, profile, dw_item_cache=None, group_baseline_cache=None):
    floor, level = _get_analogue_floor(item_code, dw_item_cache, group_baseline_cache or {})
    if floor > 0:
        return floor, level, None, "low", False
    return 0.0, "none", None, "none", False


def _get_seasonality_indexes_preloaded(item_code, dw_item_cache, seasonality_index_map, reliable_levels, horizon_days):
    dw_item = dw_item_cache.get(item_code)
    if not dw_item:
        return 1.0, 1.0, "none", None, "none"

    source = "none"
    level_code = None
    confidence = "none"

    brand = dw_item.brand_code_365
    if brand and ("brand", brand) in reliable_levels:
        source = "brand"
        level_code = brand
        confidence = reliable_levels[("brand", brand)]

    if source == "none":
        supplier = dw_item.supplier_code_365
        if supplier and ("supplier", supplier) in reliable_levels:
            source = "supplier"
            level_code = supplier
            confidence = reliable_levels[("supplier", supplier)]

    if source == "none" or not level_code:
        return 1.0, 1.0, "none", None, "none"

    completed_week_cutoff = get_completed_week_cutoff()
    from collections import defaultdict

    week_dates = []
    for i in range(8):
        d = completed_week_cutoff - timedelta(weeks=i + 1)
        week_dates.append(d)

    month_counts = defaultdict(int)
    for d in week_dates:
        month_counts[d.month] += 1
    total_weeks = sum(month_counts.values())

    if total_weeks > 0:
        weighted_sum = Decimal("0")
        for month_no, cnt in month_counts.items():
            factor = seasonality_index_map.get((source, level_code, month_no), Decimal("1"))
            weighted_sum += factor * Decimal(str(cnt))
        hist_index = float(weighted_sum / Decimal(str(total_weeks)))
    else:
        hist_index = 1.0

    from datetime import date as date_type
    today = date_type.today()
    month_days = defaultdict(int)
    for i in range(horizon_days):
        d = today + timedelta(days=i + 1)
        month_days[d.month] += 1

    if month_days and horizon_days > 0:
        weighted_sum = Decimal("0")
        for month_no, days in month_days.items():
            factor = seasonality_index_map.get((source, level_code, month_no), Decimal("1"))
            weighted_sum += factor * Decimal(str(days))
        future_index = float(weighted_sum / Decimal(str(horizon_days)))
    else:
        future_index = 1.0

    if hist_index <= 0:
        hist_index = 1.0
    if future_index <= 0:
        future_index = 1.0

    return hist_index, future_index, source, level_code, confidence


def _get_seasonality_indexes(session, item_code, profile):
    try:
        from services.forecast.seasonality_service import (
            choose_seasonality_source,
            get_historical_embedded_index,
            get_future_seasonal_index,
        )
    except ImportError:
        return 1.0, 1.0, "none", None, "none"

    source, level_code, confidence = choose_seasonality_source(session, item_code)
    if source == "none" or not level_code:
        return 1.0, 1.0, "none", None, "none"

    horizon_days = 14
    try:
        horizon_str = Setting.get(session, "forecast_horizon_days", "14")
        horizon_days = int(horizon_str)
    except (ValueError, TypeError):
        pass

    hist_index = get_historical_embedded_index(session, item_code, 8, source, level_code)
    future_index = get_future_seasonal_index(session, item_code, horizon_days, source, level_code)

    if hist_index is None or hist_index <= 0:
        hist_index = 1.0
    if future_index is None or future_index <= 0:
        future_index = 1.0

    return float(hist_index), float(future_index), source, level_code, confidence


def _safe_float(session, key, default):
    try:
        return float(Setting.get(session, key, str(default)))
    except (ValueError, TypeError):
        return default


def _min_clean_weeks_for_method(demand_class: str) -> int:
    if demand_class in ("smooth", "erratic"):
        return 8
    if demand_class in ("intermittent", "lumpy"):
        return 6
    if demand_class in ("new_sparse", "new_true", "sparse_valid", "availability_distorted"):
        return 4
    return 0


def _recent_trend_allowed(recent_calendar_week_starts, item_oos_weeks):
    if not item_oos_weeks:
        return True
    recent_two = recent_calendar_week_starts[:2]
    if not recent_two:
        return False
    return all(ws not in item_oos_weeks for ws in recent_two)


def _preload_group_baselines(session, profiles, dw_item_cache):
    return _build_analogue_floors(session, profiles, dw_item_cache)


def compute_base_forecasts(session: Session, run_id=None, progress_callback=None):
    from models import ForecastSeasonalityMonthly

    uplift_trigger = _safe_float(session, TREND_UPLIFT_TRIGGER_KEY, 1.15)
    down_trigger = _safe_float(session, TREND_DOWN_TRIGGER_KEY, 0.90)
    uplift_cap = _safe_float(session, TREND_UPLIFT_CAP_KEY, 1.25)
    down_floor = _safe_float(session, TREND_DOWN_FLOOR_KEY, 0.75)

    horizon_days = 14
    try:
        horizon_str = Setting.get(session, "forecast_horizon_days", "14")
        horizon_days = int(horizon_str)
    except (ValueError, TypeError):
        pass

    profiles = (
        session.query(SkuForecastProfile)
        .join(DwItem, DwItem.item_code_365 == SkuForecastProfile.item_code_365)
        .filter(DwItem.active == True)
        .all()
    )
    logger.info(f"Computing base forecasts for {len(profiles)} active items")

    now = get_utc_now()
    count = 0
    
    completed_week_cutoff = get_completed_week_cutoff()
    cutoff = completed_week_cutoff - timedelta(weeks=26)
    
    all_weekly_sales = (
        session.query(
            FactSalesWeeklyItem.item_code_365,
            FactSalesWeeklyItem.week_start,
            FactSalesWeeklyItem.gross_qty,
        )
        .filter(
            FactSalesWeeklyItem.week_start >= cutoff,
            FactSalesWeeklyItem.week_start < completed_week_cutoff,
        )
        .all()
    )
    
    existing_by_item = {}
    for item_code, week_start, gross_qty in all_weekly_sales:
        if item_code not in existing_by_item:
            existing_by_item[item_code] = {}
        existing_by_item[item_code][week_start] = float(gross_qty or 0)
    
    sales_by_item = {}
    week_starts_by_item = {}
    for item_code in set(p.item_code_365 for p in profiles):
        weekly_qtys = []
        ws_list = []
        for i in range(26):
            ws = completed_week_cutoff - timedelta(weeks=(i + 1))
            qty = existing_by_item.get(item_code, {}).get(ws, 0.0)
            weekly_qtys.append(qty)
            ws_list.append(ws)
        sales_by_item[item_code] = weekly_qtys
        week_starts_by_item[item_code] = ws_list
    
    active_codes = {p.item_code_365 for p in profiles}
    old_results = {}
    for result in session.query(SkuForecastResult).filter(SkuForecastResult.item_code_365.in_(active_codes)).all():
        old_results[result.item_code_365] = result
    logger.info(f"Preloaded {len(old_results)} existing forecast results for {len(active_codes)} active items")

    active_codes = [p.item_code_365 for p in profiles]
    dw_item_cache = {
        item.item_code_365: item
        for item in session.query(DwItem)
        .filter(DwItem.item_code_365.in_(active_codes))
        .all()
    }
    logger.info(f"Preloaded {len(dw_item_cache)} DwItem rows for {len(active_codes)} profiled items")

    seasonality_index_map = {}
    reliable_levels = {}
    brand_codes = {i.brand_code_365 for i in dw_item_cache.values() if i.brand_code_365}
    supplier_codes = {i.supplier_code_365 for i in dw_item_cache.values() if i.supplier_code_365}

    seasonality_rows = session.query(ForecastSeasonalityMonthly).filter(
        (
            (ForecastSeasonalityMonthly.level_type == 'brand') &
            (ForecastSeasonalityMonthly.level_code.in_(brand_codes))
        ) |
        (
            (ForecastSeasonalityMonthly.level_type == 'supplier') &
            (ForecastSeasonalityMonthly.level_code.in_(supplier_codes))
        )
    ).all()

    for row in seasonality_rows:
        seasonality_index_map[(row.level_type, row.level_code, row.month_no)] = Decimal(str(row.smoothed_index))
        if row.is_reliable:
            reliable_levels[(row.level_type, row.level_code)] = row.confidence
    logger.info(f"Preloaded {len(seasonality_index_map)} seasonality index rows, {len(reliable_levels)} reliable levels")

    analogue_cache = _build_analogue_floors(session, profiles, dw_item_cache)
    logger.info(f"Preloaded {len(analogue_cache)} analogue floors")

    try:
        from services.forecast.oos_demand_service import bulk_get_oos_weeks, OOS_THRESHOLD_DAYS
        oos_map = bulk_get_oos_weeks(session, 26, OOS_THRESHOLD_DAYS)
        logger.info(f"OOS data loaded for base forecast: {len(oos_map)} items with OOS weeks")
    except Exception as e:
        logger.warning(f"Could not load OOS data for base forecast: {e}")
        oos_map = {}

    for idx, profile in enumerate(profiles, start=1):
        item_code = profile.item_code_365
        demand_class = profile.demand_class
        weekly_qtys = sales_by_item.get(item_code, [])
        week_starts_for_item = week_starts_by_item.get(item_code, [])

        item_oos_weeks = oos_map.get(item_code, set())
        required_clean_weeks = _min_clean_weeks_for_method(demand_class)

        if item_oos_weeks and weekly_qtys:
            clean_qtys = [q for q, ws in zip(weekly_qtys, week_starts_for_item) if ws not in item_oos_weeks]

            if len(clean_qtys) >= required_clean_weeks:
                forecast_qtys = clean_qtys
                oos_was_applied = True
            else:
                forecast_qtys = weekly_qtys
                oos_was_applied = False
        else:
            forecast_qtys = weekly_qtys
            oos_was_applied = False

        profile.oos_weeks_26 = len(item_oos_weeks)
        profile.oos_adjusted = oos_was_applied

        allow_trend = _recent_trend_allowed(week_starts_for_item, item_oos_weeks)

        base_forecast = 0.0
        forecast_method = "ZERO"
        trend_flag = "flat"
        trend_pct = None
        forecast_confidence = "medium"
        seed_source = None
        analogue_item = None
        analogue_level = None

        is_incomplete = getattr(profile, 'history_incomplete', False)

        if is_incomplete:
            floor, level, conf = _compute_seeded_new_forecast(
                item_code, dw_item_cache, analogue_cache
            )
            base_forecast = floor
            analogue_level = level
            forecast_confidence = conf
            forecast_method = "INSUFFICIENT_HISTORY"
            seed_source = analogue_level
            profile.baseline_source = analogue_level
        elif demand_class == "smooth":
            base_forecast = _compute_ma8(forecast_qtys)
            forecast_method = "MA8"
            forecast_confidence = "high"
        elif demand_class == "erratic":
            base_forecast = _compute_ma8(forecast_qtys)
            forecast_method = "MA8"
            forecast_confidence = "medium"
        elif demand_class in ("intermittent", "lumpy"):
            base_forecast = _compute_median6(forecast_qtys)
            forecast_method = "MEDIAN6"
            forecast_confidence = "medium"

            if base_forecast == 0.0 and any(q > 0 for q in weekly_qtys):
                rate_based = _compute_rate_based_forecast(
                    weekly_qtys, week_starts_for_item, item_oos_weeks
                )
                if rate_based > 0:
                    base_forecast = rate_based
                    forecast_method = "RATE_BASED"
                    forecast_confidence = "low"
                    logger.info(
                        f"MEDIAN6 collapse fallback for {item_code}: "
                        f"rate_based={rate_based:.4f}"
                    )
        elif demand_class == "new_true":
            floor, level, conf = _compute_seeded_new_forecast(
                item_code, dw_item_cache, analogue_cache
            )
            base_forecast = floor
            analogue_level = level
            forecast_confidence = conf
            forecast_method = "SEEDED_NEW"
            seed_source = level
            profile.baseline_source = level
        elif demand_class == "sparse_valid":
            base_forecast = _compute_rate_based_forecast(
                weekly_qtys, week_starts_for_item, item_oos_weeks
            )
            forecast_method = "RATE_BASED"
            forecast_confidence = "medium" if base_forecast > 0 else "low"
        elif demand_class == "availability_distorted":
            forecast, level, conf, review_note = _compute_availability_distorted_forecast(
                item_code, weekly_qtys, week_starts_for_item,
                item_oos_weeks, dw_item_cache, analogue_cache
            )
            base_forecast = forecast
            analogue_level = level
            forecast_confidence = conf
            forecast_method = "AVAILABILITY_DISTORTED"
            seed_source = level if level != "exposure_adjusted" else None
            profile.baseline_source = level
            if review_note:
                profile.review_flag = True
                existing_reason = profile.review_reason or ""
                parts = [r.strip() for r in existing_reason.split(";") if r.strip()] if existing_reason else []
                if review_note not in parts:
                    parts.append(review_note)
                profile.review_reason = "; ".join(parts)
        elif demand_class == "new_sparse":
            base_forecast, analogue_level, analogue_item, forecast_confidence, cap_applied = _compute_seeded_forecast(
                item_code, forecast_qtys, profile,
                dw_item_cache=dw_item_cache,
                group_baseline_cache=analogue_cache,
            )
            forecast_method = "SEEDED"
            seed_source = analogue_level
        elif demand_class == "no_demand":
            base_forecast = 0.0
            forecast_method = "ZERO"
            forecast_confidence = "none"

        profile.forecast_method = forecast_method
        profile.forecast_confidence = forecast_confidence
        profile.seed_source = seed_source
        profile.analogue_item_code = analogue_item
        profile.analogue_level = analogue_level

        trend_adjusted = base_forecast

        if demand_class == "smooth" and base_forecast > 0 and allow_trend:
            last2 = forecast_qtys[:2]
            avg_last2 = sum(last2) / len(last2) if last2 else 0.0

            if avg_last2 > base_forecast * uplift_trigger:
                trend_flag = "up"
                raw_adj = base_forecast + 0.5 * max(0, avg_last2 - base_forecast)
                trend_adjusted = min(base_forecast * uplift_cap, raw_adj)
                if base_forecast > 0:
                    trend_pct = ((trend_adjusted - base_forecast) / base_forecast) * 100
            elif (
                len(last2) == 2
                and last2[0] < base_forecast
                and last2[1] < base_forecast
                and avg_last2 < base_forecast * down_trigger
            ):
                trend_flag = "down"
                raw_adj = base_forecast - 0.5 * (base_forecast - avg_last2)
                trend_adjusted = max(base_forecast * down_floor, raw_adj)
                if base_forecast > 0:
                    trend_pct = ((trend_adjusted - base_forecast) / base_forecast) * 100
            else:
                trend_flag = "flat"
                trend_adjusted = base_forecast
        else:
            trend_adjusted = base_forecast

        profile.trend_flag = trend_flag
        profile.trend_pct = Decimal(str(round(trend_pct, 6))) if trend_pct is not None else None

        review_notes = []
        if oos_was_applied:
            review_notes.append("OOS-adjusted history used")
        if item_oos_weeks and not oos_was_applied and required_clean_weeks > 0:
            review_notes.append("Too few clean weeks for OOS-adjusted forecast")
        if demand_class == "smooth" and base_forecast > 0 and not allow_trend and item_oos_weeks:
            review_notes.append("Trend suppressed due to recent OOS weeks")

        hist_index, future_index, seas_source, seas_level, seas_conf = _get_seasonality_indexes_preloaded(
            item_code, dw_item_cache, seasonality_index_map, reliable_levels, horizon_days
        )
        profile.seasonality_source = seas_source
        profile.seasonality_level_code = seas_level
        profile.seasonality_confidence = seas_conf or "none"

        if hist_index > 0:
            final_forecast = trend_adjusted * (future_index / hist_index)
        else:
            final_forecast = trend_adjusted

        final_forecast = max(0.0, final_forecast)
        final_daily = final_forecast / 7.0

        old_result = old_results.get(item_code)
        old_final = _to_float(old_result.final_forecast_weekly_qty) if old_result else None

        forecast_change_pct = None
        if old_final is not None and old_final > 0:
            forecast_change_pct = ((final_forecast - old_final) / old_final) * 100

        result = old_result or SkuForecastResult(item_code_365=item_code)
        result.base_forecast_weekly_qty = Decimal(str(round(base_forecast, 6)))
        result.trend_adjusted_weekly_qty = Decimal(str(round(trend_adjusted, 6)))
        result.hist_embedded_seasonal_index = Decimal(str(round(hist_index, 6)))
        result.future_seasonal_index = Decimal(str(round(future_index, 6)))
        result.final_forecast_weekly_qty = Decimal(str(round(final_forecast, 6)))
        result.final_forecast_daily_qty = Decimal(str(round(final_daily, 6)))
        result.forecast_change_pct = (
            Decimal(str(round(forecast_change_pct, 6))) if forecast_change_pct is not None else None
        )
        result.calculated_at = now
        if run_id is not None:
            result.run_id = run_id

        if old_result is None:
            session.add(result)

        if review_notes:
            existing = profile.review_reason or ""
            existing_parts = [r.strip() for r in existing.split(";") if r.strip()] if existing else []
            for note in review_notes:
                if note not in existing_parts:
                    existing_parts.append(note)
            profile.review_reason = "; ".join(existing_parts) if existing_parts else None
            profile.review_flag = True

        profile.updated_at = now
        count += 1

        if count % 500 == 0:
            session.flush()
            logger.info(f"Processed {count} forecasts...")
            if progress_callback:
                progress_callback(f"Processed {count}/{len(profiles)} base forecasts")

        if progress_callback and (idx % 25 == 0 or idx == len(profiles)):
            progress_callback(f"Processed {idx}/{len(profiles)} base forecasts")

    session.flush()
    logger.info(f"Completed base forecasts for {count} items")
    return count


def compute_single_base_forecast(session: Session, item_code: str, run_id=None):
    profile = session.query(SkuForecastProfile).filter_by(item_code_365=item_code).first()
    if not profile:
        logger.warning(f"No profile for {item_code}, skipping forecast")
        return None

    uplift_trigger = _safe_float(session, TREND_UPLIFT_TRIGGER_KEY, 1.15)
    down_trigger = _safe_float(session, TREND_DOWN_TRIGGER_KEY, 0.90)
    uplift_cap = _safe_float(session, TREND_UPLIFT_CAP_KEY, 1.25)
    down_floor = _safe_float(session, TREND_DOWN_FLOOR_KEY, 0.75)

    weekly_qtys = _get_recent_weekly_qtys(session, item_code, 26)
    demand_class = profile.demand_class

    completed_week_cutoff = get_completed_week_cutoff()
    week_starts = [completed_week_cutoff - timedelta(weeks=(i + 1)) for i in range(26)]

    try:
        from services.forecast.oos_demand_service import get_oos_weeks_set, OOS_THRESHOLD_DAYS
        item_oos_weeks = get_oos_weeks_set(session, item_code, 26, OOS_THRESHOLD_DAYS)
    except Exception:
        item_oos_weeks = set()

    single_dw_cache = {}
    dw_item = session.query(DwItem).filter_by(item_code_365=item_code).first()
    if dw_item:
        single_dw_cache[item_code] = dw_item

    all_profiles = session.query(SkuForecastProfile).join(
        DwItem, DwItem.item_code_365 == SkuForecastProfile.item_code_365
    ).filter(DwItem.active == True).all()
    all_dw = {
        it.item_code_365: it
        for it in session.query(DwItem).filter(DwItem.active == True).all()
    }
    single_analogue_cache = _build_analogue_floors(session, all_profiles, all_dw)

    base_forecast = 0.0
    forecast_method = "ZERO"
    trend_flag = "flat"
    trend_pct = None

    is_incomplete = getattr(profile, 'history_incomplete', False)

    if is_incomplete:
        floor, level, conf = _compute_seeded_new_forecast(
            item_code, single_dw_cache, single_analogue_cache
        )
        base_forecast = floor
        forecast_method = "INSUFFICIENT_HISTORY"
        profile.seed_source = level
        profile.analogue_item_code = None
        profile.analogue_level = level
        profile.forecast_confidence = conf
        profile.baseline_source = level
    elif demand_class == "smooth":
        base_forecast = _compute_ma8(weekly_qtys)
        forecast_method = "MA8"
    elif demand_class == "erratic":
        base_forecast = _compute_ma8(weekly_qtys)
        forecast_method = "MA8"
    elif demand_class in ("intermittent", "lumpy"):
        base_forecast = _compute_median6(weekly_qtys)
        forecast_method = "MEDIAN6"

        if base_forecast == 0.0 and any(q > 0 for q in weekly_qtys):
            rate_based = _compute_rate_based_forecast(weekly_qtys, week_starts, item_oos_weeks)
            if rate_based > 0:
                base_forecast = rate_based
                forecast_method = "RATE_BASED"
    elif demand_class == "new_true":
        floor, level, conf = _compute_seeded_new_forecast(
            item_code, single_dw_cache, single_analogue_cache
        )
        base_forecast = floor
        forecast_method = "SEEDED_NEW"
        profile.seed_source = level
        profile.analogue_item_code = None
        profile.analogue_level = level
        profile.forecast_confidence = conf
        profile.baseline_source = level
    elif demand_class == "sparse_valid":
        base_forecast = _compute_rate_based_forecast(weekly_qtys, week_starts, item_oos_weeks)
        forecast_method = "RATE_BASED"
    elif demand_class == "availability_distorted":
        forecast, level, conf, review_note = _compute_availability_distorted_forecast(
            item_code, weekly_qtys, week_starts,
            item_oos_weeks, single_dw_cache, single_analogue_cache
        )
        base_forecast = forecast
        forecast_method = "AVAILABILITY_DISTORTED"
        profile.analogue_level = level
        profile.forecast_confidence = conf
        profile.baseline_source = level
        if review_note:
            profile.review_flag = True
            profile.review_reason = review_note
    elif demand_class == "new_sparse":
        base_forecast, analogue_level, analogue_item, confidence, cap_applied = _compute_seeded_forecast(
            item_code, weekly_qtys, profile,
            dw_item_cache=single_dw_cache,
            group_baseline_cache=single_analogue_cache,
        )
        forecast_method = "SEEDED"
        profile.seed_source = analogue_level
        profile.analogue_item_code = analogue_item
        profile.analogue_level = analogue_level
        profile.forecast_confidence = confidence
    elif demand_class == "no_demand":
        base_forecast = 0.0
        forecast_method = "ZERO"

    profile.forecast_method = forecast_method
    trend_adjusted = base_forecast

    if demand_class == "smooth" and base_forecast > 0:
        last2 = weekly_qtys[:2]
        avg_last2 = sum(last2) / len(last2) if last2 else 0.0

        if avg_last2 > base_forecast * uplift_trigger:
            trend_flag = "up"
            raw_adj = base_forecast + 0.5 * max(0, avg_last2 - base_forecast)
            trend_adjusted = min(base_forecast * uplift_cap, raw_adj)
            if base_forecast > 0:
                trend_pct = ((trend_adjusted - base_forecast) / base_forecast) * 100
        elif (
            len(last2) == 2
            and last2[0] < base_forecast
            and last2[1] < base_forecast
            and avg_last2 < base_forecast * down_trigger
        ):
            trend_flag = "down"
            raw_adj = base_forecast - 0.5 * (base_forecast - avg_last2)
            trend_adjusted = max(base_forecast * down_floor, raw_adj)
            if base_forecast > 0:
                trend_pct = ((trend_adjusted - base_forecast) / base_forecast) * 100

    profile.trend_flag = trend_flag
    profile.trend_pct = Decimal(str(round(trend_pct, 6))) if trend_pct is not None else None

    hist_index, future_index, seas_source, seas_level, seas_conf = _get_seasonality_indexes(
        session, item_code, profile
    )
    profile.seasonality_source = seas_source
    profile.seasonality_level_code = seas_level
    profile.seasonality_confidence = seas_conf or "none"

    if hist_index > 0:
        final_forecast = trend_adjusted * (future_index / hist_index)
    else:
        final_forecast = trend_adjusted

    final_forecast = max(0.0, final_forecast)
    final_daily = final_forecast / 7.0

    now = get_utc_now()
    result = session.query(SkuForecastResult).filter_by(item_code_365=item_code).first()
    if result is None:
        result = SkuForecastResult(item_code_365=item_code)
        session.add(result)

    result.base_forecast_weekly_qty = Decimal(str(round(base_forecast, 6)))
    result.trend_adjusted_weekly_qty = Decimal(str(round(trend_adjusted, 6)))
    result.hist_embedded_seasonal_index = Decimal(str(round(hist_index, 6)))
    result.future_seasonal_index = Decimal(str(round(future_index, 6)))
    result.final_forecast_weekly_qty = Decimal(str(round(final_forecast, 6)))
    result.final_forecast_daily_qty = Decimal(str(round(final_daily, 6)))
    result.calculated_at = now
    if run_id is not None:
        result.run_id = run_id

    profile.updated_at = now
    session.flush()
    return result
