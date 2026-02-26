"""
Delete Guards - Prevent data inconsistency by blocking hard deletes when dependencies exist.

This module implements SQLAlchemy before_delete event listeners that check for dependent records
before allowing hard deletes. Critical entities with financial, audit, or proof-of-delivery
records cannot be hard-deleted.

Usage:
    from delete_guards import register_all_guards
    register_all_guards()  # Call once during app initialization
"""

from sqlalchemy import event
from sqlalchemy.orm import object_session


def has_rows(sess, query):
    """Check if a query returns any rows"""
    return sess.query(query.exists()).scalar()


def block_if_related(entity_label, checks):
    """
    Create a before_delete listener that blocks deletion if any dependency check passes.
    
    Args:
        entity_label: Human-readable entity name for error messages
        checks: List of (label, query_fn) tuples where query_fn returns True if dependencies exist
    
    Returns:
        Event listener function
    """
    def _inner(mapper, connection, target):
        sess = object_session(target)
        if sess is None:
            return
        
        for label, query_fn in checks:
            if query_fn(sess, target):
                entity_id = getattr(target, 'id', None) or getattr(target, 'username', None) or getattr(target, 'invoice_no', None)
                raise ValueError(
                    f"Cannot delete {entity_label} '{entity_id}': {label} exist. "
                    f"Use soft delete instead (.soft_delete()) or remove dependencies first."
                )
    return _inner


# =============================================================================
# USER DELETE GUARDS
# =============================================================================

def user_has_invoices(sess, user):
    """Check if user has assigned invoices"""
    from models import Invoice
    return has_rows(sess, sess.query(Invoice).filter(
        (Invoice.assigned_to == user.username) | (Invoice.shipped_by == user.username)
    ))


def user_has_activity_logs(sess, user):
    """Check if user has activity logs"""
    from models import ActivityLog
    return has_rows(sess, sess.query(ActivityLog).filter_by(picker_username=user.username))


def user_has_discrepancies(sess, user):
    """Check if user reported, validated, or resolved discrepancies"""
    from models import DeliveryDiscrepancy
    return has_rows(sess, sess.query(DeliveryDiscrepancy).filter(
        (DeliveryDiscrepancy.reported_by == user.username) |
        (DeliveryDiscrepancy.validated_by == user.username) |
        (DeliveryDiscrepancy.resolved_by == user.username) |
        (DeliveryDiscrepancy.picker_username == user.username)
    ))


def user_has_batch_sessions(sess, user):
    """Check if user created or was assigned batch picking sessions"""
    from models import BatchPickingSession
    return has_rows(sess, sess.query(BatchPickingSession).filter(
        (BatchPickingSession.created_by == user.username) |
        (BatchPickingSession.assigned_to == user.username)
    ))


def user_has_cod_receipts(sess, user):
    """Check if user created COD receipts"""
    from models import CODReceipt
    return has_rows(sess, sess.query(CODReceipt).filter_by(created_by=user.username))


def user_has_pod_records(sess, user):
    """Check if user collected POD records"""
    from models import PODRecord
    return has_rows(sess, sess.query(PODRecord).filter_by(collected_by=user.username))


def user_has_receiving_sessions(sess, user):
    """Check if user operated receiving sessions"""
    from models import ReceivingSession
    return has_rows(sess, sess.query(ReceivingSession).filter_by(operator=user.username))


# =============================================================================
# INVOICE DELETE GUARDS
# =============================================================================

def invoice_has_discrepancies(sess, inv):
    """Check if invoice has delivery discrepancies"""
    from models import DeliveryDiscrepancy
    return has_rows(sess, sess.query(DeliveryDiscrepancy).filter_by(invoice_no=inv.invoice_no))


def invoice_has_cod_receipts(sess, inv):
    """Check if invoice has COD receipts"""
    from models import CODReceipt
    return has_rows(sess, sess.query(CODReceipt).filter_by(invoice_no=inv.invoice_no))


