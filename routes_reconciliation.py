"""
Flask routes for Route Reconciliation
Handles reconciliation lifecycle: refresh, submit, review, finalize
"""

import os
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from functools import wraps
from decimal import Decimal
import logging

from app import db
from models import Shipment, RouteStop, CODReceipt, CODInvoiceAllocation, Invoice, RouteStopInvoice, BankTransaction, PSCustomer, CustomerBalanceCache, DwInvoiceHeader
from models import utc_now
import services_reconciliation as recon
from services.communications_service import get_customer_comm_history, get_enabled_templates, render_template_for_customer, send_microsms

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
        for alloc in old_allocs:
            alloc.cod_receipt_id = new_receipt.id
            alloc.payment_method = new_method
            alloc.received_amount = new_receipt.received_amount / max(len(old_allocs), 1) if len(old_allocs) > 1 else new_receipt.received_amount
            alloc.is_pending = False
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
            SUM(cia.deduct_amount) as total_deductions
        FROM cod_invoice_allocations cia
        WHERE cia.route_id = :route_id
        GROUP BY cia.payment_method
        ORDER BY cia.payment_method
    """), {'route_id': shipment_id}).fetchall()
    
    payments_by_method = {}
    collected_total = Decimal('0')
    
    for row in payment_details:
        method = row.payment_method
        
        payments_by_method[method] = {
            'invoice_count': row.invoice_count,
            'expected': float(row.total_expected or 0),
            'received': float(row.total_received or 0),
            'deductions': float(row.total_deductions or 0),
        }
        
        collected_total += Decimal(str(row.total_received or 0))
    
    # Get POD vs Credit group breakdown
    pod_invoices = [inv for inv in summary['invoices'] if inv['payment_group'] == 'POD']
    credit_invoices = [inv for inv in summary['invoices'] if inv['payment_group'] == 'CREDIT']
    
    return render_template('reconciliation/settlement_summary.html',
                         route=route,
                         summary=summary,
                         payments_by_method=payments_by_method,
                         pending_total=0,
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
        return redirect(url_for('reconciliation.shipment_list'))
    try:
        result = import_and_match(f, f.filename, current_user.username)
        flash(f"Imported {result['credit_rows']} credit transactions. "
              f"(Batch: {result['batch_id']})", 'success')
    except ValueError as e:
        flash(f'Import error: {str(e)}', 'error')
    except Exception as e:
        logger.exception("Bank statement import failed")
        flash(f'Import failed: {str(e)}', 'error')
    return redirect(url_for('reconciliation.shipment_list'))


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


@reconciliation_bp.route('/api/customer-balance', methods=['GET'])
@login_required
@admin_or_warehouse_required
def api_customer_balance():
    import os
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from models import CustomerBalanceCache

    customer_code = (request.args.get("customer_code_365") or "").strip()
    if not customer_code:
        return jsonify({"ok": False, "error": "customer_code_365 is required"}), 400

    force = request.args.get("force", "0") == "1"
    cache_minutes = int(os.getenv("PS365_BALANCE_CACHE_MINUTES", "60"))

    try:
        today = datetime.now(ZoneInfo("Asia/Nicosia")).date()
    except Exception:
        today = datetime.utcnow().date()

    cached = CustomerBalanceCache.query.get(customer_code)
    if not force and cached and cached.as_of_date == today and CustomerBalanceCache.is_fresh(cached, cache_minutes):
        return jsonify({
            "ok": True,
            "source": "cache",
            "customer_code_365": customer_code,
            "as_of": str(cached.as_of_date),
            "balance": float(cached.balance),
            "drcr": cached.drcr,
            "signed_balance": float(cached.signed_balance),
        })

    try:
        from services.ps365_statement import get_customer_balance_as_of_today
        bal = get_customer_balance_as_of_today(customer_code)
        row = cached or CustomerBalanceCache(customer_code_365=customer_code)
        row.as_of_date = today
        row.balance = bal["balance"]
        row.drcr = bal["drcr"]
        row.signed_balance = bal["signed_balance"]
        row.ps_last_line_balance = bal.get("ps_last_line_balance")
        row.ps_last_balance_drcr = bal.get("ps_last_balance_drcr")
        row.fetched_at = datetime.utcnow()

        db.session.add(row)
        db.session.commit()

        return jsonify({
            "ok": True,
            "source": "ps365",
            "customer_code_365": customer_code,
            "as_of": bal["as_of"],
            "balance": bal["balance"],
            "drcr": bal["drcr"],
            "signed_balance": bal["signed_balance"],
        })
    except Exception as e:
        logger.error(f"Failed to fetch balance for {customer_code}: {e}")
        if cached:
            return jsonify({
                "ok": True,
                "source": "stale_cache",
                "customer_code_365": customer_code,
                "as_of": str(cached.as_of_date),
                "balance": float(cached.balance),
                "drcr": cached.drcr,
                "signed_balance": float(cached.signed_balance),
            })
        return jsonify({"ok": False, "error": "Could not retrieve balance from PS365"}), 500


def _get_excluded_agents():
    from models import Setting
    raw = Setting.get(db.session, 'balance_excluded_agents', '')
    if not raw or not raw.strip():
        return []
    return [a.strip() for a in raw.split(',') if a.strip()]


def _customer_base_filter():
    excluded = _get_excluded_agents()
    q = PSCustomer.query.filter(PSCustomer.active == True)
    for agent in excluded:
        q = q.filter(db.or_(PSCustomer.agent_name == None, ~PSCustomer.agent_name.ilike(f'%{agent}%')))
    return q


def _get_latest_invoice_dates(customer_codes):
    from sqlalchemy import func
    if not customer_codes:
        return {}
    rows = (
        db.session.query(
            DwInvoiceHeader.customer_code_365,
            func.max(DwInvoiceHeader.invoice_date_utc0).label('latest_date')
        )
        .filter(
            DwInvoiceHeader.customer_code_365.in_(customer_codes),
            DwInvoiceHeader.invoice_type == 'SALE',
        )
        .group_by(DwInvoiceHeader.customer_code_365)
        .all()
    )
    return {r[0]: r[1] for r in rows if r[1]}


def _get_recent_invoice_totals(customer_codes):
    from sqlalchemy import func
    from datetime import date, timedelta
    if not customer_codes:
        return {}
    yesterday = date.today() - timedelta(days=1)
    rows = (
        db.session.query(
            DwInvoiceHeader.customer_code_365,
            func.sum(DwInvoiceHeader.total_grand).label('recent_total')
        )
        .filter(
            DwInvoiceHeader.customer_code_365.in_(customer_codes),
            DwInvoiceHeader.invoice_type.in_(['SALE', 'SALE RETURN']),
            DwInvoiceHeader.invoice_date_utc0 >= yesterday,
            DwInvoiceHeader.invoice_date_utc0 <= date.today(),
        )
        .group_by(DwInvoiceHeader.customer_code_365)
        .all()
    )
    return {r[0]: float(r[1]) for r in rows if r[1]}


def _get_last_delivery_info(customer_codes):
    from sqlalchemy import func, text
    from datetime import date, timedelta
    if not customer_codes:
        return {}

    cutoff_date = date.today() - timedelta(days=1)

    last_inv_sub = (
        db.session.query(
            Invoice.customer_code_365,
            func.max(Invoice.delivered_at).label('max_delivered')
        )
        .join(Shipment, Invoice.route_id == Shipment.id)
        .filter(
            Invoice.customer_code_365.in_(customer_codes),
            Invoice.status == 'delivered',
            Invoice.delivered_at.isnot(None),
            Invoice.route_id.isnot(None),
            Shipment.delivery_date < cutoff_date,
        )
        .group_by(Invoice.customer_code_365)
        .subquery()
    )

    rows = (
        db.session.query(
            Invoice.customer_code_365,
            Invoice.invoice_no,
            Invoice.delivered_at,
            Invoice.total_grand,
            Shipment.route_name,
            Shipment.delivery_date,
            Shipment.driver_name,
            RouteStopInvoice.expected_payment_method,
        )
        .join(last_inv_sub, db.and_(
            Invoice.customer_code_365 == last_inv_sub.c.customer_code_365,
            Invoice.delivered_at == last_inv_sub.c.max_delivered,
        ))
        .join(Shipment, Invoice.route_id == Shipment.id)
        .outerjoin(RouteStopInvoice, db.and_(
            RouteStopInvoice.invoice_no == Invoice.invoice_no,
            RouteStopInvoice.is_active == True,
        ))
        .all()
    )

    result = {}
    for ccode, inv_no, delivered_at, total_grand, route_name, delivery_date, driver_name, expected_pm in rows:
        if ccode in result:
            continue
        alloc = db.session.query(
            CODInvoiceAllocation.payment_method,
            CODInvoiceAllocation.received_amount,
        ).filter(
            CODInvoiceAllocation.invoice_no == inv_no
        ).first()

        if alloc:
            pm = alloc[0] or ''
            received = float(alloc[1] or 0)
        else:
            pm = expected_pm or ''
            received = 0

        pm_display = pm.replace('_', ' ').title() if pm else 'N/A'

        result[ccode] = {
            'route_name': route_name or '',
            'delivery_date': delivery_date.strftime('%d/%m/%Y') if delivery_date else (delivered_at.strftime('%d/%m/%Y') if delivered_at else ''),
            'invoice_no': inv_no or '',
            'invoice_amount': float(total_grand) if total_grand else 0,
            'payment_method': pm_display,
            'payment_received': received,
            'driver_name': driver_name or '',
        }
    return result


@reconciliation_bp.route('/customer-balances')
@login_required
@admin_or_warehouse_required
def customer_balances_report():
    from sqlalchemy import func

    excluded = _get_excluded_agents()

    q = (
        db.session.query(
            PSCustomer.customer_code_365,
            PSCustomer.company_name,
            PSCustomer.town,
            PSCustomer.category_1_name,
            PSCustomer.agent_name,
            PSCustomer.tel_1,
            PSCustomer.mobile,
            PSCustomer.sms,
            PSCustomer.credit_limit_amount,
            CustomerBalanceCache.balance,
            CustomerBalanceCache.drcr,
            CustomerBalanceCache.signed_balance,
            CustomerBalanceCache.as_of_date,
            CustomerBalanceCache.fetched_at,
            PSCustomer.address_line_1,
            PSCustomer.address_line_2,
            PSCustomer.postal_code,
            PSCustomer.email,
            PSCustomer.vat_registration_number,
            PSCustomer.contact_first_name,
            PSCustomer.contact_last_name,
            PSCustomer.category_2_name,
        )
        .outerjoin(CustomerBalanceCache, CustomerBalanceCache.customer_code_365 == PSCustomer.customer_code_365)
        .filter(PSCustomer.active == True)
    )
    for agent in excluded:
        q = q.filter(db.or_(PSCustomer.agent_name == None, ~PSCustomer.agent_name.ilike(f'%{agent}%')))

    rows = q.all()

    customer_codes = [r[0] for r in rows]
    last_delivery_map = _get_last_delivery_info(customer_codes)
    latest_invoice_dates = _get_latest_invoice_dates(customer_codes)
    recent_invoice_totals = _get_recent_invoice_totals(customer_codes)

    customers = []
    total_dr = Decimal('0')
    total_cr = Decimal('0')
    fetched_count = 0
    with_balance_count = 0

    for (code, name, town, cat, agent, tel, mobile, sms_num, credit_limit,
         balance, drcr, signed_balance, as_of, fetched_at,
         addr1, addr2, postal, email, vat, contact_first, contact_last, cat2) in rows:
        has_data = balance is not None
        if has_data:
            fetched_count += 1
        sb = Decimal(str(signed_balance or 0))
        bal = float(balance or 0)
        if sb > 0:
            total_dr += sb
        elif sb < 0:
            total_cr += abs(sb)
        if has_data and sb != 0:
            with_balance_count += 1
        addr_parts = [p for p in [addr1, addr2, postal, town] if p]
        contact_name = ' '.join(p for p in [contact_first, contact_last] if p)
        ld = last_delivery_map.get(code, {})
        recent_total = recent_invoice_totals.get(code, 0)
        overdue_balance = float(sb) - recent_total
        customers.append({
            'code': code,
            'name': name or code,
            'town': town or '',
            'category': cat or '',
            'category2': cat2 or '',
            'agent': agent or '',
            'phone': sms_num or mobile or tel or '',
            'tel': tel or '',
            'mobile': mobile or '',
            'sms': sms_num or '',
            'email': email or '',
            'credit_limit': float(credit_limit or 0),
            'balance': bal,
            'drcr': drcr or '',
            'signed_balance': float(sb),
            'as_of': str(as_of) if as_of else '',
            'fetched_at': fetched_at.strftime('%Y-%m-%d %H:%M') if fetched_at else '',
            'has_data': has_data,
            'address': ', '.join(addr_parts),
            'vat': vat or '',
            'contact': contact_name,
            'last_route': ld.get('route_name', ''),
            'last_delivery_date': ld.get('delivery_date', ''),
            'last_invoice_date': latest_invoice_dates.get(code, None),
            'last_invoice_no': ld.get('invoice_no', ''),
            'last_invoice_amount': ld.get('invoice_amount', ''),
            'last_payment_method': ld.get('payment_method', ''),
            'last_payment_received': ld.get('payment_received', 0),
            'last_driver': ld.get('driver_name', ''),
            'recent_invoices_total': recent_total,
            'overdue_balance': overdue_balance,
        })

    customers.sort(key=lambda c: c['signed_balance'], reverse=True)

    return render_template('reconciliation/customer_balances.html',
                           customers=customers,
                           total_dr=float(total_dr),
                           total_cr=float(total_cr),
                           net_balance=float(total_dr - total_cr),
                           total_customers=len(rows),
                           fetched_count=fetched_count,
                           with_balance_count=with_balance_count,
                           excluded_agents=excluded)


@reconciliation_bp.route('/api/customer-balances/sms-preview/<customer_code>')
@login_required
@admin_or_warehouse_required
def api_customer_balance_sms_preview(customer_code):
    try:
        template_code = (request.args.get('template_code') or '').strip()
        row = (
            db.session.query(
                PSCustomer.customer_code_365,
                PSCustomer.company_name,
                PSCustomer.sms,
                PSCustomer.mobile,
                CustomerBalanceCache.balance,
                CustomerBalanceCache.drcr,
                CustomerBalanceCache.signed_balance,
                CustomerBalanceCache.fetched_at
            )
            .outerjoin(CustomerBalanceCache, CustomerBalanceCache.customer_code_365 == PSCustomer.customer_code_365)
            .filter(PSCustomer.customer_code_365 == customer_code)
            .first()
        )
        if not row:
            return jsonify({'success': False, 'error': 'Customer not found'}), 404
        code, name, sms_number, mobile, balance, drcr, signed_balance, fetched_at = row
        ld = _get_last_delivery_info([code]).get(code, {})
        balance_value = float(signed_balance or 0)
        balance_text = f"€{abs(balance_value):,.2f}"
        if balance_value > 0:
            balance_text = f"due {balance_text}"
        elif balance_value < 0:
            balance_text = f"credit {balance_text}"
        else:
            balance_text = "€0.00"
        if template_code:
            tpl = render_template_for_customer(template_code, {
                'customer_name': name or code,
                'customer_code': code,
                'current_balance': balance_value,
                'balance_text': balance_text,
                'last_delivery_date': ld.get('delivery_date', ''),
            })
            if tpl.get('error'):
                return jsonify({'success': False, 'error': tpl['error']}), 400
            message = tpl.get('rendered_body') or ''
            template_title = tpl.get('title')
        else:
            message = (
                f"Dear {name or code}, your current balance is {balance_text}. "
                f"Last delivery date: {ld.get('delivery_date', 'N/A')}."
            )
            template_title = None
        return jsonify({
            'success': True,
            'customer_code': code,
            'customer_name': name or code,
            'mobile': sms_number or mobile or '',
            'mobile_display': sms_number or mobile or '',
            'current_balance': balance_value,
            'last_delivery_date': ld.get('delivery_date', ''),
            'message': message,
            'template_title': template_title,
            'sms_parameters': {
                'customer_name': '{{ customer_name }}',
                'customer_code': '{{ customer_code }}',
                'current_balance': '{{ current_balance }}',
                'last_delivery_date': '{{ last_delivery_date }}',
                'balance_text': '{{ balance_text }}',
            }
        })
    except Exception as e:
        logger.error(f"Error building customer balance SMS preview for {customer_code}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@reconciliation_bp.route('/api/customer-balances/invoices/<customer_code>')
@login_required
@admin_or_warehouse_required
def api_customer_invoices(customer_code):
    from datetime import date, timedelta
    try:
        invoices = (
            db.session.query(
                DwInvoiceHeader.invoice_no_365,
                DwInvoiceHeader.invoice_date_utc0,
                DwInvoiceHeader.invoice_type,
                DwInvoiceHeader.total_grand,
                DwInvoiceHeader.total_net,
                DwInvoiceHeader.total_vat,
            )
            .filter(
                DwInvoiceHeader.customer_code_365 == customer_code,
                DwInvoiceHeader.invoice_type.in_(['SALE', 'SALE RETURN']),
                DwInvoiceHeader.invoice_date_utc0 >= date.today() - timedelta(days=1),
                DwInvoiceHeader.invoice_date_utc0 <= date.today(),
            )
            .order_by(DwInvoiceHeader.invoice_date_utc0.desc())
            .all()
        )
        today = date.today()
        yesterday = today - timedelta(days=1)
        result = []
        for inv_no, inv_date, inv_type, total_grand, total_net, total_vat in invoices:
            is_recent = inv_date is not None and inv_date >= yesterday and inv_date <= today
            result.append({
                'invoice_no': inv_no or '',
                'date': inv_date.strftime('%d/%m/%Y') if inv_date else '',
                'type': inv_type or '',
                'total_grand': float(total_grand) if total_grand else 0,
                'total_net': float(total_net) if total_net else 0,
                'total_vat': float(total_vat) if total_vat else 0,
                'is_recent': is_recent,
            })
        return jsonify({'success': True, 'invoices': result})
    except Exception as e:
        logger.error(f"Error fetching invoices for {customer_code}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@reconciliation_bp.route('/api/customer-balances/sms-history/<customer_code>')
@login_required
@admin_or_warehouse_required
def api_customer_sms_history(customer_code):
    try:
        comms = get_customer_comm_history(customer_code, limit=10)
        messages = []
        for m in comms:
            messages.append({
                'date': m.created_at.strftime('%d-%m %H:%M') if m.created_at else '',
                'status': (m.status or '').lower(),
                'template_title': m.template_title or '',
                'message_text': m.message_text or '',
            })
        return jsonify({'success': True, 'messages': messages})
    except Exception as e:
        logger.error(f"Error fetching SMS history for {customer_code}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@reconciliation_bp.route('/api/customer-balances/sms-templates')
@login_required
@admin_or_warehouse_required
def api_customer_balance_sms_templates():
    try:
        templates = get_enabled_templates(channel_filter='microsms')
        return jsonify({'success': True, 'templates': templates})
    except Exception as e:
        logger.error(f"Error loading customer balance SMS templates: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@reconciliation_bp.route('/api/customer-balances/send-sms/<customer_code>', methods=['POST'])
@login_required
@admin_or_warehouse_required
def api_customer_balance_send_sms(customer_code):
    try:
        data = request.get_json(force=True) if request.is_json else {}
        template_code = (data.get('template_code') or '').strip()
        if not template_code:
            return jsonify({'success': False, 'error': 'Template is required'}), 400

        row = (
            db.session.query(
                PSCustomer.customer_code_365,
                PSCustomer.company_name,
                PSCustomer.sms,
                PSCustomer.mobile,
            )
            .filter(PSCustomer.customer_code_365 == customer_code)
            .first()
        )
        if not row:
            return jsonify({'success': False, 'error': 'Customer not found'}), 404

        code, name, sms_number, mobile = row
        mobile_number = sms_number or mobile or ''
        if not mobile_number:
            return jsonify({'success': False, 'error': 'No SMS number found'}), 400

        ld = _get_last_delivery_info([code]).get(code, {})
        balance_row = (
            db.session.query(CustomerBalanceCache.signed_balance)
            .filter(CustomerBalanceCache.customer_code_365 == code)
            .first()
        )
        current_balance = float(balance_row[0] or 0) if balance_row else 0
        balance_text = f"€{abs(current_balance):,.2f}"
        if current_balance > 0:
            balance_text = f"due {balance_text}"
        elif current_balance < 0:
            balance_text = f"credit {balance_text}"
        else:
            balance_text = "€0.00"

        tpl = render_template_for_customer(template_code, {
            'customer_name': name or code,
            'customer_code': code,
            'current_balance': current_balance,
            'balance_text': balance_text,
            'last_delivery_date': ld.get('delivery_date', ''),
        })
        if tpl.get('error'):
            return jsonify({'success': False, 'error': tpl['error']}), 400

        sender_title = tpl.get('sender_title') or 'EPLATTFORMA'
        message = tpl.get('rendered_body') or ''
        result = send_microsms(
            mobile_number,
            sender_title,
            message,
            customer_code_365=code,
            customer_name=name or code,
            template_code=template_code,
            template_title=tpl.get('title'),
            source_screen='reconciliation/customer-balances',
            context_type='customer_balance',
            context_id=code,
            username=current_user.username,
        )
        return jsonify({'success': True, 'result': result, 'message': message})
        return jsonify({'success': True, 'result': result, 'message': message, 'mobile': mobile_number})
    except Exception as e:
        logger.error(f"Error sending customer balance SMS for {customer_code}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@reconciliation_bp.route('/api/customer-statement/<customer_code>')
@login_required
@admin_or_warehouse_required
def api_customer_statement(customer_code):
    try:
        from services.ps365_statement import fetch_statement
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        try:
            today = datetime.now(ZoneInfo("Asia/Nicosia")).date()
        except Exception:
            today = datetime.utcnow().date()

        months = request.args.get('months', type=int)
        if months:
            from_dt = today - timedelta(days=months * 30)
            from_date = from_dt.isoformat()
        else:
            lookback = int(os.getenv("PS365_BALANCE_LOOKBACK_YEARS", "10"))
            from_date = f"{today.year - lookback}-01-01"
        to_date = today.isoformat()

        truncated = False
        try:
            stmt = fetch_statement(customer_code, from_date=from_date, to_date=to_date)
        except RuntimeError as e:
            if 'more than 500 rows' in str(e).lower() or '500 rows' in str(e):
                for fallback_months in [24, 12, 6, 3]:
                    try:
                        from_dt = today - timedelta(days=fallback_months * 30)
                        from_date = from_dt.isoformat()
                        stmt = fetch_statement(customer_code, from_date=from_date, to_date=to_date)
                        truncated = True
                        break
                    except RuntimeError as e2:
                        if 'more than 500 rows' not in str(e2).lower() and '500 rows' not in str(e2):
                            raise
                        continue
                else:
                    raise RuntimeError("Statement too large even for 3-month range")
            else:
                raise

        lines = (stmt or {}).get("list_statement_lines") or []

        result = []
        for ln in lines:
            result.append({
                'date': ln.get('transaction_date', ''),
                'doc_number': ln.get('transaction_number', '') or ln.get('reference_number', '') or ln.get('document_number', ''),
                'type': ln.get('transaction_type', '') or '',
                'description': ln.get('transaction_description', '') or ln.get('general_description', '') or ln.get('detail_description', '') or '',
                'amount': float(ln.get('transaction_amount') or 0),
                'drcr': (ln.get('transaction_drcr') or '').upper().strip(),
                'line_balance': float(ln.get('line_balance') or 0) if ln.get('line_balance') is not None else None,
                'balance_drcr': (ln.get('balance_drcr') or '').upper().strip(),
            })

        aging = (stmt or {}).get("aging_analysis") or {}
        aging_data = {
            'postdated_payments': float(aging.get('postdated_payments') or 0),
            'aging_0_30': float(aging.get('aging_0_30') or 0),
            'aging_30_60': float(aging.get('aging_30_60') or 0),
            'aging_60_90': float(aging.get('aging_60_90') or 0),
            'aging_90_120': float(aging.get('aging_90_120') or 0),
            'aging_121': float(aging.get('aging_121') or 0),
        }

        resp = {'ok': True, 'lines': result, 'from_date': from_date, 'to_date': to_date, 'aging': aging_data}
        if truncated:
            resp['truncated'] = True
            resp['note'] = f'Statement limited to {from_date} onwards (PS365 max 500 lines)'
        return jsonify(resp)
    except Exception as e:
        logger.error(f"Failed to fetch statement for {customer_code}: {e}")
        return jsonify({'ok': False, 'error': 'Could not retrieve statement from PS365. Please try again later.'}), 500


@reconciliation_bp.route('/api/customer-balances/excluded-agents', methods=['POST'])
@login_required
@admin_or_warehouse_required
def api_save_excluded_agents():
    from models import Setting
    data = request.get_json(silent=True) or {}
    agents_raw = data.get('agents', '').strip()
    Setting.set(db.session, 'balance_excluded_agents', agents_raw)
    db.session.commit()
    excluded = [a.strip() for a in agents_raw.split(',') if a.strip()]
    return jsonify({'ok': True, 'excluded': excluded})


_balance_fetch_status = {'running': False, 'success': 0, 'failed': 0, 'skipped': 0, 'total': 0, 'errors': [], 'done': False, 'started_at': None, 'finished_at': None}


def _run_balance_fetch(app):
    import requests
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from config_ps365 import PS365_BASE_URL, PS365_TOKEN

    global _balance_fetch_status
    with app.app_context():
        try:
            today = datetime.now(ZoneInfo("Asia/Nicosia")).date()
        except Exception:
            today = datetime.utcnow().date()

        base = PS365_BASE_URL
        token = PS365_TOKEN
        if not base or not token:
            _balance_fetch_status['errors'].append("Missing PS365_BASE_URL or PS365_TOKEN")
            _balance_fetch_status['done'] = True
            _balance_fetch_status['running'] = False
            _balance_fetch_status['finished_at'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            return

        url = f"{base.rstrip('/')}/list_customers"

        try:
            count_payload = {
                "api_credentials": {"token": token},
                "filter_define": {
                    "only_counted": "Y",
                    "active_type": "active",
                }
            }
            r = requests.post(url, json=count_payload, timeout=60)
            r.raise_for_status()
            total_count = r.json().get("total_count_list_customers", 0)
            _balance_fetch_status['total'] = total_count
            logger.info(f"Balance fetch: {total_count} active customers to fetch via list_customers API")
        except Exception as e:
            _balance_fetch_status['errors'].append(f"Count API failed: {str(e)[:100]}")
            _balance_fetch_status['done'] = True
            _balance_fetch_status['running'] = False
            _balance_fetch_status['finished_at'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            logger.error(f"Balance fetch count failed: {e}")
            return

        page_size = 100
        page = 1
        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1

        while page <= total_pages:
            try:
                payload = {
                    "api_credentials": {"token": token},
                    "filter_define": {
                        "only_counted": "N",
                        "page_number": page,
                        "page_size": page_size,
                        "active_type": "active",
                        "display_fields": "customer_code_365,balance",
                    }
                }
                r = requests.post(url, json=payload, timeout=120)
                r.raise_for_status()
                data = r.json()
                customers_list = data.get("list_customers") or []

                if not customers_list:
                    break

                for cust_data in customers_list:
                    code = cust_data.get("customer_code_365", "")
                    if not code:
                        continue

                    raw_balance = cust_data.get("balance", 0)
                    try:
                        balance_val = float(raw_balance or 0)
                    except (ValueError, TypeError):
                        balance_val = 0.0

                    abs_balance = abs(balance_val)
                    if balance_val > 0:
                        drcr = "DR"
                    elif balance_val < 0:
                        drcr = "CR"
                    else:
                        drcr = ""

                    try:
                        cached = CustomerBalanceCache.query.get(code)
                        row = cached or CustomerBalanceCache(customer_code_365=code)
                        row.as_of_date = today
                        row.balance = abs_balance
                        row.drcr = drcr
                        row.signed_balance = balance_val
                        row.fetched_at = datetime.utcnow()
                        db.session.add(row)
                        _balance_fetch_status['success'] += 1
                    except Exception as e:
                        _balance_fetch_status['failed'] += 1
                        if _balance_fetch_status['failed'] <= 10:
                            _balance_fetch_status['errors'].append(f"{code}: {str(e)[:80]}")

                db.session.commit()
                logger.info(f"Balance fetch: page {page}/{total_pages} done ({len(customers_list)} customers)")
                page += 1

            except Exception as e:
                _balance_fetch_status['failed'] += 1
                if _balance_fetch_status['failed'] <= 10:
                    _balance_fetch_status['errors'].append(f"Page {page} failed: {str(e)[:100]}")
                logger.warning(f"Balance fetch page {page} failed: {e}")
                page += 1

        _balance_fetch_status['done'] = True
        _balance_fetch_status['running'] = False
        _balance_fetch_status['finished_at'] = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"Balance fetch complete: {_balance_fetch_status['success']} ok, {_balance_fetch_status['failed']} failed")


@reconciliation_bp.route('/api/customer-balances/fetch-all', methods=['POST'])
@login_required
@admin_or_warehouse_required
def api_fetch_all_balances():
    import threading
    from flask import current_app

    global _balance_fetch_status
    if _balance_fetch_status.get('running'):
        return jsonify({'ok': True, 'status': 'already_running', **_balance_fetch_status})

    from datetime import datetime
    _balance_fetch_status = {'running': True, 'success': 0, 'failed': 0, 'skipped': 0, 'total': 0, 'errors': [], 'done': False, 'started_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), 'finished_at': None}
    app = current_app._get_current_object()
    t = threading.Thread(target=_run_balance_fetch, args=(app,), daemon=True)
    t.start()

    return jsonify({'ok': True, 'status': 'started'})


@reconciliation_bp.route('/api/customer-balances/fetch-status', methods=['GET'])
@login_required
@admin_or_warehouse_required
def api_fetch_balances_status():
    return jsonify(_balance_fetch_status)
