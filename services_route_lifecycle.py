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
    # ONLY consider active mappings (is_active=True)
    pending_count = db.session.query(RouteStopInvoice).join(RouteStop).filter(
        RouteStop.shipment_id == route_id,
        RouteStop.deleted_at == None,
        RouteStopInvoice.is_active == True,
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


# =============================================================================
# CANONICAL INVOICE-TO-STOP MAPPING FUNCTIONS
# =============================================================================

def assign_invoice_to_stop(invoice_no: str, route_stop_id: int, actor: str, 
                           status: str = "PENDING", weight_kg: float = None,
                           notes: str = None, commit: bool = True):
    """
    Assign an invoice to a route stop (initial assignment).
    
    This creates an active mapping in route_stop_invoice and updates
    the cache columns on invoices for backward compatibility.
    
    Args:
        invoice_no: The invoice number to assign
        route_stop_id: The route stop ID to assign to
        actor: Username performing the action
        status: Initial status (default PENDING)
        weight_kg: Optional weight in kg
        notes: Optional notes
        commit: Whether to commit the transaction
    
    Returns:
        RouteStopInvoice: The created mapping
    
    Raises:
        ValueError: If invoice or stop not found, or invoice already assigned
    """
    from models import Invoice, InvoiceRouteHistory
    
    stop = RouteStop.query.get(route_stop_id)
    if not stop:
        raise ValueError(f"Route stop {route_stop_id} not found")
    
    invoice = Invoice.query.get(invoice_no)
    if not invoice:
        raise ValueError(f"Invoice {invoice_no} not found")
    
    now = get_utc_now()
    
    # Check for existing active mapping
    active = RouteStopInvoice.query.filter_by(invoice_no=invoice_no, is_active=True).first()
    if active:
        # Close existing mapping (this handles re-assignment case)
        active.is_active = False
        active.effective_to = now
        active.changed_by = actor
        logging.info(f"Closed existing mapping for {invoice_no} from stop {active.route_stop_id}")
    
    # Create new active mapping
    rsi = RouteStopInvoice(
        route_stop_id=route_stop_id,
        invoice_no=invoice_no,
        status=status,
        weight_kg=weight_kg,
        notes=notes,
        is_active=True,
        effective_from=now,
        changed_by=actor
    )
    db.session.add(rsi)
    
    # Update cache columns on invoice for backward compatibility
    invoice.stop_id = route_stop_id
    invoice.route_id = stop.shipment_id
    
    # Add audit history
    db.session.add(InvoiceRouteHistory(
        invoice_no=invoice_no,
        route_id=stop.shipment_id,
        route_stop_id=route_stop_id,
        action="ASSIGNED",
        reason=None,
        notes=notes,
        actor_username=actor
    ))
    
    if commit:
        db.session.commit()
    
    logging.info(f"Invoice {invoice_no} assigned to stop {route_stop_id} by {actor}")
    return rsi


def reroute_invoice(invoice_no: str, new_route_stop_id: int, actor: str,
                    reason: str = None, notes: str = None, commit: bool = True):
    """
    Reroute an invoice from its current stop to a new stop.
    
    This closes the old active mapping and creates a new one,
    preserving full history for reconciliation.
    
    Args:
        invoice_no: The invoice number to reroute
        new_route_stop_id: The new route stop ID
        actor: Username performing the action
        reason: Optional reason for rerouting
        notes: Optional additional notes
        commit: Whether to commit the transaction
    
    Returns:
        RouteStopInvoice: The new active mapping
    
    Raises:
        ValueError: If invoice has no active mapping or new stop not found
    """
    from models import Invoice, InvoiceRouteHistory
    
    now = get_utc_now()
    
    # Find current active mapping
    old = RouteStopInvoice.query.filter_by(invoice_no=invoice_no, is_active=True).first()
    if not old:
        raise ValueError(f"Invoice {invoice_no} has no active stop mapping")
    
    new_stop = RouteStop.query.get(new_route_stop_id)
    if not new_stop:
        raise ValueError(f"Route stop {new_route_stop_id} not found")
    
    old_stop_id = old.route_stop_id
    old_status = old.status or "PENDING"
    
    # Close old mapping
    old.is_active = False
    old.effective_to = now
    old.changed_by = actor
    
    # Create new mapping (preserve status from old mapping)
    new_rsi = RouteStopInvoice(
        route_stop_id=new_route_stop_id,
        invoice_no=invoice_no,
        status=old_status,
        weight_kg=old.weight_kg,
        notes=old.notes,
        is_active=True,
        effective_from=now,
        changed_by=actor
    )
    db.session.add(new_rsi)
    
    # Update cache columns on invoice
    invoice = Invoice.query.get(invoice_no)
    if invoice:
        invoice.stop_id = new_route_stop_id
        invoice.route_id = new_stop.shipment_id
    
    # Add audit history
    history_notes = f"from_stop={old_stop_id}"
    if notes:
        history_notes += f"; {notes}"
    
    db.session.add(InvoiceRouteHistory(
        invoice_no=invoice_no,
        route_id=new_stop.shipment_id,
        route_stop_id=new_route_stop_id,
        action="REROUTED",
        reason=reason,
        notes=history_notes,
        actor_username=actor
    ))
    
    if commit:
        db.session.commit()
    
    logging.info(f"Invoice {invoice_no} rerouted from stop {old_stop_id} to stop {new_route_stop_id} by {actor}")
    return new_rsi


def unassign_invoice_from_route(invoice_no: str, actor: str, 
                                 reason: str = None, commit: bool = True):
    """
    Remove an invoice from its current route (close active mapping without creating new one).
    
    Args:
        invoice_no: The invoice number to unassign
        actor: Username performing the action
        reason: Optional reason for unassignment
        commit: Whether to commit the transaction
    
    Returns:
        bool: True if successful
    
    Raises:
        ValueError: If invoice has no active mapping
    """
    from models import Invoice, InvoiceRouteHistory
    
    now = get_utc_now()
    
    active = RouteStopInvoice.query.filter_by(invoice_no=invoice_no, is_active=True).first()
    if not active:
        raise ValueError(f"Invoice {invoice_no} has no active stop mapping")
    
    old_stop_id = active.route_stop_id
    old_route_id = active.stop.shipment_id if active.stop else None
    
    # Close mapping
    active.is_active = False
    active.effective_to = now
    active.changed_by = actor
    
    # Clear cache columns on invoice
    invoice = Invoice.query.get(invoice_no)
    if invoice:
        invoice.stop_id = None
        invoice.route_id = None
    
    # Add audit history
    db.session.add(InvoiceRouteHistory(
        invoice_no=invoice_no,
        route_id=old_route_id,
        route_stop_id=old_stop_id,
        action="UNASSIGNED",
        reason=reason,
        notes=None,
        actor_username=actor
    ))
    
    if commit:
        db.session.commit()
    
    logging.info(f"Invoice {invoice_no} unassigned from stop {old_stop_id} by {actor}")
    return True


def get_active_invoice_mapping(invoice_no: str):
    """
    Get the current active route mapping for an invoice.
    
    Returns:
        RouteStopInvoice | None: The active mapping or None
    """
    return RouteStopInvoice.query.filter_by(invoice_no=invoice_no, is_active=True).first()


def get_stop_active_invoices(route_stop_id: int):
    """
    Get all active invoice mappings for a stop.
    
    Returns:
        list[RouteStopInvoice]: List of active mappings
    """
    return RouteStopInvoice.query.filter_by(
        route_stop_id=route_stop_id, 
        is_active=True
    ).all()


def get_route_active_invoices(route_id: int):
    """
    Get all active invoice mappings for a route (shipment).
    
    Returns:
        list[RouteStopInvoice]: List of active mappings
    """
    return db.session.query(RouteStopInvoice).join(RouteStop).filter(
        RouteStop.shipment_id == route_id,
        RouteStop.deleted_at == None,
        RouteStopInvoice.is_active == True
    ).all()


def compute_stop_status(route_stop_id: int):
    """
    Compute aggregate status for a stop based on its active invoices.
    
    Returns:
        str: 'DELIVERED', 'FAILED', 'PARTIAL', or 'IN_PROGRESS'
    """
    invoices = get_stop_active_invoices(route_stop_id)
    if not invoices:
        return 'IN_PROGRESS'
    
    delivered_count = sum(1 for i in invoices if i.status and i.status.upper() == 'DELIVERED')
    failed_count = sum(1 for i in invoices if i.status and i.status.upper() in ('FAILED', 'RETURNED'))
    total = len(invoices)
    
    if delivered_count == total:
        return 'DELIVERED'
    elif failed_count == total:
        return 'FAILED'
    elif delivered_count + failed_count > 0:
        return 'PARTIAL'
    else:
        return 'IN_PROGRESS'


def check_route_mapping_drift(route_id: int = None):
    """
    Check for drift between route_stop_invoice canonical mapping and 
    invoices.route_id/stop_id cache columns.
    
    Args:
        route_id: Optional route ID to check (None = check all)
    
    Returns:
        list[dict]: List of invoices with drift
    """
    from models import Invoice
    
    query = db.session.query(
        Invoice.invoice_no,
        Invoice.route_id,
        Invoice.stop_id,
        RouteStopInvoice.route_stop_id,
        RouteStop.shipment_id
    ).join(
        RouteStopInvoice, 
        db.and_(
            RouteStopInvoice.invoice_no == Invoice.invoice_no,
            RouteStopInvoice.is_active == True
        )
    ).join(
        RouteStop,
        RouteStop.route_stop_id == RouteStopInvoice.route_stop_id
    ).filter(
        db.or_(
            Invoice.stop_id != RouteStopInvoice.route_stop_id,
            Invoice.route_id != RouteStop.shipment_id
        )
    )
    
    if route_id:
        query = query.filter(RouteStop.shipment_id == route_id)
    
    results = query.all()
    
    drift_list = []
    for row in results:
        drift_list.append({
            "invoice_no": row.invoice_no,
            "invoice_route_id": row.route_id,
            "invoice_stop_id": row.stop_id,
            "canonical_stop_id": row.route_stop_id,
            "canonical_route_id": row.shipment_id
        })
    
    return drift_list


def fix_route_mapping_drift(route_id: int = None, commit: bool = True):
    """
    Fix drift by updating invoice cache columns from canonical mapping.
    
    Args:
        route_id: Optional route ID to fix (None = fix all)
        commit: Whether to commit the transaction
    
    Returns:
        int: Number of invoices fixed
    """
    from models import Invoice
    
    drift_list = check_route_mapping_drift(route_id)
    
    for drift in drift_list:
        invoice = Invoice.query.get(drift["invoice_no"])
        if invoice:
            invoice.route_id = drift["canonical_route_id"]
            invoice.stop_id = drift["canonical_stop_id"]
    
    if commit and drift_list:
        db.session.commit()
    
    logging.info(f"Fixed route mapping drift for {len(drift_list)} invoices")
    return len(drift_list)
