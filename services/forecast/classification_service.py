import logging
import math
import os
import resource
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_
from sqlalchemy.orm import Session

from models import DwItem, FactSalesWeeklyItem, SkuForecastProfile
from timezone_utils import get_utc_now
from services.forecast.week_utils import get_completed_week_cutoff

logger = logging.getLogger(__name__)


def _rss_mb() -> tuple:
    """Return (current_rss_mb, peak_rss_mb). Either may be -1.0 if unavailable."""
    cur = -1.0
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    cur = float(parts[1]) / 1024.0
                    break
    except Exception:
        pass
    peak = -1.0
    try:
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if os.uname().sysname == "Darwin":
            peak = ru / (1024.0 * 1024.0)
        else:
            peak = ru / 1024.0
    except Exception:
        pass
    return cur, peak


def _mem_str() -> str:
    cur, peak = _rss_mb()
    return f"rss={cur:.1f}MB peak={peak:.1f}MB"

WEEKS_WINDOW = 26
HISTORY_WINDOW_DAYS = 365
MIN_HISTORY_COVERAGE_WEEKS = 20

NEW_TRUE_AGE_THRESHOLD = 6
NEW_TRUE_NZ_THRESHOLD = 4
SPARSE_VALID_NZ_THRESHOLD = 3
AVAILABILITY_DISTORTED_OOS_THRESHOLD = 3


def _get_weekly_gross_qtys(session: Session, item_code: str, num_weeks: int = WEEKS_WINDOW):
    completed_week_cutoff = get_completed_week_cutoff()
    window_start = completed_week_cutoff - timedelta(weeks=num_weeks)

    rows = (
        session.query(FactSalesWeeklyItem.week_start, FactSalesWeeklyItem.gross_qty)
        .filter(
            FactSalesWeeklyItem.item_code_365 == item_code,
            FactSalesWeeklyItem.week_start >= window_start,
            FactSalesWeeklyItem.week_start < completed_week_cutoff,
        )
        .order_by(FactSalesWeeklyItem.week_start)
        .all()
    )

    data_by_week = {r.week_start: float(r.gross_qty or 0) for r in rows}

    result = []
    ws = window_start
    while ws < completed_week_cutoff and len(result) < num_weeks:
        result.append(data_by_week.get(ws, 0.0))
        ws += timedelta(weeks=1)

    return result


def _classify_demand(weeks_non_zero: int, adi: float, cv2: float) -> str:
    if weeks_non_zero == 0:
        return "no_demand"
    if weeks_non_zero < 6:
        return "new_sparse"
    if adi < 1.32 and cv2 < 0.49:
        return "smooth"
    if adi < 1.32 and cv2 >= 0.49:
        return "erratic"
    if adi >= 1.32 and cv2 < 0.49:
        return "intermittent"
    return "lumpy"


def _reclassify_low_history(weeks_non_zero, item_age_weeks, oos_weeks_26):
    if oos_weeks_26 >= AVAILABILITY_DISTORTED_OOS_THRESHOLD:
        return "availability_distorted"
    if item_age_weeks < NEW_TRUE_AGE_THRESHOLD and weeks_non_zero < NEW_TRUE_NZ_THRESHOLD:
        return "new_true"
    if item_age_weeks >= NEW_TRUE_AGE_THRESHOLD and weeks_non_zero >= SPARSE_VALID_NZ_THRESHOLD:
        return "sparse_valid"
    if weeks_non_zero < NEW_TRUE_NZ_THRESHOLD:
        return "new_true"
    return "sparse_valid"


def _forecast_method(demand_class: str) -> str:
    methods = {
        "smooth": "MA8",
        "erratic": "MA8",
        "intermittent": "MEDIAN6",
        "lumpy": "MEDIAN6",
        "new_sparse": "SEEDED",
        "new_true": "SEEDED_NEW",
        "sparse_valid": "RATE_BASED",
        "availability_distorted": "AVAILABILITY_DISTORTED",
        "no_demand": "ZERO",
    }
    return methods.get(demand_class, "ZERO")


