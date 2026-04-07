import logging
import math
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_
from sqlalchemy.orm import Session

from models import DwItem, FactSalesWeeklyItem, SkuForecastProfile
from timezone_utils import get_utc_now
from services.forecast.week_utils import get_completed_week_cutoff

logger = logging.getLogger(__name__)

WEEKS_WINDOW = 26


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


def _forecast_method(demand_class: str) -> str:
    methods = {
        "smooth": "MA8",
        "erratic": "MA8",
        "intermittent": "MEDIAN6",
        "lumpy": "MEDIAN6",
        "new_sparse": "SEEDED",
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


def classify_single_item(session: Session, item_code: str) -> dict:
    weekly_qtys = _get_weekly_gross_qtys(session, item_code, WEEKS_WINDOW)
    profile = _compute_profile(weekly_qtys)

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
    items = (
        session.query(DwItem.item_code_365)
        .filter(DwItem.active.is_(True))
        .all()
    )

    logger.info(f"Classifying {len(items)} active items")
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
    
    sales_by_item = {}
    for item_code, week_start, gross_qty in all_sales:
        if item_code not in sales_by_item:
            sales_by_item[item_code] = {}
        sales_by_item[item_code][week_start] = float(gross_qty or 0)

    try:
        from services.forecast.oos_demand_service import bulk_get_oos_weeks, OOS_THRESHOLD_DAYS
        oos_map = bulk_get_oos_weeks(session, WEEKS_WINDOW, OOS_THRESHOLD_DAYS)
        logger.info(f"OOS data loaded: {len(oos_map)} items with OOS-impacted weeks")
    except Exception as e:
        logger.warning(f"Could not load OOS data for classification: {e}")
        oos_map = {}

    for (item_code,) in items:
        item_sales = sales_by_item.get(item_code, {})
        weekly_qtys = []
        week_starts = []
        ws = window_start
        while ws < completed_week_cutoff and len(weekly_qtys) < WEEKS_WINDOW:
            weekly_qtys.append(item_sales.get(ws, 0.0))
            week_starts.append(ws)
            ws += timedelta(weeks=1)

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
            logger.info(f"Classified {count}/{len(items)} items")

    session.flush()
    logger.info(f"Classification complete: {count} items processed")
    return count
