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

    if os.environ.get("SCHEDULER_WORKER") == "0":
        logger.info("Scheduler skipped — not the designated scheduler worker")
        return
    
    try:
        scheduler = BackgroundScheduler(daemon=True)
        
        # Only set up scheduled jobs in production or if explicitly enabled
        is_production = os.environ.get("REPLIT_ENVIRONMENT") == "production" or os.environ.get("REPLIT_DEPLOYMENT") == "1"
        if os.environ.get("ENABLE_BACKGROUND_JOBS") == "true" or is_production:
            from datawarehouse_sync import full_dw_update, incremental_dw_update
            from app import db
            
            logger.info("Setting up background scheduled jobs...")
            
            # Full DW sync - runs daily at 3:00 AM
            scheduler.add_job(
                func=_run_full_sync,
                trigger=CronTrigger(hour=3, minute=0),
                id='full_dw_sync',
                name='Full Data Warehouse Sync',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=3600
            )
            logger.info("✓ Full DW sync scheduled: Daily at 3:00 AM")

            # Daily invoice/customer sync - also daily at 4:00 AM
            # (Note: we already have incremental below, but user wants daily full update)
            
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

            scheduler.add_job(
                func=_run_customer_sync,
                trigger=CronTrigger(hour=4, minute=0),
                id='customer_sync',
                name='Customer Sync from PS365',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=3600
            )
            logger.info("✓ Customer sync scheduled: Daily at 4:00 AM")

            scheduler.add_job(
                func=_run_invoice_sync,
                trigger=CronTrigger(hour=18, minute=0),
                id='invoice_sync',
                name='Invoice Sync from PS365',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=3600
            )
            logger.info("✓ Invoice sync scheduled: Daily at 6:00 PM (last 2 days)")

            scheduler.add_job(
                func=_run_balance_fetch,
                trigger=CronTrigger(hour=2, minute=30),
                id='balance_fetch',
                name='Customer Balance Fetch from PS365',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=3600
            )
            logger.info("✓ Balance fetch scheduled: Daily at 2:30 AM")

            scheduler.add_job(
                func=_run_forecast,
                trigger=CronTrigger(hour=5, minute=0),
                id='forecast_run',
                name='Nightly Forecast Run',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=3600
            )
            logger.info("✓ Forecast run scheduled: Daily at 5:00 AM")

            scheduler.add_job(
                func=_run_pending_orders_sync,
                trigger=CronTrigger(minute="0,30"),
                id='pending_orders_sync',
                name='PS365 Pending Orders Sync',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=600
            )
            logger.info("✓ Pending orders sync scheduled: Every 30 minutes")

            scheduler.add_job(
                func=_retry_pending_payments,
                trigger=CronTrigger(minute="*/5"),
                id='retry_pending_payments',
                name='Retry PENDING_RETRY Payments to PS365',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=300
            )
            logger.info("✓ PENDING_RETRY payment retry scheduled: Every 5 minutes")

            scheduler.add_job(
                func=_run_dropbox_cost_import,
                trigger=CronTrigger(hour=2, minute=0),
                id='dropbox_cost_import',
                name='Dropbox Cost Import',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=3600
            )
            logger.info("✓ Dropbox cost import scheduled: Daily at 2:00 AM")

            scheduler.add_job(
                func=_run_expiry_ftp_upload,
                trigger=CronTrigger(hour=21, minute=0),
                id='expiry_ftp_upload',
                name='Expiry Dates FTP Upload',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=3600
            )
            logger.info("✓ Expiry dates FTP upload scheduled: Daily at 9:00 PM (21:00)")

            scheduler.add_job(
                func=_run_stock_777_sync,
                trigger=CronTrigger(hour="7,18", minute=0),
                id='stock_777_sync',
                name='PS365 Stock 777 Daily Sync',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=3600
            )
            logger.info("✓ Stock 777 sync scheduled: Daily at 7:00 AM and 6:00 PM")

            if is_production:
                scheduler.add_job(
                    func=_run_stock_777_sync,
                    trigger=CronTrigger(hour="7,18", minute=0),
                    id='stock_777_sync_production',
                    name='PS365 Stock 777 Daily Sync (Production)',
                    replace_existing=True,
                    max_instances=1,
                    misfire_grace_time=3600
                )
                logger.info("✓ Stock 777 production sync scheduled: Daily at 7:00 AM and 6:00 PM")

            if is_production:
                _run_stock_777_catch_up_on_startup()

            is_deployed = os.environ.get("REPLIT_DEPLOYMENT") == "1"
            if is_deployed:
                scheduler.add_job(
                    func=_run_ftp_login_sync,
                    trigger=CronTrigger(minute="15,45"),
                    id='ftp_login_sync',
                    name='FTP Login Logs Sync',
                    replace_existing=True,
                    max_instances=1,
                    misfire_grace_time=600
                )
                logger.info("✓ FTP login sync scheduled: Every 30 minutes (at :15 and :45)")

                scheduler.add_job(
                    func=_run_ftp_price_master_sync,
                    trigger=CronTrigger(hour=6, minute=0),
                    id='ftp_price_master_sync',
                    name='FTP Price Master Sync',
                    replace_existing=True,
                    max_instances=1,
                    misfire_grace_time=3600
                )
                logger.info("✓ FTP price master sync scheduled: Daily at 6:00 AM")
            else:
                logger.info("⏭ FTP login sync skipped (not deployed)")
                logger.info("⏭ FTP price master sync skipped (not deployed)")

        scheduler.start()
        logger.info("Background scheduler started successfully")

        if is_production:
            import threading
            def _deferred_missed_sync_check():
                import time
                time.sleep(60)
                _check_missed_syncs_on_startup()
            t = threading.Thread(target=_deferred_missed_sync_check, daemon=True)
            t.start()
        
    except Exception as e:
        logger.error(f"Error setting up scheduler: {str(e)}", exc_info=True)


