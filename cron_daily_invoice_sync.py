"""
Daily invoice sync cron job.
Syncs today's invoices from PS365 at scheduled time.
Run via Replit Scheduled Deployment: python cron_daily_invoice_sync.py
"""
import os
import sys
import logging
from datetime import datetime

os.environ['TZ'] = 'Europe/Athens'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [CRON] %(levelname)s %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


def _write_cron_log(app, job_name, started_at, status, message=None):
    """Persist a cron run outcome to the database."""
    try:
        from app import db
        from models import CronRunLog
        from timezone_utils import get_utc_now
        with app.app_context():
            entry = CronRunLog(
                job_name=job_name,
                started_at=started_at,
                finished_at=get_utc_now(),
                status=status,
                message=message,
            )
            db.session.add(entry)
            db.session.commit()
    except Exception as log_err:
        logger.error(f"Could not write cron run log: {log_err}")


def main():
    logger.info("=" * 60)
    logger.info("DAILY INVOICE SYNC CRON - STARTED")
    logger.info("=" * 60)

    from app import app, db
    from datawarehouse_sync import sync_invoices_from_date
    from timezone_utils import get_utc_now

    JOB_NAME = 'daily_invoice_sync'
    started_at = get_utc_now()
    today = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"Syncing invoices for date: {today}")

    with app.app_context():
        try:
            result = sync_invoices_from_date(db.session, today, today)
            h = result.get("headers_inserted", 0)
            l = result.get("lines_inserted", 0)
            s = result.get("stores_inserted", 0)
            u = result.get("cashiers_inserted", 0)
            summary = (
                f"Synced {today} — "
                f"headers: {h}, lines: {l}, stores: {s}, cashiers: {u}"
            )
            logger.info(f"Daily invoice sync completed: {summary}")
            _write_cron_log(app, JOB_NAME, started_at, 'success', summary)
        except Exception as e:
            logger.error(f"Daily invoice sync FAILED: {e}", exc_info=True)
            _write_cron_log(app, JOB_NAME, started_at, 'failed', str(e)[:1000])
            sys.exit(1)

    logger.info("=" * 60)
    logger.info("DAILY INVOICE SYNC CRON - FINISHED")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