def invoice_has_pod_records(sess, inv):
    """Check if invoice has POD records"""
    from models import PODRecord
    return has_rows(sess, sess.query(PODRecord).filter_by(invoice_no=inv.invoice_no))


def invoice_has_activity_logs(sess, inv):
    """Check if invoice has activity logs"""
    from models import ActivityLog
    return has_rows(sess, sess.query(ActivityLog).filter_by(invoice_no=inv.invoice_no))


def invoice_has_delivery_events(sess, inv):
    """Check if invoice has delivery events"""
    from models import DeliveryEvent
    return has_rows(sess, sess.query(DeliveryEvent).filter_by(invoice_no=inv.invoice_no))


def invoice_has_route_assignment(sess, inv):
    """Check if invoice is assigned to a route/stop"""
    return inv.route_id is not None or inv.stop_id is not None


# =============================================================================
# SHIPMENT/ROUTE DELETE GUARDS
# =============================================================================

def shipment_has_stops(sess, shipment):
    """Check if shipment has route stops"""
    from models import RouteStop
    return has_rows(sess, sess.query(RouteStop).filter_by(shipment_id=shipment.id))


def shipment_has_delivery_events(sess, shipment):
    """Check if shipment has delivery events"""
    from models import DeliveryEvent
    return has_rows(sess, sess.query(DeliveryEvent).filter_by(route_id=shipment.id))


def shipment_has_cod_receipts(sess, shipment):
    """Check if shipment has COD receipts"""
    from models import CODReceipt
    return has_rows(sess, sess.query(CODReceipt).filter_by(route_id=shipment.id))


def shipment_has_pod_records(sess, shipment):
    """Check if shipment has POD records"""
    from models import PODRecord
    return has_rows(sess, sess.query(PODRecord).filter_by(route_id=shipment.id))


def shipment_has_assigned_invoices(sess, shipment):
    """Check if any invoices are assigned to this shipment"""
    from models import Invoice
    return has_rows(sess, sess.query(Invoice).filter_by(route_id=shipment.id))


# =============================================================================
# ROUTE STOP DELETE GUARDS
# =============================================================================

def route_stop_has_invoices(sess, stop):
    """Check if stop has assigned invoices"""
    from models import Invoice
    return has_rows(sess, sess.query(Invoice).filter_by(stop_id=stop.route_stop_id))


def route_stop_has_pod_records(sess, stop):
    """Check if stop has POD records"""
    from models import PODRecord
    return has_rows(sess, sess.query(PODRecord).filter_by(route_stop_id=stop.route_stop_id))


def route_stop_has_delivery_events(sess, stop):
    """Check if stop has delivery events"""
    from models import DeliveryEvent
    return has_rows(sess, sess.query(DeliveryEvent).filter_by(route_stop_id=stop.route_stop_id))


# =============================================================================
# BATCH PICKING SESSION DELETE GUARDS
# =============================================================================

def batch_has_locked_items(sess, batch):
    """Check if batch has locked items"""
    from models import InvoiceItem
    return has_rows(sess, sess.query(InvoiceItem).filter_by(locked_by_batch_id=batch.id))


def batch_has_activity_logs(sess, batch):
    """Check if batch has activity logs (batch number in details)"""
    from models import ActivityLog
    # This is approximate - checking if batch is referenced in logs
    return batch.batch_number and has_rows(sess, 
        sess.query(ActivityLog).filter(ActivityLog.details.like(f'%{batch.batch_number}%'))
    )


# =============================================================================
# PSCUSTOMER DELETE GUARDS
# =============================================================================

def customer_has_invoices(sess, customer):
    """Check if customer has invoices"""
    from models import Invoice
    return has_rows(sess, sess.query(Invoice).filter_by(customer_code_365=customer.customer_code_365))