def _check_missed_syncs_on_startup():
    try:
        from app import app, db
        from models import PS365SyncLog
        from datetime import timedelta
        with app.app_context():
            now = datetime.utcnow()
            logger.info("Checking for missed scheduled syncs after startup...")

            last_full = (
                PS365SyncLog.query
                .filter(PS365SyncLog.sync_type == 'FULL_DW_UPDATE')
                .filter(PS365SyncLog.status.in_(['COMPLETED', 'COMPLETED_WITH_ERRORS']))
                .order_by(PS365SyncLog.started_at.desc())
                .first()
            )
            hours_since_full = None
            if last_full and last_full.started_at:
                hours_since_full = (now - last_full.started_at).total_seconds() / 3600
                logger.info(f"Last successful full DW sync: {last_full.started_at} ({hours_since_full:.1f}h ago)")
            else:
                logger.info("No previous full DW sync found")

            if hours_since_full is None or hours_since_full > 20:
                logger.info("Full DW sync is overdue (>20h or never ran) — triggering now")
                _run_full_sync()
            else:
                logger.info(f"Full DW sync is recent ({hours_since_full:.1f}h ago), skipping")

            db.session.remove()
            db.engine.dispose()

            now = datetime.utcnow()
            last_invoice = (
                PS365SyncLog.query
                .filter(PS365SyncLog.sync_type == 'INVOICE_SYNC')
                .filter(PS365SyncLog.status.in_(['COMPLETED', 'COMPLETED_WITH_ERRORS']))
                .order_by(PS365SyncLog.started_at.desc())
                .first()
            )
            hours_since_inv = None
            if last_invoice and last_invoice.started_at:
                hours_since_inv = (now - last_invoice.started_at).total_seconds() / 3600
                logger.info(f"Last successful invoice sync: {last_invoice.started_at} ({hours_since_inv:.1f}h ago)")
            else:
                logger.info("No previous invoice sync found")

            if hours_since_inv is None or hours_since_inv > 20:
                logger.info("Invoice sync is overdue (>20h or never ran) — triggering now")
                _run_invoice_sync()
            else:
                logger.info(f"Invoice sync is recent ({hours_since_inv:.1f}h ago), skipping")

    except Exception as e:
        logger.error(f"Error checking missed syncs on startup: {str(e)}", exc_info=True)


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
            
            full_dw_update(db.session, sync_trigger='scheduled')
            
            logger.info("=" * 80)
            logger.info("SCHEDULED FULL DW SYNC COMPLETED")
            logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
            logger.info("=" * 80)
    except Exception as e:
        logger.error(f"Error in scheduled full sync: {str(e)}", exc_info=True)
        try:
            from services.sync_logger import fail_sync_log
            from models import PS365SyncLog
            with app.app_context():
                running = PS365SyncLog.query.filter_by(sync_type='FULL_DW_UPDATE', status='RUNNING').order_by(PS365SyncLog.started_at.desc()).first()
                if running:
                    fail_sync_log(db.session, running, str(e))
        except Exception:
            pass


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
            
            incremental_dw_update(db.session, sync_trigger='scheduled')
            
            logger.info("=" * 80)
            logger.info("SCHEDULED INCREMENTAL DW SYNC COMPLETED")
            logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
            logger.info("=" * 80)
    except Exception as e:
        logger.error(f"Error in scheduled incremental sync: {str(e)}", exc_info=True)
        try:
            from services.sync_logger import fail_sync_log
            from models import PS365SyncLog
            with app.app_context():
                running = PS365SyncLog.query.filter_by(sync_type='INCREMENTAL_ITEMS', status='RUNNING').order_by(PS365SyncLog.started_at.desc()).first()
                if running:
                    fail_sync_log(db.session, running, str(e))
        except Exception:
            pass


