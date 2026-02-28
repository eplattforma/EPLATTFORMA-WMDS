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
from models import Shipment, RouteStop, CODReceipt, CODInvoiceAllocation, Invoice, RouteStopInvoice, BankTransaction
from models import utc_now
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


@reconciliation_bp.route('/pending-payments')
@login_required
@admin_or_warehouse_required
def pending_payments():
    """List all pending payments (online and post-dated)"""
    from models import CODInvoiceAllocation, Shipment, Invoice
    
    pending_allocs = db.session.query(
        CODInvoiceAllocation,
        Shipment.driver_name,
        Shipment.delivery_date,
        Invoice.customer_name,
        Invoice.customer_code
    ).join(
        Shipment, CODInvoiceAllocation.route_id == Shipment.id
    ).join(
        Invoice, CODInvoiceAllocation.invoice_no == Invoice.invoice_no
    ).filter(
        Shipment.reconciliation_status == 'RECONCILED',
        CODInvoiceAllocation.is_pending == True
    ).order_by(
        Shipment.delivery_date.asc()
    ).all()
    
    # Group by customer for better visual presentation
    from collections import OrderedDict
    grouped = OrderedDict()
    from datetime import date as date_type
    for alloc, driver_name, delivery_date, customer_name, customer_code in pending_allocs:
        due = float((alloc.expected_amount or 0) - (alloc.deduct_amount or 0))
        if due <= 0.01:
            continue
        key = customer_name or 'Unknown'
        if key not in grouped:
            grouped[key] = {
                'customer_name': customer_name,
                'customer_code': customer_code,
                'invoices': [],
                'total_due': 0
            }
        if delivery_date:
            age_days = (date_type.today() - (delivery_date if isinstance(delivery_date, date_type) else delivery_date.date())).days
        else:
            age_days = 999
        grouped[key]['invoices'].append({
            'alloc': alloc,
            'driver_name': driver_name,
            'delivery_date': delivery_date,
            'due': due,
            'age_days': age_days
        })
        grouped[key]['total_due'] += due
    
    all_alloc_ids = []
    for cust_data in grouped.values():
        for inv in cust_data['invoices']:
            all_alloc_ids.append(inv['alloc'].id)

    from services.bank_matching import get_matches_for_allocations
    bank_matches = get_matches_for_allocations(all_alloc_ids)

    active_batches = db.session.query(
        BankTransaction.batch_id,
        db.func.min(BankTransaction.uploaded_at).label('uploaded_at'),
        db.func.count(BankTransaction.id).label('total'),
        db.func.sum(db.case((BankTransaction.match_status == 'SUGGESTED', 1), else_=0)).label('suggested'),
    ).filter(
        BankTransaction.dismissed == False
    ).group_by(BankTransaction.batch_id).order_by(db.func.min(BankTransaction.uploaded_at).desc()).limit(5).all()

    return render_template('reconciliation/pending_payments.html',
                         grouped_customers=grouped,
                         today=date_type.today(),
                         bank_matches=bank_matches,
                         active_batches=active_batches)


