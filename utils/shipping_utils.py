"""
Shipping utilities for direct order shipping without shipment assignments.
Provides functions for shipping invoices directly and managing delivery status.
"""

import logging
from datetime import datetime
from typing import List, Optional, Dict, Tuple, Union
from flask import current_app
from sqlalchemy.orm import Session
from sqlalchemy.orm.scoping import scoped_session
from sqlalchemy import text
from models import Invoice, ShippingEvent, DeliveryEvent, ActivityLog, Setting, db
# Status constants - using string values directly
STATUS_NOT_STARTED = 'not_started'
STATUS_PICKING = 'picking'
STATUS_AWAITING_BATCH_ITEMS = 'awaiting_batch_items'
STATUS_AWAITING_PACKING = 'awaiting_packing'
STATUS_READY_FOR_DISPATCH = 'ready_for_dispatch'
STATUS_SHIPPED = 'shipped'
STATUS_DELIVERED = 'delivered'
STATUS_DELIVERY_FAILED = 'delivery_failed'
STATUS_CANCELLED = 'cancelled'
from timezone_utils import get_local_time, utc_now_for_db

logger = logging.getLogger(__name__)


def ship_invoices(invoice_numbers: List[str], shipped_by: str, 
                 session: Optional[Union[Session, scoped_session]] = None) -> Tuple[List[str], List[str], List[str]]:
    """
    Ship multiple invoices directly without creating shipment assignments.
    Uses per-invoice commits to ensure transactional integrity - successful shipments 
    are immediately persisted and cannot be rolled back by subsequent failures.
    
    Args:
        invoice_numbers: List of invoice numbers to ship
        shipped_by: Username of the person shipping the orders
        session: Database session (uses db.session if not provided)
        
    Returns:
        Tuple of (successfully_shipped, skipped, failed) invoice numbers
        
    Note:
        Each invoice is processed with its own commit, ensuring that successful
        invoices are immediately persisted to the database. This provides
        guaranteed partial success semantics.
    """
    if session is None:
        session = db.session
        
    successfully_shipped = []
    skipped = []
    failed = []
    
    shipped_at = utc_now_for_db()
    
    # Process each invoice with immediate commit for true partial success
    for invoice_no in invoice_numbers:
        try:
            # Get invoice
            logger.debug(f"Processing invoice {invoice_no} with immediate commit")
            invoice = session.query(Invoice).filter_by(invoice_no=invoice_no).first()
            if not invoice:
                logger.warning(f"Invoice {invoice_no} not found in database")
                failed.append(invoice_no)
                continue
            
            # Validate invoice can be shipped
            if not _can_ship_invoice(invoice):
                reason = _get_skip_reason(invoice)
                logger.info(f"Invoice {invoice_no} cannot be shipped - {reason}")
                skipped.append(invoice_no)
                continue
            
            # Update invoice shipping fields
            invoice.shipped_at = shipped_at
            invoice.shipped_by = shipped_by
            invoice.status = STATUS_SHIPPED
            invoice.status_updated_at = shipped_at
            
            # Create shipping event for audit trail
            shipping_event = ShippingEvent()
            shipping_event.invoice_no = invoice_no
            shipping_event.action = 'shipped'
            shipping_event.actor = shipped_by
            shipping_event.timestamp = shipped_at
            shipping_event.note = f"Direct ship via Ship Now functionality"
            session.add(shipping_event)
            
            # Log activity
            _log_shipping_activity(invoice_no, shipped_by, 'shipped', shipped_at, session)
            
            # Flush to ensure data integrity before commit
            session.flush()
            
            # Immediately commit this invoice - it's now permanently shipped
            session.commit()
            successfully_shipped.append(invoice_no)
            logger.info(f"Invoice {invoice_no} successfully shipped and committed to database")
            
        except Exception as e:
            # Roll back only this invoice's changes and continue with next invoice
            try:
                session.rollback()
                logger.error(f"Invoice {invoice_no} failed to ship - {str(e)}")
            except Exception as rollback_error:
                logger.error(f"Critical: Failed to rollback changes for invoice {invoice_no}: {rollback_error}")
            
            failed.append(invoice_no)
            continue
    
    # No final commit needed - each successful invoice was already committed
    logger.info(f"Batch shipping complete - Success: {len(successfully_shipped)}, "
               f"Skipped: {len(skipped)}, Failed: {len(failed)}")
    if successfully_shipped:
        logger.info(f"Successfully shipped invoices: {', '.join(successfully_shipped)}")
    if failed:
        logger.warning(f"Failed to ship invoices: {', '.join(failed)}")
    if skipped:
        logger.info(f"Skipped invoices: {', '.join(skipped)}")
        
    return successfully_shipped, skipped, failed