def _run_invoice_sync():
    """Wrapper to run invoice sync (last 2 days) with proper app context."""
    try:
        from app import app, db
        from datawarehouse_sync import sync_invoices_from_date
        from datetime import timedelta

        with app.app_context():
            date_from = (datetime.utcnow() - timedelta(days=2)).strftime('%Y-%m-%d')
            logger.info("=" * 80)
            logger.info(f"SCHEDULED INVOICE SYNC STARTED (from {date_from})")
            logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
            logger.info("=" * 80)

            sync_invoices_from_date(db.session, date_from, sync_trigger='scheduled')

            logger.info("=" * 80)
            logger.info("SCHEDULED INVOICE SYNC COMPLETED")
            logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
            logger.info("=" * 80)
    except Exception as e:
        logger.error(f"Error in scheduled invoice sync: {str(e)}", exc_info=True)


def _run_customer_sync():
    """Wrapper to run customer sync with proper app context."""
    try:
        from app import app, db
        from background_sync import start_customer_sync_background
        from services.sync_logger import start_sync_log, finish_sync_log, fail_sync_log

        with app.app_context():
            logger.info("=" * 80)
            logger.info("SCHEDULED CUSTOMER SYNC STARTED")
            logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
            logger.info("=" * 80)

            slog = start_sync_log(db.session, 'CUSTOMER_SYNC', trigger='scheduled')
            result = start_customer_sync_background(app)

            if result.get("success"):
                import time
                from background_sync import get_sync_status, is_sync_running
                for _ in range(300):
                    time.sleep(2)
                    if not is_sync_running():
                        break
                status = get_sync_status()
                finish_sync_log(db.session, slog,
                    items_found=status.get('total', 0),
                    items_inserted=status.get('created', 0),
                    items_updated=status.get('updated', 0),
                    items_skipped=status.get('skipped', 0),
                    details=status.get('message', ''))
                logger.info("SCHEDULED CUSTOMER SYNC COMPLETED")
            else:
                fail_sync_log(db.session, slog, result.get('error', 'Failed to start'))
                logger.error(f"Customer sync failed to start: {result.get('error')}")
    except Exception as e:
        logger.error(f"Error in scheduled customer sync: {str(e)}", exc_info=True)
        try:
            from services.sync_logger import fail_sync_log
            from models import PS365SyncLog
            with app.app_context():
                running = PS365SyncLog.query.filter_by(sync_type='CUSTOMER_SYNC', status='RUNNING').order_by(PS365SyncLog.started_at.desc()).first()
                if running:
                    fail_sync_log(db.session, running, str(e))
        except Exception:
            pass


def _run_balance_fetch():
    """Wrapper to run customer balance fetch with proper app context."""
    try:
        from app import app
        import routes_reconciliation as recon_routes

        logger.info("=" * 80)
        logger.info("SCHEDULED CUSTOMER BALANCE FETCH STARTED")
        logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
        logger.info("=" * 80)

        if recon_routes._balance_fetch_status.get('running'):
            logger.warning("Balance fetch already running, skipping scheduled run")
            return

        recon_routes._balance_fetch_status = {
            'running': True, 'success': 0, 'failed': 0, 'skipped': 0,
            'total': 0, 'errors': [], 'done': False,
            'started_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
            'finished_at': None
        }

        recon_routes._run_balance_fetch(app)

        logger.info("=" * 80)
        logger.info("SCHEDULED CUSTOMER BALANCE FETCH COMPLETED")
        logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
        logger.info("=" * 80)
    except Exception as e:
        logger.error(f"Error in scheduled balance fetch: {str(e)}", exc_info=True)


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


