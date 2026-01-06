"""
Background task scheduler for running tasks at specific hours.
Uses APScheduler to manage scheduled jobs.
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
import os

logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler = None


def setup_scheduler(app):
    """
    Initialize and start the background scheduler.
    Call this from app.py after app context is created.
    """
    global scheduler
    
    try:
        scheduler = BackgroundScheduler(daemon=True)
        
        # Only set up scheduled jobs in production or if explicitly enabled
        if os.environ.get("ENABLE_BACKGROUND_JOBS") == "true" or os.environ.get("REPLIT_DEPLOYMENT") == "1":
            from datawarehouse_sync import full_dw_update, incremental_dw_update
            from app import db
            
            logger.info("Setting up background scheduled jobs...")
            
            # Full DW sync - runs every Sunday at 3:00 AM
            scheduler.add_job(
                func=_run_full_sync,
                trigger=CronTrigger(day_of_week="sun", hour=3, minute=0),
                id='full_dw_sync',
                name='Full Data Warehouse Sync',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=3600
            )
            logger.info("✓ Full DW sync scheduled: Every Sunday at 3:00 AM")
            
            # Incremental sync - runs daily at 1:00 AM and 1:00 PM
            scheduler.add_job(
                func=_run_incremental_sync,
                trigger=CronTrigger(hour="1,13", minute=0),
                id='incremental_dw_sync',
                name='Incremental Data Warehouse Sync',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=3600
            )
            logger.info("✓ Incremental DW sync scheduled: Daily at 1:00 AM and 1:00 PM")
            
        scheduler.start()
        logger.info("Background scheduler started successfully")
        
    except Exception as e:
        logger.error(f"Error setting up scheduler: {str(e)}", exc_info=True)


def stop_scheduler():
    """Stop the background scheduler gracefully."""
    global scheduler
    if scheduler and scheduler.running:
        try:
            scheduler.shutdown()
            logger.info("Scheduler shut down successfully")
        except Exception as e:
            logger.error(f"Error shutting down scheduler: {str(e)}")


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
    """
    Add a custom scheduled job.
    
    Args:
        schedule_description: Human description of when to run (e.g., "Daily at 6 PM")
        job_name: Unique job identifier
        job_func: The function to execute
        hour: Hour of day (0-23) or list of hours (e.g., "1,13" for 1 AM and 1 PM)
        minute: Minute of hour (default: 0)
        day_of_week: Day(s) of week (0=Monday, 6=Sunday, or list)
    """
    global scheduler
    
    if not scheduler:
        logger.warning("Scheduler not initialized. Cannot add job.")
        return False
    
    try:
        trigger = CronTrigger(hour=hour, minute=minute, day_of_week=day_of_week)
        scheduler.add_job(
            func=job_func,
            trigger=trigger,
            id=job_name,
            name=schedule_description,
            replace_existing=True,
            max_instances=1
        )
        logger.info(f"✓ Job '{job_name}' scheduled: {schedule_description}")
        return True
    except Exception as e:
        logger.error(f"Error adding job '{job_name}': {str(e)}")
        return False


def list_scheduled_jobs():
    """Get list of all scheduled jobs."""
    global scheduler
    
    if not scheduler:
        return []
    
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            'id': job.id,
            'name': job.name,
            'trigger': str(job.trigger),
            'next_run': job.next_run_time.isoformat() if job.next_run_time else None
        })
    
    return jobs
