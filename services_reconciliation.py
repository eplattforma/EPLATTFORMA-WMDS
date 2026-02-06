"""
Route Reconciliation Services
Handles reconciliation lifecycle: refresh, submit, review, finalize
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Any
from sqlalchemy import text, func
from sqlalchemy.orm import Session

from app import db
from models import (
    Shipment, RouteStop, RouteStopInvoice, Invoice, 
    CODReceipt, PODRecord, DeliveryDiscrepancy, 
    InvoicePostDeliveryCase, InvoicePaymentExpectation,
    RouteReturnHandover, CreditTerms,
    utc_now, get_utc_now
)

logger = logging.getLogger(__name__)

FINAL_DELIVERY_STATUSES = {'DELIVERED', 'FAILED', 'PARTIAL', 'RETURNED', 'SKIPPED'}


def get_shipment_invoices(shipment_id: int) -> List[Dict]:
    """Get all active invoices for a shipment via canonical route_stop_invoice join"""
    sql = text("""
        SELECT 
            rsi.invoice_no,
            rsi.status AS rsi_status,
            rsi.route_stop_id,
            rs.seq_no,
            i.total_grand,
            i.customer_code_365,
            i.customer_name
        FROM route_stop rs
        JOIN route_stop_invoice rsi 
            ON rsi.route_stop_id = rs.route_stop_id 
            AND rsi.is_active = true
        JOIN invoices i ON i.invoice_no = rsi.invoice_no
        WHERE rs.shipment_id = :shipment_id
          AND rs.deleted_at IS NULL
        ORDER BY rs.seq_no, rsi.invoice_no
    """)
    result = db.session.execute(sql, {'shipment_id': shipment_id})
    return [dict(row._mapping) for row in result]


def get_invoice_reconciliation_report(shipment_id: int) -> List[Dict]:
    """Get invoice-level reconciliation report for a route.
    
    Returns per-invoice rows with:
    - Route ID, stop seq, customer name, terms (POD/CREDIT)
    - Expected amount, received (cash/day cheque only), payment type
    - Discrepancy value, outstanding amount
    
    Outstanding = Expected - Received - Discrepancy (for POD terms)
    Outstanding = 0 (for CREDIT terms)
    """
    sql = text("""
        SELECT 
            rs.shipment_id AS route_id,
            rs.seq_no AS stop_seq,
            rs.stop_name AS customer_name,
            rsi.invoice_no,
            COALESCE(i.total_grand, 0) AS expected,
            COALESCE(ct.is_credit, false) AS is_credit,
            rsi.status AS delivery_status,
            ca.payment_method,
            ca.received_amount,
            ca.deduct_amount,
            ca.cheque_date,
            ca.is_pending AS alloc_pending,
            COALESCE(disc.discrepancy_total, 0) AS discrepancy_value
        FROM route_stop_invoice rsi
        JOIN route_stop rs ON rs.route_stop_id = rsi.route_stop_id
        JOIN invoices i ON i.invoice_no = rsi.invoice_no
        LEFT JOIN credit_terms ct ON ct.customer_code = i.customer_code 
            AND (ct.valid_to IS NULL OR ct.valid_to >= CURRENT_DATE)
        LEFT JOIN cod_invoice_allocations ca 
            ON ca.invoice_no = rsi.invoice_no AND ca.route_id = rs.shipment_id
        LEFT JOIN (
            SELECT invoice_no, SUM(COALESCE(reported_value, 0)) AS discrepancy_total
            FROM delivery_discrepancies
            GROUP BY invoice_no
        ) disc ON disc.invoice_no = rsi.invoice_no
        WHERE rs.shipment_id = :shipment_id
          AND rs.deleted_at IS NULL
          AND rsi.is_active = true
        ORDER BY rs.seq_no, rsi.invoice_no
    """)
    result = db.session.execute(sql, {'shipment_id': shipment_id})
    
    rows = []
    for row in result:
        r = dict(row._mapping)
        is_credit = r['is_credit']
        expected = float(r['expected'] or 0)
        payment_method = r['payment_method'] or ''
        received = float(r['received_amount'] or 0)
        discrepancy = float(r['discrepancy_value'] or 0)
        cheque_date = r['cheque_date']
        
        if is_credit:
            terms_label = 'CREDIT'
            display_received = None
            display_payment = 'CREDIT'
            outstanding = 0
        else:
            terms_label = 'POD'
            pm_upper = payment_method.upper() if payment_method else ''
            is_day_cheque = (pm_upper == 'CHEQUE' and cheque_date and cheque_date <= datetime.now().date())
            is_cash = pm_upper == 'CASH'
            
            if is_cash or is_day_cheque:
                display_received = received
                display_payment = pm_upper
                counted_received = received
            else:
                display_received = None
                display_payment = pm_upper or '-'
                counted_received = 0
            
            outstanding = max(0, expected - counted_received - discrepancy)
            if outstanding < 0.005:
                outstanding = 0
        
        rows.append({
            'route_id': r['route_id'],
            'stop_seq': int(r['stop_seq'] or 0),
            'customer_name': r['customer_name'] or '',
            'invoice_no': r['invoice_no'],
            'terms': terms_label,
            'expected': expected,
            'received': display_received,
            'payment_type': display_payment,
            'discrepancy': discrepancy if discrepancy > 0 else None,
            'outstanding': outstanding if outstanding > 0 else None,
            'delivery_status': r['delivery_status']
        })
    
    return rows


def check_missing_final_status(shipment_id: int) -> List[Dict]:
    """Find invoices without final delivery status (blocking)"""
    sql = text("""
        SELECT rsi.invoice_no, rsi.status
        FROM route_stop rs
        JOIN route_stop_invoice rsi 
            ON rsi.route_stop_id = rs.route_stop_id 
            AND rsi.is_active = true
        WHERE rs.shipment_id = :shipment_id
          AND rs.deleted_at IS NULL
          AND (rsi.status IS NULL OR UPPER(rsi.status) NOT IN ('DELIVERED','FAILED','PARTIAL','RETURNED','SKIPPED'))
    """)
    result = db.session.execute(sql, {'shipment_id': shipment_id})
    return [dict(row._mapping) for row in result]


def check_missing_pod(shipment_id: int) -> List[Dict]:
    """Find delivered invoices without POD (blocking if POD required)"""
    sql = text("""
        SELECT rsi.invoice_no, rs.route_stop_id
        FROM route_stop rs
        JOIN route_stop_invoice rsi
            ON rsi.route_stop_id = rs.route_stop_id
            AND rsi.is_active = true
        LEFT JOIN pod_records pr
            ON pr.route_id = rs.shipment_id
            AND pr.route_stop_id = rs.route_stop_id
        WHERE rs.shipment_id = :shipment_id
          AND rs.deleted_at IS NULL
          AND UPPER(rsi.status) = 'DELIVERED'
          AND pr.id IS NULL
    """)
    result = db.session.execute(sql, {'shipment_id': shipment_id})
    return [dict(row._mapping) for row in result]


def check_open_post_delivery_cases(shipment_id: int) -> List[Dict]:
    """Find open post-delivery cases (blocking)"""
    sql = text("""
        SELECT ipdc.invoice_no, ipdc.status, ipdc.reason
        FROM invoice_post_delivery_cases ipdc
        JOIN route_stop_invoice rsi
            ON rsi.invoice_no = ipdc.invoice_no
            AND rsi.is_active = true
        JOIN route_stop rs
            ON rs.route_stop_id = rsi.route_stop_id
        WHERE rs.shipment_id = :shipment_id
          AND rs.deleted_at IS NULL
          AND ipdc.status = 'OPEN'
    """)
    result = db.session.execute(sql, {'shipment_id': shipment_id})
    return [dict(row._mapping) for row in result]


def check_unresolved_discrepancies(shipment_id: int) -> List[Dict]:
    """Find unresolved delivery discrepancies (blocking)"""
    sql = text("""
        SELECT d.invoice_no, d.discrepancy_type, d.status, d.is_resolved
        FROM delivery_discrepancies d
        JOIN route_stop_invoice rsi
            ON rsi.invoice_no = d.invoice_no
            AND rsi.is_active = true
        JOIN route_stop rs
            ON rs.route_stop_id = rsi.route_stop_id
        WHERE rs.shipment_id = :shipment_id
          AND rs.deleted_at IS NULL
          AND d.is_resolved = false
    """)
    result = db.session.execute(sql, {'shipment_id': shipment_id})
    return [dict(row._mapping) for row in result]


def get_cash_totals(shipment_id: int) -> Dict:
    """Get cash totals from COD receipts"""
    sql = text("""
        SELECT
            COALESCE(SUM(expected_amount), 0) AS cash_expected,
            COALESCE(SUM(received_amount), 0) AS cash_collected,
            COALESCE(SUM(variance), 0) AS cash_variance_sum,
            COUNT(CASE WHEN ps365_synced_at IS NULL THEN 1 END) AS not_synced_count
        FROM cod_receipts
        WHERE route_id = :shipment_id
    """)
    result = db.session.execute(sql, {'shipment_id': shipment_id}).fetchone()
    return {
        'cash_expected': Decimal(str(result.cash_expected or 0)),
        'cash_collected': Decimal(str(result.cash_collected or 0)),
        'cash_variance_sum': Decimal(str(result.cash_variance_sum or 0)),
        'not_synced_count': result.not_synced_count or 0
    }


def get_reconciliation_summary(shipment_id: int) -> Dict:
    """Get complete reconciliation summary for a shipment"""
    sql = text("""
        WITH inv AS (
            SELECT rsi.invoice_no, rsi.status
            FROM route_stop rs
            JOIN route_stop_invoice rsi 
                ON rsi.route_stop_id = rs.route_stop_id 
                AND rsi.is_active = true
            WHERE rs.shipment_id = :shipment_id 
              AND rs.deleted_at IS NULL
        ),
        pod_missing AS (
            SELECT COUNT(*) AS cnt
            FROM route_stop rs
            JOIN route_stop_invoice rsi 
                ON rsi.route_stop_id = rs.route_stop_id 
                AND rsi.is_active = true
            LEFT JOIN pod_records pr
                ON pr.route_id = rs.shipment_id
                AND pr.route_stop_id = rs.route_stop_id
            WHERE rs.shipment_id = :shipment_id
              AND rs.deleted_at IS NULL
              AND UPPER(rsi.status) = 'DELIVERED'
              AND pr.id IS NULL
        ),
        open_cases AS (
            SELECT COUNT(*) AS cnt
            FROM invoice_post_delivery_cases ipdc
            JOIN route_stop_invoice rsi 
                ON rsi.invoice_no = ipdc.invoice_no 
                AND rsi.is_active = true
            JOIN route_stop rs 
                ON rs.route_stop_id = rsi.route_stop_id
            WHERE rs.shipment_id = :shipment_id
              AND rs.deleted_at IS NULL
              AND ipdc.status = 'OPEN'
        ),
        unresolved_disc AS (
            SELECT COUNT(*) AS cnt
            FROM delivery_discrepancies d
            JOIN route_stop_invoice rsi 
                ON rsi.invoice_no = d.invoice_no 
                AND rsi.is_active = true
            JOIN route_stop rs 
                ON rs.route_stop_id = rsi.route_stop_id
            WHERE rs.shipment_id = :shipment_id
              AND rs.deleted_at IS NULL
              AND d.is_resolved = false
        ),
        cash AS (
            SELECT
                COALESCE(SUM(expected_amount), 0) AS expected,
                COALESCE(SUM(received_amount), 0) AS received,
                COUNT(CASE WHEN ps365_synced_at IS NULL THEN 1 END) AS not_synced_count
            FROM cod_receipts
            WHERE route_id = :shipment_id
        )
        SELECT
            (SELECT COUNT(*) FROM inv) AS invoices_total,
            (SELECT COUNT(*) FROM inv WHERE UPPER(status) = 'DELIVERED') AS delivered,
            (SELECT COUNT(*) FROM inv WHERE UPPER(status) = 'FAILED') AS failed,
            (SELECT COUNT(*) FROM inv WHERE UPPER(status) = 'PARTIAL') AS partial,
            (SELECT COUNT(*) FROM inv WHERE status IS NULL OR UPPER(status) NOT IN ('DELIVERED','FAILED','PARTIAL','RETURNED','SKIPPED')) AS pending,
            (SELECT cnt FROM pod_missing) AS missing_pod,
            (SELECT cnt FROM open_cases) AS open_cases,
            (SELECT cnt FROM unresolved_disc) AS unresolved_discrepancies,
            (SELECT expected FROM cash) AS cash_expected,
            (SELECT received FROM cash) AS cash_received,
            (SELECT not_synced_count FROM cash) AS receipts_not_synced
    """)
    result = db.session.execute(sql, {'shipment_id': shipment_id}).fetchone()
    return dict(result._mapping)


def get_stop_details(shipment_id: int) -> List[Dict]:
    """Get stop-by-stop details for reconciliation"""
    sql = text("""
        SELECT
            rs.route_stop_id,
            rs.seq_no,
            rs.stop_name,
            rs.stop_addr,
            rs.stop_city,
            rs.delivered_at,
            rs.failed_at,
            rs.failure_reason,
            rs.customer_code
        FROM route_stop rs
        WHERE rs.shipment_id = :shipment_id
          AND rs.deleted_at IS NULL
        ORDER BY rs.seq_no
    """)
    result = db.session.execute(sql, {'shipment_id': shipment_id})
    return [dict(row._mapping) for row in result]


def get_stop_invoices(route_stop_id: int) -> List[Dict]:
    """Get invoices for a specific stop"""
    sql = text("""
        SELECT 
            rsi.invoice_no, 
            rsi.status, 
            i.total_grand, 
            i.customer_name
        FROM route_stop_invoice rsi
        JOIN invoices i ON i.invoice_no = rsi.invoice_no
        WHERE rsi.route_stop_id = :route_stop_id 
          AND rsi.is_active = true
        ORDER BY rsi.invoice_no
    """)
    result = db.session.execute(sql, {'route_stop_id': route_stop_id})
    return [dict(row._mapping) for row in result]


def get_stop_cod_receipts(shipment_id: int, route_stop_id: int) -> List[Dict]:
    """Get COD receipts for a specific stop"""
    sql = text("""
        SELECT
            id, payment_method, expected_amount, received_amount, variance,
            cheque_number, cheque_date, ps365_receipt_id, ps365_synced_at
        FROM cod_receipts
        WHERE route_id = :shipment_id 
          AND route_stop_id = :route_stop_id
        ORDER BY created_at DESC
    """)
    result = db.session.execute(sql, {
        'shipment_id': shipment_id, 
        'route_stop_id': route_stop_id
    })
    return [dict(row._mapping) for row in result]


def get_stop_pod_records(shipment_id: int, route_stop_id: int) -> List[Dict]:
    """Get POD records for a specific stop"""
    sql = text("""
        SELECT id, collected_at, collected_by, receiver_name, photo_paths, notes
        FROM pod_records
        WHERE route_id = :shipment_id 
          AND route_stop_id = :route_stop_id
        ORDER BY collected_at DESC
    """)
    result = db.session.execute(sql, {
        'shipment_id': shipment_id, 
        'route_stop_id': route_stop_id
    })
    return [dict(row._mapping) for row in result]


def get_exceptions_report(shipment_id: int) -> List[Dict]:
    """Get exceptions that need attention including full discrepancy details"""
    sql = text("""
        SELECT
            rsi.invoice_no,
            rsi.status,
            rs.seq_no,
            rs.stop_name,
            COALESCE(ipdc.status, 'NONE') AS post_delivery_case,
            CASE WHEN d.id IS NOT NULL THEN 'OPEN' ELSE 'N/A' END AS discrepancy_status,
            cr.variance AS cod_variance,
            cr.ps365_synced_at,
            d.id AS discrepancy_id,
            d.discrepancy_type,
            d.item_code_expected,
            d.item_name,
            d.qty_expected,
            d.qty_actual,
            d.reported_value,
            d.deduct_amount,
            d.warehouse_checked_at,
            d.warehouse_result,
            d.warehouse_note,
            d.credit_note_required,
            d.credit_note_no,
            d.credit_note_amount,
            dt.cn_required AS type_cn_required,
            dt.return_expected AS type_return_expected
        FROM route_stop rs
        JOIN route_stop_invoice rsi 
            ON rsi.route_stop_id = rs.route_stop_id 
            AND rsi.is_active = true
        LEFT JOIN invoice_post_delivery_cases ipdc 
            ON ipdc.invoice_no = rsi.invoice_no AND ipdc.status = 'OPEN'
        LEFT JOIN delivery_discrepancies d 
            ON d.invoice_no = rsi.invoice_no AND d.is_resolved = false
        LEFT JOIN discrepancy_types dt
            ON dt.name = d.discrepancy_type
        LEFT JOIN cod_receipts cr 
            ON cr.route_id = rs.shipment_id AND cr.route_stop_id = rs.route_stop_id
        WHERE rs.shipment_id = :shipment_id
          AND rs.deleted_at IS NULL
          AND (
            rsi.status IS NULL
            OR rsi.status NOT IN ('DELIVERED', 'FAILED', 'PARTIAL', 'RETURNED', 'SKIPPED')
            OR ipdc.status = 'OPEN'
            OR d.id IS NOT NULL
            OR (cr.variance IS NOT NULL AND cr.variance <> 0)
            OR (cr.id IS NOT NULL AND cr.ps365_synced_at IS NULL)
          )
        ORDER BY rs.seq_no, rsi.invoice_no
    """)
    result = db.session.execute(sql, {'shipment_id': shipment_id})
    return [dict(row._mapping) for row in result]


def refresh_reconciliation(shipment_id: int) -> Dict:
    """
    Refresh reconciliation status for a shipment.
    Computes all checks and updates shipments table.
    Returns issues dict with blocking/warning lists.
    """
    shipment = db.session.get(Shipment, shipment_id)
    if not shipment:
        raise ValueError(f"Shipment {shipment_id} not found")
    
    issues = {
        'blocking': [],
        'warnings': [],
        'summary': {}
    }
    
    # Check for blocking issues
    missing_status = check_missing_final_status(shipment_id)
    if missing_status:
        issues['blocking'].append({
            'type': 'MISSING_FINAL_STATUS',
            'message': f"{len(missing_status)} invoice(s) without final delivery status",
            'details': missing_status
        })
    
    missing_pod = check_missing_pod(shipment_id)
    if missing_pod:
        issues['blocking'].append({
            'type': 'MISSING_POD',
            'message': f"{len(missing_pod)} delivered invoice(s) missing POD",
            'details': missing_pod
        })
    
    open_cases = check_open_post_delivery_cases(shipment_id)
    if open_cases:
        issues['blocking'].append({
            'type': 'OPEN_CASES',
            'message': f"{len(open_cases)} open post-delivery case(s)",
            'details': open_cases
        })
    
    # Check failed invoices without driver handover
    failed_no_handover = check_failed_without_driver_handover(shipment_id)
    if failed_no_handover:
        issues['blocking'].append({
            'type': 'FAILED_NO_DRIVER_HANDOVER',
            'message': f"{len(failed_no_handover)} failed invoice(s) without driver return handover",
            'details': failed_no_handover
        })
    
    # Check failed invoices without warehouse receipt
    failed_no_warehouse = check_failed_without_warehouse_receipt(shipment_id)
    if failed_no_warehouse:
        issues['blocking'].append({
            'type': 'FAILED_NO_WAREHOUSE_RECEIPT',
            'message': f"{len(failed_no_warehouse)} failed invoice(s) without warehouse receipt confirmation",
            'details': failed_no_warehouse
        })
    
    # Get cash totals
    cash = get_cash_totals(shipment_id)
    
    # Update shipment cash fields
    shipment.cash_expected = cash['cash_expected']
    shipment.cash_collected = cash['cash_collected']
    
    # Warning for unsynced receipts
    if cash['not_synced_count'] > 0:
        issues['warnings'].append({
            'type': 'RECEIPTS_NOT_SYNCED',
            'message': f"{cash['not_synced_count']} COD receipt(s) not synced to PS365"
        })
    
    # Warning for cash variance
    if cash['cash_variance_sum'] != 0:
        issues['warnings'].append({
            'type': 'CASH_VARIANCE',
            'message': f"Cash variance of {cash['cash_variance_sum']}"
        })
    
    # Set reconciliation status based on blocking issues
    # Only auto-upgrade from NOT_READY to PENDING when route is complete and no blocking issues
    if shipment.status in ('COMPLETED', 'completed') and not issues['blocking']:
        if shipment.reconciliation_status == 'NOT_READY':
            shipment.reconciliation_status = 'PENDING'
    # Only reset to NOT_READY if currently NOT_READY or PENDING (never downgrade from IN_REVIEW or RECONCILED)
    elif issues['blocking']:
        if shipment.reconciliation_status in ('NOT_READY', 'PENDING'):
            shipment.reconciliation_status = 'NOT_READY'
    
    # Get summary
    issues['summary'] = get_reconciliation_summary(shipment_id)
    
    db.session.commit()
    logger.info(f"Refreshed reconciliation for shipment {shipment_id}: status={shipment.reconciliation_status}")
    
    return issues


def submit_route(shipment_id: int, actor: str, cash_handed_in: Decimal, notes: Optional[str] = None) -> Dict:
    """
    Driver submits route after completion.
    Sets driver_submitted_at, cash_handed_in, updates status.
    """
    shipment = db.session.get(Shipment, shipment_id)
    if not shipment:
        raise ValueError(f"Shipment {shipment_id} not found")
    
    # Refresh first to check for blocking issues
    issues = refresh_reconciliation(shipment_id)
    
    if issues['blocking']:
        return {
            'success': False,
            'message': 'Cannot submit route with blocking issues',
            'issues': issues
        }
    
    now = get_utc_now()
    shipment.driver_submitted_at = now
    shipment.cash_handed_in = cash_handed_in
    shipment.settlement_notes = notes
    shipment.reconciliation_status = 'PENDING'
    shipment.settlement_status = 'DRIVER_SUBMITTED'
    
    # Calculate handover variance
    if shipment.cash_collected:
        shipment.cash_variance = cash_handed_in - shipment.cash_collected
    
    db.session.commit()
    logger.info(f"Route {shipment_id} submitted by driver {actor}")
    
    return {
        'success': True,
        'message': 'Route submitted successfully',
        'issues': issues
    }


def start_review(shipment_id: int, actor: str) -> Dict:
    """Admin starts reviewing a submitted route"""
    shipment = db.session.get(Shipment, shipment_id)
    if not shipment:
        raise ValueError(f"Shipment {shipment_id} not found")
    
    if shipment.reconciliation_status not in ('PENDING', 'NOT_READY'):
        return {
            'success': False,
            'message': f'Cannot start review: status is {shipment.reconciliation_status}'
        }
    
    shipment.reconciliation_status = 'IN_REVIEW'
    db.session.commit()
    logger.info(f"Reconciliation review started for shipment {shipment_id} by {actor}")
    
    return {'success': True, 'message': 'Review started'}


def finalize_reconciliation(shipment_id: int, actor: str) -> Dict:
    """Admin finalizes reconciliation (all issues resolved)"""
    shipment = db.session.get(Shipment, shipment_id)
    if not shipment:
        raise ValueError(f"Shipment {shipment_id} not found")
    
    # Refresh to ensure no blocking issues
    issues = refresh_reconciliation(shipment_id)
    
    if issues['blocking']:
        return {
            'success': False,
            'message': 'Cannot finalize with blocking issues',
            'issues': issues
        }
    
    now = get_utc_now()
    shipment.reconciliation_status = 'RECONCILED'
    shipment.reconciled_at = now
    shipment.reconciled_by = actor
    
    db.session.commit()
    logger.info(f"Reconciliation finalized for shipment {shipment_id} by {actor}")
    
    return {'success': True, 'message': 'Reconciliation completed'}


def clear_settlement(shipment_id: int, actor: str) -> Dict:
    """Finance clears settlement after receipts synced"""
    shipment = db.session.get(Shipment, shipment_id)
    if not shipment:
        raise ValueError(f"Shipment {shipment_id} not found")
    
    if shipment.reconciliation_status != 'RECONCILED':
        return {
            'success': False,
            'message': 'Reconciliation must be completed before clearing settlement'
        }
    
    now = get_utc_now()
    shipment.settlement_status = 'CLEARED'
    shipment.settlement_cleared_at = now
    shipment.settlement_cleared_by = actor
    
    db.session.commit()
    logger.info(f"Settlement cleared for shipment {shipment_id} by {actor}")
    
    return {'success': True, 'message': 'Settlement cleared'}


def get_reroute_audit(date_from: str, date_to: str) -> List[Dict]:
    """Get reroute audit trail for date range"""
    sql = text("""
        SELECT invoice_no, action, reason, notes, actor_username, created_at
        FROM invoice_route_history
        WHERE action = 'REROUTED'
          AND created_at::date BETWEEN :d1 AND :d2
        ORDER BY created_at DESC
    """)
    result = db.session.execute(sql, {'d1': date_from, 'd2': date_to})
    return [dict(row._mapping) for row in result]


def clear_pending_payment(allocation_id: int, actor: str) -> Dict:
    """Clear a specific pending payment allocation"""
    from models import CODInvoiceAllocation, CODReceipt
    alloc = db.session.get(CODInvoiceAllocation, allocation_id)
    if not alloc:
        return {'success': False, 'message': 'Allocation not found'}
    
    alloc.is_pending = False
    
    # Also update the parent receipt if all its allocations are now cleared
    receipt = db.session.get(CODReceipt, alloc.cod_receipt_id)
    if receipt:
        # Check if any other allocations for this receipt are still pending
        still_pending = CODInvoiceAllocation.query.filter(
            CODInvoiceAllocation.cod_receipt_id == receipt.id,
            CODInvoiceAllocation.is_pending == True,
            CODInvoiceAllocation.id != allocation_id
        ).count()
        
        if still_pending == 0:
            receipt.is_pending = False
            
    db.session.commit()
    logger.info(f"Pending payment {allocation_id} cleared by {actor}")
    return {'success': True, 'message': 'Payment cleared successfully'}


def check_failed_without_driver_handover(shipment_id: int) -> List[Dict]:
    """Find FAILED invoices without driver return handover confirmation"""
    sql = text("""
        SELECT rsi.invoice_no, rs.seq_no, rs.stop_name
        FROM route_stop rs
        JOIN route_stop_invoice rsi 
            ON rsi.route_stop_id = rs.route_stop_id 
            AND rsi.is_active = true
        LEFT JOIN route_return_handover rrh
            ON rrh.route_id = rs.shipment_id
            AND rrh.invoice_no = rsi.invoice_no
        WHERE rs.shipment_id = :shipment_id
          AND rs.deleted_at IS NULL
          AND UPPER(rsi.status) = 'FAILED'
          AND (rrh.driver_confirmed_at IS NULL OR rrh.id IS NULL)
    """)
    result = db.session.execute(sql, {'shipment_id': shipment_id})
    return [dict(row._mapping) for row in result]


def check_failed_without_warehouse_receipt(shipment_id: int) -> List[Dict]:
    """Find FAILED invoices with driver handover but no warehouse receipt"""
    sql = text("""
        SELECT rsi.invoice_no, rs.seq_no, rs.stop_name, rrh.driver_confirmed_at
        FROM route_stop rs
        JOIN route_stop_invoice rsi 
            ON rsi.route_stop_id = rs.route_stop_id 
            AND rsi.is_active = true
        JOIN route_return_handover rrh
            ON rrh.route_id = rs.shipment_id
            AND rrh.invoice_no = rsi.invoice_no
        WHERE rs.shipment_id = :shipment_id
          AND rs.deleted_at IS NULL
          AND UPPER(rsi.status) = 'FAILED'
          AND rrh.driver_confirmed_at IS NOT NULL
          AND rrh.warehouse_received_at IS NULL
    """)
    result = db.session.execute(sql, {'shipment_id': shipment_id})
    return [dict(row._mapping) for row in result]


def check_discrepancies_needing_credit_note(shipment_id: int) -> List[Dict]:
    """Find validated discrepancies where credit note is required but not issued"""
    sql = text("""
        SELECT d.id, d.invoice_no, d.item_code_expected, d.discrepancy_type, 
               d.warehouse_result, d.credit_note_amount
        FROM delivery_discrepancies d
        JOIN route_stop_invoice rsi
            ON rsi.invoice_no = d.invoice_no
            AND rsi.is_active = true
        JOIN route_stop rs
            ON rs.route_stop_id = rsi.route_stop_id
        WHERE rs.shipment_id = :shipment_id
          AND rs.deleted_at IS NULL
          AND d.is_validated = true
          AND d.credit_note_required = true
          AND d.credit_note_no IS NULL
    """)
    result = db.session.execute(sql, {'shipment_id': shipment_id})
    return [dict(row._mapping) for row in result]


def check_discrepancies_needing_warehouse_check(shipment_id: int) -> List[Dict]:
    """Find discrepancies that need warehouse verification"""
    sql = text("""
        SELECT d.id, d.invoice_no, d.item_code_expected, d.discrepancy_type, d.reported_at
        FROM delivery_discrepancies d
        JOIN route_stop_invoice rsi
            ON rsi.invoice_no = d.invoice_no
            AND rsi.is_active = true
        JOIN route_stop rs
            ON rs.route_stop_id = rsi.route_stop_id
        WHERE rs.shipment_id = :shipment_id
          AND rs.deleted_at IS NULL
          AND d.warehouse_checked_at IS NULL
    """)
    result = db.session.execute(sql, {'shipment_id': shipment_id})
    return [dict(row._mapping) for row in result]


def build_route_reconciliation(shipment_id: int) -> Dict:
    """
    Build complete reconciliation dataset for a route.
    Powers both the UI and Excel export.
    
    Returns:
        invoice_list: List of invoices with full reconciliation data
        summary: POD vs CREDIT totals
        exceptions: Blocking issues
        return_handover_status: Summary of return handovers
    """
    sql = text("""
        SELECT 
            rsi.invoice_no,
            rsi.status AS delivery_status,
            rsi.expected_payment_method,
            rsi.expected_amount,
            rsi.manifest_locked_at,
            rs.seq_no AS stop_seq,
            rs.route_stop_id,
            rs.stop_name,
            i.customer_code_365 AS customer_code,
            i.customer_name,
            i.total_grand AS invoice_total,
            ct.is_credit,
            ct.terms_code
        FROM route_stop rs
        JOIN route_stop_invoice rsi 
            ON rsi.route_stop_id = rs.route_stop_id 
            AND rsi.is_active = true
        JOIN invoices i ON i.invoice_no = rsi.invoice_no
        LEFT JOIN credit_terms ct 
            ON ct.customer_code = i.customer_code_365
            AND ct.valid_to IS NULL
        WHERE rs.shipment_id = :shipment_id
          AND rs.deleted_at IS NULL
        ORDER BY rs.seq_no, rsi.invoice_no
    """)
    invoices_result = db.session.execute(sql, {'shipment_id': shipment_id})
    invoices_raw = [dict(row._mapping) for row in invoices_result]
    
    invoice_nos = [inv['invoice_no'] for inv in invoices_raw]
    
    cod_sql = text("""
        SELECT cr.invoice_nos, cr.payment_method, cr.received_amount, cr.route_stop_id
        FROM cod_receipts cr
        WHERE cr.route_id = :shipment_id
    """)
    cod_result = db.session.execute(cod_sql, {'shipment_id': shipment_id})
    
    invoice_payments = {}
    for row in cod_result:
        inv_nos = row.invoice_nos if row.invoice_nos else []
        if isinstance(inv_nos, str):
            import json
            inv_nos = json.loads(inv_nos)
        for inv_no in inv_nos:
            if inv_no not in invoice_payments:
                invoice_payments[inv_no] = []
            invoice_payments[inv_no].append({
                'payment_method': row.payment_method,
                'amount': float(row.received_amount) if row.received_amount else 0,
                'route_stop_id': row.route_stop_id
            })
    
    pod_sql = text("""
        SELECT pr.route_stop_id, pr.id
        FROM pod_records pr
        WHERE pr.route_id = :shipment_id
    """)
    pod_result = db.session.execute(pod_sql, {'shipment_id': shipment_id})
    stops_with_pod = {row.route_stop_id for row in pod_result}
    
    handover_sql = text("""
        SELECT rrh.invoice_no, rrh.driver_confirmed_at, rrh.warehouse_received_at, 
               rrh.packages_count, rrh.notes
        FROM route_return_handover rrh
        WHERE rrh.route_id = :shipment_id
    """)
    handover_result = db.session.execute(handover_sql, {'shipment_id': shipment_id})
    handovers = {row.invoice_no: dict(row._mapping) for row in handover_result}
    
    disc_sql = text("""
        SELECT d.invoice_no, 
               COUNT(*) AS disc_count,
               SUM(CASE WHEN d.is_validated THEN 1 ELSE 0 END) AS validated_count,
               SUM(CASE WHEN d.credit_note_required AND d.credit_note_no IS NULL THEN 1 ELSE 0 END) AS cn_pending,
               SUM(CASE WHEN d.warehouse_checked_at IS NULL THEN 1 ELSE 0 END) AS needs_warehouse_check
        FROM delivery_discrepancies d
        WHERE d.invoice_no = ANY(:invoice_nos)
        GROUP BY d.invoice_no
    """)
    disc_result = db.session.execute(disc_sql, {'invoice_nos': invoice_nos})
    discrepancies = {row.invoice_no: dict(row._mapping) for row in disc_result}
    
    invoices = []
    pod_total = Decimal('0')
    pod_count = 0
    credit_total = Decimal('0')
    credit_count = 0
    
    for inv in invoices_raw:
        inv_no = inv['invoice_no']
        
        payment_group = 'CREDIT' if inv.get('is_credit') else 'POD'
        
        payments = invoice_payments.get(inv_no, [])
        actual_payment_method = payments[0]['payment_method'] if payments else None
        payment_amount = sum(p['amount'] for p in payments)
        
        has_pod = inv['route_stop_id'] in stops_with_pod
        
        handover = handovers.get(inv_no)
        
        disc = discrepancies.get(inv_no, {})
        
        invoice_data = {
            'invoice_no': inv_no,
            'stop_seq': inv['stop_seq'],
            'customer_code': inv['customer_code'],
            'customer_name': inv['customer_name'],
            'invoice_total': float(inv['invoice_total']) if inv['invoice_total'] else 0,
            'expected_payment_method': inv['expected_payment_method'],
            'expected_amount': float(inv['expected_amount']) if inv['expected_amount'] else 0,
            'delivery_status': inv['delivery_status'],
            'payment_group': payment_group,
            'actual_payment_method': actual_payment_method,
            'payment_received': payment_amount,
            'has_pod_evidence': has_pod,
            'has_payment_evidence': len(payments) > 0,
            'return_handover': {
                'driver_confirmed': handover['driver_confirmed_at'] is not None if handover else False,
                'warehouse_received': handover['warehouse_received_at'] is not None if handover else False,
                'packages_count': handover.get('packages_count') if handover else None
            } if inv['delivery_status'] and inv['delivery_status'].upper() == 'FAILED' else None,
            'discrepancy_count': disc.get('disc_count', 0),
            'discrepancies_validated': disc.get('validated_count', 0),
            'credit_notes_pending': disc.get('cn_pending', 0),
            'needs_warehouse_check': disc.get('needs_warehouse_check', 0) > 0
        }
        
        if payment_group == 'POD':
            pod_total += Decimal(str(inv['invoice_total'] or 0))
            pod_count += 1
        else:
            credit_total += Decimal(str(inv['invoice_total'] or 0))
            credit_count += 1
        
        invoices.append(invoice_data)
    
    failed_no_driver = check_failed_without_driver_handover(shipment_id)
    failed_no_warehouse = check_failed_without_warehouse_receipt(shipment_id)
    missing_status = check_missing_final_status(shipment_id)
    
    exceptions = {
        'missing_final_status': missing_status,
        'failed_without_driver_handover': failed_no_driver,
        'failed_without_warehouse_receipt': failed_no_warehouse,
    }
    
    blocking_count = (
        len(missing_status) + 
        len(failed_no_driver) + 
        len(failed_no_warehouse)
    )
    
    return {
        'invoices': invoices,
        'summary': {
            'pod_count': pod_count,
            'pod_total': float(pod_total),
            'credit_count': credit_count,
            'credit_total': float(credit_total),
            'total_invoices': len(invoices),
            'total_value': float(pod_total + credit_total)
        },
        'exceptions': exceptions,
        'is_reconcilable': blocking_count == 0,
        'blocking_count': blocking_count
    }
