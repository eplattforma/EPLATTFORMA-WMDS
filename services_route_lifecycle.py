"""
Route Lifecycle Service

Handles the separation of concerns between:
- Operational completion (driver finished route work)
- Administrative reconciliation (cash/receipts/returns/POD verified)
- Archiving (post-reconciliation storage)
"""

from app import db
from models import Shipment, RouteStop, RouteStopInvoice
from timezone_utils import get_utc_now
from sqlalchemy import func
from delivery_status import TERMINAL_DELIVERY_STATUSES, normalize_status
import logging


def recompute_route_completion(route_id, *, commit: bool = True):
    """
    Automatically determine if a route is operationally complete.
    
    A route is COMPLETED when all RouteStopInvoice statuses are terminal
    (delivered, delivery_failed, or returned_to_warehouse). This is called after every driver action.
    
    Args:
        route_id: The shipment/route ID to check
        commit: Whether to commit the transaction (default True)
    
    Returns:
        dict: {"pending_count": int, "route_status": str, "status_changed": bool}
    """
    shipment = Shipment.query.get(route_id)
    if not shipment:
        logging.warning(f"Route {route_id} not found for completion check")
        return {"pending_count": -1, "route_status": None, "status_changed": False}
    
    if shipment.status in ("CANCELLED",):
        return {"pending_count": 0, "route_status": shipment.status, "status_changed": False}
    
    # Count pending invoices using case-insensitive comparison
    # An invoice is pending if status is NULL or not in the terminal set
    pending_count = db.session.query(RouteStopInvoice).join(RouteStop).filter(
        RouteStop.shipment_id == route_id,
        RouteStop.deleted_at == None,
        db.or_(
            RouteStopInvoice.status.is_(None),
            func.lower(RouteStopInvoice.status).notin_(TERMINAL_DELIVERY_STATUSES)
        )
    ).count()
    
    status_changed = False
    
    if pending_count == 0:
        if shipment.status != "COMPLETED":
            shipment.status = "COMPLETED"
            if shipment.completed_at is None:
                shipment.completed_at = get_utc_now()
            status_changed = True
            logging.info(f"Route {route_id} marked as COMPLETED (all invoices terminal)")
        
        if shipment.reconciliation_status in (None, "NOT_READY"):
            shipment.reconciliation_status = "PENDING"
            logging.info(f"Route {route_id} reconciliation_status set to PENDING")
    else:
        if shipment.status == "COMPLETED":
            shipment.status = "IN_TRANSIT"
            shipment.completed_at = None
            shipment.reconciliation_status = "NOT_READY"
            status_changed = True
            logging.info(f"Route {route_id} reopened - status back to IN_TRANSIT")
    
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    
    return {"pending_count": pending_count, "route_status": shipment.status, "status_changed": status_changed}


def get_route_reconciliation_summary(route_id):
    """
    Get a summary of all items that need to be checked before reconciliation.
    
    Returns:
        dict: Summary with cash, POD, returns, discrepancies info
    """
    from models import CODReceipt, PODRecord, DeliveryDiscrepancy, InvoicePostDeliveryCase
    
    shipment = Shipment.query.get(route_id)
    if not shipment:
        return None
    
    invoices = db.session.query(RouteStopInvoice).join(RouteStop).filter(
        RouteStop.shipment_id == route_id,
        RouteStop.deleted_at == None
    ).all()

    invoice_nos = [inv.invoice_no for inv in invoices] if invoices else []
    
    # Use normalize_status for case-insensitive counting
    delivered_count = sum(1 for inv in invoices if normalize_status(inv.status) == 'delivered')
    failed_count = sum(1 for inv in invoices if normalize_status(inv.status) == 'delivery_failed')
    returned_count = sum(1 for inv in invoices if normalize_status(inv.status) == 'returned_to_warehouse')
    pending_count = sum(1 for inv in invoices if normalize_status(inv.status) not in TERMINAL_DELIVERY_STATUSES)
    
    cod_receipts = CODReceipt.query.filter(
        CODReceipt.route_id == route_id
    ).all() if route_id else []
    
    # Calculate cash totals from COD receipts (actual data source)
    cash_expected_from_receipts = sum(float(r.expected_amount or 0) for r in cod_receipts)
    cash_collected_from_receipts = sum(float(r.received_amount or 0) for r in cod_receipts)
    cash_variance_from_receipts = cash_collected_from_receipts - cash_expected_from_receipts
    
    pod_records = PODRecord.query.filter(
        PODRecord.route_id == route_id
    ).all() if route_id else []
    
    unresolved_discrepancies = DeliveryDiscrepancy.query.filter(
        DeliveryDiscrepancy.invoice_no.in_(invoice_nos),
        DeliveryDiscrepancy.is_resolved == False
    ).count() if invoice_nos else 0
    
    open_cases = InvoicePostDeliveryCase.query.filter(
        InvoicePostDeliveryCase.route_id == route_id,
        InvoicePostDeliveryCase.status.notin_(["CLOSED", "CANCELLED"])
    ).count() if route_id else 0
    
    return {
        "route_id": route_id,
        "status": shipment.status,
        "reconciliation_status": shipment.reconciliation_status,
        "settlement_status": shipment.settlement_status,
        "invoices": {
            "total": len(invoices),
            "delivered": delivered_count,
            "failed": failed_count,
            "returned": returned_count,
            "pending": pending_count
        },
        "cash": {
            "expected": cash_expected_from_receipts,
            "handed_in": cash_collected_from_receipts,
            "variance": cash_variance_from_receipts,
            "variance_note": shipment.cash_variance_note
        },
        "cod_receipts_count": len(cod_receipts),
        "pod_records_count": len(pod_records),
        "unresolved_discrepancies": unresolved_discrepancies,
        "open_post_delivery_cases": open_cases,
        "returns": {
            "count": shipment.returns_count or 0,
            "weight": shipment.returns_weight
        },
        "is_ready_for_reconciliation": (
            shipment.status == "COMPLETED" and
            pending_count == 0
        ),
        "blocking_issues": _get_blocking_issues(shipment, pending_count, unresolved_discrepancies, open_cases)
    }


