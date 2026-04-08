import logging
import time
from datetime import date, timedelta
from decimal import Decimal
from collections import defaultdict

from sqlalchemy import func, and_, text

from app import db
from models import (
    FactSalesWeeklyItem,
    ForecastSeasonalityMonthly,
    DwItem,
    DwBrand,
    SkuForecastProfile,
    extract_item_prefix,
)
from services.forecast.week_utils import get_completed_week_cutoff

logger = logging.getLogger(__name__)

MIN_MONTHS_FOR_MEDIUM = 6
MIN_QTY_FOR_MEDIUM = 50
SEASONAL_CAP_MIN = Decimal("0.85")
SEASONAL_CAP_MAX = Decimal("1.15")
SMOOTHING_ALPHA = Decimal("0.5")

HISTORY_WINDOW_DAYS = 365


def should_recompute_seasonality(session):
    completed_week_cutoff = get_completed_week_cutoff()

    latest_seasonality_update = session.query(
        func.max(ForecastSeasonalityMonthly.updated_at)
    ).scalar()

    if latest_seasonality_update is None:
        logger.info("[seasonality] No existing indices found — recompute needed")
        return True

    latest_sales_update = session.query(
        func.max(FactSalesWeeklyItem.updated_at)
    ).scalar()

    if latest_sales_update is None:
        logger.info("[seasonality] No sales data — skip")
        return False

    if latest_sales_update > latest_seasonality_update:
        logger.info(f"[seasonality] Sales data refreshed since last seasonality (sales_upd={latest_sales_update}, seas_upd={latest_seasonality_update}) — recompute needed")
        return True

    logger.info(f"[seasonality] No new sales data since last seasonality computation (sales_upd={latest_sales_update}, seas_upd={latest_seasonality_update}) — skip")
    return False


def compute_seasonal_indices(session, force=False):
    if not force and not should_recompute_seasonality(session):
        logger.info("[seasonality] Skipped — no new data")
        return 0

    t0 = time.time()
    completed_week_cutoff = get_completed_week_cutoff()
    window_start = completed_week_cutoff - timedelta(days=HISTORY_WINDOW_DAYS)

    with session.no_autoflush:
        rows = (
            session.query(
                FactSalesWeeklyItem.week_start,
                FactSalesWeeklyItem.item_code_365,
                FactSalesWeeklyItem.gross_qty,
            )
            .filter(
                FactSalesWeeklyItem.week_start >= window_start,
                FactSalesWeeklyItem.week_start < completed_week_cutoff,
            )
            .all()
        )

    if not rows:
        logger.info("No weekly sales data found for seasonality computation (365-day window)")
        return 0

    with session.no_autoflush:
        item_brand_map = dict(
            session.query(DwItem.item_code_365, DwItem.brand_code_365)
            .filter(DwItem.brand_code_365.isnot(None))
            .all()
        )
        item_supplier_map = dict(
            session.query(DwItem.item_code_365, DwItem.supplier_code_365)
            .filter(DwItem.supplier_code_365.isnot(None))
            .all()
        )

    brand_monthly = defaultdict(lambda: defaultdict(Decimal))
    supplier_monthly = defaultdict(lambda: defaultdict(Decimal))
    prefix_monthly = defaultdict(lambda: defaultdict(Decimal))

    for week_start, item_code, gross_qty in rows:
        month_no = week_start.month
        qty = Decimal(str(gross_qty)) if gross_qty else Decimal("0")

        brand = item_brand_map.get(item_code)
        if brand:
            brand_monthly[brand][month_no] += qty

        supplier = item_supplier_map.get(item_code)
        if supplier:
            supplier_monthly[supplier][month_no] += qty

        prefix = extract_item_prefix(item_code)
        if prefix:
            prefix_monthly[prefix][month_no] += qty

    brand_count = _write_indices_bulk(session, "brand", brand_monthly)
    supplier_count = _write_indices_bulk(session, "supplier", supplier_monthly)
    prefix_count = _write_indices_bulk(session, "prefix", prefix_monthly)
    total_written = brand_count + supplier_count + prefix_count

    session.flush()
    elapsed = time.time() - t0
    logger.info(
        f"[seasonality] completed in {elapsed:.2f}s; "
        f"brands={len(brand_monthly)} suppliers={len(supplier_monthly)} prefixes={len(prefix_monthly)} "
        f"rows_written={total_written} (window={HISTORY_WINDOW_DAYS} days)"
    )
    return total_written


