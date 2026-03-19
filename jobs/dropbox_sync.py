"""
Dropbox file sync job — callable from CLI or scheduled deployment.

Usage:
    python -m jobs.dropbox_sync

Exit codes:
    0  — sync completed successfully
    1  — sync failed (check logs/sync history)
"""
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


def run():
    from app import app
    with app.app_context():
        from services.dropbox_service import sync_dropbox_file
        try:
            log = sync_dropbox_file()
            logger.info(f"Sync completed: status={log.status}, rows={log.rows_imported}")
            return 0
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            return 1


if __name__ == '__main__':
    sys.exit(run())
