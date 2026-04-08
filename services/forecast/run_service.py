import logging
import time
import traceback
from decimal import Decimal
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import desc, func, text

from models import ForecastRun, FactSalesWeeklyItem
from app import db
from timezone_utils import get_utc_now

from services.forecast.weekly_sales_builder import build_weekly_sales, update_weekly_sales
from services.forecast.seasonality_service import compute_seasonal_indices
from services.forecast.classification_service import classify_all_items
from services.forecast.base_forecast_service import compute_base_forecasts
from services.forecast.week_utils import get_completed_week_cutoff

logger = logging.getLogger(__name__)


def _utcnow():
    return datetime.utcnow()


def _heartbeat(run_id, step=None, note=None):
    try:
        payload = {
            "id": run_id,
            "hb": _utcnow(),
            "step": step,
            "note": note,
        }
        with db.engine.connect() as conn:
            conn.execute(text("""
                UPDATE forecast_runs
                SET last_heartbeat_at = :hb,
                    current_step = COALESCE(:step, current_step),
                    progress_note = COALESCE(:note, progress_note)
                WHERE id = :id
            """), payload)
            conn.commit()
    except Exception as e:
        logger.warning(f"Heartbeat update failed for run {run_id}: {e}")


def _mark_run_finished(run_id, status, note=None, completed_at=None):
    if completed_at is None:
        completed_at = _utcnow()
    try:
        with db.engine.connect() as conn:
            conn.execute(text("""
                UPDATE forecast_runs
                SET status = :status,
                    notes = COALESCE(:note, notes),
                    completed_at = :completed_at,
                    last_heartbeat_at = :completed_at
                WHERE id = :id
            """), {
                "id": run_id,
                "status": status,
                "note": note,
                "completed_at": completed_at,
            })
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to mark run {run_id} as {status}: {e}")


def _capture_sales_validation_metadata(session: Session):
    completed_week_cutoff = get_completed_week_cutoff()
    cutoff = completed_week_cutoff - timedelta(weeks=26)

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