def _write_indices_bulk(session, level_type, monthly_data):
    upsert_sql = text("""
        INSERT INTO forecast_seasonality_monthly
            (level_type, level_code, month_no, raw_index, smoothed_index,
             sample_months, sample_qty, confidence, is_reliable, updated_at)
        VALUES
            (:level_type, :level_code, :month_no, :raw_index, :smoothed_index,
             :sample_months, :sample_qty, :confidence, :is_reliable, now())
        ON CONFLICT (level_type, level_code, month_no) DO UPDATE SET
            raw_index      = EXCLUDED.raw_index,
            smoothed_index = EXCLUDED.smoothed_index,
            sample_months  = EXCLUDED.sample_months,
            sample_qty     = EXCLUDED.sample_qty,
            confidence     = EXCLUDED.confidence,
            is_reliable    = EXCLUDED.is_reliable,
            updated_at     = EXCLUDED.updated_at
    """)

    all_params = []

    for level_code, month_dict in monthly_data.items():
        months_with_data = [m for m in range(1, 13) if month_dict.get(m, Decimal("0")) > 0]
        total_qty = sum(month_dict.values())
        sample_months = len(months_with_data)

        if sample_months == 0:
            continue

        avg_monthly = total_qty / Decimal("12")

        if avg_monthly <= 0:
            continue

        if sample_months >= MIN_MONTHS_FOR_MEDIUM and total_qty >= MIN_QTY_FOR_MEDIUM:
            confidence = "medium"
            is_reliable = True
        elif sample_months >= 3:
            confidence = "low"
            is_reliable = False
        else:
            confidence = "none"
            is_reliable = False

        for month_no in range(1, 13):
            month_demand = month_dict.get(month_no, Decimal("0"))
            raw_index = month_demand / avg_monthly if avg_monthly > 0 else Decimal("1")

            smoothed = raw_index * SMOOTHING_ALPHA + Decimal("1") * (Decimal("1") - SMOOTHING_ALPHA)

            if smoothed < SEASONAL_CAP_MIN:
                smoothed = SEASONAL_CAP_MIN
            elif smoothed > SEASONAL_CAP_MAX:
                smoothed = SEASONAL_CAP_MAX

            all_params.append({
                "level_type": level_type,
                "level_code": level_code,
                "month_no": month_no,
                "raw_index": float(raw_index),
                "smoothed_index": float(smoothed),
                "sample_months": sample_months,
                "sample_qty": float(total_qty),
                "confidence": confidence,
                "is_reliable": is_reliable,
            })

    if all_params:
        conn = session.connection()
        for i in range(0, len(all_params), 500):
            chunk = all_params[i:i+500]
            conn.execute(upsert_sql, chunk)

    return len(all_params)


def choose_seasonality_source(session, item_code_365):
    item = session.query(DwItem).filter_by(item_code_365=item_code_365).first()
    if not item:
        return ("flat", None, "none")

    brand = item.brand_code_365
    if brand:
        reliable = (
            session.query(ForecastSeasonalityMonthly)
            .filter_by(level_type="brand", level_code=brand, is_reliable=True)
            .first()
        )
        if reliable:
            return ("brand", brand, reliable.confidence)

    supplier = item.supplier_code_365
    if supplier:
        reliable = (
            session.query(ForecastSeasonalityMonthly)
            .filter_by(level_type="supplier", level_code=supplier, is_reliable=True)
            .first()
        )
        if reliable:
            return ("supplier", supplier, reliable.confidence)

    return ("flat", None, "none")


def get_historical_embedded_index(session, item_code, weeks_used, source, level_code):
    if source == "flat" or source == "none" or not level_code:
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
    if source == "flat" or source == "none" or not level_code or horizon_days <= 0:
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
