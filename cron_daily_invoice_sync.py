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

def main():
    logger.info("=" * 60)
    logger.info("DAILY INVOICE SYNC CRON - STARTED")
    logger.info("=" * 60)

    from app import app, db
    from datawarehouse_sync import sync_invoices_from_date

    today = datetime.now().strftime("%Y-%m-%d")
    logger.info(f"Syncing invoices for date: {today}")

    with app.app_context():
        try:
            sync_invoices_from_date(db.session, today, today)
            logger.info("Daily invoice sync completed successfully")
        except Exception as e:
            logger.error(f"Daily invoice sync FAILED: {e}", exc_info=True)
            sys.exit(1)

    logger.info("=" * 60)
    logger.info("DAILY INVOICE SYNC CRON - FINISHED")
    logger.info("=" * 60)

if __name__ == "__main__":
    main()
