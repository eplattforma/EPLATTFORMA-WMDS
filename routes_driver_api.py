"""
Driver API Blueprint for Route and Order Management
Provides REST API endpoints for driver mobile app to manage deliveries
"""
from flask import Blueprint, request, jsonify
from functools import wraps
from datetime import datetime
from models import Shipment, Invoice, DeliveryEvent, RouteStopInvoice, RouteStop, db
from app import db as database
from services_route_lifecycle import recompute_route_completion
import logging

logger = logging.getLogger(__name__)

driver_api_bp = Blueprint('driver_api', __name__, url_prefix='/api/driver')


def driver_id_required(f):
    """Decorator to require driver ID from x-driver-id header"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        driver_id = request.headers.get('x-driver-id')
        if not driver_id:
            return jsonify({'error': 'Missing driver id'}), 401
        # Attach driver_id to request for use in the route
        request.driver_id = driver_id
        return f(*args, **kwargs)
    return decorated_function


@driver_api_bp.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({'ok': True, 'service': 'driver-api'})


@driver_api_bp.route('/routes/<int:route_id>/start', methods=['PATCH'])
@driver_id_required
def start_route(route_id):
    """
    Start a route - Changes route from 'DISPATCHED' to 'IN_TRANSIT'
    and bulk updates all orders from 'shipped' to 'out_for_delivery'
    
    This operation is idempotent - if route is already IN_TRANSIT, returns success
    """
    driver_id = request.driver_id
    
    try:
        # Lock route row for update
        route = db.session.query(Shipment).filter_by(id=route_id).with_for_update().first()
        
        if not route:
            return jsonify({'error': 'Route not found'}), 404
        
        # Verify driver owns this route
        if route.driver_name != driver_id:
            return jsonify({'error': 'Not your route'}), 403
        
        # If already in progress, return success (idempotent)
        if route.status == 'IN_TRANSIT':
            db.session.commit()
            return jsonify({
                'routeId': route_id,
                'status': 'IN_TRANSIT',
                'ordersUpdated': 0,
                'message': 'Route already in progress'
            })
        
        # Can only start from 'DISPATCHED' status
        if route.status != 'DISPATCHED':
            db.session.rollback()
            return jsonify({
                'error': f'Cannot start route from status: {route.status}'
            }), 409
        
        # Update route status
        route.status = 'IN_TRANSIT'
        route.started_at = datetime.utcnow()
        route.updated_at = datetime.utcnow()
        
        # Bulk update all orders on this route from 'shipped' to 'out_for_delivery'
        updated_orders = db.session.query(Invoice).filter(
            Invoice.route_id == route_id,
            Invoice.status == 'shipped'
        ).update({
            'status': 'out_for_delivery',
            'status_updated_at': datetime.utcnow()
        }, synchronize_session=False)
        
        db.session.commit()
        
        logger.info(f"Driver {driver_id} started route {route_id}, updated {updated_orders} orders")
        
        return jsonify({
            'routeId': route_id,
            'status': 'IN_TRANSIT',
            'ordersUpdated': updated_orders,
            'startedAt': route.started_at.isoformat() if route.started_at else None
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error starting route {route_id}: {str(e)}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


def update_order_status(invoice_no, new_status, driver_id):
    """
    Helper function to update order status
    Can only update orders that are currently 'out_for_delivery' and belong to driver's route
    """
    # CRITICAL: Lock order and route together to verify ownership and status atomically
    # This prevents information leaks and TOCTOU race conditions
    result = db.session.query(Invoice, Shipment).join(
        Shipment, Invoice.route_id == Shipment.id
    ).filter(
        Invoice.invoice_no == invoice_no
    ).with_for_update().first()
    
    if not result:
        # Order not found or not assigned to any route
        return None, 'Order not found or not assigned to a route', 404
    
    order, route = result
    
    # CRITICAL: Verify order belongs to this driver's route (before accessing ANY order data)
    if route.driver_name != driver_id:
        return None, 'Order does not belong to your route', 403
    
    # Can only update orders that are out for delivery
    if order.status != 'out_for_delivery':
        return None, f'Cannot update order from status: {order.status}', 409
    
    # Update order status
    old_status = order.status
    order.status = new_status
    order.status_updated_at = datetime.utcnow()
    
    # Set delivery-specific fields
    if new_status == 'delivered':
        order.delivered_at = datetime.utcnow()
    elif new_status in ['delivery_failed', 'returned_to_warehouse']:
        # Optionally set undelivered reason if provided in request
        reason = request.json.get('reason') if request.is_json else None
        if reason:
            order.undelivered_reason = reason
    
    # Create delivery event for audit trail
    event = DeliveryEvent(
        invoice_no=invoice_no,
        action=new_status,
        actor=driver_id,
        timestamp=datetime.utcnow(),
        reason=order.undelivered_reason if new_status != 'delivered' else None
    )
    db.session.add(event)
    
    # Update ALL RouteStopInvoice rows for this invoice to match
    rsi_status_map = {
        'delivered': 'DELIVERED',
        'delivery_failed': 'FAILED',
        'returned_to_warehouse': 'FAILED'
    }
    if new_status in rsi_status_map:
        db.session.query(RouteStopInvoice).join(RouteStop).filter(
            RouteStop.shipment_id == route.id,
            RouteStopInvoice.invoice_no == invoice_no
        ).update({RouteStopInvoice.status: rsi_status_map[new_status]}, synchronize_session=False)
    
    # Recompute route completion after every delivery action
    recompute_route_completion(route.id)
    
    db.session.commit()
    
    logger.info(f"Driver {driver_id} updated order {invoice_no}: {old_status} -> {new_status}")
    
    return order, None, None


@driver_api_bp.route('/orders/<invoice_no>/deliver', methods=['PATCH'])
@driver_id_required
def deliver_order(invoice_no):
    """Mark an order as delivered"""
    order, error_msg, status_code = update_order_status(invoice_no, 'delivered', request.driver_id)
    
    if error_msg:
        return jsonify({'error': error_msg}), status_code
    
    return jsonify({
        'invoiceNo': order.invoice_no,
        'status': order.status,
        'deliveredAt': order.delivered_at.isoformat() if order.delivered_at else None
    })


@driver_api_bp.route('/orders/<invoice_no>/return', methods=['PATCH'])
@driver_id_required
def return_order(invoice_no):
    """Mark an order as returned to warehouse"""
    order, error_msg, status_code = update_order_status(invoice_no, 'returned_to_warehouse', request.driver_id)
    
    if error_msg:
        return jsonify({'error': error_msg}), status_code
    
    return jsonify({
        'invoiceNo': order.invoice_no,
        'status': order.status,
        'reason': order.undelivered_reason
    })


@driver_api_bp.route('/orders/<invoice_no>/fail', methods=['PATCH'])
@driver_id_required
def fail_order(invoice_no):
    """Mark an order as delivery failed"""
    order, error_msg, status_code = update_order_status(invoice_no, 'delivery_failed', request.driver_id)
    
    if error_msg:
        return jsonify({'error': error_msg}), status_code
    
    return jsonify({
        'invoiceNo': order.invoice_no,
        'status': order.status,
        'reason': order.undelivered_reason
    })


@driver_api_bp.route('/routes/<int:route_id>/complete', methods=['PATCH'])
@driver_id_required
def complete_route(route_id):
    """
    Complete a route - Only allowed when no active orders remain
    Active orders are those with status 'out_for_delivery' or 'shipped'
    """
    driver_id = request.driver_id
    
    try:
        # Lock route row
        route = db.session.query(Shipment).filter_by(id=route_id).with_for_update().first()
        
        if not route:
            return jsonify({'error': 'Route not found'}), 404
        
        # Verify driver owns this route
        if route.driver_name != driver_id:
            return jsonify({'error': 'Not your route'}), 403
        
        # Can only complete from 'IN_TRANSIT' status
        if route.status != 'IN_TRANSIT':
            db.session.rollback()
            return jsonify({
                'error': f'Cannot complete route from status: {route.status}'
            }), 409
        
        # Check for any remaining active orders
        active_orders_count = db.session.query(Invoice).filter(
            Invoice.route_id == route_id,
            Invoice.status.in_(['out_for_delivery', 'shipped'])
        ).count()
        
        if active_orders_count > 0:
            db.session.rollback()
            return jsonify({
                'error': f'Route still has {active_orders_count} active order(s)',
                'activeOrders': active_orders_count
            }), 409
        
        # Complete the route
        route.status = 'COMPLETED'
        route.completed_at = datetime.utcnow()
        route.updated_at = datetime.utcnow()
        
        # Automatically send all failed deliveries to Warehouse Intake
        from models import InvoicePostDeliveryCase, InvoiceRouteHistory, RouteStopInvoice
        failed_invoices = Invoice.query.filter_by(
            route_id=route_id,
            status='delivery_failed'
        ).all()
        
        intake_cases_created = 0
        for invoice in failed_invoices:
            # Check if intake case already exists
            existing_case = InvoicePostDeliveryCase.query.filter_by(
                invoice_no=invoice.invoice_no
            ).filter(
                InvoicePostDeliveryCase.status.in_(['OPEN', 'INTAKE_RECEIVED', 'REROUTE_QUEUED'])
            ).first()
            
            if not existing_case:
                # Get the stop for this invoice
                stop_invoice = RouteStopInvoice.query.filter_by(invoice_no=invoice.invoice_no).first()
                stop_id = stop_invoice.route_stop_id if stop_invoice else None
                
                # Create warehouse intake case
                intake_case = InvoicePostDeliveryCase(
                    invoice_no=invoice.invoice_no,
                    route_id=route_id,
                    route_stop_id=stop_id,
                    status='OPEN',
                    reason='Delivery failed',
                    notes=f'Auto-created on route completion',
                    created_by='system'
                )
                db.session.add(intake_case)
                
                # Log to invoice history
                history_entry = InvoiceRouteHistory(
                    invoice_no=invoice.invoice_no,
                    route_id=route_id,
                    route_stop_id=stop_id,
                    action='SENT_TO_WAREHOUSE',
                    reason='Delivery failed',
                    notes=f'Auto-sent to warehouse intake on route completion',
                    actor_username='system'
                )
                db.session.add(history_entry)
                
                intake_cases_created += 1
        
        db.session.commit()
        
        logger.info(f"Driver {driver_id} completed route {route_id}. {intake_cases_created} failed delivery(s) sent to warehouse intake.")
        
        return jsonify({
            'routeId': route_id,
            'status': 'COMPLETED',
            'completedAt': route.completed_at.isoformat() if route.completed_at else None,
            'intakeCasesCreated': intake_cases_created
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error completing route {route_id}: {str(e)}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


@driver_api_bp.route('/routes/<int:route_id>', methods=['GET'])
@driver_id_required
def get_route_details(route_id):
    """Get route details with all orders"""
    driver_id = request.driver_id
    
    route = Shipment.query.get(route_id)
    if not route:
        return jsonify({'error': 'Route not found'}), 404
    
    # Verify driver owns this route
    if route.driver_name != driver_id:
        return jsonify({'error': 'Not your route'}), 403
    
    # Get all orders for this route
    orders = Invoice.query.filter_by(route_id=route_id).all()
    
    return jsonify({
        'route': {
            'id': route.id,
            'driverName': route.driver_name,
            'routeName': route.route_name,
            'status': route.status,
            'deliveryDate': route.delivery_date.isoformat() if route.delivery_date else None,
            'startedAt': route.started_at.isoformat() if route.started_at else None,
            'completedAt': route.completed_at.isoformat() if route.completed_at else None
        },
        'orders': [{
            'invoiceNo': order.invoice_no,
            'customerName': order.customer_name,
            'status': order.status,
            'totalItems': order.total_items,
            'totalWeight': order.total_weight,
            'deliveredAt': order.delivered_at.isoformat() if order.delivered_at else None,
            'undeliveredReason': order.undelivered_reason
        } for order in orders]
    })
