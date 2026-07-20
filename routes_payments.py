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


def _apply_payment_to_receipt(receipt, pe):
    """R4: propagate a corrected PaymentEntry onto the already-created
    CODReceipt of a closed (but unprinted/unsynced) stop, and rebuild its
    invoice allocations. Never touches expected_amount, the invoice list or
    discrepancies — this is a payment correction, not a redelivery."""
    from decimal import Decimal as _D
    from models import CODInvoiceAllocation

    receipt.received_amount = pe.amount
    receipt.payment_method = pe.method
    receipt.cheque_number = pe.cheque_no
    receipt.cheque_date = pe.cheque_date
    receipt.variance = (pe.amount or _D('0')) - (receipt.expected_amount or _D('0'))
    receipt.variance_reason = pe.variance_reason
    receipt.doc_type = pe.doc_type

    # Preserve per-invoice discrepancy deductions from the existing rows
    old_allocs = CODInvoiceAllocation.query.filter_by(cod_receipt_id=receipt.id).all()
    deduct_by_invoice = {a.invoice_no: (a.deduct_amount or _D('0')) for a in old_allocs}
    for a in old_allocs:
        db.session.delete(a)
    db.session.flush()

    invoice_nos = receipt.invoice_nos or []
    if isinstance(invoice_nos, str):
        invoice_nos = [invoice_nos]
    inv_rows = []
    for invoice_no in invoice_nos:
        inv = db.session.get(Invoice, invoice_no)
        invoice_total = _D(str(inv.total_grand or 0)) if inv else _D('0')
        invoice_deduct = deduct_by_invoice.get(invoice_no, _D('0'))
        inv_rows.append({
            'invoice_no': invoice_no,
            'invoice_total': invoice_total,
            'invoice_deduct': invoice_deduct,
            'invoice_due': max(invoice_total - invoice_deduct, _D('0')),
        })
    inv_rows.sort(key=lambda r: r['invoice_due'])

    is_pending = pe.commit_mode == 'SKIP'
    remaining = pe.amount or _D('0')
    for row in inv_rows:
        if len(invoice_nos) == 1:
            invoice_received = pe.amount or _D('0')
        else:
            invoice_received = min(row['invoice_due'], remaining)
            remaining -= invoice_received
        db.session.add(CODInvoiceAllocation(
            cod_receipt_id=receipt.id,
            invoice_no=row['invoice_no'],
            route_id=receipt.route_id,
            expected_amount=row['invoice_total'],
            received_amount=invoice_received,
            deduct_amount=row['invoice_deduct'],
            payment_method=pe.method,
            is_pending=is_pending,
            cheque_number=pe.cheque_no,
            cheque_date=pe.cheque_date,
        ))
    db.session.flush()


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
    if existing and existing.ps_status == 'PENDING_RETRY':
        # Bug 3 guard: the previous attempt may have landed in PS365 (timeout
        # is not failure). Changing now could double-post under a new reference.
        return jsonify({'error': 'Previous attempt is still being confirmed — retry or wait, then change.'}), 409

    payload = request.get_json(silent=True) or {}
    method = (payload.get('method') or '').strip().lower()
    if method not in ('cash', 'cheque', 'online', 'card'):
        return jsonify({'error': 'Invalid payment method'}), 400

    # R4: edits after stop close are only allowed while the route is open.
    from models import Shipment
    route = db.session.get(Shipment, stop.shipment_id) if stop else None
    # Deterministic "live receipt" selection — must match the ordering used
    # by the driver stops list (newest first) so the record the UI shows as
    # editable is the one the API actually updates.
    live_receipt = CODReceipt.query.filter(
        CODReceipt.route_stop_id == stop_id,
        CODReceipt.status != 'VOIDED',
        CODReceipt.first_printed_at.is_(None),
        CODReceipt.ps365_reference_number.is_(None),
    ).order_by(CODReceipt.created_at.desc(), CODReceipt.id.desc()).first()
    if live_receipt and route and route.driver_submitted_at:
        return jsonify({'error': 'Route already submitted. Payments can no longer be changed.'}), 409

    try:
        pe = upsert_active_payment(stop_id, payload)
        # Deferred commit (Bug 2): PS365 posting happens at print time, not at
        # confirm. The driver keeps an edit window until the receipt prints.
        # SKIP-mode entries (online / post-dated cheque) are already SKIPPED.
        if live_receipt:
            # R4: stop was already closed — the receipt (and its allocations)
            # were snapshotted from the old payment. Update them in the same
            # transaction so the eventual print/post reflects the correction.
            _apply_payment_to_receipt(live_receipt, pe)
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
