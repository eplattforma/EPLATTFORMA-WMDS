"""
Background task scheduler for running tasks at specific hours.
Uses APScheduler to manage scheduled jobs.
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from datetime import datetime
import os

logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler = None


class JobSkipped(Exception):
    """Body funcs raise this to signal an early-return guard path.

    `_tracked` catches it and writes status='SKIPPED' (with the message as
    the result_summary reason) instead of FAILED. APScheduler never sees the
    exception. Examples: lock already held, "already ran today", "no work to
    do this tick", concurrent run in progress.
    """
    pass


# Per-thread context for the active job_runs row id, so body funcs can
# emit heartbeats without `_tracked` having to plumb run_id through every
# call signature.
import contextvars as _ctxvars
_CURRENT_JOB_RUN_ID = _ctxvars.ContextVar("_CURRENT_JOB_RUN_ID", default=None)


def heartbeat(current_step=None, progress_current=None,
              progress_total=None, progress_message=None):
    """Emit a heartbeat for the active job_runs row, if any.

    Body funcs called via `_tracked` can call this freely; outside of a
    tracked job (e.g. ad-hoc scripts) it is a safe no-op. Failures inside
    the logger are swallowed — heartbeats must never break the body.
    """
    run_id = _CURRENT_JOB_RUN_ID.get()
    if not run_id:
        return
    try:
        from services.job_run_logger import heartbeat as _hb
        _hb(
            run_id,
            current_step=current_step,
            progress_current=progress_current,
            progress_total=progress_total,
            progress_message=progress_message,
        )
    except Exception as e:
        logger.debug(f"heartbeat() failed (non-fatal): {e}")


def _tracked(job_id, job_name=None, trigger_source="scheduled"):
    """Generic APScheduler entrypoint that emits a `job_runs` lifecycle row.

    Phase 2 visibility: every scheduled tick goes through this wrapper so a
    row appears in `job_runs` (RUNNING -> SUCCESS / FAILED / SKIPPED) for
    each fire. The body func is looked up from `JOB_FUNCTIONS` at call time
    so we don't have to ship a closure into the SQLAlchemyJobStore (it can
    only persist a module-level callable by dotted path).

    Status semantics:
      - SUCCESS  — body returned normally
      - SKIPPED  — body raised `JobSkipped(reason)` (early-return guard:
                   lock held, already ran today, no work, etc.)
      - FAILED   — body raised any other exception (re-raised so APScheduler
                   logs it too)

    Several `_run_*` body funcs still catch their own exceptions and only
    log, which is intentional ("logging failures must not stop scheduled
    jobs" per the brief) — those will appear SUCCESS at this layer;
    finer-grained business success/failure lives in domain-specific log
    tables (PS365SyncLog, ForecastRun, BotRunLog, etc.) and will be linked
    into job_runs via parent_run_id in a later phase.

    Writes are gated by the job_runs_enabled / job_runs_write_enabled flags
    inside `services.job_run_logger`; if either is OFF the lifecycle calls
    are no-ops and the body still runs normally.
    """
    if not JOB_FUNCTIONS:
        _register_job_funcs()
    body = JOB_FUNCTIONS.get(job_id)
    if body is None:
        logger.error(f"_tracked: no registered body for job_id={job_id!r}")
        return

    from app import app as _flask_app
    from services.job_run_logger import start_job_run, finish_job_run

    # Phase 2 fix: APScheduler executor threads (and the daemon thread used
    # by `run_job_now`) have no Flask app context. The lifecycle helpers in
    # `services.job_run_logger` use `db.engine.connect()` which requires
    # one. Wrap the whole tick in `app.app_context()`. Body funcs already
    # push their own `with app.app_context():` and Flask app contexts
    # nest cleanly.
    with _flask_app.app_context():
        run_id = start_job_run(
            job_id,
            job_name=job_name or job_id,
            trigger_source=trigger_source,
        )
        token = _CURRENT_JOB_RUN_ID.set(run_id)
        try:
            try:
                body()
            except JobSkipped as skip:
                reason = (str(skip) or "skipped")[:500]
                finish_job_run(
                    run_id,
                    status="SKIPPED",
                    result_summary={"reason": reason},
                )
                logger.info(f"Job {job_id} SKIPPED: {reason}")
                return
            except Exception as e:
                finish_job_run(
                    run_id,
                    status="FAILED",
                    error_message=(str(e) or type(e).__name__)[:500],
                )
                raise
            finish_job_run(run_id, status="SUCCESS")
        finally:
            _CURRENT_JOB_RUN_ID.reset(token)


def setup_scheduler(app):
    """
    Initialize and start the background scheduler.
    Call this from app.py after app context is created.
    """
    global scheduler

    worker_age = os.environ.get("GUNICORN_WORKER_AGE", "")
    if worker_age and worker_age != "1":
        logger.info(f"Scheduler skipped — not the designated scheduler worker (age={worker_age})")
        return
    
    try:
        jobstore_engine = None
        try:
            from app import db as _db
            with app.app_context():
                jobstore_engine = _db.engine
        except Exception as e:
            logger.warning(f"Could not access Flask-SQLAlchemy engine for jobstore ({e}); will fall back to DATABASE_URL")

        # Phase 2: scheduler timezone is anchored to Africa/Cairo so the
        # daily fixed-time jobs (17:35 Forecast, 17:55 Cost Update,
        # 18:10 Offers, 18:05 Stock 777, etc.) match the cadence stated
        # in SCHEDULING.md regardless of host TZ. Cairo and Athens differ
        # in DST so an explicit IANA tz is required for correctness.
        SCHEDULER_TZ = 'Africa/Cairo'
        if jobstore_engine is not None:
            jobstores = {
                'default': SQLAlchemyJobStore(
                    engine=jobstore_engine,
                    tablename='apscheduler_jobs',
                )
            }
            scheduler = BackgroundScheduler(daemon=True, jobstores=jobstores, timezone=SCHEDULER_TZ)
            logger.info(f"Scheduler using SQLAlchemy jobstore (shared engine, pool_pre_ping enabled, timezone={SCHEDULER_TZ}) — missed runs will be recovered on worker boot")
        elif os.environ.get("DATABASE_URL"):
            jobstores = {
                'default': SQLAlchemyJobStore(
                    url=os.environ["DATABASE_URL"],
                    tablename='apscheduler_jobs',
                )
            }
            scheduler = BackgroundScheduler(daemon=True, jobstores=jobstores, timezone=SCHEDULER_TZ)
            logger.info(f"Scheduler using SQLAlchemy jobstore (own engine via DATABASE_URL, timezone={SCHEDULER_TZ}) — missed runs will be recovered on worker boot")
        else:
            scheduler = BackgroundScheduler(daemon=True, timezone=SCHEDULER_TZ)
            logger.warning("DATABASE_URL not set — scheduler falling back to in-memory jobstore (missed runs will be lost on restart)")

        # Only set up scheduled jobs in production or if explicitly enabled
        is_production = os.environ.get("REPLIT_ENVIRONMENT") == "production" or os.environ.get("REPLIT_DEPLOYMENT") == "1"
        if os.environ.get("ENABLE_BACKGROUND_JOBS") == "true" or is_production:
            from datawarehouse_sync import full_dw_update, incremental_dw_update
            from app import db
            
            logger.info("Setting up background scheduled jobs...")
            
            # NOTE on scheduling window:
            # The production deployment runs on Replit Autoscale, which spins
            # gunicorn workers down to zero when there is no HTTP traffic. The
            # in-process APScheduler can therefore only fire while a worker is
            # alive — i.e. during business hours when warehouse staff are
            # actively using the CRM. All daily batch jobs are therefore
            # scheduled in a late-afternoon window (16:20–18:05 Cairo) when
            # at least one worker is reliably awake. Sub-hourly jobs
            # (pending orders, payment retries, FTP login sync) stay on their
            # original short cadence — they naturally fire whenever the app
            # is in use during the day. Misfire grace is 6 hours on every
            # daily job so even a slightly delayed worker boot still catches
            # the run.

            # Full DW sync - daily at 17:15 Cairo (heavy, given a 15min gap after incremental)
            scheduler.add_job(
                func=_tracked,
                kwargs={'job_id': 'full_dw_sync', 'job_name': 'Full Data Warehouse Sync'},
                trigger=CronTrigger(hour=17, minute=15),
                id='full_dw_sync',
                name='Full Data Warehouse Sync',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=21600,
                coalesce=True,
            )
            logger.info("✓ Full DW sync scheduled: Daily at 17:15 Cairo")

            # Incremental sync - twice daily at 13:00 (lunch traffic) and 17:00 Cairo
            scheduler.add_job(
                func=_tracked,
                kwargs={'job_id': 'incremental_dw_sync', 'job_name': 'Incremental Data Warehouse Sync'},
                trigger=CronTrigger(hour="13,17", minute=0),
                id='incremental_dw_sync',
                name='Incremental Data Warehouse Sync',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=21600,
                coalesce=True,
            )
            logger.info("✓ Incremental DW sync scheduled: Daily at 13:00 and 17:00 Cairo")

            scheduler.add_job(
                func=_tracked,
                kwargs={'job_id': 'customer_sync', 'job_name': 'Customer Sync from PS365'},
                trigger=CronTrigger(hour=16, minute=40),
                id='customer_sync',
                name='Customer Sync from PS365',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=21600,
                coalesce=True,
            )
            logger.info("✓ Customer sync scheduled: Daily at 16:40 Cairo")

            scheduler.add_job(
                func=_tracked,
                kwargs={'job_id': 'invoice_sync', 'job_name': 'Invoice Sync from PS365'},
                trigger=CronTrigger(hour=16, minute=50),
                id='invoice_sync',
                name='Invoice Sync from PS365',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=21600,
                coalesce=True,
            )
            logger.info("✓ Invoice sync scheduled: Daily at 16:50 Cairo (last 2 days)")

            scheduler.add_job(
                func=_tracked,
                kwargs={'job_id': 'balance_fetch', 'job_name': 'Customer Balance Fetch from PS365'},
                trigger=CronTrigger(hour=16, minute=30),
                id='balance_fetch',
                name='Customer Balance Fetch from PS365',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=21600,
                coalesce=True,
            )
            logger.info("✓ Balance fetch scheduled: Daily at 16:30 Cairo")

            scheduler.add_job(
                func=_tracked,
                kwargs={'job_id': 'forecast_run', 'job_name': 'Nightly Forecast Run'},
                trigger=CronTrigger(hour=17, minute=35),
                id='forecast_run',
                name='Nightly Forecast Run',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=21600,
                coalesce=True,
            )
            logger.info("✓ Forecast run scheduled: Daily at 17:35 Cairo")

            # Watchdog cadence is gated on `forecast_watchdog_enabled`:
            #   OFF (default)  -> legacy 10-min cadence
            #   ON             -> `forecast_watchdog_interval_minutes`
            #                     (default 5, clamped 1..59 — APScheduler
            #                     CronTrigger rejects `*/60`)
            # The job is ALWAYS scheduled — the flag only tunes how often
            # the watchdog sweeps. The live `/forecast/api/suppliers`
            # endpoint also calls `mark_stale_forecast_run_if_needed` on
            # every page hit, so a stuck run can never block the suppliers
            # page even if the cron is paused.
            try:
                from models import Setting
                # Settings live on the Flask-SQLAlchemy scoped session, so
                # this read must happen inside an app context. The other
                # add_job calls in setup_scheduler don't need one because
                # APScheduler.add_job just stores the func ref — it doesn't
                # touch the DB until the scheduler starts firing.
                with app.app_context():
                    wd_raw = Setting.get(db.session, 'forecast_watchdog_enabled', 'false')
                    wd_enabled = str(wd_raw).strip().lower() in ('true', '1', 'yes', 'on')
                    wd_interval_raw = Setting.get(db.session, 'forecast_watchdog_interval_minutes', '5')
                    # Clamp to 1..59 because APScheduler CronTrigger rejects
                    # `*/60` ("step value 60 is higher than range 59"). 59-min
                    # is effectively hourly and avoids the crash at boot.
                    wd_interval = max(min(int(wd_interval_raw or '5'), 59), 1)
            except Exception as e:
                logger.warning(f"Could not read watchdog settings ({e}); defaulting to OFF / 10min cadence")
                wd_enabled = False
                wd_interval = 5

            wd_cadence = wd_interval if wd_enabled else 10
            scheduler.add_job(
                func=_tracked,
                kwargs={'job_id': 'forecast_watchdog',
                        'job_name': 'Forecast Run Watchdog (auto-retry)'},
                trigger=CronTrigger(minute=f"*/{wd_cadence}"),
                id='forecast_watchdog',
                name='Forecast Run Watchdog (auto-retry)',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=300,
                coalesce=True,
            )
            logger.info(
                f"✓ Forecast watchdog scheduled: every {wd_cadence} min "
                f"(forecast_watchdog_enabled={'on' if wd_enabled else 'off → legacy 10min cadence'})"
            )

            scheduler.add_job(
                func=_tracked,
                kwargs={'job_id': 'pending_orders_sync', 'job_name': 'PS365 Pending Orders Sync'},
                trigger=CronTrigger(minute="0,30"),
                id='pending_orders_sync',
                name='PS365 Pending Orders Sync',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=600
            )
            logger.info("✓ Pending orders sync scheduled: Every 30 minutes")

            scheduler.add_job(
                func=_tracked,
                kwargs={'job_id': 'retry_pending_payments', 'job_name': 'Retry PENDING_RETRY Payments to PS365'},
                trigger=CronTrigger(minute="*/5"),
                id='retry_pending_payments',
                name='Retry PENDING_RETRY Payments to PS365',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=300
            )
            logger.info("✓ PENDING_RETRY payment retry scheduled: Every 5 minutes")

            scheduler.add_job(
                func=_tracked,
                kwargs={'job_id': 'erp_item_cost_refresh', 'job_name': 'Cost Update'},
                trigger=CronTrigger(hour=17, minute=55),
                id='erp_item_cost_refresh',
                name='Cost Update',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=21600,
                coalesce=True,
            )
            logger.info("✓ Cost Update scheduled: Daily at 17:55 Cairo (ERP Item Catalogue cost refresh)")

            # Offers Update: pull the latest customer price master from FTP
            # and rebuild per-customer offer rows + summary KPIs. Runs after
            # Cost Update so cost columns are fresh when offer margins are
            # recomputed.
            scheduler.add_job(
                func=_tracked,
                kwargs={'job_id': 'offers_update', 'job_name': 'Offers Update'},
                trigger=CronTrigger(hour=18, minute=10),
                id='offers_update',
                name='Offers Update',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=21600,
                coalesce=True,
            )
            logger.info("✓ Offers Update scheduled: Daily at 18:10 Cairo (FTP price master + customer offer summaries)")

            # Pre-warm Playwright Chromium in production so the 17:55 Cost Update cron
            # doesn't pay first-time install cost (and any install failure is
            # surfaced in boot logs instead of silently killing the cron before
            # it can write its BotRunLog row).
            if is_production:
                try:
                    from services.erp_export_bot import prewarm_playwright_browsers_async
                    prewarm_playwright_browsers_async()
                    logger.info("✓ Playwright Chromium pre-warm kicked off in background")
                except Exception as e:
                    logger.warning(f"Could not start Playwright pre-warm: {e}")

            scheduler.add_job(
                func=_tracked,
                kwargs={'job_id': 'expiry_ftp_upload', 'job_name': 'Expiry Dates FTP Upload'},
                trigger=CronTrigger(hour=17, minute=45),
                id='expiry_ftp_upload',
                name='Expiry Dates FTP Upload',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=21600,
                coalesce=True,
            )
            logger.info("✓ Expiry dates FTP upload scheduled: Daily at 17:45 Cairo")

            if is_production:
                scheduler.add_job(
                    func=_tracked,
                    kwargs={'job_id': 'stock_777_sync_production',
                            'job_name': 'PS365 Stock 777 Daily Sync (Production)'},
                    trigger=CronTrigger(hour=18, minute=5),
                    id='stock_777_sync_production',
                    name='PS365 Stock 777 Daily Sync (Production)',
                    replace_existing=True,
                    max_instances=1,
                    misfire_grace_time=21600,
                    coalesce=True,
                )
                logger.info("✓ Stock 777 production sync scheduled: Daily at 18:05 Cairo")
            else:
                scheduler.add_job(
                    func=_tracked,
                    kwargs={'job_id': 'stock_777_sync',
                            'job_name': 'PS365 Stock 777 Daily Sync'},
                    trigger=CronTrigger(hour=23, minute=30),
                    id='stock_777_sync',
                    name='PS365 Stock 777 Daily Sync',
                    replace_existing=True,
                    max_instances=1,
                    misfire_grace_time=21600,
                    coalesce=True,
                )
                logger.info("✓ Stock 777 sync scheduled: Daily at 11:30 PM")

            if is_production:
                _run_stock_777_catch_up_on_startup()

            is_deployed = os.environ.get("REPLIT_DEPLOYMENT") == "1"
            if is_deployed:
                scheduler.add_job(
                    func=_tracked,
                    kwargs={'job_id': 'ftp_login_sync', 'job_name': 'FTP Login Logs Sync'},
                    trigger=CronTrigger(minute="15,45"),
                    id='ftp_login_sync',
                    name='FTP Login Logs Sync',
                    replace_existing=True,
                    max_instances=1,
                    misfire_grace_time=600
                )
                logger.info("✓ FTP login sync scheduled: Every 30 minutes (at :15 and :45)")
            else:
                logger.info("⏭ FTP login sync skipped (not deployed)")

        scheduler.start()
        logger.info("Background scheduler started successfully")

        # Cleanup: remove the legacy FTP Price Master Sync job from the
        # jobstore if a previous deploy registered it. The Cost Update
        # job (ERP Item Catalogue export) now owns the 17:55 Cairo slot.
        # IMPORTANT: must run AFTER scheduler.start() — APScheduler does
        # not load jobs from the SQLAlchemy jobstore into its in-memory
        # view until the scheduler is started, so `get_job(...)` /
        # `remove_job(...)` on an unstarted scheduler would silently miss
        # persisted rows and the legacy duplicate job would survive.
        # See `_JobstoreContext` docstring for the same rationale.
        try:
            legacy_job = scheduler.get_job('ftp_price_master_sync')
            if legacy_job is not None:
                scheduler.remove_job('ftp_price_master_sync')
                logger.warning(
                    "🧹 Removed legacy 'ftp_price_master_sync' job from jobstore "
                    "(replaced by Cost Update at 17:55 Africa/Cairo — see SCHEDULING.md)"
                )
        except Exception as e:
            logger.debug(f"Legacy ftp_price_master_sync cleanup skipped: {e}")

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
        from datetime import timedelta, timezone

        def _aware(dt):
            if dt is None:
                return None
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        with app.app_context():
            now = datetime.now(timezone.utc)
            logger.info("Checking for missed scheduled syncs after startup...")

            running_full = (
                PS365SyncLog.query
                .filter(PS365SyncLog.sync_type == 'FULL_DW_UPDATE', PS365SyncLog.status == 'RUNNING')
                .order_by(PS365SyncLog.started_at.desc())
                .first()
            )
            if running_full and running_full.started_at \
                    and (now - _aware(running_full.started_at)) < timedelta(hours=6):
                logger.info(
                    f"Full DW sync already RUNNING (started {running_full.started_at}); "
                    f"skipping startup catch-up to avoid double-fire with APScheduler misfire recovery."
                )
            else:
                last_full = (
                    PS365SyncLog.query
                    .filter(PS365SyncLog.sync_type == 'FULL_DW_UPDATE')
                    .filter(PS365SyncLog.status.in_(['COMPLETED', 'COMPLETED_WITH_ERRORS']))
                    .order_by(PS365SyncLog.started_at.desc())
                    .first()
                )
                hours_since_full = None
                if last_full and last_full.started_at:
                    hours_since_full = (now - _aware(last_full.started_at)).total_seconds() / 3600
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

            now = datetime.now(timezone.utc)
            running_inv = (
                PS365SyncLog.query
                .filter(PS365SyncLog.sync_type == 'INVOICE_SYNC', PS365SyncLog.status == 'RUNNING')
                .order_by(PS365SyncLog.started_at.desc())
                .first()
            )
            if running_inv and running_inv.started_at \
                    and (now - _aware(running_inv.started_at)) < timedelta(hours=6):
                logger.info(
                    f"Invoice sync already RUNNING (started {running_inv.started_at}); "
                    f"skipping startup catch-up to avoid double-fire."
                )
            else:
                last_invoice = (
                    PS365SyncLog.query
                    .filter(PS365SyncLog.sync_type == 'INVOICE_SYNC')
                    .filter(PS365SyncLog.status.in_(['COMPLETED', 'COMPLETED_WITH_ERRORS']))
                    .order_by(PS365SyncLog.started_at.desc())
                    .first()
                )
                hours_since_inv = None
                if last_invoice and last_invoice.started_at:
                    hours_since_inv = (now - _aware(last_invoice.started_at)).total_seconds() / 3600
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
        # Re-raise so _tracked records this tick as FAILED, not SUCCESS.
        raise


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
        # Re-raise so _tracked records this tick as FAILED, not SUCCESS.
        raise


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
        # Re-raise so _tracked records this tick as FAILED, not SUCCESS.
        raise


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
                err_msg = result.get('error', 'Failed to start')
                fail_sync_log(db.session, slog, err_msg)
                logger.error(f"Customer sync failed to start: {err_msg}")
                # Surface this to _tracked as FAILED rather than swallowing it.
                raise RuntimeError(f"Customer sync failed to start: {err_msg}")
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
        # Re-raise so _tracked records this tick as FAILED, not SUCCESS.
        raise


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
            raise JobSkipped("balance fetch already running")

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
    except JobSkipped:
        raise
    except Exception as e:
        logger.error(f"Error in scheduled balance fetch: {str(e)}", exc_info=True)
        # Re-raise so _tracked records this tick as FAILED, not SUCCESS.
        raise


def add_custom_job(schedule_description, job_name, job_func, hour=None, minute=0, day_of_week=None):
    """
    Add a custom scheduled job.

    Phase 2 note: this public helper is intentionally NOT routed through
    `_tracked`. It exists for ad-hoc / dynamic jobs whose bodies are not
    registered in `JOB_FUNCTIONS` (callers pass arbitrary callables here),
    so `_tracked` would have no body to dispatch to. The 14 built-in
    catalogue jobs in `setup_scheduler()` all use `_tracked` directly and
    are the ones that produce `job_runs` lifecycle rows.

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

            # Heartbeat #1: signal that we're past boot and into the actual
            # forecast pipeline. The pipeline itself writes per-step
            # heartbeats into ForecastRun.last_heartbeat_at + current_step;
            # we mirror the start signal into job_runs so the watchdog and
            # the admin Job Runs page both see the run is alive.
            heartbeat(current_step="forecast_pipeline_start",
                      progress_message="execute_forecast_run dispatched")

            result = execute_forecast_run(db.session, created_by='scheduler')

            heartbeat(current_step="forecast_pipeline_done",
                      progress_message=str(result)[:200])

            logger.info("=" * 80)
            logger.info("SCHEDULED FORECAST RUN COMPLETED")
            logger.info(f"Result: {result}")
            logger.info("=" * 80)
    except Exception as e:
        logger.error(f"Error in scheduled forecast run: {str(e)}", exc_info=True)
        # Re-raise so _tracked records this tick as FAILED, not SUCCESS.
        raise


# Watchdog tunables ------------------------------------------------------
# A forecast run with no heartbeat for STALE_HEARTBEAT_MINUTES is treated as
# crashed (worker killed by autoscale, OOM, gunicorn timeout, etc.) and
# auto-marked failed. After failing it, we relaunch a fresh run unless we've
# already auto-retried MAX_AUTO_RETRIES_PER_DAY times today, to keep a broken
# pipeline from spinning forever.
STALE_HEARTBEAT_MINUTES = 45
MAX_AUTO_RETRIES_PER_DAY = 3
AUTO_RETRY_CREATED_BY = "auto_retry_watchdog"


def _run_forecast_watchdog():
    """Sweep stale forecast runs and auto-retry once per failure.

    Stale-run detection lives in `services.forecast.stale_detection` so the
    live `/forecast/api/suppliers` endpoint and this background tick agree on
    what "stale" means and on the threshold. Cadence is gated by the
    `forecast_watchdog_enabled` flag (OFF -> legacy 10-min cadence, ON ->
    `forecast_watchdog_interval_minutes`).

    If the most recent run is in 'running' state but its heartbeat is older
    than the configured threshold (`forecast_heartbeat_timeout_seconds`,
    default 2700s = 45 min), mark it failed and immediately launch a
    replacement run (in a daemon thread, so the cron tick returns fast). We
    cap auto-retries per day so a permanently broken forecast pipeline does
    not loop indefinitely.

    Early-return guard paths raise `JobSkipped` so each tick is recorded
    accurately in `job_runs` (SUCCESS only when a real action ran;
    SKIPPED when there was nothing to do or the retry budget was spent).
    """
    try:
        from app import app, db
        from models import ForecastRun
        from services.forecast.stale_detection import mark_stale_forecast_run_if_needed
        from datetime import timedelta

        with app.app_context():
            stale_marked_id = mark_stale_forecast_run_if_needed(db.session)
            stale_marked = stale_marked_id is not None

            if not stale_marked:
                last = ForecastRun.query.order_by(ForecastRun.started_at.desc()).first()
                if last is None or last.status != "failed":
                    raise JobSkipped("no stale run and last run not failed — nothing to do")

                today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                if last.started_at is None or last.started_at < today_start:
                    raise JobSkipped("last failed run is from a previous day — not retrying")

                # Only auto-retry runs that the scheduler / watchdog launched.
                # Admin-triggered runs that failed are the admin's call to retry.
                if last.created_by not in ("scheduler", AUTO_RETRY_CREATED_BY):
                    raise JobSkipped(f"last failed run was admin-triggered ({last.created_by}) — not auto-retrying")

            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            auto_retries_today = (
                ForecastRun.query
                .filter(ForecastRun.created_by == AUTO_RETRY_CREATED_BY)
                .filter(ForecastRun.started_at >= today_start)
                .count()
            )
            if auto_retries_today >= MAX_AUTO_RETRIES_PER_DAY:
                logger.warning(
                    f"forecast watchdog: skipping auto-retry — already retried "
                    f"{auto_retries_today}/{MAX_AUTO_RETRIES_PER_DAY} times today"
                )
                raise JobSkipped(
                    f"daily auto-retry budget spent ({auto_retries_today}/{MAX_AUTO_RETRIES_PER_DAY})"
                )

            already_running = (
                ForecastRun.query
                .filter_by(status="running")
                .first()
            )
            if already_running:
                logger.info(
                    f"forecast watchdog: another run already in progress (id={already_running.id}), skipping retry"
                )
                raise JobSkipped(f"another forecast run is already in progress (id={already_running.id})")

            logger.warning(
                f"forecast watchdog: launching auto-retry "
                f"(attempt {auto_retries_today + 1}/{MAX_AUTO_RETRIES_PER_DAY} today)"
            )
            heartbeat(current_step="auto_retry_dispatch",
                      progress_message=f"attempt {auto_retries_today + 1}/{MAX_AUTO_RETRIES_PER_DAY}")

            import threading as _t

            def _bg_retry():
                try:
                    from app import app as _app, db as _db
                    from services.forecast.run_service import execute_forecast_run
                    with _app.app_context():
                        result = execute_forecast_run(_db.session, created_by=AUTO_RETRY_CREATED_BY)
                        logger.info(f"forecast watchdog: auto-retry result={result}")
                except Exception as e:
                    logger.error(f"forecast watchdog: auto-retry failed: {e}", exc_info=True)

            _t.Thread(target=_bg_retry, daemon=True, name="forecast-auto-retry").start()
    except JobSkipped:
        raise
    except Exception as e:
        logger.error(f"Error in forecast watchdog: {str(e)}", exc_info=True)
        # Re-raise so _tracked records this tick as FAILED, not SUCCESS.
        raise


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
                raise JobSkipped("sync lock already held by another run")

            try:
                logger.info("=" * 80)
                logger.info("SCHEDULED PENDING ORDERS SYNC STARTED")
                logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
                logger.info("=" * 80)

                result = sync_pending_order_totals_from_ps365(triggered_by="scheduler")

                ok = bool(isinstance(result, dict) and result.get('success'))
                logger.info("=" * 80)
                logger.info(f"SCHEDULED PENDING ORDERS SYNC {'COMPLETED' if ok else 'FAILED'}")
                logger.info(f"Result: {result}")
                logger.info("=" * 80)
                if not ok:
                    err = result.get('error') if isinstance(result, dict) else str(result)
                    # Surface to _tracked as FAILED rather than swallowing.
                    raise RuntimeError(f"Pending orders sync failed: {err}")
            finally:
                release_sync_lock(JOB_NAME)
    except JobSkipped:
        raise
    except Exception as e:
        err_msg = str(e).lower()
        if any(p in err_msg for p in ('timed out', 'timeout', 'max retries exceeded', 'connectionerror')):
            logger.warning(f"Pending orders sync skipped — PS365 temporarily unavailable: {str(e)[:150]}")
            raise JobSkipped(f"PS365 temporarily unavailable: {str(e)[:150]}")
        logger.error(f"Error in scheduled pending orders sync: {str(e)}", exc_info=True)
        # Re-raise so _tracked records this tick as FAILED, not SUCCESS.
        raise


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
                raise JobSkipped("no PENDING_RETRY payments to process")

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
    except JobSkipped:
        raise
    except Exception as e:
        logger.error(f"Error in _retry_pending_payments: {str(e)}")
        # Re-raise so _tracked records this tick as FAILED, not SUCCESS.
        raise


def _run_ftp_login_sync():
    try:
        from services.ftp_login_sync import sync_login_logs_from_ftp
        logger.info("=" * 60)
        logger.info("STARTING FTP LOGIN LOGS SYNC")
        logger.info("=" * 60)
        result = sync_login_logs_from_ftp()
        ok = bool(isinstance(result, dict) and result.get('success'))
        logger.info(f"FTP LOGIN SYNC {'COMPLETED' if ok else 'FAILED'}: {result}")
        if not ok:
            # Result indicates failure — surface to _tracked as FAILED.
            err = result.get('error') if isinstance(result, dict) else str(result)
            raise RuntimeError(f"FTP login sync failed: {err}")
    except Exception as e:
        logger.error(f"Error in FTP login sync: {str(e)}", exc_info=True)
        # Re-raise so _tracked records this tick as FAILED, not SUCCESS.
        raise


def _run_ftp_price_master_sync():
    try:
        from app import app
        from services.crm_price_offers import sync_price_master_from_ftp
        logger.info("=" * 60)
        logger.info("STARTING FTP PRICE MASTER SYNC")
        logger.info("=" * 60)
        with app.app_context():
            result = sync_price_master_from_ftp()
        ok = bool(isinstance(result, dict) and result.get('success'))
        logger.info(f"FTP PRICE MASTER SYNC {'COMPLETED' if ok else 'FAILED'}: {result}")
        if not ok:
            err = result.get('error') if isinstance(result, dict) else str(result)
            raise RuntimeError(f"FTP price master sync failed: {err}")
    except Exception as e:
        logger.error(f"Error in FTP price master sync: {str(e)}", exc_info=True)
        # Re-raise so _tracked records this tick as FAILED, not SUCCESS.
        raise


def _run_offers_update():
    """Daily refresh of customer price offers from the FTP price master.

    Pulls the latest customer_price_master CSV from FTP, parses it,
    upserts the per-customer offer rows, and rebuilds the per-customer
    offer summary KPIs (offer_sales_share_pct, top_rule, etc.) used
    across the CRM dashboard.

    Uses the same DB-backed lock as the manual UI refresh button so a
    cron run cannot collide with a user-triggered refresh (and vice
    versa). The lock auto-expires after 15 minutes to prevent a crashed
    worker from leaving it stuck. Releases the lock in finally so the
    next run is never blocked by the previous failure.
    """
    from app import app
    from services.crm_price_offers import (
        sync_price_master_from_ftp,
        acquire_price_offers_lock,
        release_price_offers_lock,
    )

    with app.app_context():
        logger.info("=" * 60)
        logger.info("STARTING SCHEDULED OFFERS UPDATE")
        logger.info(f"Timestamp: {datetime.utcnow().isoformat()}")
        logger.info("=" * 60)

        if not acquire_price_offers_lock("scheduler"):
            logger.warning(
                "OFFERS UPDATE skipped: another refresh is already in "
                "progress (lock held by manual UI or previous cron). "
                "Lock auto-expires after 15 minutes."
            )
            raise JobSkipped("offers refresh lock already held")

        try:
            logger.info("OFFERS UPDATE step 1/2: downloading FTP price master and importing CSV...")
            result = sync_price_master_from_ftp()

            if isinstance(result, dict) and result.get("success"):
                logger.info(
                    "OFFERS UPDATE COMPLETED: "
                    f"raw_imported={result.get('raw_imported', '?')}, "
                    f"customers_with_offers={result.get('customers_with_offers', '?')}, "
                    f"offers_inserted={result.get('offers_inserted', '?')}, "
                    f"batch_id={result.get('batch_id', '?')}"
                )
            else:
                err = result.get("error") if isinstance(result, dict) else str(result)
                logger.error(f"OFFERS UPDATE FAILED: {err}")
                # Surface to _tracked as FAILED rather than swallowing.
                raise RuntimeError(f"Offers update failed: {err}")
        except Exception as e:
            logger.error(f"Error in scheduled offers update: {str(e)}", exc_info=True)
            # Re-raise so _tracked records this tick as FAILED, not SUCCESS
            # (after the finally: lock-release runs).
            raise
        finally:
            try:
                release_price_offers_lock()
                logger.info("OFFERS UPDATE: lock released")
            except Exception as e:
                logger.warning(f"Could not release offers update lock (will auto-expire): {e}")


def _run_erp_item_cost_refresh():
    """Daily refresh of cost_price in ps_items_dw from the PS365 ERP Item Catalogue.

    Runs the same Playwright export flow that the Stock Dashboard's
    'Update Costs' button triggers, then imports the XLSX. Only items
    whose cost actually changes get cost_price_updated_at bumped.
    """
    try:
        from app import app
        from services.erp_export_bot import check_concurrent_run, run_export_sync
        logger.info("=" * 60)
        logger.info("STARTING SCHEDULED ERP ITEM COST REFRESH")
        logger.info("=" * 60)
        with app.app_context():
            if check_concurrent_run('item_catalogue'):
                logger.warning(
                    "ERP ITEM COST REFRESH skipped: another item_catalogue export is already running"
                )
                raise JobSkipped("item_catalogue export already in progress")
            result = run_export_sync('item_catalogue', triggered_by='scheduler')
            if result.get('status') == 'success':
                post = result.get('post_process', {}) or {}
                logger.info(
                    "ERP ITEM COST REFRESH COMPLETED: "
                    f"{post.get('items_updated', 0)} changed, "
                    f"{post.get('items_unchanged', 0)} unchanged, "
                    f"{post.get('items_not_found', 0)} not in DW, "
                    f"{post.get('items_skipped', 0)} skipped"
                )
            else:
                err = result.get('error_message', 'unknown error')
                logger.error(f"ERP ITEM COST REFRESH FAILED: {err}")
                # Surface to _tracked as FAILED rather than swallowing.
                raise RuntimeError(f"ERP item cost refresh failed: {err}")
    except JobSkipped:
        raise
    except Exception as e:
        logger.error(f"Error in scheduled ERP item cost refresh: {str(e)}", exc_info=True)
        # Re-raise so _tracked records this tick as FAILED, not SUCCESS.
        raise


def _run_expiry_ftp_upload():
    try:
        from app import app
        from services.expiry_ftp_upload import run_expiry_dates_export_and_upload
        logger.info("=" * 60)
        logger.info("STARTING SCHEDULED EXPIRY DATES EXPORT AND FTP UPLOAD")
        logger.info("=" * 60)
        with app.app_context():
            result = run_expiry_dates_export_and_upload()
            csv_state = result.get('csv_export')
            ftp_state = result.get('ftp_upload')
            details = result.get('details') or {}
            logger.info(
                "EXPIRY DATES EXPORT AND FTP UPLOAD COMPLETED: "
                f"csv_export={csv_state}, ftp_upload={ftp_state}"
            )
            if details:
                logger.info(f"Details: {details}")
            # The upstream service reports failure-as-data instead of
            # raising. Surface either failure to _tracked so the
            # job_runs row is marked FAILED, not SUCCESS.
            if csv_state != "success":
                raise RuntimeError(
                    f"Expiry CSV export failed: {details.get('csv_error') or 'unknown error'}"
                )
            if ftp_state != "success":
                raise RuntimeError(
                    f"Expiry FTP upload failed: {details.get('ftp_error') or 'unknown error'}"
                )
    except Exception as e:
        logger.error(f"Error in scheduled expiry FTP upload: {str(e)}", exc_info=True)
        # Re-raise so _tracked records this tick as FAILED, not SUCCESS.
        raise


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

            ok = bool(isinstance(result, dict) and result.get('success'))
            logger.info("=" * 80)
            logger.info(
                f"SCHEDULED PS365 STOCK 777 SYNC "
                f"{'COMPLETED' if ok else 'FAILED'}"
            )
            logger.info(f"Result: {result}")
            logger.info("=" * 80)
            if not ok:
                err = result.get('error') if isinstance(result, dict) else str(result)
                # Surface to _tracked as FAILED rather than swallowing.
                raise RuntimeError(f"Stock 777 sync failed: {err}")
    except Exception as e:
        logger.error(f"Error in scheduled stock 777 sync: {str(e)}", exc_info=True)
        # Re-raise so _tracked records this tick as FAILED, not SUCCESS.
        raise


def _run_stock_777_catch_up_on_startup():
    # Note: this helper is invoked directly during boot (not via _tracked),
    # so JobSkipped wouldn't be observed by `_tracked` here. We keep the
    # plain log + return shape for parity with how the boot-path call site
    # consumes the result.
    try:
        from app import app, db
        from sqlalchemy import text
        with app.app_context():
            latest_run = db.session.execute(text("""
                SELECT MAX(started_at)
                FROM ps365_stock_777_runs
                WHERE started_at::date = CURRENT_DATE
            """)).scalar()
            if latest_run:
                logger.info("Stock 777 catch-up skipped: already ran today")
                return
            logger.info("Stock 777 catch-up triggered on startup")
            _run_stock_777_sync()
    except Exception as e:
        logger.error(f"Error in stock 777 catch-up on startup: {str(e)}", exc_info=True)


def list_scheduled_jobs():
    """Get list of all scheduled jobs."""
    global scheduler
    
    if scheduler:
        jobs = []
        for job in scheduler.get_jobs():
            jobs.append({
                'id': job.id,
                'name': job.name,
                'trigger': str(job.trigger),
                'next_run': job.next_run_time.isoformat() if job.next_run_time else None
            })
        return jobs
    
    is_production = os.environ.get("REPLIT_ENVIRONMENT") == "production" or os.environ.get("REPLIT_DEPLOYMENT") == "1"
    if is_production or os.environ.get("ENABLE_BACKGROUND_JOBS") == "true":
        return [
            {'id': 'full_dw_sync', 'name': 'Full Data Warehouse Sync', 'trigger': 'Daily at 3:00 AM', 'next_run': None},
            {'id': 'incremental_dw_sync', 'name': 'Incremental Data Warehouse Sync', 'trigger': 'Daily at 1:00 AM and 1:00 PM', 'next_run': None},
            {'id': 'customer_sync', 'name': 'Customer Sync from PS365', 'trigger': 'Daily at 4:00 AM', 'next_run': None},
            {'id': 'invoice_sync', 'name': 'Invoice Sync from PS365', 'trigger': 'Daily at 6:00 PM', 'next_run': None},
            {'id': 'balance_fetch', 'name': 'Customer Balance Fetch from PS365', 'trigger': 'Daily at 2:30 AM', 'next_run': None},
            {'id': 'erp_item_cost_refresh', 'name': 'Cost Update', 'trigger': 'Daily at 17:55 Cairo', 'next_run': None},
            {'id': 'nightly_forecast', 'name': 'Nightly Forecast Run', 'trigger': 'Daily at 5:00 AM', 'next_run': None},
            {'id': 'pending_orders', 'name': 'PS365 Pending Orders Sync', 'trigger': 'Every 30 minutes', 'next_run': None},
            {'id': 'payment_retry', 'name': 'Retry PENDING_RETRY Payments to PS365', 'trigger': 'Every 5 minutes', 'next_run': None},
            {'id': 'expiry_ftp_upload', 'name': 'Expiry Dates FTP Upload', 'trigger': 'Daily at 9:00 PM', 'next_run': None},
            {'id': 'stock_777_sync_production', 'name': 'PS365 Stock 777 Daily Sync', 'trigger': 'Daily at 11:30 PM', 'next_run': None},
        ]
    
    return []


# ============================================================================
# Admin UI helpers (used by routes_admin_scheduler)
# ============================================================================
#
# In autoscale, only the "designated scheduler worker" (worker age=1) actually
# starts the BackgroundScheduler. Other workers leave the module-level
# `scheduler` global as None. Admin requests can land on any worker, so we
# need helpers that talk to the shared SQLAlchemy jobstore directly rather
# than the in-process scheduler. Job mutations (modify/pause/resume) write
# straight to the apscheduler_jobs table; the live scheduler picks them up
# on its next jobstore reload.

JOB_FUNCTIONS = {}

# Phase 2: human-readable display names mirrored against the job IDs in
# JOB_FUNCTIONS. `_tracked` reads from this map when invoked from `run_job_now`
# so manual "Run Now" entries get a proper job_name written into job_runs
# instead of falling back to the bare job_id.
JOB_DISPLAY_NAMES = {
    'full_dw_sync': 'Full Data Warehouse Sync',
    'incremental_dw_sync': 'Incremental Data Warehouse Sync',
    'customer_sync': 'Customer Sync from PS365',
    'invoice_sync': 'Invoice Sync from PS365',
    'balance_fetch': 'Customer Balance Fetch from PS365',
    'forecast_run': 'Nightly Forecast Run',
    'forecast_watchdog': 'Forecast Run Watchdog (auto-retry)',
    'pending_orders_sync': 'PS365 Pending Orders Sync',
    'retry_pending_payments': 'Retry PENDING_RETRY Payments to PS365',
    'erp_item_cost_refresh': 'Cost Update',
    'offers_update': 'Offers Update',
    'expiry_ftp_upload': 'Expiry Dates FTP Upload',
    'stock_777_sync_production': 'PS365 Stock 777 Daily Sync (Production)',
    'stock_777_sync': 'PS365 Stock 777 Daily Sync',
    'ftp_login_sync': 'FTP Login Logs Sync',
}


def _register_job_funcs():
    """Register the canonical job_id -> function mapping for 'Run Now'."""
    global JOB_FUNCTIONS
    JOB_FUNCTIONS = {
        'full_dw_sync': _run_full_sync,
        'incremental_dw_sync': _run_incremental_sync,
        'customer_sync': _run_customer_sync,
        'invoice_sync': _run_invoice_sync,
        'balance_fetch': _run_balance_fetch,
        'forecast_run': _run_forecast,
        'forecast_watchdog': _run_forecast_watchdog,
        'pending_orders_sync': _run_pending_orders_sync,
        'retry_pending_payments': _retry_pending_payments,
        'erp_item_cost_refresh': _run_erp_item_cost_refresh,
        'offers_update': _run_offers_update,
        'expiry_ftp_upload': _run_expiry_ftp_upload,
        'stock_777_sync_production': _run_stock_777_sync,
        'stock_777_sync': _run_stock_777_sync,
        'ftp_login_sync': _run_ftp_login_sync,
    }


class _JobstoreContext:
    """Context manager that yields a usable scheduler bound to the shared jobstore.

    APScheduler's mutation/read methods (`get_jobs`, `reschedule_job`,
    `pause_job`, `resume_job`) only work on a *started* scheduler — an
    unstarted one returns empty / raises JobLookupError because it hasn't
    populated its in-memory view from the jobstore. So:

    - On the designated scheduler worker, we yield the live module-level
      scheduler (already running, jobs already loaded).
    - On any other worker we build a proxy scheduler bound to the same
      SQLAlchemy jobstore, start it paused (loads jobs from DB without
      firing anything), let the caller use it, then shut it down on exit.
    """
    def __init__(self):
        self._proxy = None
        self._owned = False

    def __enter__(self):
        global scheduler
        if scheduler is not None and scheduler.running:
            return scheduler

        try:
            from app import db as _db, app as _app
            with _app.app_context():
                engine = _db.engine
            jobstores = {
                'default': SQLAlchemyJobStore(engine=engine, tablename='apscheduler_jobs')
            }
        except Exception:
            if not os.environ.get("DATABASE_URL"):
                raise RuntimeError("No DATABASE_URL — cannot reach scheduler jobstore")
            jobstores = {
                'default': SQLAlchemyJobStore(
                    url=os.environ["DATABASE_URL"],
                    tablename='apscheduler_jobs',
                )
            }

        self._proxy = BackgroundScheduler(daemon=True, jobstores=jobstores)
        # Start paused so the proxy reads jobs from the jobstore but never
        # fires anything (only the designated worker's scheduler should fire).
        self._proxy.start(paused=True)
        self._owned = True
        return self._proxy

    def __exit__(self, exc_type, exc, tb):
        if self._owned and self._proxy is not None:
            try:
                self._proxy.shutdown(wait=False)
            except Exception as e:
                logger.warning(f"Could not shut down jobstore proxy scheduler: {e}")
        return False


def _trigger_summary(trigger):
    """Build a human-readable string describing a CronTrigger's schedule."""
    fields = {}
    try:
        for f in getattr(trigger, 'fields', []):
            fields[f.name] = str(f)
    except Exception:
        return str(trigger)
    if not fields:
        return str(trigger)
    parts = []
    for k in ('day_of_week', 'hour', 'minute'):
        if k in fields:
            parts.append(f"{k}={fields[k]}")
    return ", ".join(parts) if parts else str(trigger)


def list_scheduled_jobs_full():
    """List all jobs from the shared jobstore with editable fields broken out.

    Works from any worker via _JobstoreContext. Each item:
        { id, name, trigger_str, hour, minute, day_of_week, next_run, paused }
    """
    out = []
    with _JobstoreContext() as sch:
        for job in sch.get_jobs():
            trigger = job.trigger
            hour = minute = day_of_week = None
            try:
                for f in trigger.fields:
                    if f.name == 'hour':
                        hour = str(f)
                    elif f.name == 'minute':
                        minute = str(f)
                    elif f.name == 'day_of_week':
                        dow = str(f)
                        if dow not in ('*', '?'):
                            day_of_week = dow
            except Exception:
                pass
            out.append({
                'id': job.id,
                'name': job.name,
                'trigger_str': _trigger_summary(trigger),
                'hour': hour,
                'minute': minute,
                'day_of_week': day_of_week,
                'next_run': job.next_run_time.isoformat() if job.next_run_time else None,
                'paused': job.next_run_time is None,
            })
    out.sort(key=lambda j: (j['next_run'] or 'zzz', j['id']))
    return out


def reschedule_job(job_id, hour, minute, day_of_week=None):
    """Reschedule a job's CronTrigger. hour/minute are strings (cron syntax OK)."""
    kwargs = {'hour': hour, 'minute': minute}
    if day_of_week:
        kwargs['day_of_week'] = day_of_week
    new_trigger = CronTrigger(**kwargs)
    with _JobstoreContext() as sch:
        sch.reschedule_job(job_id, trigger=new_trigger)
    logger.info(f"Job {job_id} rescheduled: hour={hour} minute={minute} dow={day_of_week or '*'}")


def pause_job(job_id):
    with _JobstoreContext() as sch:
        sch.pause_job(job_id)
    logger.info(f"Job {job_id} paused")


def resume_job(job_id):
    with _JobstoreContext() as sch:
        sch.resume_job(job_id)
    logger.info(f"Job {job_id} resumed")


# Per-worker guard against double-firing the same job via "Run Now". This
# is *per process* — a click on worker A and a simultaneous click on worker
# B can still both fire (rare in practice, since admin actions are bursty
# and route to one worker). Combined with the confirm() dialog and the
# in-job idempotency (every _run_* helper wraps with app.app_context()
# and writes its own log row, so a double-fire is detectable and not
# catastrophic for the data), this is enough defence for an admin tool.
import threading as _threading
_RUN_NOW_GUARD = _threading.Lock()
_RUN_NOW_IN_PROGRESS = set()


def run_job_now(job_id):
    """Invoke a job's function immediately, in a daemon thread.

    Bypasses the scheduler entirely (so this works from any worker). The
    function is responsible for its own app context / error handling — every
    _run_* helper in this module already wraps with `with app.app_context()`.

    A per-process lock prevents a double-click from launching two parallel
    instances of the same heavy job from this worker.
    """
    if not JOB_FUNCTIONS:
        _register_job_funcs()
    func = JOB_FUNCTIONS.get(job_id)
    if func is None:
        raise KeyError(f"No registered function for job_id={job_id!r}")

    with _RUN_NOW_GUARD:
        if job_id in _RUN_NOW_IN_PROGRESS:
            raise RuntimeError(
                f"Job '{job_id}' is already running via Run Now on this worker. "
                "Wait for it to finish before triggering it again."
            )
        _RUN_NOW_IN_PROGRESS.add(job_id)

    def _runner():
        try:
            # Route through `_tracked` so manual runs also emit a `job_runs`
            # row (trigger_source='manual'), giving operators the same
            # visibility on Run Now ticks as on scheduled cron ticks.
            _tracked(
                job_id,
                job_name=JOB_DISPLAY_NAMES.get(job_id, job_id),
                trigger_source='manual',
            )
        except Exception as e:
            logger.error(f"Manual 'Run Now' for {job_id} failed: {e}", exc_info=True)
        finally:
            with _RUN_NOW_GUARD:
                _RUN_NOW_IN_PROGRESS.discard(job_id)

    t = _threading.Thread(target=_runner, daemon=True, name=f"run-now:{job_id}")
    t.start()
    logger.info(f"Job {job_id} manually triggered (Run Now) by admin UI")
    return True
