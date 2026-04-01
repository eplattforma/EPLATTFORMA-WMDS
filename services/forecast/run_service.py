import logging
import traceback
from decimal import Decimal
from datetime import date, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import desc, func

from models import ForecastRun, FactSalesWeeklyItem
from timezone_utils import get_utc_now

from services.forecast.weekly_sales_builder import build_weekly_sales, update_weekly_sales
from services.forecast.seasonality_service import compute_seasonal_indices
from services.forecast.classification_service import classify_all_items
from services.forecast.base_forecast_service import compute_base_forecasts
from services.forecast.replenishment_service import compute_replenishment
from services.forecast.week_utils import get_completed_week_cutoff

logger = logging.getLogger(__name__)


def _capture_sales_validation_metadata(session: Session):
    """
    Capture the actual sales period used for forecasting.
    Returns tuple of (start_date, end_date, total_qty, total_value_ex_vat)
    """
    completed_week_cutoff = get_completed_week_cutoff()
    # For 52 weeks back (standard)
    cutoff = completed_week_cutoff - timedelta(weeks=52)

    result = session.query(
        func.min(FactSalesWeeklyItem.week_start).label('start_date'),
        func.max(FactSalesWeeklyItem.week_start).label('end_date'),
        func.sum(FactSalesWeeklyItem.gross_qty).label('total_qty'),
        func.sum(FactSalesWeeklyItem.sales_ex_vat).label('total_value'),
    ).filter(
        FactSalesWeeklyItem.week_start >= cutoff,
        FactSalesWeeklyItem.week_start < completed_week_cutoff,
    ).first()

    if result and result.start_date:
        start_date = result.start_date
        end_date = result.end_date
        total_qty = Decimal(str(result.total_qty or 0))
        total_value = Decimal(str(result.total_value or 0))
        return start_date, end_date, total_qty, total_value
    return None, None, Decimal('0'), Decimal('0')


def execute_forecast_run(session: Session, created_by=None, cover_days=7, horizon_days=14):
    now = get_utc_now()
    run = ForecastRun(
        started_at=now,
        status="running",
        default_cover_days=Decimal(str(cover_days)),
        horizon_days=horizon_days,
        created_by=created_by,
        created_at=now,
    )
    session.add(run)
    session.flush()
    run_id = run.id
    logger.info(f"Forecast run {run_id} started by {created_by}")

    try:
        logger.info(f"[Run {run_id}] Step 1/5: Building weekly sales")
        build_weekly_sales(session, weeks_back=52)
        
        # Capture sales validation metadata
        start_date, end_date, total_qty, total_value = _capture_sales_validation_metadata(session)
        run.sales_period_start = start_date
        run.sales_period_end = end_date
        run.sales_total_qty = total_qty
        run.sales_total_value_ex_vat = total_value
        session.flush()
        logger.info(f"[Run {run_id}] Sales period: {start_date} to {end_date}, qty={total_qty}, value={total_value}")

        logger.info(f"[Run {run_id}] Step 2/5: Computing seasonal indices")
        compute_seasonal_indices(session)

        logger.info(f"[Run {run_id}] Step 3/5: Classifying all items")
        sku_count = classify_all_items(session)

        logger.info(f"[Run {run_id}] Step 4/5: Computing base forecasts")
        compute_base_forecasts(session, run_id=run_id)

        logger.info(f"[Run {run_id}] Step 5/5: Computing replenishment")
        compute_replenishment(session, run_id=run_id)

        run.completed_at = get_utc_now()
        run.status = "completed"
        run.sku_count = sku_count
        session.commit()

        logger.info(f"Forecast run {run_id} completed successfully ({sku_count} SKUs)")
        return {
            "run_id": run_id,
            "status": "completed",
            "sku_count": sku_count,
            "started_at": str(run.started_at),
            "completed_at": str(run.completed_at),
        }

    except Exception as e:
        logger.error(f"Forecast run {run_id} failed: {e}")
        logger.error(traceback.format_exc())
        session.rollback()
        failed_run = ForecastRun(
            started_at=now,
            completed_at=get_utc_now(),
            status="failed",
            notes=str(e)[:2000],
            default_cover_days=Decimal(str(cover_days)),
            horizon_days=horizon_days,
            created_by=created_by,
            created_at=now,
        )
        session.add(failed_run)
        session.flush()
        failed_run_id = failed_run.id
        session.commit()
        return {
            "run_id": failed_run_id,
            "status": "failed",
            "error": str(e),
        }


def get_last_run(session: Session):
    return (
        session.query(ForecastRun)
        .order_by(desc(ForecastRun.id))
        .first()
    )
