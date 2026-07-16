import logging
from flask import Blueprint, request, jsonify, abort
from flask_login import login_required, current_user

from app import db
from models import PaymentEntry, RouteStop, RouteStopInvoice, Invoice, CODReceipt
from services.payments import upsert_active_payment, commit_to_ps365, get_active_payment

logger = logging.getLogger(__name__)

payments_bp = Blueprint('payments', __name__)


def _authorize_stop_access(stop):
    """Only the driver assigned to this stop's route (or admin/warehouse
    manager) may read or modify its payment. Prevents cross-driver IDOR."""
    if current_user.role in ('admin', 'warehouse_manager'):
        return None
    shipment = stop.shipment
    if current_user.role == 'driver' and shipment and shipment.driver_name == current_user.username:
        return None
    return jsonify({'error': 'Not authorized for this route stop'}), 403


def _get_stop_context(stop_id):
    stop = RouteStop.query.get_or_404(stop_id)
    rsis = RouteStopInvoice.query.filter_by(route_stop_id=stop_id, is_active=True).all()
    invoice_nos = [r.invoice_no for r in rsis]
    customer_code = stop.customer_code or ''
    if not customer_code and invoice_nos:
        inv = Invoice.query.get(invoice_nos[0])
        if inv:
            customer_code = inv.customer_code_365 or ''
    return stop, invoice_nos, customer_code


@payments_bp.route('/api/route-stops/<int:stop_id>/payment', methods=['POST'])
@login_required
def create_payment(stop_id):
    stop, invoice_nos, customer_code = _get_stop_context(stop_id)
    denied = _authorize_stop_access(stop)
    if denied:
        return denied

    # Freeze on print: once a receipt for this stop has been printed, no driver-side changes
    printed_receipt = CODReceipt.query.filter(
        CODReceipt.route_stop_id == stop_id,
        CODReceipt.status != 'VOIDED',
        CODReceipt.first_printed_at.isnot(None)
    ).first()
    if printed_receipt:
        return jsonify({
            'error': 'Receipt already printed. To change it, request a cancellation from the office.',
            'receipt_locked': True,
            'receipt_id': printed_receipt.id,
            'print_count': printed_receipt.print_count or 0,
        }), 409

    existing = get_active_payment(stop_id)
    if existing and existing.ps_status == 'SUCCESS':
        return jsonify({'error': 'Payment already synced to PS365. Cannot change a committed receipt.'}), 409

    payload = request.get_json(silent=True) or {}
    method = (payload.get('method') or '').strip().lower()
    if method not in ('cash', 'cheque', 'online', 'card'):
        return jsonify({'error': 'Invalid payment method'}), 400

    try:
        pe = upsert_active_payment(stop_id, payload)
        pe = commit_to_ps365(pe, customer_code, invoice_nos, current_user.username)
        db.session.commit()
        return jsonify(pe.to_dict()), 200
    except Exception as exc:
        db.session.rollback()
        logger.error(f"create_payment error for stop {stop_id}: {exc}")
        return jsonify({'error': str(exc)}), 500


@payments_bp.route('/api/payments/<int:pe_id>/retry', methods=['POST'])
@login_required
def retry_payment(pe_id):
    pe = PaymentEntry.query.get_or_404(pe_id)
    if pe.ps_status not in ('FAILED', 'NEW', 'PENDING_RETRY'):
        return jsonify({'error': 'Only FAILED, NEW, or PENDING_RETRY payments can be retried'}), 400

    stop, invoice_nos, customer_code = _get_stop_context(pe.route_stop_id)
    denied = _authorize_stop_access(stop)
    if denied:
        return denied

    try:
        pe = commit_to_ps365(pe, customer_code, invoice_nos, current_user.username)
        db.session.commit()
        return jsonify(pe.to_dict()), 200
    except Exception as exc:
        db.session.rollback()
        logger.error(f"retry_payment error for pe {pe_id}: {exc}")
        return jsonify({'error': str(exc)}), 500


@payments_bp.route('/api/route-stops/<int:stop_id>/payment', methods=['GET'])
@login_required
def get_payment(stop_id):
    stop = RouteStop.query.get_or_404(stop_id)
    denied = _authorize_stop_access(stop)
    if denied:
        return denied
    pe = get_active_payment(stop_id)
    printed_receipt = CODReceipt.query.filter(
        CODReceipt.route_stop_id == stop_id,
        CODReceipt.status != 'VOIDED',
        CODReceipt.first_printed_at.isnot(None)
    ).first()
    if not pe:
        if printed_receipt:
            return jsonify({'receipt_locked': True,
                            'receipt_id': printed_receipt.id,
                            'print_count': printed_receipt.print_count or 0}), 200
        return jsonify(None), 200
    out = pe.to_dict()
    if printed_receipt:
        out['receipt_locked'] = True
        out['locked_receipt_id'] = printed_receipt.id
        out['locked_print_count'] = printed_receipt.print_count or 0
    return jsonify(out), 200