def _run_forecast():
    try:
        from app import app, db
        from services.forecast.run_service import execute_forecast_run

        with app.app_context():
            logger.info("=" * 80)
            logger.info("SCHEDULED FORECAST RUN STARTED")
            logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
            logger.info("=" * 80)

            result = execute_forecast_run(db.session, created_by='scheduler')

            logger.info("=" * 80)
            logger.info("SCHEDULED FORECAST RUN COMPLETED")
            logger.info(f"Result: {result}")
            logger.info("=" * 80)
    except Exception as e:
        logger.error(f"Error in scheduled forecast run: {str(e)}", exc_info=True)


def _run_pending_orders_sync():
    try:
        from app import app, db
        from services.ps365_pending_orders_service import (
            sync_pending_order_totals_from_ps365, acquire_sync_lock, release_sync_lock, JOB_NAME
        )

        with app.app_context():
            locked = acquire_sync_lock(JOB_NAME, "scheduler")
            if not locked:
                logger.warning("Pending orders sync already running, skipping scheduled run")
                return

            try:
                logger.info("=" * 80)
                logger.info("SCHEDULED PENDING ORDERS SYNC STARTED")
                logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
                logger.info("=" * 80)

                result = sync_pending_order_totals_from_ps365(triggered_by="scheduler")

                logger.info("=" * 80)
                logger.info(f"SCHEDULED PENDING ORDERS SYNC {'COMPLETED' if result.get('success') else 'FAILED'}")
                logger.info(f"Result: {result}")
                logger.info("=" * 80)
            finally:
                release_sync_lock(JOB_NAME)
    except Exception as e:
        err_msg = str(e).lower()
        if any(p in err_msg for p in ('timed out', 'timeout', 'max retries exceeded', 'connectionerror')):
            logger.warning(f"Pending orders sync skipped — PS365 temporarily unavailable: {str(e)[:150]}")
        else:
            logger.error(f"Error in scheduled pending orders sync: {str(e)}", exc_info=True)


def _retry_pending_payments():
    try:
        from app import app, db
        from models import PaymentEntry, RouteStop, RouteStopInvoice, Invoice, Shipment
        from services.payments import commit_to_ps365

        with app.app_context():
            pending = PaymentEntry.query.filter_by(
                ps_status='PENDING_RETRY',
                is_active=True,
            ).filter(
                PaymentEntry.attempt_count < 10
            ).order_by(PaymentEntry.created_at).limit(20).all()

            if not pending:
                return

            logger.info(f"Retrying {len(pending)} PENDING_RETRY payment(s)")

            for pe in pending:
                try:
                    stop = RouteStop.query.get(pe.route_stop_id)
                    if not stop:
                        continue
                    rsis = RouteStopInvoice.query.filter_by(
                        route_stop_id=pe.route_stop_id, is_active=True
                    ).all()
                    invoice_nos = [r.invoice_no for r in rsis]
                    customer_code = stop.customer_code or ''
                    if not customer_code and invoice_nos:
                        inv = Invoice.query.get(invoice_nos[0])
                        if inv:
                            customer_code = inv.customer_code_365 or ''

                    shipment = Shipment.query.get(stop.shipment_id)
                    driver_username = shipment.driver_name if shipment else 'system'
                    commit_to_ps365(pe, customer_code, invoice_nos, driver_username)
                    db.session.commit()

                    if pe.ps_status == 'SUCCESS':
                        logger.info(f"Background retry SUCCESS for PaymentEntry {pe.id}")
                    else:
                        logger.info(f"Background retry still {pe.ps_status} for PaymentEntry {pe.id} (attempt {pe.attempt_count})")
                except Exception as exc:
                    db.session.rollback()
                    logger.warning(f"Background retry error for PaymentEntry {pe.id}: {str(exc)[:120]}")
    except Exception as e:
        logger.error(f"Error in _retry_pending_payments: {str(e)}")