@reconciliation_bp.route('/api/receipts/<int:receipt_id>/void', methods=['POST'])
@login_required
@admin_or_warehouse_required
def api_void_receipt(receipt_id):
    """Admin voids an ISSUED receipt so it can be reissued"""
    try:
        receipt = db.session.get(CODReceipt, receipt_id)
        if not receipt:
            return jsonify({'success': False, 'error': 'Receipt not found'}), 404

        if receipt.status == 'VOIDED':
            return jsonify({'success': False, 'error': 'Receipt is already voided'}), 400

        data = request.get_json(force=True) if request.is_json else {}
        reason = (data.get('reason') or '').strip()
        if not reason:
            return jsonify({'success': False, 'error': 'A void reason is required'}), 400

        now = utc_now()
        receipt.status = 'VOIDED'
        receipt.voided_at = now
        receipt.voided_by = current_user.username
        receipt.void_reason = reason

        db.session.commit()
        logger.info(f"Receipt {receipt_id} voided by {current_user.username}: {reason}")

        return jsonify({'success': True, 'message': f'Receipt {receipt_id} voided'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error voiding receipt {receipt_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@reconciliation_bp.route('/api/receipts/<int:receipt_id>/reissue', methods=['POST'])
@login_required
@admin_or_warehouse_required
def api_reissue_receipt(receipt_id):
    """Admin reissues a VOIDED receipt with corrected data"""
    try:
        old_receipt = db.session.get(CODReceipt, receipt_id)
        if not old_receipt:
            return jsonify({'success': False, 'error': 'Receipt not found'}), 404

        if old_receipt.status != 'VOIDED':
            return jsonify({'success': False, 'error': 'Only VOIDED receipts can be reissued'}), 400

        if old_receipt.replaced_by_cod_receipt_id:
            return jsonify({'success': False, 'error': 'Receipt has already been reissued'}), 400

        data = request.get_json(force=True)
        now = utc_now()

        new_receipt = CODReceipt(
            route_id=old_receipt.route_id,
            route_stop_id=old_receipt.route_stop_id,
            driver_username=old_receipt.driver_username,
            invoice_nos=old_receipt.invoice_nos,
            expected_amount=Decimal(str(data.get('expected_amount', old_receipt.expected_amount))),
            received_amount=Decimal(str(data.get('received_amount', old_receipt.received_amount))),
            payment_method=data.get('payment_method', old_receipt.payment_method),
            cheque_number=data.get('cheque_number', old_receipt.cheque_number),
            cheque_date=old_receipt.cheque_date,
            note=data.get('note', f'Reissued from receipt #{old_receipt.id}'),
            doc_type=data.get('doc_type', old_receipt.doc_type),
            status='DRAFT',
            created_at=now
        )
        new_receipt.variance = new_receipt.received_amount - new_receipt.expected_amount

        db.session.add(new_receipt)
        db.session.flush()

        old_receipt.replaced_by_cod_receipt_id = new_receipt.id

        old_allocs = CODInvoiceAllocation.query.filter_by(cod_receipt_id=old_receipt.id).all()
        new_method = new_receipt.payment_method or old_receipt.payment_method
        new_is_pending = new_method in ('online', 'postdated', 'post_dated')
        for alloc in old_allocs:
            alloc.cod_receipt_id = new_receipt.id
            alloc.payment_method = new_method
            alloc.received_amount = new_receipt.received_amount / max(len(old_allocs), 1) if len(old_allocs) > 1 else new_receipt.received_amount
            due = float((alloc.expected_amount or 0) - (alloc.received_amount or 0) - (alloc.deduct_amount or 0))
            alloc.is_pending = new_is_pending and due > 0.01
            if new_receipt.cheque_number:
                alloc.cheque_number = new_receipt.cheque_number
            if new_receipt.cheque_date:
                alloc.cheque_date = new_receipt.cheque_date

        db.session.commit()
        logger.info(f"Receipt {receipt_id} reissued as {new_receipt.id} by {current_user.username}, {len(old_allocs)} allocations updated")

        return jsonify({
            'success': True,
            'message': f'Receipt reissued as #{new_receipt.id}',
            'new_receipt_id': new_receipt.id
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error reissuing receipt {receipt_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@reconciliation_bp.route('/api/ps365-payment-types')
@login_required
@admin_or_warehouse_required
def api_ps365_payment_types():
    """Fetch active payment types from PS365 list_payment_types API, filtered by display_on_app=true"""
    import os, requests as req
    base = os.getenv('POWERSOFT_BASE', '') or os.getenv('PS365_BASE_URL', '')
    token = os.getenv('POWERSOFT_TOKEN', '') or os.getenv('PS365_TOKEN', '')
    if not base or not token:
        return jsonify({'success': False, 'error': 'PS365 not configured'}), 500
    try:
        url = f"{base.rstrip('/')}/list_payment_types"
        resp = req.get(url, params={'token': token, 'active_type': 'active'}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = data.get('list_payment_types') or []
        filtered = [
            {'code': p.get('payment_type_code_365', ''),
             'name': p.get('payment_type_name', ''),
             'is_cash': p.get('is_cash', False),
             'is_card': p.get('is_card', False),
             'sort_order': p.get('sort_order', 999)}
            for p in items
            if p.get('display_on_app') is True
        ]
        filtered.sort(key=lambda x: x.get('sort_order', 999))
        return jsonify({'success': True, 'payment_types': filtered})
    except Exception as e:
        logger.error(f"Error fetching PS365 payment types: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@reconciliation_bp.route('/api/ps365-payment-types-all')
@login_required
@admin_or_warehouse_required
def api_ps365_payment_types_all():
    """Fetch all active payment types from PS365 list_payment_types API"""
    import os, requests as req
    base = os.getenv('POWERSOFT_BASE', '') or os.getenv('PS365_BASE_URL', '')
    token = os.getenv('POWERSOFT_TOKEN', '') or os.getenv('PS365_TOKEN', '')
    if not base or not token:
        return jsonify({'success': False, 'error': 'PS365 not configured'}), 500
    try:
        url = f"{base.rstrip('/')}/list_payment_types"
        resp = req.get(url, params={'token': token, 'active_type': 'active'}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = data.get('list_payment_types') or []
        result = [
            {'code': p.get('payment_type_code_365', ''),
             'name': p.get('payment_type_name', ''),
             'is_cash': p.get('is_cash', False),
             'is_card': p.get('is_card', False),
             'sort_order': p.get('sort_order', 999)}
            for p in items
            if p.get('active') is True
        ]
        result.sort(key=lambda x: x.get('sort_order', 999))
        return jsonify({'success': True, 'payment_types': result})
    except Exception as e:
        logger.error(f"Error fetching PS365 payment types (all): {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@reconciliation_bp.route('/api/pending-payments/<int:allocation_id>/clear', methods=['POST'])
@login_required
@admin_or_warehouse_required
def api_clear_pending(allocation_id):
    """API: Clear a specific pending payment, optionally sending to PS365 first"""
    try:
        data = request.get_json(silent=True) or {}
        send_ps365 = data.get('send_ps365', False)
        payment_type_code = data.get('payment_type_code', '')
        receipt_date = data.get('receipt_date', '')
        reference = (data.get('reference', '') or '').strip()

        if send_ps365:
            alloc = CODInvoiceAllocation.query.get(allocation_id)
            if not alloc:
                return jsonify({'success': False, 'error': 'Allocation not found'}), 404

            cr = alloc.cod_receipt
            if not cr:
                return jsonify({'success': False, 'error': 'No COD receipt linked to this allocation'}), 400

            if cr.ps365_receipt_id or cr.ps365_reference_number:
                pass
            else:
                from datetime import date as date_type
                if (alloc.payment_method in ('cheque', 'postdated')
                        and alloc.cheque_date and alloc.cheque_date > date_type.today()):
                    return jsonify({
                        'success': False,
                        'error': f'Post-dated cheque not eligible until {alloc.cheque_date.strftime("%d/%m/%Y")}'
                    }), 400

                stop = RouteStop.query.get(cr.route_stop_id)
                customer_code = stop.customer_code if stop else ''
                invoice_nos = ", ".join(cr.invoice_nos) if isinstance(cr.invoice_nos, list) else str(cr.invoice_nos or '')
                comments = f"Cust: {customer_code} | {(cr.payment_method or 'PAYMENT').upper()}"
                if cr.note:
                    comments += f" | {cr.note}"

                from routes_receipts import create_receipt_core
                ok, ref_num, resp_id, status_code, ps_json = create_receipt_core(
                    customer_code=customer_code,
                    amount_val=float(cr.received_amount or 0),
                    comments=comments,
                    agent_code="2",
                    user_code=current_user.username,
                    invoice_no=invoice_nos,
                    driver_username=cr.driver_username,
                    route_stop_id=cr.route_stop_id,
                    cheque_number=cr.cheque_number or "",
                    cheque_date=cr.cheque_date.strftime('%Y-%m-%d') if cr.cheque_date else "",
                    allow_duplicate_stop=True,
                    payment_type_code_override=payment_type_code,
                    receipt_date_override=receipt_date,
                    bank_reference=reference
                )
                if not ok:
                    return jsonify({
                        'success': False,
                        'error': f'PS365 receipt creation failed: {ps_json}'
                    }), 500

                cr.ps365_reference_number = ref_num
                cr.ps365_receipt_id = str(resp_id) if resp_id else ref_num
                cr.ps365_synced_at = utc_now()
                db.session.flush()

        result = recon.clear_pending_payment(allocation_id, current_user.username)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error clearing pending payment: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

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
    
    
    invoice_report = recon.get_invoice_reconciliation_report(shipment_id)

    # Inject stops that have no invoices so they appear in the report view
    reported_stop_ids = {s['route_stop_id'] for s in invoice_report}
    for stop in stops:
        if stop['route_stop_id'] not in reported_stop_ids:
            invoice_report.append({
                'route_stop_id': stop['route_stop_id'],
                'stop_seq': float(stop['seq_no'] or 0),
                'customer_name': stop['stop_name'] or f"Stop {stop['seq_no']}",
                'terms': '—',
                'invoices': [],
                'stop_expected': 0.0,
                'stop_received': 0.0,
                'stop_discrepancy': 0.0,
                'stop_outstanding': 0.0,
                'payment_type': '—',
                'is_pending_payment': False,
                'no_invoices': True,
                'delivered_at': stop['delivered_at'],
                'failed_at': stop['failed_at'],
            })
    invoice_report.sort(key=lambda s: s['stop_seq'])

    return render_template('reconciliation/shipment_detail.html',
                         shipment=shipment,
                         summary=summary,
                         invoice_report=invoice_report,
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


@reconciliation_bp.route('/shipments/<int:shipment_id>/settlement-summary')
@login_required
@admin_or_warehouse_required
def settlement_summary(shipment_id):
    """Route settlement summary with POD breakdown by payment method"""
    from services_discrepancy import get_route_settlement_summary
    from models import CODReceipt
    from sqlalchemy import text
    
    route = Shipment.query.get_or_404(shipment_id)
    summary = get_route_settlement_summary(shipment_id)
    
    # Get detailed payment breakdown from per-invoice allocations (more accurate)
    payment_details = db.session.execute(text("""
        SELECT 
            cia.payment_method,
            COUNT(DISTINCT cia.invoice_no) as invoice_count,
            SUM(cia.expected_amount) as total_expected,
            SUM(cia.received_amount) as total_received,
            SUM(cia.deduct_amount) as total_deductions,
            BOOL_OR(cia.is_pending) as is_pending
        FROM cod_invoice_allocations cia
        WHERE cia.route_id = :route_id
        GROUP BY cia.payment_method
        ORDER BY cia.payment_method
    """), {'route_id': shipment_id}).fetchall()
    
    payments_by_method = {}
    pending_total = Decimal('0')
    collected_total = Decimal('0')
    
    for row in payment_details:
        method = row.payment_method
        is_pending = row.is_pending or method in ('online', 'postdated', 'post_dated')
        
        payments_by_method[method] = {
            'invoice_count': row.invoice_count,
            'expected': float(row.total_expected or 0),
            'received': float(row.total_received or 0),
            'deductions': float(row.total_deductions or 0),
            'is_pending': is_pending
        }
        
        if is_pending:
            pending_total += Decimal(str(row.total_expected or 0)) - Decimal(str(row.total_deductions or 0))
        else:
            collected_total += Decimal(str(row.total_received or 0))
    
    # Get POD vs Credit group breakdown
    pod_invoices = [inv for inv in summary['invoices'] if inv['payment_group'] == 'POD']
    credit_invoices = [inv for inv in summary['invoices'] if inv['payment_group'] == 'CREDIT']
    
    return render_template('reconciliation/settlement_summary.html',
                         route=route,
                         summary=summary,
                         payments_by_method=payments_by_method,
                         pending_total=float(pending_total),
                         collected_total=float(collected_total),
                         pod_invoices=pod_invoices,
                         credit_invoices=credit_invoices)


@reconciliation_bp.route('/bank-statement/upload', methods=['POST'])
@login_required
@admin_or_warehouse_required
def upload_bank_statement():
    from services.bank_matching import import_and_match
    f = request.files.get('bank_file')
    if not f or not f.filename:
        flash('Please select a bank statement file to upload.', 'error')
        return redirect(url_for('reconciliation.pending_payments'))
    try:
        result = import_and_match(f, f.filename, current_user.username)
        flash(f"Imported {result['credit_rows']} credit transactions. "
              f"{result['matched']} matched to pending payments, "
              f"{result['unmatched']} unmatched. (Batch: {result['batch_id']})", 'success')
    except ValueError as e:
        flash(f'Import error: {str(e)}', 'error')
    except Exception as e:
        logger.exception("Bank statement import failed")
        flash(f'Import failed: {str(e)}', 'error')
    return redirect(url_for('reconciliation.pending_payments'))


@reconciliation_bp.route('/api/bank-match/<int:txn_id>/dismiss', methods=['POST'])
@login_required
@admin_or_warehouse_required
def api_dismiss_bank_match(txn_id):
    from services.bank_matching import dismiss_match
    bt = dismiss_match(txn_id)
    if not bt:
        return jsonify({'success': False, 'error': 'Transaction not found'}), 404
    return jsonify({'success': True})


@reconciliation_bp.route('/api/bank-match/<int:txn_id>/confirm', methods=['POST'])
@login_required
@admin_or_warehouse_required
def api_confirm_bank_match(txn_id):
    from services.bank_matching import confirm_match
    bt = confirm_match(txn_id)
    if not bt:
        return jsonify({'success': False, 'error': 'Transaction not found'}), 404
    return jsonify({'success': True})


@reconciliation_bp.route('/api/bank-batch/<batch_id>/clear', methods=['POST'])
@login_required
@admin_or_warehouse_required
def api_clear_bank_batch(batch_id):
    count = BankTransaction.query.filter_by(batch_id=batch_id).update({'dismissed': True, 'match_status': 'DISMISSED'})
    db.session.commit()
    return jsonify({'success': True, 'cleared': count})
