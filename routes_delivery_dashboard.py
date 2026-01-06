"""
Flask blueprint for Delivery Dashboard
Shows dispatched routes with expandable order details
"""
from flask import Blueprint, request, render_template, jsonify
from flask_login import login_required, current_user
from datetime import datetime, date
from functools import wraps
from models import Shipment, RouteStop, RouteStopInvoice, Invoice
from app import db

bp = Blueprint("delivery_dashboard", __name__)

def admin_or_warehouse_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role not in ['admin', 'warehouse_manager']:
            from flask import abort
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


@bp.route("/delivery-dashboard")
@login_required
@admin_or_warehouse_required
def dashboard():
    """Display delivery dashboard with active routes only (excludes completed)"""
    import services
    
    # Get only active routes (exclude completed and cancelled - case insensitive)
    routes = Shipment.query.filter(
        ~Shipment.status.ilike('completed'),
        ~Shipment.status.ilike('cancelled')
    ).order_by(
        Shipment.delivery_date.desc(), 
        Shipment.id.desc()
    ).all()
    
    # Build route summary data
    route_summaries = []
    for route in routes:
        # Count total invoices in this route
        invoice_count = db.session.query(Invoice).join(
            RouteStopInvoice, Invoice.invoice_no == RouteStopInvoice.invoice_no
        ).join(
            RouteStop, RouteStopInvoice.route_stop_id == RouteStop.route_stop_id
        ).filter(
            RouteStop.shipment_id == route.id
        ).count()
        
        # Count stops
        stop_count = RouteStop.query.filter_by(shipment_id=route.id).count()
        
        # Get route progress
        progress = services.route_progress(route.id)
        
        # Check if all invoices are ready for dispatch
        all_invoices = Invoice.query.filter_by(route_id=route.id).all()
        all_ready_for_dispatch = len(all_invoices) > 0 and all(inv.status == 'ready_for_dispatch' for inv in all_invoices)
        
        route_summaries.append({
            'route': route,
            'invoice_count': invoice_count,
            'stop_count': stop_count,
            'progress': progress,
            'all_ready_for_dispatch': all_ready_for_dispatch
        })
    
    return render_template(
        "delivery_dashboard.html",
        routes=route_summaries
    )


@bp.route("/delivery-dashboard/completed")
@login_required
@admin_or_warehouse_required
def get_completed_routes():
    """AJAX endpoint to get completed routes on demand"""
    import services
    
    # Get completed routes (limit to last 50) - case insensitive
    routes = Shipment.query.filter(
        Shipment.status.ilike('completed')
    ).order_by(
        Shipment.completed_at.desc()
    ).limit(50).all()
    
    # Build route summary data
    route_summaries = []
    for route in routes:
        # Count total invoices in this route
        invoice_count = db.session.query(Invoice).join(
            RouteStopInvoice, Invoice.invoice_no == RouteStopInvoice.invoice_no
        ).join(
            RouteStop, RouteStopInvoice.route_stop_id == RouteStop.route_stop_id
        ).filter(
            RouteStop.shipment_id == route.id
        ).count()
        
        # Count stops
        stop_count = RouteStop.query.filter_by(shipment_id=route.id).count()
        
        # Get route progress
        progress = services.route_progress(route.id)
        
        route_summaries.append({
            'id': route.id,
            'route_name': route.route_name,
            'driver_name': route.driver_name,
            'delivery_date': route.delivery_date.strftime('%d/%m/%Y') if route.delivery_date else '-',
            'status': route.status,
            'invoice_count': invoice_count,
            'stop_count': stop_count,
            'progress': {
                'done': progress['done'],
                'total': progress['total'],
                'percentage': progress['percentage']
            },
            'completed_at': route.completed_at.strftime('%d/%m/%Y %H:%M') if route.completed_at else '-'
        })
    
    return jsonify({'routes': route_summaries})


@bp.route("/delivery-dashboard/route/<int:route_id>/orders")
@login_required
@admin_or_warehouse_required
def get_route_orders(route_id):
    """AJAX endpoint to get orders for a specific route"""
    # Get route
    route = Shipment.query.get_or_404(route_id)
    
    # Get all invoices for this route with their items
    invoices = db.session.query(Invoice).join(
        RouteStopInvoice, Invoice.invoice_no == RouteStopInvoice.invoice_no
    ).join(
        RouteStop, RouteStopInvoice.route_stop_id == RouteStop.route_stop_id
    ).filter(
        RouteStop.shipment_id == route_id
    ).order_by(RouteStop.seq_no).all()
    
    # Build response data
    orders_data = []
    for invoice in invoices:
        # Get stop info for THIS route specifically
        stop_invoice = db.session.query(RouteStopInvoice).join(
            RouteStop, RouteStopInvoice.route_stop_id == RouteStop.route_stop_id
        ).filter(
            RouteStopInvoice.invoice_no == invoice.invoice_no,
            RouteStop.shipment_id == route_id
        ).first()
        
        stop = None
        if stop_invoice:
            stop = RouteStop.query.get(stop_invoice.route_stop_id)
        
        orders_data.append({
            'invoice_no': invoice.invoice_no,
            'customer_name': invoice.customer_name,
            'routing': invoice.routing,
            'status': invoice.status,
            'total_items': invoice.total_items,
            'total_weight': invoice.total_weight,
            'stop_seq': stop.seq_no if stop else None,
            'stop_name': stop.stop_name if stop else None
        })
    
    return jsonify({'orders': orders_data})
