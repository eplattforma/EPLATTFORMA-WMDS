"""
Background task scheduler for running tasks at specific hours.
Uses APScheduler with a PostgreSQL advisory lock to guarantee
only one process runs scheduled jobs, even with multiple workers.
"""

import logging
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
import os

logger = logging.getLogger(__name__)

_scheduler = None
_lock = threading.Lock()
_lock_conn = None


def _try_acquire_advisory_lock(engine, lock_name="wmds_scheduler"):
    """Acquire a PostgreSQL advisory lock so only one process runs the scheduler."""
    global _lock_conn
    if _lock_conn is not None:
        return True

    try:
        from sqlalchemy import text
        conn = engine.connect()
        got = conn.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:name))"),
            {"name": lock_name},
        ).scalar()
        if got:
            _lock_conn = conn
            return True
        conn.close()
    except Exception as e:
        logger.warning(f"Advisory lock check failed (SQLite?): {e}")
        return True

    return False


def setup_scheduler(app):
    """
    Initialize and start the background scheduler.
    Idempotent: safe to call multiple times; only starts once.
    Uses a DB advisory lock so only one worker runs jobs.
    """
    global _scheduler

    with _lock:
        if _scheduler is not None and getattr(_scheduler, "running", False):
            logger.info("Scheduler already running; skipping duplicate start")
            return

        from app import db
        if not _try_acquire_advisory_lock(db.engine):
            logger.info("Scheduler advisory lock not acquired; another process holds it")
            return

        try:
            _scheduler = BackgroundScheduler(daemon=True)

            if os.environ.get("ENABLE_BACKGROUND_JOBS") == "true" or os.environ.get("REPLIT_DEPLOYMENT") == "1":
                logger.info("Setting up background scheduled jobs...")

                _scheduler.add_job(
                    func=_run_full_sync,
                    trigger=CronTrigger(hour=3, minute=0),
                    id='full_dw_sync',
                    name='Full Data Warehouse Sync',
                    replace_existing=True,
                    max_instances=1,
                    misfire_grace_time=3600
                )
                logger.info("Full DW sync scheduled: Daily at 3:00 AM")

                _scheduler.add_job(
                    func=_run_incremental_sync,
                    trigger=CronTrigger(hour="1,13", minute=0),
                    id='incremental_dw_sync',
                    name='Incremental Data Warehouse Sync',
                    replace_existing=True,
                    max_instances=1,
                    misfire_grace_time=3600
                )
                logger.info("Incremental DW sync scheduled: Daily at 1:00 AM and 1:00 PM")

            _scheduler.start()
            logger.info("Background scheduler started successfully (lock acquired, pid=%s)", os.getpid())

        except Exception as e:
            logger.error(f"Error setting up scheduler: {str(e)}", exc_info=True)


def stop_scheduler():
    """Stop the background scheduler gracefully."""
    global _scheduler, _lock_conn
    with _lock:
        if _scheduler and getattr(_scheduler, "running", False):
            try:
                _scheduler.shutdown(wait=False)
                logger.info("Scheduler shut down successfully")
            except Exception as e:
                logger.error(f"Error shutting down scheduler: {str(e)}")
            _scheduler = None
        if _lock_conn is not None:
            try:
                _lock_conn.close()
            except Exception:
                pass
            _lock_conn = None


def _run_full_sync():
    """Wrapper to run full sync with proper app context."""
    try:
        from app import app, db
        from datawarehouse_sync import full_dw_update

        with app.app_context():
            logger.info("=" * 80)
            logger.info("SCHEDULED FULL DW SYNC STARTED")
            logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
            logger.info("=" * 80)

            full_dw_update(db.session)

            logger.info("=" * 80)
            logger.info("SCHEDULED FULL DW SYNC COMPLETED")
            logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
            logger.info("=" * 80)
    except Exception as e:
        logger.error(f"Error in scheduled full sync: {str(e)}", exc_info=True)


def _run_incremental_sync():
    """Wrapper to run incremental sync with proper app context."""
    try:
        from app import app, db
        from datawarehouse_sync import incremental_dw_update

        with app.app_context():
            logger.info("=" * 80)
            logger.info("SCHEDULED INCREMENTAL DW SYNC STARTED")
            logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
            logger.info("=" * 80)

            incremental_dw_update(db.session)

            logger.info("=" * 80)
            logger.info("SCHEDULED INCREMENTAL DW SYNC COMPLETED")
            logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
            logger.info("=" * 80)
    except Exception as e:
        logger.error(f"Error in scheduled incremental sync: {str(e)}", exc_info=True)


def add_custom_job(schedule_description, job_name, job_func, hour=None, minute=0, day_of_week=None):
    """Add a custom scheduled job."""
    global _scheduler

    if not _scheduler or not getattr(_scheduler, "running", False):
        logger.warning("Scheduler not running. Cannot add job.")
        return False

    try:
        trigger = CronTrigger(hour=hour, minute=minute, day_of_week=day_of_week)
        _scheduler.add_job(
            func=job_func,
            trigger=trigger,
            id=job_name,
            name=schedule_description,
            replace_existing=True,
            max_instances=1
        )
        logger.info(f"Job '{job_name}' scheduled: {schedule_description}")
        return True
    except Exception as e:
        logger.error(f"Error adding job '{job_name}': {str(e)}")
        return False


def list_scheduled_jobs():
    """Get list of all scheduled jobs."""
    global _scheduler

    if not _scheduler:
        return []

    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            'id': job.id,
            'name': job.name,
            'trigger': str(job.trigger),
            'next_run': job.next_run_time.isoformat() if job.next_run_time else None
        })

    return jobs