def _get_blocking_issues(shipment, pending_count, unresolved_discrepancies, open_cases):
    """Get list of issues that block reconciliation"""
    issues = []
    
    if shipment.status != "COMPLETED":
        issues.append(f"Route is not completed (status: {shipment.status})")
    
    if pending_count > 0:
        issues.append(f"{pending_count} invoice(s) still pending delivery")
    
    if shipment.cash_variance and shipment.cash_variance != 0 and not shipment.cash_variance_note:
        issues.append("Cash variance requires explanation note")
    
    if unresolved_discrepancies > 0:
        issues.append(f"{unresolved_discrepancies} unresolved delivery discrepancy(ies)")
    
    if open_cases > 0:
        issues.append(f"{open_cases} open post-delivery case(s)")
    
    return issues


def start_reconciliation_review(route_id, admin_username):
    """
    Move route to IN_REVIEW status when admin starts reviewing.
    
    Args:
        route_id: The shipment/route ID
        admin_username: Username of the admin starting review
    
    Returns:
        tuple: (success, message)
    """
    shipment = Shipment.query.get(route_id)
    if not shipment:
        return False, "Route not found"
    
    if shipment.status != "COMPLETED":
        return False, f"Route must be completed before review (current: {shipment.status})"
    
    if shipment.reconciliation_status == "RECONCILED":
        return False, "Route is already reconciled"
    
    shipment.reconciliation_status = "IN_REVIEW"
    db.session.commit()
    
    logging.info(f"Route {route_id} reconciliation started by {admin_username}")
    return True, "Reconciliation review started"


def reconcile_route(route_id, admin_username, force=False):
    """
    Finalize route reconciliation and optionally archive.
    
    Requirements:
    - Route must be COMPLETED
    - Settlement must be SETTLED (unless force=True)
    - No unresolved issues (unless force=True)
    
    Args:
        route_id: The shipment/route ID
        admin_username: Username of the admin reconciling
        force: If True, skip some validation checks (use with caution)
    
    Returns:
        tuple: (success, message)
    """
    shipment = Shipment.query.get(route_id)
    if not shipment:
        return False, "Route not found"
    
    if shipment.status != "COMPLETED":
        return False, f"Route must be completed before reconciliation (current: {shipment.status})"
    
    if shipment.reconciliation_status == "RECONCILED":
        return False, "Route is already reconciled"
    
    if not force:
        # Check settlement is cleared
        if shipment.settlement_status != "SETTLED":
            return False, f"Settlement must be cleared before reconciliation (current: {shipment.settlement_status})"
        
        if shipment.cash_variance and shipment.cash_variance != 0 and not shipment.cash_variance_note:
            return False, "Cash variance note is required when there is a variance"
    
    # Set reconciliation status - do NOT change settlement_status here
    shipment.reconciliation_status = "RECONCILED"
    shipment.reconciled_at = get_utc_now()
    shipment.reconciled_by = admin_username
    
    # Archive the route after reconciliation
    shipment.is_archived = True
    shipment.archived_at = get_utc_now()
    shipment.archived_by = admin_username
    
    db.session.commit()
    
    logging.info(f"Route {route_id} reconciled and archived by {admin_username}")
    return True, "Route reconciled and archived successfully"


def unarchive_route(route_id, admin_username):
    """
    Unarchive a route (e.g., if issues are discovered post-reconciliation).
    
    Args:
        route_id: The shipment/route ID
        admin_username: Username of the admin unarchiving
    
    Returns:
        tuple: (success, message)
    """
    shipment = Shipment.query.get(route_id)
    if not shipment:
        return False, "Route not found"
    
    if not shipment.is_archived:
        return False, "Route is not archived"
    
    shipment.is_archived = False
    shipment.archived_at = None
    shipment.archived_by = None
    shipment.reconciliation_status = "IN_REVIEW"
    
    db.session.commit()
    
    logging.info(f"Route {route_id} unarchived by {admin_username} for re-review")
    return True, "Route unarchived and moved back to review"


def get_dashboard_routes(user_role=None, driver_username=None):
    """
    Get routes grouped by dashboard section.
    
    Returns:
        dict: Routes grouped into in_progress, pending_reconciliation, archived
    """
    base_query = Shipment.query.filter(Shipment.deleted_at == None)
    
    if user_role == 'driver' and driver_username:
        base_query = base_query.filter(Shipment.driver_name == driver_username)
    
    in_progress = base_query.filter(
        Shipment.is_archived == False,
        Shipment.status.in_(['PLANNED', 'DISPATCHED', 'IN_TRANSIT', 'created'])
    ).order_by(Shipment.delivery_date.desc()).all()
    
    pending_reconciliation = base_query.filter(
        Shipment.is_archived == False,
        Shipment.status == 'COMPLETED',
        Shipment.reconciliation_status != 'RECONCILED'
    ).order_by(Shipment.completed_at.desc()).all()
    
    archived = base_query.filter(
        Shipment.is_archived == True
    ).order_by(Shipment.archived_at.desc()).limit(50).all()
    
    return {
        "in_progress": in_progress,
        "pending_reconciliation": pending_reconciliation,
        "archived": archived
    }
