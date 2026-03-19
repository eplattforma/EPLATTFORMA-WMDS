import os
import logging
import secrets
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash, request, session, abort
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)

dropbox_bp = Blueprint('dropbox_integration', __name__, url_prefix='/admin/integrations/dropbox')


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != 'admin':
            flash('Access denied. Admin privileges required.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def _validate_csrf():
    token = request.form.get('csrf_token', '')
    session_token = session.get('csrf_token', '')
    if not token or not session_token or token != session_token:
        abort(403)


@dropbox_bp.route('')
@admin_required
def dropbox_status():
    from services.dropbox_service import get_dropbox_status
    status_filter = request.args.get('filter_status', '')
    status = get_dropbox_status()
    if status_filter:
        status['sync_history'] = [
            log for log in status['sync_history'] if log.status == status_filter
        ]
    status['filter_status'] = status_filter
    return render_template('admin_tools/dropbox_integration.html', status=status)


@dropbox_bp.route('/connect')
@admin_required
def dropbox_connect():
    from services.dropbox_service import build_dropbox_authorize_url
    try:
        state_token = secrets.token_urlsafe(32)
        session['dropbox_oauth_state'] = state_token
        auth_url = build_dropbox_authorize_url(state_token)
        logger.info("Redirecting to Dropbox for authorization")
        return redirect(auth_url)
    except ValueError as e:
        flash(f'Configuration error: {e}', 'danger')
        return redirect(url_for('dropbox_integration.dropbox_status'))


@dropbox_bp.route('/callback')
@admin_required
def dropbox_callback():
    from services.dropbox_service import exchange_code_for_tokens

    error = request.args.get('error')
    if error:
        desc = request.args.get('error_description', error)
        flash(f'Dropbox authorization denied: {desc}', 'danger')
        return redirect(url_for('dropbox_integration.dropbox_status'))

    state = request.args.get('state', '')
    expected_state = session.pop('dropbox_oauth_state', None)
    if not expected_state or state != expected_state:
        flash('Invalid OAuth state — please try connecting again.', 'danger')
        return redirect(url_for('dropbox_integration.dropbox_status'))

    code = request.args.get('code', '')
    if not code:
        flash('No authorization code received from Dropbox.', 'danger')
        return redirect(url_for('dropbox_integration.dropbox_status'))

    try:
        cred = exchange_code_for_tokens(code)
        label = cred.dropbox_email or cred.account_label or 'Dropbox account'
        flash(f'Dropbox connected successfully as {label}.', 'success')
    except Exception as e:
        logger.error(f"Dropbox callback failed: {e}")
        flash(f'Failed to connect Dropbox: {e}', 'danger')

    return redirect(url_for('dropbox_integration.dropbox_status'))


@dropbox_bp.route('/sync', methods=['POST'])
@admin_required
def dropbox_sync():
    _validate_csrf()
    from services.dropbox_service import sync_dropbox_file
    try:
        log = sync_dropbox_file()
        if log.status == 'success_no_change':
            flash('File unchanged since last sync — no import needed.', 'info')
        elif log.status == 'skipped_concurrent':
            flash('Sync skipped — another sync is already running.', 'warning')
        else:
            flash(f'Sync completed: {log.rows_imported:,} rows imported.', 'success')
    except Exception as e:
        logger.error(f"Dropbox sync error: {e}")
        flash(f'Sync failed: {e}', 'danger')
    return redirect(url_for('dropbox_integration.dropbox_status'))


@dropbox_bp.route('/disconnect', methods=['POST'])
@admin_required
def dropbox_disconnect():
    _validate_csrf()
    from services.dropbox_service import disconnect_dropbox
    try:
        disconnect_dropbox()
        flash('Dropbox disconnected. Sync history preserved.', 'info')
    except Exception as e:
        flash(f'Error disconnecting: {e}', 'danger')
    return redirect(url_for('dropbox_integration.dropbox_status'))


@dropbox_bp.route('/test', methods=['POST'])
@admin_required
def dropbox_test():
    _validate_csrf()
    from services.dropbox_service import test_dropbox_connection
    result = test_dropbox_connection()
    if result['success']:
        flash(f"Connection OK — file: {result['file_name']}, size: {result['file_size']:,} bytes, modified: {result['modified']}", 'success')
    else:
        flash(f"Connection test failed: {result['error']}", 'danger')
    return redirect(url_for('dropbox_integration.dropbox_status'))
