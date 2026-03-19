"""
Dropbox cost import job — reads items.xlsx from Dropbox, updates cost_price in ps_items_dw.

Usage:
    python -m jobs.dropbox_sync

Exit codes:
    0  — import completed (success or unchanged)
    1  — import failed (check logs / import history)
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
            md = log.metadata_json or {}
            if log.status == 'success_no_change':
                logger.info(f"Cost import: file unchanged — no update needed ({duration:.1f}s)")
            elif log.status == 'skipped_concurrent':
                logger.info(f"Cost import: skipped — another import already running ({duration:.1f}s)")
            else:
                logger.info(
                    f"Cost import complete in {duration:.1f}s: "
                    f"read={md.get('rows_read', 0)}, matched={md.get('rows_matched', 0)}, "
                    f"updated={log.rows_imported}, unmatched={md.get('unmatched_count', 0)}"
                )
            return 0
        except Exception as e:
            duration = time.time() - start
            logger.error(f"Cost import failed after {duration:.1f}s: {e}")
            return 1


if __name__ == '__main__':
    sys.exit(run())