def _compute_profile(weekly_qtys: list) -> dict:
    n = len(weekly_qtys)
    non_zero = [q for q in weekly_qtys if q > 0]
    weeks_non_zero = len(non_zero)
    sales_frequency = weeks_non_zero / n if n > 0 else 0.0

    if weeks_non_zero == 0:
        return {
            "weeks_total_26": n,
            "weeks_non_zero_26": 0,
            "sales_frequency_26": 0.0,
            "adi_26": None,
            "avg_non_zero_26": None,
            "std_non_zero_26": None,
            "cv2_26": None,
            "demand_class": "no_demand",
            "forecast_method": "ZERO",
        }

    avg_nz = sum(non_zero) / len(non_zero)

    if len(non_zero) >= 2:
        variance = sum((q - avg_nz) ** 2 for q in non_zero) / (len(non_zero) - 1)
        std_nz = math.sqrt(variance)
    else:
        std_nz = 0.0

    cv2 = (std_nz / avg_nz) ** 2 if avg_nz > 0 else 0.0

    adi = n / weeks_non_zero if weeks_non_zero > 0 else float("inf")

    demand_class = _classify_demand(weeks_non_zero, adi, cv2)
    method = _forecast_method(demand_class)

    return {
        "weeks_total_26": n,
        "weeks_non_zero_26": weeks_non_zero,
        "sales_frequency_26": round(sales_frequency, 6),
        "adi_26": round(adi, 6),
        "avg_non_zero_26": round(avg_nz, 6),
        "std_non_zero_26": round(std_nz, 6),
        "cv2_26": round(cv2, 6),
        "demand_class": demand_class,
        "forecast_method": method,
    }


def _compute_item_age_weeks(item_sales, completed_week_cutoff):
    sale_weeks = [ws for ws, qty in item_sales.items() if qty > 0]
    if not sale_weeks:
        return 0
    first_sale = min(sale_weeks)
    return (completed_week_cutoff - first_sale).days / 7.0


def classify_single_item(session: Session, item_code: str) -> dict:
    completed_week_cutoff = get_completed_week_cutoff()
    window_start = completed_week_cutoff - timedelta(weeks=WEEKS_WINDOW)

    weekly_qtys = _get_weekly_gross_qtys(session, item_code, WEEKS_WINDOW)
    profile = _compute_profile(weekly_qtys)

    if profile["demand_class"] == "new_sparse":
        rows = (
            session.query(FactSalesWeeklyItem.week_start, FactSalesWeeklyItem.gross_qty)
            .filter(
                FactSalesWeeklyItem.item_code_365 == item_code,
                FactSalesWeeklyItem.week_start >= window_start,
                FactSalesWeeklyItem.week_start < completed_week_cutoff,
                FactSalesWeeklyItem.gross_qty > 0,
            )
            .all()
        )
        item_sales = {r.week_start: float(r.gross_qty) for r in rows}
        item_age_weeks = _compute_item_age_weeks(item_sales, completed_week_cutoff)

        try:
            from services.forecast.oos_demand_service import get_oos_weeks_set, OOS_THRESHOLD_DAYS
            oos_weeks = get_oos_weeks_set(session, item_code, WEEKS_WINDOW, OOS_THRESHOLD_DAYS)
            oos_count = len(oos_weeks)
        except Exception:
            oos_count = 0

        new_class = _reclassify_low_history(
            profile["weeks_non_zero_26"], item_age_weeks, oos_count
        )
        profile["demand_class"] = new_class
        profile["forecast_method"] = _forecast_method(new_class)

    now = get_utc_now()
    existing = session.get(SkuForecastProfile, item_code)

    if existing:
        for k, v in profile.items():
            setattr(existing, k, v)
        existing.updated_at = now
    else:
        obj = SkuForecastProfile(item_code_365=item_code, updated_at=now, **profile)
        session.add(obj)

    session.flush()
    profile["item_code_365"] = item_code
    return profile


