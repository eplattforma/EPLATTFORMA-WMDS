"""
Route management business logic and services
"""
from datetime import date, datetime
from sqlalchemy import select, func
from models import Shipment, RouteStop, RouteStopInvoice, Invoice
from app import db


def upsert_route(driver_name: str, route_name: str, delivery_date: date, status="PLANNED", route_id=None):
    """
    Create or update a route. Routes are independent entities.
    Driver can be assigned/changed as needed.
    
    Args:
        driver_name: Driver assigned to route (can be changed later)
        route_name: Name/description of route
        delivery_date: Delivery date
        status: Route status (default: PLANNED)
        route_id: If provided, updates existing route instead of creating new
    
    Returns the Shipment object.
    """
    if route_id:
        # Update specific existing route
        route = Shipment.query.get(route_id)
        if not route:
            raise ValueError(f"Route {route_id} not found")
        
        if route_name and route.route_name != route_name:
            route.route_name = route_name
        if driver_name and route.driver_name != driver_name:
            route.driver_name = driver_name
        if status and route.status != status:
            route.status = status
        if delivery_date and route.delivery_date != delivery_date:
            route.delivery_date = delivery_date
        
        db.session.commit()
        return route
    
    # Always create new route (no driver/date uniqueness check)
    route = Shipment(
        driver_name=driver_name,
        route_name=route_name,
        status=status,
        delivery_date=delivery_date,
        created_at=datetime.utcnow()
    )
    db.session.add(route)
    db.session.commit()
    return route


def create_stop(shipment_id: int, seq_no, **kwargs):
    """
    Create a new stop in a route.
    seq_no can be int or Decimal (supports values like 1.1, 1.5, etc.)
    Returns the RouteStop object.
    """
    stop = RouteStop(
        shipment_id=shipment_id,
        seq_no=seq_no,
        stop_name=kwargs.get('stop_name'),
        stop_addr=kwargs.get('stop_addr'),
        stop_city=kwargs.get('stop_city'),
        stop_postcode=kwargs.get('stop_postcode'),
        notes=kwargs.get('notes'),
        window_start=kwargs.get('window_start'),
        window_end=kwargs.get('window_end')
    )
    db.session.add(stop)
    db.session.commit()
    return stop


def attach_invoices_to_stop(route_stop_id: int, invoice_nos: list):
    """
    Attach multiple invoices to a stop.
    If invoice is already on another route, it will be removed and reassigned.
    Returns list of RouteStopInvoice objects.
    """
    # Get the stop to find the shipment_id
    stop = RouteStop.query.get(route_stop_id)
    if not stop:
        return []
    
    attached = []
    for invoice_no in invoice_nos:
        # Check if invoice is already on ANY route (not just this stop)
        existing = RouteStopInvoice.query.filter_by(
            invoice_no=invoice_no
        ).first()
        
        if existing:
            # If already on this exact stop, skip
            if existing.route_stop_id == route_stop_id:
                continue
            # Invoice is on a different route/stop - remove old assignment
            db.session.delete(existing)
            db.session.flush()
        
        # Update Invoice with new route and stop references
        invoice = Invoice.query.get(invoice_no)
        if invoice:
            invoice.route_id = stop.shipment_id
            invoice.stop_id = route_stop_id
        
        # Create new assignment
        rsi = RouteStopInvoice(
            route_stop_id=route_stop_id,
            invoice_no=invoice_no,
            status="ASSIGNED"
        )
        db.session.add(rsi)
        attached.append(rsi)
    
    db.session.commit()
    return attached


def set_invoice_in_stop_status(route_stop_id: int, invoice_no: str, status: str, mirror_invoice=True):
    """
    Update the status of an invoice in a stop.
    Optionally also update the invoice's main status.
    Returns the RouteStopInvoice object.
    """
    rsi = RouteStopInvoice.query.filter_by(
        route_stop_id=route_stop_id,
        invoice_no=invoice_no
    ).first_or_404()
    
    rsi.status = status
    db.session.commit()
    
    if mirror_invoice:
        inv = Invoice.query.get(invoice_no)
        if inv:
            now = datetime.utcnow()
            inv.status = status.lower() if status else inv.status
            inv.status_updated_at = now
            
            if status == "DELIVERED":
                inv.delivered_at = now
            elif status == "DISPATCHED":
                inv.shipped_at = now
            
            db.session.commit()
    
    return rsi


def route_progress(shipment_id: int):
    """
    Calculate progress statistics for a route.
    Returns dict with total, done, and percentage.
    """
    # Count total invoices in this route
    total = db.session.query(func.count(RouteStopInvoice.route_stop_invoice_id))\
        .join(RouteStop, RouteStop.route_stop_id == RouteStopInvoice.route_stop_id)\
        .filter(RouteStop.shipment_id == shipment_id)\
        .scalar() or 0
    
    # Count completed invoices by checking Invoice.status (delivered, returned, delivery_failed)
    done = db.session.query(func.count(RouteStopInvoice.route_stop_invoice_id))\
        .join(RouteStop, RouteStop.route_stop_id == RouteStopInvoice.route_stop_id)\
        .join(Invoice, Invoice.invoice_no == RouteStopInvoice.invoice_no)\
        .filter(
            RouteStop.shipment_id == shipment_id,
            Invoice.status.in_(["delivered", "returned", "delivery_failed"])
        )\
        .scalar() or 0
    
    percentage = (done / total * 100.0) if total > 0 else 0.0
    
    return {
        "total": total,
        "done": done,
        "pct": percentage
    }


def get_next_seq_no(shipment_id: int):
    """
    Get the next available sequence number for a stop in a route.
    Returns a Decimal to support decimal sequence numbers.
    """
    from decimal import Decimal
    max_seq = db.session.query(func.max(RouteStop.seq_no))\
        .filter(RouteStop.shipment_id == shipment_id)\
        .scalar()
    
    return Decimal(str((max_seq or 0))) + Decimal('1')


def delete_stop(route_stop_id: int):
    """
    Delete a stop and all its invoice assignments.
    """
    stop = RouteStop.query.get_or_404(route_stop_id)
    
    # First, unassign all invoices from this stop (clear both stop_id AND route_id)
    Invoice.query.filter_by(stop_id=route_stop_id).update({'stop_id': None, 'route_id': None})
    
    # Delete all invoice assignments for this stop
    RouteStopInvoice.query.filter_by(route_stop_id=route_stop_id).delete()
    
    # Delete related delivery records
    from models import DeliveryEvent, DeliveryLine, PODRecord, CODReceipt
    DeliveryEvent.query.filter_by(route_stop_id=route_stop_id).delete()
    DeliveryLine.query.filter_by(route_stop_id=route_stop_id).delete()
    PODRecord.query.filter_by(route_stop_id=route_stop_id).delete()
    CODReceipt.query.filter_by(route_stop_id=route_stop_id).delete()
    
    # Finally delete the stop itself
    db.session.delete(stop)
    db.session.commit()
    return True
