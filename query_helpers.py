"""
Query Helpers - Convenience functions for filtering soft-deleted records.

This module provides helper functions to ensure soft-deleted and disabled records
are excluded from queries by default throughout the application.

Usage:
    from query_helpers import active_users, not_deleted_invoices
    
    # Get only active users (is_active=True)
    users = active_users().all()
    
    # Get only non-deleted invoices
    invoices = not_deleted_invoices().filter_by(status='ready_for_dispatch').all()
"""

from models import (
    User, Invoice, Shipment, RouteStop, 
    BatchPickingSession, PSCustomer, PurchaseOrder
)


def active_users():
    """
    Query for active users only (is_active=True).
    Excludes disabled users.
    
    Returns:
        Query object filtered for active users
    
    Example:
        active_pickers = active_users().filter_by(role='picker').all()
    """
    return User.query.filter_by(is_active=True)


def all_users_including_disabled():
    """
    Query for all users including disabled ones.
    Use this explicitly when you need to see disabled users.
    
    Returns:
        Query object for all users
    """
    return User.query


def not_deleted_invoices():
    """
    Query for non-deleted invoices only (deleted_at IS NULL).
    This should be the default for all invoice queries.
    
    Returns:
        Query object filtered for non-deleted invoices
    
    Example:
        picking_invoices = not_deleted_invoices().filter(
            Invoice.status.in_(['not_started', 'picking'])
        ).all()
    """
    return Invoice.query.filter(Invoice.deleted_at.is_(None))


def not_deleted_shipments():
    """
    Query for non-deleted shipments/routes only (deleted_at IS NULL).
    This should be the default for all shipment queries.
    
    Returns:
        Query object filtered for non-deleted shipments
    
    Example:
        active_routes = not_deleted_shipments().filter_by(status='IN_TRANSIT').all()
    """
    return Shipment.query.filter(Shipment.deleted_at.is_(None))


def not_deleted_route_stops():
    """
    Query for non-deleted route stops only (deleted_at IS NULL).
    This should be the default for all route stop queries.
    
    Returns:
        Query object filtered for non-deleted route stops
    """
    return RouteStop.query.filter(RouteStop.deleted_at.is_(None))


def not_deleted_batches():
    """
    Query for non-deleted batch picking sessions only (deleted_at IS NULL).
    This should be the default for all batch session queries.
    
    Returns:
        Query object filtered for non-deleted batch sessions
    """
    return BatchPickingSession.query.filter(BatchPickingSession.deleted_at.is_(None))


def active_customers():
    """
    Query for active PS365 customers only (is_active=True AND deleted_at IS NULL).
    This should be the default for all customer queries.
    
    Returns:
        Query object filtered for active customers
    
    Example:
        customers = active_customers().filter(
            PSCustomer.company_name.like('%Corp%')
        ).all()
    """
    return PSCustomer.query.filter(
        PSCustomer.is_active == True,
        PSCustomer.deleted_at.is_(None)
    )


def not_deleted_purchase_orders():
    """
    Query for non-deleted purchase orders only (deleted_at IS NULL).
    This should be the default for all PO queries.
    
    Returns:
        Query object filtered for non-deleted purchase orders
    """
    return PurchaseOrder.query.filter(PurchaseOrder.deleted_at.is_(None))


def include_deleted(query_obj, model_class):
    """
    Explicitly include soft-deleted records in a query.
    Use this when you need to see deleted records (e.g., admin views).
    
    Args:
        query_obj: The base query object
        model_class: The model class being queried
    
    Returns:
        Query object that includes soft-deleted records
    
    Example:
        # Show all invoices including deleted ones
        all_invoices = include_deleted(Invoice.query, Invoice).all()
    """
    # This is a no-op function - just returns the query as-is
    # Useful for documenting intent when you explicitly want deleted records
    return query_obj


def only_deleted(model_class):
    """
    Query for only soft-deleted records.
    Use this for admin views of deleted records.
    
    Args:
        model_class: The model class to query (must have deleted_at column)
    
    Returns:
        Query object filtered for soft-deleted records only
    
    Example:
        deleted_invoices = only_deleted(Invoice).all()
        deleted_users = only_deleted(User).filter_by(role='picker').all()
    """
    return model_class.query.filter(model_class.deleted_at.isnot(None))


# =============================================================================
# Bulk Query Helpers
# =============================================================================

def get_dashboard_invoices(assigned_to=None, statuses=None):
    """
    Get non-deleted invoices for dashboard views.
    
    Args:
        assigned_to: Optional username to filter by assignment
        statuses: Optional list of statuses to filter by
    
    Returns:
        List of Invoice objects
    """
    query = not_deleted_invoices()
    
    if assigned_to:
        query = query.filter_by(assigned_to=assigned_to)
    
    if statuses:
        query = query.filter(Invoice.status.in_(statuses))
    
    return query.all()


def get_active_routes(driver_name=None, delivery_date=None):
    """
    Get non-deleted active routes/shipments.
    
    Args:
        driver_name: Optional driver username to filter by
        delivery_date: Optional delivery date to filter by
    
    Returns:
        List of Shipment objects
    """
    query = not_deleted_shipments().filter(
        Shipment.status.in_(['PLANNED', 'DISPATCHED', 'IN_TRANSIT'])
    )
    
    if driver_name:
        query = query.filter_by(driver_name=driver_name)
    
    if delivery_date:
        query = query.filter_by(delivery_date=delivery_date)
    
    return query.all()


# =============================================================================
# Admin Soft Delete Utilities
# =============================================================================

def count_deleted_records(model_class):
    """
    Count how many soft-deleted records exist for a model.
    
    Args:
        model_class: The model class to count (must have deleted_at column)
    
    Returns:
        Integer count of deleted records
    
    Example:
        deleted_invoice_count = count_deleted_records(Invoice)
        print(f"{deleted_invoice_count} invoices have been soft-deleted")
    """
    return model_class.query.filter(model_class.deleted_at.isnot(None)).count()


def get_recently_deleted(model_class, days=7):
    """
    Get recently soft-deleted records (within last N days).
    
    Args:
        model_class: The model class to query
        days: Number of days to look back (default: 7)
    
    Returns:
        List of recently deleted records
    """
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    return model_class.query.filter(
        model_class.deleted_at.isnot(None),
        model_class.deleted_at >= cutoff
    ).all()