def classify_all_items(session: Session) -> int:
    logger.info(f"[classify] start: {_mem_str()}")
    items = (
        session.query(DwItem.item_code_365)
        .filter(DwItem.active.is_(True))
        .all()
    )

    logger.info(f"[classify] active items loaded: {len(items)} ({_mem_str()})")
    count = 0
    now = get_utc_now()
    
    completed_week_cutoff = get_completed_week_cutoff()
    window_start = completed_week_cutoff - timedelta(weeks=WEEKS_WINDOW)
    
    all_sales = (
        session.query(
            FactSalesWeeklyItem.item_code_365,
            FactSalesWeeklyItem.week_start,
            FactSalesWeeklyItem.gross_qty,
        )
        .filter(
            FactSalesWeeklyItem.week_start >= window_start,
            FactSalesWeeklyItem.week_start < completed_week_cutoff,
        )
        .all()
    )
    logger.info(f"[classify] weekly sales rows loaded: {len(all_sales)} ({_mem_str()})")
    
    sales_by_item = {}
    for item_code, week_start, gross_qty in all_sales:
        if item_code not in sales_by_item:
            sales_by_item[item_code] = {}
        sales_by_item[item_code][week_start] = float(gross_qty or 0)
    del all_sales
    logger.info(f"[classify] sales_by_item built: {len(sales_by_item)} items ({_mem_str()})")

    all_items_with_any_row = set(sales_by_item.keys())
    total_distinct_weeks = set()
    for item_sales in sales_by_item.values():
        total_distinct_weeks.update(item_sales.keys())
    global_week_count = len(total_distinct_weeks)
    logger.info(f"Global weekly coverage: {global_week_count} distinct weeks in {WEEKS_WINDOW}-week window")

    try:
        from services.forecast.oos_demand_service import bulk_get_oos_weeks, OOS_THRESHOLD_DAYS
        oos_map = bulk_get_oos_weeks(session, WEEKS_WINDOW, OOS_THRESHOLD_DAYS)
        logger.info(f"OOS data loaded: {len(oos_map)} items with OOS-impacted weeks")
    except Exception as e:
        logger.warning(f"Could not load OOS data for classification: {e}")
        oos_map = {}

    item_age_cache = {}
    for item_code, item_sales in sales_by_item.items():
        item_age_cache[item_code] = _compute_item_age_weeks(item_sales, completed_week_cutoff)

    stats = {
        "no_demand": 0, "new_true": 0, "sparse_valid": 0,
        "availability_distorted": 0, "history_incomplete": 0,
        "smooth": 0, "erratic": 0, "intermittent": 0, "lumpy": 0,
    }

    for (item_code,) in items:
        item_sales = sales_by_item.get(item_code, {})
        weekly_qtys = []
        week_starts = []
        ws = window_start
        while ws < completed_week_cutoff and len(weekly_qtys) < WEEKS_WINDOW:
            weekly_qtys.append(item_sales.get(ws, 0.0))
            week_starts.append(ws)
            ws += timedelta(weeks=1)

        item_distinct_weeks = len(item_sales)
        item_has_some_sales = any(q > 0 for q in item_sales.values()) if item_sales else False
        is_history_incomplete = (global_week_count >= MIN_HISTORY_COVERAGE_WEEKS and
                                 item_distinct_weeks < MIN_HISTORY_COVERAGE_WEEKS and
                                 item_has_some_sales)

        item_oos_weeks = oos_map.get(item_code, set())
        oos_count = sum(1 for w in week_starts if w in item_oos_weeks)

        if item_oos_weeks:
            clean_qtys = [q for q, w in zip(weekly_qtys, week_starts) if w not in item_oos_weeks]
            if len(clean_qtys) >= 4:
                profile = _compute_profile(clean_qtys)
                profile["oos_adjusted"] = True
            else:
                profile = _compute_profile(weekly_qtys)
                profile["oos_adjusted"] = False
        else:
            profile = _compute_profile(weekly_qtys)
            profile["oos_adjusted"] = False

        profile["oos_weeks_26"] = oos_count

        if profile["demand_class"] == "new_sparse":
            item_age = item_age_cache.get(item_code, 0)
            new_class = _reclassify_low_history(
                profile["weeks_non_zero_26"], item_age, oos_count
            )
            profile["demand_class"] = new_class
            profile["forecast_method"] = _forecast_method(new_class)

        if is_history_incomplete and profile["demand_class"] == "no_demand":
            profile["history_incomplete"] = True
            profile["forecast_method"] = "INSUFFICIENT_HISTORY"
            profile["review_reason"] = "History not fully loaded"
            profile["review_flag"] = True
            stats["history_incomplete"] += 1
        else:
            profile["history_incomplete"] = False
            dc = profile["demand_class"]
            if dc in stats:
                stats[dc] += 1

        existing = session.get(SkuForecastProfile, item_code)
        if existing:
            for k, v in profile.items():
                setattr(existing, k, v)
            existing.updated_at = now
        else:
            obj = SkuForecastProfile(item_code_365=item_code, updated_at=now, **profile)
            session.add(obj)

        count += 1
        if count % 500 == 0:
            session.flush()
            session.expire_all()
            logger.info(f"[classify] processed {count}/{len(items)} items ({_mem_str()})")

    session.flush()
    session.expire_all()
    del sales_by_item, item_age_cache, oos_map
    logger.info(f"[classify] complete: {count} items processed ({_mem_str()})")
    logger.info(f"[classify] breakdown: {stats}")
    return count
