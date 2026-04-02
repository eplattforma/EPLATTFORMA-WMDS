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


def _compute_seeded_forecast(item_code, weekly_qtys, profile, dw_item_cache=None, group_baseline_cache=None):
    non_zero = [q for q in weekly_qtys[:8] if q > 0]
    if not non_zero:
        return 0.0, "none", None, "none", False

    own_signal = sum(non_zero) / len(non_zero) if non_zero else 0.0

    dw_item = dw_item_cache.get(item_code) if dw_item_cache else None
    supplier_code = dw_item.supplier_code_365 if dw_item else None
    brand_code = dw_item.brand_code_365 if dw_item else None
    item_prefix = extract_item_prefix(item_code)

    analogue_baseline = None
    analogue_level = "none"
    analogue_item = None

    if group_baseline_cache is not None:
        if supplier_code and ("supplier", supplier_code) in group_baseline_cache:
            baseline = group_baseline_cache[("supplier", supplier_code)]
            if baseline is not None and baseline > 0:
                analogue_baseline = baseline
                analogue_level = "supplier"
        if analogue_baseline is None and brand_code and ("brand", brand_code) in group_baseline_cache:
            baseline = group_baseline_cache[("brand", brand_code)]
            if baseline is not None and baseline > 0:
                analogue_baseline = baseline
                analogue_level = "brand"
        if analogue_baseline is None and item_prefix and ("prefix", item_prefix) in group_baseline_cache:
            baseline = group_baseline_cache[("prefix", item_prefix)]
            if baseline is not None and baseline > 0:
                analogue_baseline = baseline
                analogue_level = "prefix"

    if analogue_baseline is not None:
        forecast = 0.70 * own_signal + 0.30 * analogue_baseline
    else:
        forecast = own_signal * 0.70
        analogue_level = "none"

    seeded_cap = max(2.0, own_signal * 2.5)
    capped_forecast = min(forecast, seeded_cap)
    cap_applied = capped_forecast < forecast

    if cap_applied:
        logger.info(f"SEEDED cap applied for {item_code}: raw={forecast:.2f}, capped={capped_forecast:.2f}, own_signal={own_signal:.2f}")

    return capped_forecast, analogue_level, analogue_item, "low", cap_applied


