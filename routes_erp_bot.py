import os
import logging
import threading
from functools import wraps
from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, abort
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)

erp_bot_bp = Blueprint('erp_bot', __name__, url_prefix='/admin/erp-bot')


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
    if not token:
        abort(400, 'Missing CSRF token')


@erp_bot_bp.route('/')
@admin_required
def erp_bot_dashboard():
    from app import db
    from models import BotRunLog
    from services.erp_export_flows import list_flows

    flows = list_flows()
    recent_runs = db.session.query(BotRunLog).order_by(
        BotRunLog.started_at.desc()
    ).limit(30).all()

    config_status = {
        'base_url': bool(os.environ.get('ERP_BASE_URL')),
        'username': bool(os.environ.get('ERP_USERNAME')),
        'password': bool(os.environ.get('ERP_PASSWORD')),
    }
    all_configured = all(config_status.values())

    return render_template(
        'admin_tools/erp_bot_dashboard.html',
        flows=flows,
        recent_runs=recent_runs,
        config_status=config_status,
        all_configured=all_configured,
    )


@erp_bot_bp.route('/run', methods=['POST'])
@admin_required
def erp_bot_run():
    _validate_csrf()
    export_name = request.form.get('export_name', '').strip()
    if not export_name:
        flash('Please select an export flow.', 'warning')
        return redirect(url_for('erp_bot.erp_bot_dashboard'))

    from services.erp_export_bot import check_concurrent_run
    if check_concurrent_run(export_name):
        flash(f'Export "{export_name}" is already running.', 'warning')
        return redirect(url_for('erp_bot.erp_bot_dashboard'))

    from services.erp_export_bot import run_export_sync

    def _run_in_background():
        try:
            run_export_sync(export_name, triggered_by='manual')
        except Exception as e:
            logger.error(f"Background ERP export failed: {e}", exc_info=True)

    t = threading.Thread(target=_run_in_background, daemon=True)
    t.start()

    flash(f'Export "{export_name}" started. Refresh to see progress.', 'info')
    return redirect(url_for('erp_bot.erp_bot_dashboard'))


@erp_bot_bp.route('/run/<int:run_id>')
@admin_required
def erp_bot_run_detail(run_id):
    from app import db
    from models import BotRunLog

    run = db.session.query(BotRunLog).get(run_id)
    if not run:
        flash('Run not found.', 'danger')
        return redirect(url_for('erp_bot.erp_bot_dashboard'))

    return render_template('admin_tools/erp_bot_run_detail.html', run=run)


@erp_bot_bp.route('/screenshot/<int:run_id>')
@admin_required
def erp_bot_screenshot(run_id):
    from app import db
    from models import BotRunLog

    run = db.session.query(BotRunLog).get(run_id)
    if not run or not run.screenshot_path:
        abort(404)

    if os.path.exists(run.screenshot_path):
        return send_file(run.screenshot_path, mimetype='image/png')
    abort(404)


@erp_bot_bp.route('/refresh-stock-positions', methods=['POST'])
@login_required
def erp_refresh_stock_positions():
    if current_user.role not in ['admin', 'warehouse_manager']:
        return {'success': False, 'error': 'Access denied'}, 403

    from services.erp_export_bot import check_concurrent_run, run_export_sync

    if check_concurrent_run('stock_position'):
        return {'success': False, 'error': 'Stock position export is already running'}, 409

    try:
        result = run_export_sync('stock_position', triggered_by=f'manual:{current_user.username}')

        if result.get('status') == 'success':
            post = result.get('post_process', {})
            return {
                'success': True,
                'message': f"Imported {post.get('records_imported', 0):,} stock records from ERP",
                'count': post.get('records_imported', 0),
                'file_name': result.get('file_name'),
                'file_size': result.get('file_size'),
            }
        else:
            return {
                'success': False,
                'error': result.get('error_message', 'Export failed'),
            }, 500
    except Exception as e:
        logger.error(f"ERP stock position refresh failed: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}, 500


@erp_bot_bp.route('/refresh-item-costs', methods=['POST'])
@login_required
def erp_refresh_item_costs():
    if current_user.role not in ['admin', 'warehouse_manager']:
        return {'success': False, 'error': 'Access denied'}, 403

    from services.erp_export_bot import check_concurrent_run, run_export_sync

    if check_concurrent_run('item_catalogue'):
        return {'success': False, 'error': 'Item catalogue export is already running'}, 409

    try:
        result = run_export_sync('item_catalogue', triggered_by=f'manual:{current_user.username}')

        if result.get('status') == 'success':
            post = result.get('post_process', {})
            return {
                'success': True,
                'message': (
                    f"Updated {post.get('items_updated', 0):,} item costs from ERP"
                    f" ({post.get('items_not_found', 0)} not in DW,"
                    f" {post.get('items_skipped', 0)} skipped)"
                ),
                'items_updated': post.get('items_updated', 0),
                'items_not_found': post.get('items_not_found', 0),
                'file_name': result.get('file_name'),
                'file_size': result.get('file_size'),
            }
        else:
            return {
                'success': False,
                'error': result.get('error_message', 'Export failed'),
            }, 500
    except Exception as e:
        logger.error(f"ERP item cost refresh failed: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}, 500


@erp_bot_bp.route('/download/<int:run_id>')
@admin_required
def erp_bot_download(run_id):
    from app import db
    from models import BotRunLog

    run = db.session.query(BotRunLog).get(run_id)
    if not run or not run.file_path:
        abort(404)

    if os.path.exists(run.file_path):
        return send_file(run.file_path, as_attachment=True, download_name=run.file_name)
    abort(404)
