"""
Flask routes for Route Reconciliation
Handles reconciliation lifecycle: refresh, submit, review, finalize
"""

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from functools import wraps
from decimal import Decimal
import logging

from app import db
from models import Shipment, RouteStop
import services_reconciliation as recon

logger = logging.getLogger(__name__)

reconciliation_bp = Blueprint('reconciliation', __name__, url_prefix='/reconciliation')


def admin_or_warehouse_required(f):
    """Decorator to require admin or warehouse_manager role"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        if current_user.role not in ('admin', 'warehouse_manager'):
            flash('Access denied. Admin or warehouse manager privileges required.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


@reconciliation_bp.route('/shipments')
@login_required
@admin_or_warehouse_required
def shipment_list():
    """List shipments with reconciliation status"""
    status_filter = request.args.get('status', '')
    date_filter = request.args.get('date', '')
    
    query = Shipment.query.filter(Shipment.deleted_at.is_(None))
    
    if status_filter:
        query = query.filter(Shipment.reconciliation_status == status_filter)
    
    if date_filter:
        query = query.filter(Shipment.delivery_date == date_filter)
    
    shipments = query.order_by(Shipment.delivery_date.desc(), Shipment.id.desc()).limit(100).all()
    
    return render_template('reconciliation/shipment_list.html',
                         shipments=shipments,
                         status_filter=status_filter,
                         date_filter=date_filter)


@reconciliation_bp.route('/shipments/<int:shipment_id>')
@login_required
@admin_or_warehouse_required
def shipment_detail(shipment_id):
    """Reconciliation detail page for a shipment"""
    shipment = db.session.get(Shipment, shipment_id)
    if not shipment:
        flash('Shipment not found', 'error')
        return redirect(url_for('reconciliation.shipment_list'))
    
    summary = recon.get_reconciliation_summary(shipment_id)
    stops = recon.get_stop_details(shipment_id)
    
    for stop in stops:
        stop['invoices'] = recon.get_stop_invoices(stop['route_stop_id'])
        stop['cod_receipts'] = recon.get_stop_cod_receipts(shipment_id, stop['route_stop_id'])
        stop['pod_records'] = recon.get_stop_pod_records(shipment_id, stop['route_stop_id'])
    
    issues = {
        'blocking': [],
        'warnings': []
    }
    
    missing_status = recon.check_missing_final_status(shipment_id)
    if missing_status:
        issues['blocking'].append({
            'type': 'MISSING_FINAL_STATUS',
            'message': f"{len(missing_status)} invoice(s) without final delivery status",
            'details': missing_status
        })
    
    missing_pod = recon.check_missing_pod(shipment_id)
    if missing_pod:
        issues['blocking'].append({
            'type': 'MISSING_POD',
            'message': f"{len(missing_pod)} delivered invoice(s) missing POD",
            'details': missing_pod
        })
    
    open_cases = recon.check_open_post_delivery_cases(shipment_id)
    if open_cases:
        issues['blocking'].append({
            'type': 'OPEN_CASES',
            'message': f"{len(open_cases)} open post-delivery case(s)",
            'details': open_cases
        })
    
    unresolved = recon.check_unresolved_discrepancies(shipment_id)
    if unresolved:
        issues['blocking'].append({
            'type': 'UNRESOLVED_DISCREPANCIES',
            'message': f"{len(unresolved)} unresolved discrepancy(ies)",
            'details': unresolved
        })
    
    return render_template('reconciliation/shipment_detail.html',
                         shipment=shipment,
                         summary=summary,
                         stops=stops,
                         issues=issues)


@reconciliation_bp.route('/shipments/<int:shipment_id>/exceptions')
@login_required
@admin_or_warehouse_required
def exceptions_report(shipment_id):
    """Exceptions report for a shipment"""
    shipment = db.session.get(Shipment, shipment_id)
    if not shipment:
        flash('Shipment not found', 'error')
        return redirect(url_for('reconciliation.shipment_list'))
    
    exceptions = recon.get_exceptions_report(shipment_id)
    
    return render_template('reconciliation/exceptions_report.html',
                         shipment=shipment,
                         exceptions=exceptions)


@reconciliation_bp.route('/api/shipments/<int:shipment_id>/refresh', methods=['POST'])
@login_required
@admin_or_warehouse_required
def api_refresh(shipment_id):
    """API: Refresh reconciliation status"""
    try:
        result = recon.refresh_reconciliation(shipment_id)
        return jsonify({
            'success': True,
            'blocking_count': len(result['blocking']),
            'warning_count': len(result['warnings']),
            'issues': result
        })
    except Exception as e:
        logger.error(f"Error refreshing reconciliation: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@reconciliation_bp.route('/api/shipments/<int:shipment_id>/submit', methods=['POST'])
@login_required
def api_submit(shipment_id):
    """API: Driver submits route"""
    data = request.get_json() or {}
    cash_handed_in = Decimal(str(data.get('cash_handed_in', 0)))
    notes = data.get('notes', '')
    
    try:
        result = recon.submit_route(
            shipment_id,
            current_user.username,
            cash_handed_in,
            notes
        )
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error submitting route: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@reconciliation_bp.route('/api/shipments/<int:shipment_id>/start-review', methods=['POST'])
@login_required
@admin_or_warehouse_required
def api_start_review(shipment_id):
    """API: Admin starts review"""
    try:
        result = recon.start_review(shipment_id, current_user.username)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error starting review: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@reconciliation_bp.route('/api/shipments/<int:shipment_id>/finalize', methods=['POST'])
@login_required
@admin_or_warehouse_required
def api_finalize(shipment_id):
    """API: Admin finalizes reconciliation"""
    try:
        result = recon.finalize_reconciliation(shipment_id, current_user.username)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error finalizing reconciliation: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@reconciliation_bp.route('/api/shipments/<int:shipment_id>/clear-settlement', methods=['POST'])
@login_required
@admin_or_warehouse_required
def api_clear_settlement(shipment_id):
    """API: Finance clears settlement"""
    try:
        result = recon.clear_settlement(shipment_id, current_user.username)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error clearing settlement: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@reconciliation_bp.route('/api/shipments/<int:shipment_id>/summary')
@login_required
@admin_or_warehouse_required
def api_summary(shipment_id):
    """API: Get reconciliation summary"""
    try:
        summary = recon.get_reconciliation_summary(shipment_id)
        return jsonify({'success': True, 'summary': summary})
    except Exception as e:
        logger.error(f"Error getting summary: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@reconciliation_bp.route('/reroute-audit')
@login_required
@admin_or_warehouse_required
def reroute_audit():
    """Reroute audit trail report"""
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    audit_records = []
    if date_from and date_to:
        audit_records = recon.get_reroute_audit(date_from, date_to)
    
    return render_template('reconciliation/reroute_audit.html',
                         date_from=date_from,
                         date_to=date_to,
                         records=audit_records)
