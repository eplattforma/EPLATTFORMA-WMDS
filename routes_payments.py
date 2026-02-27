import logging
from flask import Blueprint, request, jsonify, abort
from flask_login import login_required, current_user

from app import db
from models import PaymentEntry, RouteStop, RouteStopInvoice, Invoice
from services.payments import upsert_active_payment, commit_to_ps365, get_active_payment

logger = logging.getLogger(__name__)

payments_bp = Blueprint('payments', __name__)


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
    if pe.ps_status not in ('FAILED', 'NEW'):
        return jsonify({'error': 'Only FAILED or NEW payments can be retried'}), 400

    stop, invoice_nos, customer_code = _get_stop_context(pe.route_stop_id)

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
    pe = get_active_payment(stop_id)
    if not pe:
        return jsonify(None), 200
    return jsonify(pe.to_dict()), 200