def _preload_group_baselines(session, profiles, dw_item_cache):
    completed_week_cutoff = get_completed_week_cutoff()
    cutoff_8w = completed_week_cutoff - timedelta(weeks=8)

    eligible_profiles = {
        p.item_code_365: p for p in profiles
        if p.demand_class in ("smooth", "erratic")
        and (p.weeks_non_zero_26 or 0) >= 6
    }

    supplier_groups = {}
    brand_groups = {}
    prefix_groups = {}

    for item_code, dw_item in dw_item_cache.items():
        if not dw_item.active or item_code not in eligible_profiles:
            continue
        if dw_item.supplier_code_365:
            supplier_groups.setdefault(dw_item.supplier_code_365, []).append(item_code)
        if dw_item.brand_code_365:
            brand_groups.setdefault(dw_item.brand_code_365, []).append(item_code)
        prefix = extract_item_prefix(item_code)
        if prefix:
            prefix_groups.setdefault(prefix, []).append(item_code)

    all_eligible_items = list(eligible_profiles.keys())

    sales_8w = {}
    if all_eligible_items:
        rows = (
            session.query(
                FactSalesWeeklyItem.item_code_365,
                FactSalesWeeklyItem.week_start,
                FactSalesWeeklyItem.gross_qty,
            )
            .filter(
                FactSalesWeeklyItem.item_code_365.in_(all_eligible_items),
                FactSalesWeeklyItem.week_start >= cutoff_8w,
                FactSalesWeeklyItem.week_start < completed_week_cutoff,
            )
            .all()
        )
        for item_code, week_start, gross_qty in rows:
            sales_8w.setdefault(item_code, {})
            sales_8w[item_code][week_start] = _to_float(gross_qty)

    weeks_list = [completed_week_cutoff - timedelta(weeks=(i + 1)) for i in range(8)]

    def _calc_group_avg(member_items):
        weekly_totals = {}
        for ws in weeks_list:
            total = 0.0
            for ic in member_items[:50]:
                total += sales_8w.get(ic, {}).get(ws, 0.0)
            weekly_totals[ws] = total
        total_qty = sum(weekly_totals.values())
        avg = total_qty / 8.0
        return avg if avg > 0 else None

    cache = {}
    for code, members in supplier_groups.items():
        cache[("supplier", code)] = _calc_group_avg(members)
    for code, members in brand_groups.items():
        cache[("brand", code)] = _calc_group_avg(members)
    for code, members in prefix_groups.items():
        cache[("prefix", code)] = _calc_group_avg(members)

    return cache


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
        prefix = extract_item_prefix(item_code)
        if prefix and ("prefix", prefix) in reliable_levels:
            source = "prefix"
            level_code = prefix
            confidence = reliable_levels[("prefix", prefix)]

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
    for item_code in set(p.item_code_365 for p in profiles):
        weekly_qtys = []
        for i in range(26):
            ws = completed_week_cutoff - timedelta(weeks=(i + 1))
            qty = existing_by_item.get(item_code, {}).get(ws, 0.0)
            weekly_qtys.append(qty)
        sales_by_item[item_code] = weekly_qtys
    
    active_codes = {p.item_code_365 for p in profiles}
    old_results = {}
    for result in session.query(SkuForecastResult).filter(SkuForecastResult.item_code_365.in_(active_codes)).all():
        old_results[result.item_code_365] = result
    logger.info(f"Preloaded {len(old_results)} existing forecast results for {len(active_codes)} active items")

    dw_item_cache = {}
    for item in session.query(DwItem).filter(DwItem.active == True).all():
        dw_item_cache[item.item_code_365] = item
    logger.info(f"Preloaded {len(dw_item_cache)} active DwItem rows")

    seasonality_index_map = {}
    reliable_levels = {}
    for row in session.query(ForecastSeasonalityMonthly).all():
        seasonality_index_map[(row.level_type, row.level_code, row.month_no)] = Decimal(str(row.smoothed_index))
        if row.is_reliable:
            reliable_levels[(row.level_type, row.level_code)] = row.confidence
    logger.info(f"Preloaded {len(seasonality_index_map)} seasonality index rows, {len(reliable_levels)} reliable levels")

    group_baseline_cache = _preload_group_baselines(session, profiles, dw_item_cache)
    logger.info(f"Preloaded {len(group_baseline_cache)} group baselines")

    for profile in profiles:
        item_code = profile.item_code_365
        demand_class = profile.demand_class
        weekly_qtys = sales_by_item.get(item_code, [])

        base_forecast = 0.0
        forecast_method = "ZERO"
        trend_flag = "flat"
        trend_pct = None
        forecast_confidence = "medium"
        seed_source = None
        analogue_item = None
        analogue_level = None

        if demand_class == "smooth":
            base_forecast = _compute_ma8(weekly_qtys)
            forecast_method = "MA8"
            forecast_confidence = "high"
        elif demand_class == "erratic":
            base_forecast = _compute_ma8(weekly_qtys)
            forecast_method = "MA8"
            forecast_confidence = "medium"
        elif demand_class in ("intermittent", "lumpy"):
            base_forecast = _compute_median6(weekly_qtys)
            forecast_method = "MEDIAN6"
            forecast_confidence = "medium"
        elif demand_class == "new_sparse":
            base_forecast, analogue_level, analogue_item, forecast_confidence, cap_applied = _compute_seeded_forecast(
                item_code, weekly_qtys, profile,
                dw_item_cache=dw_item_cache,
                group_baseline_cache=group_baseline_cache,
            )
            forecast_method = "SEEDED"
            seed_source = analogue_level
            profile.seeded_cap_applied = cap_applied
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
            else:
                trend_flag = "flat"
                trend_adjusted = base_forecast
        else:
            trend_adjusted = base_forecast

        profile.trend_flag = trend_flag
        profile.trend_pct = Decimal(str(round(trend_pct, 6))) if trend_pct is not None else None

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

        profile.updated_at = now
        count += 1

        if count % 500 == 0:
            session.flush()
            logger.info(f"Processed {count} forecasts...")
            if progress_callback:
                progress_callback(f"Processed {count}/{len(profiles)} base forecasts")

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

    base_forecast = 0.0
    forecast_method = "ZERO"
    trend_flag = "flat"
    trend_pct = None

    if demand_class == "smooth":
        base_forecast = _compute_ma8(weekly_qtys)
        forecast_method = "MA8"
    elif demand_class == "erratic":
        base_forecast = _compute_ma8(weekly_qtys)
        forecast_method = "MA8"
    elif demand_class in ("intermittent", "lumpy"):
        base_forecast = _compute_median6(weekly_qtys)
        forecast_method = "MEDIAN6"
    elif demand_class == "new_sparse":
        single_dw_cache = {}
        dw_item = session.query(DwItem).filter_by(item_code_365=item_code).first()
        if dw_item:
            single_dw_cache[item_code] = dw_item
        single_group_cache = _preload_group_baselines(session, [profile], single_dw_cache)
        base_forecast, analogue_level, analogue_item, confidence, cap_applied = _compute_seeded_forecast(
            item_code, weekly_qtys, profile,
            dw_item_cache=single_dw_cache,
            group_baseline_cache=single_group_cache,
        )
        forecast_method = "SEEDED"
        profile.seed_source = analogue_level
        profile.analogue_item_code = analogue_item
        profile.analogue_level = analogue_level
        profile.forecast_confidence = confidence
        profile.seeded_cap_applied = cap_applied
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
