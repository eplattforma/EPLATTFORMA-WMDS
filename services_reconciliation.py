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
          AND (rsi.status IS NULL OR rsi.status NOT IN ('DELIVERED','FAILED','PARTIAL','RETURNED','SKIPPED'))
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
          AND rsi.status = 'DELIVERED'
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
              AND rsi.status = 'DELIVERED'
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
            (SELECT COUNT(*) FROM inv WHERE status = 'DELIVERED') AS delivered,
            (SELECT COUNT(*) FROM inv WHERE status = 'FAILED') AS failed,
            (SELECT COUNT(*) FROM inv WHERE status = 'PARTIAL') AS partial,
            (SELECT COUNT(*) FROM inv WHERE status IS NULL OR status NOT IN ('DELIVERED','FAILED','PARTIAL','RETURNED','SKIPPED')) AS pending,
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
    """Get exceptions that need attention"""
    sql = text("""
        SELECT
            rsi.invoice_no,
            rsi.status,
            rs.seq_no,
            rs.stop_name,
            COALESCE(ipdc.status, 'NONE') AS post_delivery_case,
            CASE WHEN d.id IS NOT NULL THEN 'OPEN' ELSE 'N/A' END AS discrepancy_status,
            cr.variance AS cod_variance,
            cr.ps365_synced_at
        FROM route_stop rs
        JOIN route_stop_invoice rsi 
            ON rsi.route_stop_id = rs.route_stop_id 
            AND rsi.is_active = true
        LEFT JOIN invoice_post_delivery_cases ipdc 
            ON ipdc.invoice_no = rsi.invoice_no AND ipdc.status = 'OPEN'
        LEFT JOIN delivery_discrepancies d 
            ON d.invoice_no = rsi.invoice_no AND d.is_resolved = false
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
    
    unresolved = check_unresolved_discrepancies(shipment_id)
    if unresolved:
        issues['blocking'].append({
            'type': 'UNRESOLVED_DISCREPANCIES',
            'message': f"{len(unresolved)} unresolved discrepanc(ies)",
            'details': unresolved
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
    if shipment.status in ('COMPLETED', 'completed') and not issues['blocking']:
        if shipment.reconciliation_status == 'NOT_READY':
            shipment.reconciliation_status = 'PENDING'
    elif issues['blocking']:
        if shipment.reconciliation_status not in ('RECONCILED',):
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
