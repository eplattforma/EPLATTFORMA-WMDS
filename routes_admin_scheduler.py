"""
Admin UI for the background scheduler.

Lists every APScheduler job with its current schedule and next-run time, and
lets administrators reschedule, pause, resume, or trigger a job on demand.

Mutations write directly to the shared SQLAlchemy jobstore via helpers in
`scheduler.py`, so they work even when the request lands on a worker that
isn't the designated scheduler worker (only one worker runs the scheduler in
autoscale; all workers share the jobstore).
"""

import logging
from functools import wraps

from flask import (
    Blueprint, render_template, redirect, url_for, flash, request, abort
)
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)

admin_scheduler_bp = Blueprint(
    'admin_scheduler', __name__, url_prefix='/admin/scheduler'
)


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if getattr(current_user, 'role', None) != 'admin':
            flash('Access denied. Admin privileges required.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def _validate_csrf():
    """Use the project's session-based CSRF validator (see routes.py)."""
    from routes import validate_csrf_token
    if not validate_csrf_token():
        abort(400, 'Invalid or missing CSRF token')


@admin_scheduler_bp.route('/', methods=['GET'])
@admin_required
def list_jobs():
    """Render the scheduler dashboard."""
    try:
        from scheduler import list_scheduled_jobs_full
        jobs = list_scheduled_jobs_full()
        error = None
    except Exception as e:
        logger.error(f"Failed to load scheduler jobs: {e}", exc_info=True)
        jobs = []
        error = str(e)
    # csrf_token() is provided by the global context processor in routes.py
    return render_template('admin/scheduler.html', jobs=jobs, error=error)


@admin_scheduler_bp.route('/<job_id>/reschedule', methods=['POST'])
@admin_required
def reschedule(job_id):
    _validate_csrf()
    hour = (request.form.get('hour') or '').strip()
    minute = (request.form.get('minute') or '').strip()
    day_of_week = (request.form.get('day_of_week') or '').strip() or None

    if not hour or not minute:
        flash('Hour and minute are required.', 'danger')
        return redirect(url_for('admin_scheduler.list_jobs'))

    try:
        from scheduler import reschedule_job
        reschedule_job(job_id, hour=hour, minute=minute, day_of_week=day_of_week)
        flash(
            f"Rescheduled '{job_id}' to hour={hour} minute={minute}"
            + (f" day_of_week={day_of_week}" if day_of_week else "")
            + ". Note: changes only take effect after the next deployment "
              "(the in-process scheduler reloads jobs at boot).",
            'success',
        )
    except Exception as e:
        logger.error(f"Reschedule failed for {job_id}: {e}", exc_info=True)
        flash(f"Reschedule failed: {e}", 'danger')

    return redirect(url_for('admin_scheduler.list_jobs'))


@admin_scheduler_bp.route('/<job_id>/pause', methods=['POST'])
@admin_required
def pause(job_id):
    _validate_csrf()
    try:
        from scheduler import pause_job
        pause_job(job_id)
        flash(f"Paused '{job_id}'.", 'success')
    except Exception as e:
        logger.error(f"Pause failed for {job_id}: {e}", exc_info=True)
        flash(f"Pause failed: {e}", 'danger')
    return redirect(url_for('admin_scheduler.list_jobs'))


@admin_scheduler_bp.route('/<job_id>/resume', methods=['POST'])
@admin_required
def resume(job_id):
    _validate_csrf()
    try:
        from scheduler import resume_job
        resume_job(job_id)
        flash(f"Resumed '{job_id}'.", 'success')
    except Exception as e:
        logger.error(f"Resume failed for {job_id}: {e}", exc_info=True)
        flash(f"Resume failed: {e}", 'danger')
    return redirect(url_for('admin_scheduler.list_jobs'))


@admin_scheduler_bp.route('/<job_id>/run-now', methods=['POST'])
@admin_required
def run_now(job_id):
    _validate_csrf()
    try:
        from scheduler import run_job_now
        run_job_now(job_id)
        flash(
            f"'{job_id}' triggered. It runs in the background — "
            "check the relevant log table in a minute or two for results.",
            'success',
        )
    except KeyError:
        flash(f"No registered function for job '{job_id}'.", 'danger')
    except Exception as e:
        logger.error(f"Run-now failed for {job_id}: {e}", exc_info=True)
        flash(f"Run-now failed: {e}", 'danger')
    return redirect(url_for('admin_scheduler.list_jobs'))
