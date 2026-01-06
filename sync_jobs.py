"""
Background sync job system for long-running import operations.
This module provides async job execution for date-based invoice imports.
"""

import logging
import threading
from datetime import datetime
from typing import Optional, Dict, Any
from app import db, app
from timezone_utils import utc_now_for_db

logger = logging.getLogger(__name__)

# In-memory job tracking (could be upgraded to Redis for production scaling)
_active_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()


class SyncJob(db.Model):
    """Track sync job executions for observability and debugging."""
    __tablename__ = 'sync_jobs'
    
    id = db.Column(db.String(50), primary_key=True)
    job_type = db.Column(db.String(50), nullable=False)  # 'invoice_date_sync', 'invoice_single_sync'
    params = db.Column(db.Text, nullable=True)  # JSON params
    status = db.Column(db.String(20), default='pending')  # pending, running, completed, failed
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    created_by = db.Column(db.String(64), nullable=True)
    
    # Results
    success = db.Column(db.Boolean, nullable=True)
    invoices_created = db.Column(db.Integer, default=0)
    invoices_updated = db.Column(db.Integer, default=0)
    items_created = db.Column(db.Integer, default=0)
    items_updated = db.Column(db.Integer, default=0)
    error_count = db.Column(db.Integer, default=0)
    error_message = db.Column(db.Text, nullable=True)
    
    # Progress tracking
    progress_current = db.Column(db.Integer, default=0)
    progress_total = db.Column(db.Integer, default=0)
    progress_message = db.Column(db.String(255), nullable=True)


def generate_job_id() -> str:
    """Generate a unique job ID."""
    import uuid
    return f"sync_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def create_sync_job(job_type: str, params: dict, created_by: str = None) -> SyncJob:
    """Create a new sync job record."""
    import json
    
    job = SyncJob(
        id=generate_job_id(),
        job_type=job_type,
        params=json.dumps(params),
        status='pending',
        created_by=created_by
    )
    db.session.add(job)
    db.session.commit()
    return job


def get_job_status(job_id: str) -> Optional[Dict[str, Any]]:
    """Get the current status of a job."""
    job = SyncJob.query.get(job_id)
    if not job:
        return None
    
    return {
        'id': job.id,
        'job_type': job.job_type,
        'status': job.status,
        'started_at': job.started_at.isoformat() if job.started_at else None,
        'finished_at': job.finished_at.isoformat() if job.finished_at else None,
        'success': job.success,
        'invoices_created': job.invoices_created,
        'invoices_updated': job.invoices_updated,
        'items_created': job.items_created,
        'items_updated': job.items_updated,
        'error_count': job.error_count,
        'error_message': job.error_message,
        'progress_current': job.progress_current,
        'progress_total': job.progress_total,
        'progress_message': job.progress_message
    }


def run_date_sync_job(job_id: str, import_date: str):
    """Execute the date sync job in a background thread with isolated session."""
    from sqlalchemy.orm import scoped_session, sessionmaker
    
    with app.app_context():
        # Create an isolated session for this background thread INSIDE app context
        # This prevents corrupting the shared Flask-SQLAlchemy session
        Session = scoped_session(sessionmaker(bind=db.engine))
        session = Session()
        
        try:
            job = session.query(SyncJob).get(job_id)
            if not job:
                logger.error(f"Job {job_id} not found")
                return
            
            # Update job status to running
            job.status = 'running'
            job.started_at = utc_now_for_db()
            job.progress_message = f"Starting sync for date {import_date}..."
            session.commit()
            
            # Import the sync function
            from services_powersoft import sync_invoices_from_ps365
            
            # Execute the sync with extended timeouts for date imports
            logger.info(f"Job {job_id}: Starting date sync for {import_date}")
            
            result = sync_invoices_from_ps365(invoice_no_365=None, import_date=import_date)
            
            # Refresh job from isolated session after sync (sync uses its own session)
            session.rollback()  # Clear any stale state
            job = session.query(SyncJob).get(job_id)
            
            # Update job with results
            job.finished_at = utc_now_for_db()
            job.success = result.get('success', False)
            job.invoices_created = result.get('invoices_created', 0)
            job.invoices_updated = result.get('invoices_updated', 0)
            job.items_created = result.get('items_created', 0)
            job.items_updated = result.get('items_updated', 0)
            job.error_count = result.get('import_errors', 0)
            job.error_message = result.get('error', None)
            job.status = 'completed' if job.success else 'failed'
            job.progress_message = f"Completed: {job.invoices_created} created, {job.invoices_updated} updated"
            
            session.commit()
            logger.info(f"Job {job_id}: Completed with success={job.success}")
            
        except Exception as e:
            logger.error(f"Job {job_id} failed with exception: {str(e)}", exc_info=True)
            try:
                session.rollback()
                job = session.query(SyncJob).get(job_id)
                if job:
                    job.finished_at = utc_now_for_db()
                    job.success = False
                    job.status = 'failed'
                    job.error_message = str(e)
                    job.progress_message = f"Failed: {str(e)[:100]}"
                    session.commit()
            except Exception as inner_e:
                logger.error(f"Failed to update job status: {str(inner_e)}")
        finally:
            # Always clean up the isolated session
            try:
                session.close()
                Session.remove()
            except Exception as cleanup_e:
                logger.error(f"Session cleanup error: {str(cleanup_e)}")


def start_date_sync_async(import_date: str, created_by: str = None) -> Dict[str, Any]:
    """Start an async date sync job and return immediately."""
    
    # Create job record
    job = create_sync_job(
        job_type='invoice_date_sync',
        params={'import_date': import_date},
        created_by=created_by
    )
    
    # Start background thread
    thread = threading.Thread(
        target=run_date_sync_job,
        args=(job.id, import_date),
        daemon=True
    )
    thread.start()
    
    logger.info(f"Started async date sync job {job.id} for date {import_date}")
    
    return {
        'success': True,
        'job_id': job.id,
        'message': f'Sync job started for date {import_date}. Poll /api/jobs/{job.id} for status.'
    }


def get_recent_jobs(limit: int = 20) -> list:
    """Get recent sync jobs for monitoring."""
    jobs = SyncJob.query.order_by(SyncJob.started_at.desc()).limit(limit).all()
    return [get_job_status(job.id) for job in jobs]