def unship_invoice(invoice_no: str, cancelled_by: str, reason: str,
                  session: Optional[Union[Session, scoped_session]] = None) -> bool:
    """
    Cancel shipment and return invoice to ready_for_dispatch status.
    
    Args:
        invoice_no: Invoice number to cancel shipment for
        cancelled_by: Username cancelling the shipment
        reason: Reason for cancelling the shipment (required)
        session: Database session (uses db.session if not provided)
        
    Returns:
        True if cancellation successful, False otherwise
    """
    if session is None:
        session = db.session
        
    try:
        # Get invoice
        invoice = session.query(Invoice).filter_by(invoice_no=invoice_no).first()
        if not invoice:
            logger.error(f"Invoice {invoice_no} not found for shipment cancellation")
            return False
        
        # Validate invoice can be unshipped
        if invoice.status not in [STATUS_SHIPPED, STATUS_DELIVERY_FAILED]:
            logger.warning(f"Invoice {invoice_no} cannot be unshipped (current status: {invoice.status})")
            return False
        
        cancelled_at = utc_now_for_db()
        old_status = invoice.status
        
        # Clear shipping information and return to ready_for_dispatch
        invoice.shipped_at = None
        invoice.shipped_by = None
        invoice.delivered_at = None
        invoice.undelivered_reason = None
        invoice.status = STATUS_READY_FOR_DISPATCH
        invoice.status_updated_at = cancelled_at
        
        # Create shipping event for audit trail
        shipping_event = ShippingEvent()
        shipping_event.invoice_no = invoice_no
        shipping_event.action = 'unship'
        shipping_event.actor = cancelled_by
        shipping_event.timestamp = cancelled_at
        shipping_event.note = f"Shipment cancelled: {reason}"
        session.add(shipping_event)
        
        # Log activity
        _log_shipping_activity(invoice_no, cancelled_by, 'shipment_cancelled', cancelled_at, session, reason)
        
        session.commit()
        logger.info(f"Successfully cancelled shipment for invoice {invoice_no}: {reason}")
        
        return True
        
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to cancel shipment for invoice {invoice_no}: {str(e)}")
        return False


def deliver_invoice(invoice_no: str, delivered_by: str, 
                   delivery_successful: bool = True, reason: Optional[str] = None,
                   session: Optional[Union[Session, scoped_session]] = None) -> bool:
    """
    Mark an invoice as delivered or failed delivery.
    
    Args:
        invoice_no: Invoice number to update
        delivered_by: Username marking the delivery status
        delivery_successful: True for successful delivery, False for failed
        reason: Required if delivery failed, optional for successful delivery
        session: Database session (uses db.session if not provided)
        
    Returns:
        True if update successful, False otherwise
    """
    if session is None:
        session = db.session
        
    try:
        # Get invoice
        invoice = session.query(Invoice).filter_by(invoice_no=invoice_no).first()
        if not invoice:
            logger.error(f"Invoice {invoice_no} not found for delivery update")
            return False
        
        # Validate invoice can be marked as delivered
        if invoice.status != STATUS_SHIPPED:
            logger.warning(f"Invoice {invoice_no} is not in shipped status (current: {invoice.status})")
            return False
        
        delivered_at = utc_now_for_db()
        
        if delivery_successful:
            # Successful delivery
            invoice.delivered_at = delivered_at
            invoice.status = STATUS_DELIVERED
            invoice.undelivered_reason = None  # Clear any previous failure reason
            
            # Create delivery event
            delivery_event = DeliveryEvent()
            delivery_event.invoice_no = invoice_no
            delivery_event.action = 'delivered'
            delivery_event.actor = delivered_by
            delivery_event.timestamp = delivered_at
            delivery_event.reason = reason  # Optional success note
            
            _log_shipping_activity(invoice_no, delivered_by, 'delivered', delivered_at, session)
            logger.info(f"Marked invoice {invoice_no} as delivered")
            
        else:
            # Failed delivery
            if not reason:
                logger.error(f"Reason required for failed delivery of invoice {invoice_no}")
                return False
                
            invoice.status = STATUS_DELIVERY_FAILED
            invoice.undelivered_reason = reason
            # Don't set delivered_at for failed deliveries
            
            # Create delivery event
            delivery_event = DeliveryEvent()
            delivery_event.invoice_no = invoice_no
            delivery_event.action = 'delivery_failed'
            delivery_event.actor = delivered_by
            delivery_event.timestamp = delivered_at
            delivery_event.reason = reason
            
            _log_shipping_activity(invoice_no, delivered_by, 'delivery_failed', delivered_at, session, reason)
            logger.info(f"Marked invoice {invoice_no} as delivery failed: {reason}")
        
        invoice.status_updated_at = delivered_at
        session.add(delivery_event)
        session.commit()
        
        return True
        
    except Exception as e:
        session.rollback()
        logger.error(f"Failed to update delivery status for invoice {invoice_no}: {str(e)}")
        return False