def execute_forecast_run(session: Session, created_by=None, cover_days=7, horizon_days=14, mode="incremental"):
    now = get_utc_now()
    now_naive = _utcnow()
    run = ForecastRun(
        started_at=now,
        status="running",
        default_cover_days=Decimal(str(cover_days)),
        horizon_days=horizon_days,
        created_by=created_by,
        created_at=now,
        last_heartbeat_at=now_naive,
        current_step="initializing",
        progress_note="Starting forecast run",
    )
    session.add(run)
    session.commit()
    run_id = run.id
    logger.info(f"[Run {run_id}] Forecast run started by {created_by}, mode={mode}")

    current_step = "initializing"
    step_timings = {}

    def _make_hb_callback(step_name):
        def hb(note):
            _heartbeat(run_id, step_name, note)
        return hb

    try:
        current_step = "weekly_sales"
        _heartbeat(run_id, current_step, f"Building weekly sales ({mode})")
        logger.info(f"[Run {run_id}] Step 1/4: Building weekly sales (mode={mode})")

        t0 = time.time()
        sales_result = build_weekly_sales(session, weeks_back=52, mode=mode, progress_callback=_make_hb_callback(current_step))
        sales_rows = sales_result["upserted"] if isinstance(sales_result, dict) else sales_result
        effective_mode = sales_result.get("mode", mode) if isinstance(sales_result, dict) else mode
        auto_switched = sales_result.get("auto_switched", False) if isinstance(sales_result, dict) else False
        step_timings["weekly_sales"] = time.time() - t0

        if auto_switched:
            mode = effective_mode
            logger.warning(f"[Run {run_id}] Weekly sales auto-switched to {effective_mode}")

        session.commit()
        session.expire_all()
        logger.info(f"[Run {run_id}] weekly_sales completed in {step_timings['weekly_sales']:.2f}s; upserted={sales_rows}, effective_mode={effective_mode}")
        _heartbeat(run_id, current_step, f"Weekly sales completed ({sales_rows} rows, {step_timings['weekly_sales']:.1f}s)")

        start_date, end_date, total_qty, total_value = _capture_sales_validation_metadata(session)
        run = session.get(ForecastRun, run_id)
        run.sales_period_start = start_date
        run.sales_period_end = end_date
        run.sales_total_qty = total_qty
        run.sales_total_value_ex_vat = total_value
        session.commit()
        session.expire_all()
        logger.info(f"[Run {run_id}] Sales period: {start_date} to {end_date}, qty={total_qty}, value={total_value}")

        current_step = "seasonality"
        _heartbeat(run_id, current_step, "Computing seasonal indices")
        logger.info(f"[Run {run_id}] Step 2/4: Computing seasonal indices")

        t0 = time.time()
        seas_rows = compute_seasonal_indices(session)
        step_timings["seasonality"] = time.time() - t0

        session.commit()
        session.expire_all()
        logger.info(f"[Run {run_id}] seasonality completed in {step_timings['seasonality']:.2f}s; rows_written={seas_rows}")
        _heartbeat(run_id, current_step, f"Seasonality completed ({seas_rows} rows, {step_timings['seasonality']:.1f}s)")

        current_step = "classification"
        _heartbeat(run_id, current_step, "Classifying items")
        logger.info(f"[Run {run_id}] Step 3/4: Classifying all items")

        t0 = time.time()
        sku_count = classify_all_items(session)
        step_timings["classification"] = time.time() - t0

        session.commit()
        session.expire_all()
        logger.info(f"[Run {run_id}] classification completed in {step_timings['classification']:.2f}s; items={sku_count}")
        _heartbeat(run_id, current_step, f"Classification completed ({sku_count} items, {step_timings['classification']:.1f}s)")

        current_step = "base_forecast"
        _heartbeat(run_id, current_step, "Computing base forecasts")
        logger.info(f"[Run {run_id}] Step 4/4: Computing base forecasts")

        t0 = time.time()
        forecast_count = compute_base_forecasts(session, run_id=run_id, progress_callback=_make_hb_callback(current_step))
        step_timings["base_forecast"] = time.time() - t0

        session.commit()
        session.expire_all()
        logger.info(f"[Run {run_id}] base_forecast completed in {step_timings['base_forecast']:.2f}s; items={forecast_count}")
        _heartbeat(run_id, current_step, f"Base forecasts completed ({forecast_count} items, {step_timings['base_forecast']:.1f}s)")

        current_step = "finalizing"
        _heartbeat(run_id, current_step, "Finalizing forecast run")

        total_time = sum(step_timings.values())

        run = session.get(ForecastRun, run_id)
        run.completed_at = get_utc_now()
        run.status = "completed"
        run.sku_count = sku_count
        run.current_step = "completed"
        run.progress_note = f"Forecast completed in {total_time:.1f}s"
        run.last_heartbeat_at = _utcnow()
        session.commit()

        logger.info("=" * 70)
        logger.info(f"[Run {run_id}] FORECAST RUN COMPLETED ({mode} mode)")
        logger.info(f"[Run {run_id}] Total: {total_time:.2f}s | SKUs: {sku_count}")
        for step_name, elapsed in step_timings.items():
            logger.info(f"[Run {run_id}]   {step_name}: {elapsed:.2f}s")
        logger.info("=" * 70)

        return {
            "run_id": run_id,
            "status": "completed",
            "sku_count": sku_count,
            "started_at": str(run.started_at),
            "completed_at": str(run.completed_at),
            "mode": mode,
            "step_timings": {k: round(v, 2) for k, v in step_timings.items()},
        }

    except Exception as e:
        logger.error(f"Forecast run {run_id} failed at step '{current_step}': {e}")
        logger.error(traceback.format_exc())
        try:
            session.rollback()
        except Exception:
            pass
        error_note = f"Failed at step '{current_step}': {str(e)[:1900]}"
        _mark_run_finished(run_id, "failed", note=error_note)
        return {
            "run_id": run_id,
            "status": "failed",
            "error": str(e),
        }


def get_last_run(session: Session):
    return (
        session.query(ForecastRun)
        .order_by(desc(ForecastRun.id))
        .first()
    )
