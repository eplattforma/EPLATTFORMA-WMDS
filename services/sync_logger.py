import time
import logging
from datetime import datetime
from timezone_utils import utc_now_for_db

logger = logging.getLogger(__name__)


def start_sync_log(session, sync_type, trigger='manual'):
    from models import PS365SyncLog
    log_entry = PS365SyncLog(
        sync_type=sync_type,
        trigger=trigger,
        status='RUNNING',
        started_at=utc_now_for_db(),
    )
    session.add(log_entry)
    session.commit()
    return log_entry


def finish_sync_log(session, log_entry, items_found=0, items_inserted=0,
                    items_updated=0, items_skipped=0, details=None):
    now = utc_now_for_db()
    log_entry.status = 'SUCCESS'
    log_entry.finished_at = now
    if log_entry.started_at:
        log_entry.duration_seconds = round((now - log_entry.started_at).total_seconds(), 1)
    log_entry.items_found = items_found
    log_entry.items_inserted = items_inserted
    log_entry.items_updated = items_updated
    log_entry.items_skipped = items_skipped
    log_entry.details = details
    try:
        session.commit()
    except Exception as e:
        logger.error(f"Error saving sync log: {e}")
        session.rollback()


def fail_sync_log(session, log_entry, error_message):
    now = utc_now_for_db()
    log_entry.status = 'FAILED'
    log_entry.finished_at = now
    if log_entry.started_at:
        log_entry.duration_seconds = round((now - log_entry.started_at).total_seconds(), 1)
    log_entry.error_message = str(error_message)[:2000]
    try:
        session.commit()
    except Exception as e:
        logger.error(f"Error saving sync log failure: {e}")
        session.rollback()