def get_ready_to_ship_orders(session: Optional[Union[Session, scoped_session]] = None) -> List[Invoice]:
    """
    Get all orders that are ready to ship (status = ready_for_dispatch).
    
    Args:
        session: Database session (uses db.session if not provided)
        
    Returns:
        List of Invoice objects ready for shipping
    """
    if session is None:
        session = db.session
        
    try:
        orders = session.query(Invoice).filter_by(
            status=STATUS_READY_FOR_DISPATCH
        ).order_by(Invoice.routing.asc(), Invoice.customer_name.asc()).all()
        
        logger.debug(f"Found {len(orders)} orders ready to ship")
        return orders
        
    except Exception as e:
        logger.error(f"Failed to get ready to ship orders: {str(e)}")
        return []


def get_shipped_orders(start_date: Optional[datetime] = None, end_date: Optional[datetime] = None,
                      session: Optional[Union[Session, scoped_session]] = None) -> List[Invoice]:
    """
    Get shipped orders within a date range.
    
    Args:
        start_date: Start date for filtering (optional)
        end_date: End date for filtering (optional) 
        session: Database session (uses db.session if not provided)
        
    Returns:
        List of shipped Invoice objects
    """
    if session is None:
        session = db.session
        
    try:
        query = session.query(Invoice).filter(
            Invoice.status.in_([STATUS_SHIPPED, STATUS_DELIVERED, STATUS_DELIVERY_FAILED])
        )
        
        if start_date:
            query = query.filter(Invoice.shipped_at >= start_date)
        if end_date:
            query = query.filter(Invoice.shipped_at <= end_date)
            
        orders = query.order_by(Invoice.shipped_at.desc()).all()
        
        logger.debug(f"Found {len(orders)} shipped orders")
        return orders
        
    except Exception as e:
        logger.error(f"Failed to get shipped orders: {str(e)}")
        return []


# Private helper functions

def _can_ship_invoice(invoice: Invoice) -> bool:
    """Check if an invoice can be shipped."""
    return invoice.status == STATUS_READY_FOR_DISPATCH


def _get_skip_reason(invoice: Invoice) -> str:
    """Get human-readable reason why an invoice was skipped."""
    if invoice.status == STATUS_NOT_STARTED:
        return "Order not started picking"
    elif invoice.status == STATUS_PICKING:
        return "Order still being picked"
    elif invoice.status == STATUS_AWAITING_BATCH_ITEMS:
        return "Order awaiting batch items"
    elif invoice.status == STATUS_AWAITING_PACKING:
        return "Order awaiting packing"
    elif invoice.status == STATUS_SHIPPED:
        return "Order already shipped"
    elif invoice.status == STATUS_DELIVERED:
        return "Order already delivered"
    elif invoice.status == STATUS_CANCELLED:
        return "Order cancelled"
    else:
        return f"Invalid status: {invoice.status}"


def _log_shipping_activity(invoice_no: str, actor: str, action: str, 
                          timestamp: datetime, session: Optional[Union[Session, scoped_session]], note: Optional[str] = None):
    """Log shipping activity to activity log."""
    if session is None:
        logger.warning("Cannot log shipping activity: session is None")
        return
        
    try:
        # Verify user exists before logging
        from models import User
        user_exists = session.query(User).filter_by(username=actor).first()
        if not user_exists:
            logger.warning(f"Cannot log shipping activity: User '{actor}' does not exist in database")
            return
            
        description = {
            'shipped': f"Order shipped directly via Ship Now by {actor}",
            'delivered': f"Order marked as delivered by {actor}",
            'delivery_failed': f"Order delivery failed - {note}" if note else f"Order delivery failed by {actor}",
            'shipment_cancelled': f"Shipment cancelled by {actor} - {note}" if note else f"Shipment cancelled by {actor}"
        }.get(action, f"Shipping action '{action}' by {actor}")
        
        activity = ActivityLog()
        activity.invoice_no = invoice_no
        activity.picker_username = actor
        activity.activity_type = action
        activity.details = description  # Note: ActivityLog uses 'details' not 'description'
        activity.timestamp = timestamp
        session.add(activity)
        
    except Exception as e:
        logger.warning(f"Failed to log shipping activity: {str(e)}")