def customer_has_credit_terms(sess, customer):
    """Check if customer has credit terms"""
    from models import CreditTerms
    # PSCustomer links to PaymentCustomer via customer_code, which links to CreditTerms
    from models import PaymentCustomer
    # Check if there's a payment customer record for this code
    # Note: This is a simplified check - adjust based on your actual schema
    return False  # Placeholder - adjust based on actual relationship


# =============================================================================
# PURCHASE ORDER DELETE GUARDS
# =============================================================================

def po_has_receiving_sessions(sess, po):
    """Check if PO has receiving sessions"""
    from models import ReceivingSession
    return has_rows(sess, sess.query(ReceivingSession).filter_by(purchase_order_id=po.id))


def po_has_receiving_lines(sess, po):
    """Check if PO has received lines"""
    from models import ReceivingLine, ReceivingSession
    return has_rows(sess, 
        sess.query(ReceivingLine).join(ReceivingSession).filter(
            ReceivingSession.purchase_order_id == po.id
        )
    )


# =============================================================================
# REGISTER ALL GUARDS
# =============================================================================

def register_all_guards():
    """
    Register all delete guards. Call this once during app initialization.
    
    Example:
        from app import app
        with app.app_context():
            register_all_guards()
    """
    from models import (
        User, Invoice, Shipment, RouteStop, 
        BatchPickingSession, PSCustomer, PurchaseOrder
    )
    
    # User guards - NEVER allow hard delete if referenced
    event.listen(
        User, "before_delete",
        block_if_related("user", [
            ("assigned invoices", user_has_invoices),
            ("activity logs", user_has_activity_logs),
            ("delivery discrepancies", user_has_discrepancies),
            ("batch picking sessions", user_has_batch_sessions),
            ("COD receipts", user_has_cod_receipts),
            ("POD records", user_has_pod_records),
            ("receiving sessions", user_has_receiving_sessions),
        ])
    )
    
    # Invoice guards - Block if any financial/delivery/audit records exist
    event.listen(
        Invoice, "before_delete",
        block_if_related("invoice", [
            ("delivery discrepancies", invoice_has_discrepancies),
            ("COD receipts", invoice_has_cod_receipts),
            ("POD records", invoice_has_pod_records),
            ("activity logs", invoice_has_activity_logs),
            ("delivery events", invoice_has_delivery_events),
            ("route/stop assignment", invoice_has_route_assignment),
        ])
    )
    
    # Shipment/Route guards
    event.listen(
        Shipment, "before_delete",
        block_if_related("shipment/route", [
            ("route stops", shipment_has_stops),
            ("delivery events", shipment_has_delivery_events),
            ("COD receipts", shipment_has_cod_receipts),
            ("POD records", shipment_has_pod_records),
            ("assigned invoices", shipment_has_assigned_invoices),
        ])
    )
    
    # Route Stop guards
    event.listen(
        RouteStop, "before_delete",
        block_if_related("route stop", [
            ("assigned invoices", route_stop_has_invoices),
            ("POD records", route_stop_has_pod_records),
            ("delivery events", route_stop_has_delivery_events),
        ])
    )
    
    # Batch Picking Session guards
    event.listen(
        BatchPickingSession, "before_delete",
        block_if_related("batch picking session", [
            ("locked items", batch_has_locked_items),
            ("activity logs", batch_has_activity_logs),
        ])
    )
    
    # PSCustomer guards
    event.listen(
        PSCustomer, "before_delete",
        block_if_related("PS365 customer", [
            ("invoices", customer_has_invoices),
            ("credit terms", customer_has_credit_terms),
        ])
    )
    
    # Purchase Order guards
    event.listen(
        PurchaseOrder, "before_delete",
        block_if_related("purchase order", [
            ("receiving sessions", po_has_receiving_sessions),
            ("received lines", po_has_receiving_lines),
        ])
    )
    
    print("✓ Delete guards registered for: User, Invoice, Shipment, RouteStop, BatchPickingSession, PSCustomer, PurchaseOrder")