def _run_ftp_login_sync():
    try:
        from services.ftp_login_sync import sync_login_logs_from_ftp
        logger.info("=" * 60)
        logger.info("STARTING FTP LOGIN LOGS SYNC")
        logger.info("=" * 60)
        result = sync_login_logs_from_ftp()
        logger.info(f"FTP LOGIN SYNC {'COMPLETED' if result.get('success') else 'FAILED'}: {result}")
    except Exception as e:
        logger.error(f"Error in FTP login sync: {str(e)}", exc_info=True)


def _run_ftp_price_master_sync():
    try:
        from app import app
        from services.crm_price_offers import sync_price_master_from_ftp
        logger.info("=" * 60)
        logger.info("STARTING FTP PRICE MASTER SYNC")
        logger.info("=" * 60)
        with app.app_context():
            result = sync_price_master_from_ftp()
        logger.info(f"FTP PRICE MASTER SYNC {'COMPLETED' if result.get('success') else 'FAILED'}: {result}")
    except Exception as e:
        logger.error(f"Error in FTP price master sync: {str(e)}", exc_info=True)


def _run_dropbox_cost_import():
    try:
        from app import app
        from services.dropbox_service import sync_dropbox_file
        logger.info("=" * 60)
        logger.info("STARTING SCHEDULED DROPBOX COST IMPORT")
        logger.info("=" * 60)
        with app.app_context():
            log = sync_dropbox_file(skip_unchanged=True)
            if log.status == 'success_no_change':
                logger.info("DROPBOX COST IMPORT: File unchanged — no update needed")
            elif log.status in ('config_error', 'auth_error'):
                logger.error(f"DROPBOX COST IMPORT FAILED: {log.error_message}")
            else:
                md = log.metadata_json or {}
                logger.info(
                    f"DROPBOX COST IMPORT COMPLETED: "
                    f"{md.get('rows_read', 0)} read, "
                    f"{md.get('rows_matched', 0)} matched, "
                    f"{log.rows_imported} updated"
                )
    except Exception as e:
        logger.error(f"Error in scheduled Dropbox cost import: {str(e)}", exc_info=True)


def _run_expiry_ftp_upload():
    try:
        from app import app
        from services.expiry_ftp_upload import run_expiry_dates_export_and_upload
        logger.info("=" * 60)
        logger.info("STARTING SCHEDULED EXPIRY DATES EXPORT AND FTP UPLOAD")
        logger.info("=" * 60)
        with app.app_context():
            result = run_expiry_dates_export_and_upload()
            logger.info(
                "EXPIRY DATES EXPORT AND FTP UPLOAD COMPLETED: "
                f"csv_export={result.get('csv_export')}, "
                f"ftp_upload={result.get('ftp_upload')}"
            )
            if result.get('details'):
                logger.info(f"Details: {result['details']}")
    except Exception as e:
        logger.error(f"Error in scheduled expiry FTP upload: {str(e)}", exc_info=True)


def _run_stock_777_sync():
    try:
        from app import app
        from services.ps365_stock_777_service import sync_ps365_stock_777

        with app.app_context():
            logger.info("=" * 80)
            logger.info("SCHEDULED PS365 STOCK 777 SYNC STARTED")
            logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
            logger.info("=" * 80)

            result = sync_ps365_stock_777(trigger="scheduled")

            logger.info("=" * 80)
            logger.info(
                f"SCHEDULED PS365 STOCK 777 SYNC "
                f"{'COMPLETED' if result.get('success') else 'FAILED'}"
            )
            logger.info(f"Result: {result}")
            logger.info("=" * 80)
    except Exception as e:
        logger.error(f"Error in scheduled stock 777 sync: {str(e)}", exc_info=True)


def _run_stock_777_catch_up_on_startup():
    try:
        from app import app, db
        from sqlalchemy import text
        from datetime import date
        with app.app_context():
            latest_run = db.session.execute(text("""
                SELECT MAX(created_at)
                FROM ps365_stock_777_runs
                WHERE created_at::date = CURRENT_DATE
            """)).scalar()
            if latest_run == date.today():
                logger.info("Stock 777 catch-up skipped: already ran today")
                return
            logger.info("Stock 777 catch-up triggered on startup")
            _run_stock_777_sync()
    except Exception as e:
        logger.error(f"Error in stock 777 catch-up on startup: {str(e)}", exc_info=True)


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
