"""
Dropbox file sync job — callable from CLI or scheduled deployment.

Usage:
    python -m jobs.dropbox_sync

Exit codes:
    0  — sync completed (success or unchanged)
    1  — sync failed (check logs / sync history)
"""
import sys
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


def run():
    start = time.time()
    from app import app
    with app.app_context():
        from services.dropbox_service import sync_dropbox_file
        try:
            log = sync_dropbox_file()
            duration = time.time() - start
            if log.status == 'success_no_change':
                logger.info(f"Sync complete: file unchanged — no import needed ({duration:.1f}s)")
            elif log.status == 'skipped_concurrent':
                logger.info(f"Sync skipped: another sync already running ({duration:.1f}s)")
            else:
                logger.info(f"Sync complete: {log.rows_imported:,} rows imported in {duration:.1f}s (rev: {log.file_revision or 'n/a'})")
            return 0
        except Exception as e:
            duration = time.time() - start
            logger.error(f"Sync failed after {duration:.1f}s: {e}")
            return 1


if __name__ == '__main__':
    sys.exit(run())
