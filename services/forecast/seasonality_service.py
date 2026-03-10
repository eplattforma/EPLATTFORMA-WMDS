import logging
from datetime import date, timedelta
from decimal import Decimal
from collections import defaultdict

from sqlalchemy import func, and_

from app import db
from models import (
    FactSalesWeeklyItem,
    ForecastSeasonalityMonthly,
    DwItem,
    DwBrand,
    SkuForecastProfile,
    extract_item_prefix,
)

logger = logging.getLogger(__name__)

MIN_MONTHS_FOR_MEDIUM = 6
MIN_QTY_FOR_MEDIUM = 50
SEASONAL_CAP_MIN = Decimal("0.85")
SEASONAL_CAP_MAX = Decimal("1.15")
SMOOTHING_ALPHA = Decimal("0.5")


def compute_seasonal_indices(session):
    rows = (
        session.query(
            FactSalesWeeklyItem.week_start,
            FactSalesWeeklyItem.item_code_365,
            FactSalesWeeklyItem.gross_qty,
        )
        .all()
    )

    if not rows:
        logger.info("No weekly sales data found for seasonality computation")
        return

    item_brand_map = dict(
        session.query(DwItem.item_code_365, DwItem.brand_code_365)
        .filter(DwItem.brand_code_365.isnot(None))
        .all()
    )

    prefix_monthly = defaultdict(lambda: defaultdict(Decimal))
    brand_monthly = defaultdict(lambda: defaultdict(Decimal))

    for week_start, item_code, gross_qty in rows:
        month_no = week_start.month
        qty = Decimal(str(gross_qty)) if gross_qty else Decimal("0")

        prefix = extract_item_prefix(item_code)
        if prefix:
            prefix_monthly[prefix][month_no] += qty

        brand = item_brand_map.get(item_code)
        if brand:
            brand_monthly[brand][month_no] += qty

    _write_indices(session, "prefix", prefix_monthly)
    _write_indices(session, "brand", brand_monthly)

    session.flush()
    logger.info("Seasonal indices computed for %d prefixes, %d brands",
                len(prefix_monthly), len(brand_monthly))


def _write_indices(session, level_type, monthly_data):
    for level_code, month_dict in monthly_data.items():
        months_with_data = [m for m in range(1, 13) if month_dict.get(m, Decimal("0")) > 0]
        total_qty = sum(month_dict.values())
        sample_months = len(months_with_data)

        if sample_months == 0:
            continue

        avg_monthly = total_qty / Decimal("12")

        if avg_monthly <= 0:
            continue

        for month_no in range(1, 13):
            month_demand = month_dict.get(month_no, Decimal("0"))
            raw_index = month_demand / avg_monthly if avg_monthly > 0 else Decimal("1")

            smoothed = raw_index * SMOOTHING_ALPHA + Decimal("1") * (Decimal("1") - SMOOTHING_ALPHA)

            if smoothed < SEASONAL_CAP_MIN:
                smoothed = SEASONAL_CAP_MIN
            elif smoothed > SEASONAL_CAP_MAX:
                smoothed = SEASONAL_CAP_MAX

            if sample_months >= MIN_MONTHS_FOR_MEDIUM and total_qty >= MIN_QTY_FOR_MEDIUM:
                confidence = "medium"
                is_reliable = True
            elif sample_months >= 3:
                confidence = "low"
                is_reliable = False
            else:
                confidence = "none"
                is_reliable = False

            existing = (
                session.query(ForecastSeasonalityMonthly)
                .filter_by(level_type=level_type, level_code=level_code, month_no=month_no)
                .first()
            )

            if existing:
                existing.raw_index = raw_index
                existing.smoothed_index = smoothed
                existing.sample_months = sample_months
                existing.sample_qty = total_qty
                existing.confidence = confidence
                existing.is_reliable = is_reliable
            else:
                session.add(ForecastSeasonalityMonthly(
                    level_type=level_type,
                    level_code=level_code,
                    month_no=month_no,
                    raw_index=raw_index,
                    smoothed_index=smoothed,
                    sample_months=sample_months,
                    sample_qty=total_qty,
                    confidence=confidence,
                    is_reliable=is_reliable,
                ))


def choose_seasonality_source(session, item_code_365):
    item = session.query(DwItem).filter_by(item_code_365=item_code_365).first()
    if not item:
        return ("none", None, "none")

    brand = item.brand_code_365
    if brand:
        reliable = (
            session.query(ForecastSeasonalityMonthly)
            .filter_by(level_type="brand", level_code=brand, is_reliable=True)
            .first()
        )
        if reliable:
            return ("brand", brand, reliable.confidence)

    prefix = extract_item_prefix(item_code_365)
    if prefix:
        reliable = (
            session.query(ForecastSeasonalityMonthly)
            .filter_by(level_type="prefix", level_code=prefix, is_reliable=True)
            .first()
        )
        if reliable:
            return ("prefix", prefix, reliable.confidence)

    return ("none", None, "none")


def get_historical_embedded_index(session, item_code, weeks_used, source, level_code):
    if source == "none" or not level_code:
        return Decimal("1")

    today = date.today()
    week_dates = []
    for i in range(weeks_used):
        d = today - timedelta(weeks=i + 1)
        monday = d - timedelta(days=d.weekday())
        week_dates.append(monday)

    if not week_dates:
        return Decimal("1")

    month_counts = defaultdict(int)
    for d in week_dates:
        month_counts[d.month] += 1

    total_weeks = sum(month_counts.values())
    if total_weeks == 0:
        return Decimal("1")

    indices = (
        session.query(ForecastSeasonalityMonthly.month_no, ForecastSeasonalityMonthly.smoothed_index)
        .filter_by(level_type=source, level_code=level_code)
        .all()
    )
    index_map = {row.month_no: Decimal(str(row.smoothed_index)) for row in indices}

    weighted_sum = Decimal("0")
    for month_no, count in month_counts.items():
        factor = index_map.get(month_no, Decimal("1"))
        weighted_sum += factor * Decimal(str(count))

    return weighted_sum / Decimal(str(total_weeks))


def get_future_seasonal_index(session, item_code, horizon_days, source, level_code):
    if source == "none" or not level_code or horizon_days <= 0:
        return Decimal("1")

    today = date.today()

    month_days = defaultdict(int)
    for i in range(horizon_days):
        d = today + timedelta(days=i + 1)
        month_days[d.month] += 1

    if not month_days:
        return Decimal("1")

    indices = (
        session.query(ForecastSeasonalityMonthly.month_no, ForecastSeasonalityMonthly.smoothed_index)
        .filter_by(level_type=source, level_code=level_code)
        .all()
    )
    index_map = {row.month_no: Decimal(str(row.smoothed_index)) for row in indices}

    weighted_sum = Decimal("0")
    for month_no, days in month_days.items():
        factor = index_map.get(month_no, Decimal("1"))
        weighted_sum += factor * Decimal(str(days))

    return weighted_sum / Decimal(str(horizon_days))
