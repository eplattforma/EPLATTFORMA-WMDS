"""
Driver API Blueprint for Route and Order Management
Provides REST API endpoints for driver mobile app to manage deliveries
"""
from flask import Blueprint, request, jsonify
from functools import wraps
from datetime import datetime
from sqlalchemy import func
from models import Shipment, Invoice, DeliveryEvent, RouteStopInvoice, RouteStop, db
from app import db as database
from services_route_lifecycle import recompute_route_completion
from delivery_status import normalize_status, TERMINAL_DELIVERY_STATUSES
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
        request.driver_id = driver_id
        return f(*args, **kwargs)
    return decorated_function


def _lock_invoice_route_context(invoice_no, driver_id):
    """
    Lock and validate invoice+route ownership atomically via canonical RSI mapping.
    
    Uses RSI → RouteStop → Shipment as the source of truth instead of
    Invoice.route_id cache column.
    
    Returns:
        tuple: (invoice, rsi, route_stop, shipment, error_msg, status_code)
        On success error_msg and status_code are None.
    """
    result = (
        db.session.query(Invoice, RouteStopInvoice, RouteStop, Shipment)
        .join(RouteStopInvoice, RouteStopInvoice.invoice_no == Invoice.invoice_no)
        .join(RouteStop, RouteStop.route_stop_id == RouteStopInvoice.route_stop_id)
        .join(Shipment, Shipment.id == RouteStop.shipment_id)
        .filter(
            Invoice.invoice_no == invoice_no,
            RouteStopInvoice.is_active == True,
            RouteStop.deleted_at == None,
        )
        .with_for_update()
        .first()
    )

    if not result:
        return None, None, None, None, 'Order not found or not assigned to a route', 404

    invoice, rsi, stop, shipment = result

    if shipment.driver_name != driver_id:
        return None, None, None, None, 'Order does not belong to your route', 403

    return invoice, rsi, stop, shipment, None, None


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
        route = db.session.query(Shipment).filter_by(id=route_id).with_for_update().first()
        
        if not route:
            return jsonify({'error': 'Route not found'}), 404
        
        if route.driver_name != driver_id:
            return jsonify({'error': 'Not your route'}), 403
        
        if route.status == 'IN_TRANSIT':
            db.session.commit()
            return jsonify({
                'routeId': route_id,
                'status': 'IN_TRANSIT',
                'ordersUpdated': 0,
                'message': 'Route already in progress'
            })
        
        if route.status != 'DISPATCHED':
            db.session.rollback()
            return jsonify({
                'error': f'Cannot start route from status: {route.status}'
            }), 409
        
        route.status = 'IN_TRANSIT'
        route.started_at = datetime.utcnow()
        route.updated_at = datetime.utcnow()
        
        updated_orders = db.session.query(Invoice).filter(
            Invoice.route_id == route_id,
            func.lower(Invoice.status).in_(['shipped', 'ready_for_dispatch'])
        ).update({
            'status': 'out_for_delivery',
            'status_updated_at': datetime.utcnow()
        }, synchronize_session=False)
        
        rsi_subq = (
            db.session.query(RouteStopInvoice.route_stop_invoice_id)
            .join(RouteStop, RouteStop.route_stop_id == RouteStopInvoice.route_stop_id)
            .filter(
                RouteStop.shipment_id == route_id,
                RouteStop.deleted_at == None,
                RouteStopInvoice.is_active == True,
            )
            .subquery()
        )
        db.session.query(RouteStopInvoice).filter(
            RouteStopInvoice.route_stop_invoice_id.in_(db.session.query(rsi_subq.c.route_stop_invoice_id))
        ).update({RouteStopInvoice.status: 'out_for_delivery'}, synchronize_session=False)
        
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
    Helper function to update order status using canonical RSI→RouteStop→Shipment mapping.
    Makes actions idempotent: if invoice is already at target status, returns OK.
    """
    invoice, rsi, stop, shipment, err_msg, err_code = _lock_invoice_route_context(invoice_no, driver_id)

    if err_msg:
        return None, err_msg, err_code

    current_normalized = normalize_status(invoice.status)
    target_normalized = normalize_status(new_status)

    if current_normalized == target_normalized:
        db.session.commit()
        return invoice, None, None

    if current_normalized != 'out_for_delivery':
        return None, f'Cannot update order from status: {invoice.status}', 409

    old_status = invoice.status
    invoice.status = new_status
    invoice.status_updated_at = datetime.utcnow()
    
    if new_status == 'delivered':
        invoice.delivered_at = datetime.utcnow()
    elif new_status in ('delivery_failed', 'returned_to_warehouse'):
        reason = request.json.get('reason') if request.is_json else None
        if reason:
            invoice.undelivered_reason = reason
    
    event = DeliveryEvent(
        invoice_no=invoice_no,
        action=new_status,
        actor=driver_id,
        timestamp=datetime.utcnow(),
        reason=invoice.undelivered_reason if new_status != 'delivered' else None
    )
    db.session.add(event)
    
    rsi.status = new_status
    
    recompute_route_completion(shipment.id)
    
    db.session.commit()
    
    logger.info(f"Driver {driver_id} updated order {invoice_no}: {old_status} -> {new_status}")
    
    return invoice, None, None


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
    Complete a route - Only allowed when no active orders remain.
    Uses RSI as source of truth for pending/failed/returned status checks.
    """
    driver_id = request.driver_id
    
    try:
        route = db.session.query(Shipment).filter_by(id=route_id).with_for_update().first()
        
        if not route:
            return jsonify({'error': 'Route not found'}), 404
        
        if route.driver_name != driver_id:
            return jsonify({'error': 'Not your route'}), 403
        
        if route.status == 'COMPLETED':
            db.session.commit()
            return jsonify({
                'routeId': route_id,
                'status': 'COMPLETED',
                'completedAt': route.completed_at.isoformat() if route.completed_at else None,
                'intakeCasesCreated': 0,
                'message': 'Route already completed'
            })
        
        if route.status != 'IN_TRANSIT':
            db.session.rollback()
            return jsonify({
                'error': f'Cannot complete route from status: {route.status}'
            }), 409
        
        active_rsis = (
            db.session.query(RouteStopInvoice)
            .join(RouteStop, RouteStop.route_stop_id == RouteStopInvoice.route_stop_id)
            .filter(
                RouteStop.shipment_id == route_id,
                RouteStop.deleted_at == None,
                RouteStopInvoice.is_active == True,
            )
            .all()
        )

        pending_count = 0
        for rsi in active_rsis:
            norm = normalize_status(rsi.status)
            if norm not in TERMINAL_DELIVERY_STATUSES:
                pending_count += 1

        if pending_count > 0:
            db.session.rollback()
            return jsonify({
                'error': f'Route still has {pending_count} active order(s)',
                'activeOrders': pending_count
            }), 409
        
        result = recompute_route_completion(route_id, commit=False)
        
        if result['route_status'] != 'COMPLETED':
            db.session.rollback()
            return jsonify({
                'error': f'Route could not be completed. {result["pending_count"]} invoice(s) still pending.'
            }), 409
        
        route.updated_at = datetime.utcnow()
        
        from models import InvoicePostDeliveryCase, InvoiceRouteHistory
        
        failed_rsis = [
            r for r in active_rsis
            if normalize_status(r.status) in ('delivery_failed', 'returned_to_warehouse')
        ]
        
        intake_cases_created = 0
        for rsi in failed_rsis:
            existing_case = InvoicePostDeliveryCase.query.filter_by(
                invoice_no=rsi.invoice_no
            ).filter(
                InvoicePostDeliveryCase.status.in_(['OPEN', 'INTAKE_RECEIVED', 'REROUTE_QUEUED'])
            ).first()
            
            if not existing_case:
                intake_case = InvoicePostDeliveryCase(
                    invoice_no=rsi.invoice_no,
                    route_id=route_id,
                    route_stop_id=rsi.route_stop_id,
                    status='OPEN',
                    reason='Delivery failed',
                    notes=f'Auto-created on route completion',
                    created_by='system'
                )
                db.session.add(intake_case)
                
                history_entry = InvoiceRouteHistory(
                    invoice_no=rsi.invoice_no,
                    route_id=route_id,
                    route_stop_id=rsi.route_stop_id,
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
    
    if route.driver_name != driver_id:
        return jsonify({'error': 'Not your route'}), 403
    
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
