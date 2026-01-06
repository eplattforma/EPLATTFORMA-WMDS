"""
Shipment Management Routes
Handles creating shipments, assigning orders, and tracking delivery status
"""

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from app import db
from models import Shipment, ShipmentOrder, Invoice
from datetime import datetime, date
from sqlalchemy import and_, or_
from routes import validate_csrf_token

shipments_bp = Blueprint('shipments', __name__)

@shipments_bp.route('/shipments')
def shipments_dashboard():
    """Show all shipments with filtering options"""
    # Get filter parameters
    status_filter = request.args.get('status', '')
    driver_filter = request.args.get('driver', '')
    date_filter = request.args.get('date', '')
    
    # Build query with filters
    query = Shipment.query
    
    if status_filter:
        query = query.filter(Shipment.status == status_filter)
    
    if driver_filter:
        query = query.filter(Shipment.driver_name.ilike(f'%{driver_filter}%'))
    
    if date_filter:
        try:
            filter_date = datetime.strptime(date_filter, '%Y-%m-%d').date()
            query = query.filter(Shipment.delivery_date == filter_date)
        except ValueError:
            pass  # Invalid date format, ignore filter
    
    shipments = query.order_by(Shipment.delivery_date.desc(), Shipment.created_at.desc()).all()
    
    # Get unique drivers for filter dropdown
    drivers = db.session.query(Shipment.driver_name).distinct().all()
    drivers = [driver[0] for driver in drivers if driver[0]]
    
    return render_template('shipments_dashboard.html', 
                         shipments=shipments, 
                         drivers=drivers,
                         status_filter=status_filter,
                         driver_filter=driver_filter,
                         date_filter=date_filter)

@shipments_bp.route('/shipments/create', methods=['GET', 'POST'])
def create_shipment():
    """Create a new shipment"""
    if request.method == 'POST':
        # CSRF Protection
        if not validate_csrf_token():
            flash('Security error. Please try again.', 'error')
            return redirect(url_for('shipments.create_shipment'))
        
        driver_name = request.form.get('driver_name', '').strip()
        route_name = request.form.get('route_name', '').strip()
        delivery_date_str = request.form.get('delivery_date', '')
        
        if not driver_name:
            flash('Driver name is required', 'error')
            return redirect(url_for('shipments.create_shipment'))
        
        if not delivery_date_str:
            flash('Delivery date is required', 'error')
            return redirect(url_for('shipments.create_shipment'))
        
        try:
            delivery_date = datetime.strptime(delivery_date_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Invalid delivery date format', 'error')
            return redirect(url_for('shipments.create_shipment'))
        
        # Create new shipment
        shipment = Shipment(
            driver_name=driver_name,
            route_name=route_name if route_name else None,
            delivery_date=delivery_date,
            status='created'
        )
        
        db.session.add(shipment)
        db.session.commit()
        
        flash(f'Shipment created successfully for driver {driver_name}', 'success')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment.id))
    
    return render_template('create_shipment.html')

@shipments_bp.route('/shipments/<int:shipment_id>')
def shipment_details(shipment_id):
    """View shipment details and manage orders"""
    shipment = Shipment.query.get_or_404(shipment_id)
    
    # Get all orders in this shipment with invoice details
    shipment_orders_raw = db.session.query(ShipmentOrder, Invoice).join(
        Invoice, ShipmentOrder.invoice_no == Invoice.invoice_no
    ).filter(ShipmentOrder.shipment_id == shipment_id).all()
    
    # Sort by routing number numerically (handle empty/null values)
    def numeric_routing_key(item):
        shipment_order, invoice = item
        routing = invoice.routing
        if not routing or routing.strip() == '':
            return float('inf')  # Put empty values at the end
        try:
            return float(routing)
        except (ValueError, TypeError):
            return float('inf')  # Put non-numeric values at the end
    
    shipment_orders = sorted(shipment_orders_raw, key=numeric_routing_key)
    
    return render_template('shipment_details.html', 
                         shipment=shipment, 
                         shipment_orders=shipment_orders)

@shipments_bp.route('/orders/available')
def available_orders():
    """Show available ready-for-dispatch orders not assigned to any shipment"""
    # Get optional shipment_id from query parameters
    shipment_id = request.args.get('shipment_id')
    # Get ready-for-dispatch orders that are not assigned to any shipment
    assigned_invoices = db.session.query(ShipmentOrder.invoice_no).distinct().subquery()
    
    available = db.session.query(Invoice).filter(
        and_(
            Invoice.status == 'ready_for_dispatch',
            ~Invoice.invoice_no.in_(assigned_invoices)
        )
    ).order_by(Invoice.invoice_no.desc()).all()
    
    # Get shipment info if shipment_id is provided
    target_shipment = None
    if shipment_id:
        target_shipment = Shipment.query.get(shipment_id)
    
    return render_template('available_orders.html', orders=available, target_shipment=target_shipment)

@shipments_bp.route('/shipments/<int:shipment_id>/add-order', methods=['POST'])
def add_order_to_shipment(shipment_id):
    """Add an order to a shipment"""
    # CSRF Protection
    if not validate_csrf_token():
        flash('Security error. Please try again.', 'error')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))
    
    shipment = Shipment.query.get_or_404(shipment_id)
    invoice_no = request.form.get('invoice_no', '').strip()
    
    if not invoice_no:
        flash('Invoice number is required', 'error')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))
    
    # Check if invoice exists and is completed
    invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if not invoice:
        flash('Invoice not found', 'error')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))
    
    if invoice.status != 'ready_for_dispatch':
        flash('Only orders ready for dispatch can be added to shipments', 'error')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))
    
    # Check if order is already assigned to a shipment
    existing = ShipmentOrder.query.filter_by(invoice_no=invoice_no).first()
    if existing:
        flash('Order is already assigned to a shipment', 'error')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))
    
    # Add order to shipment
    shipment_order = ShipmentOrder(
        shipment_id=shipment_id,
        invoice_no=invoice_no
    )
    
    # Update order status to 'shipped' when added to shipment
    invoice.status = 'shipped'
    
    db.session.add(shipment_order)
    db.session.commit()
    
    flash(f'Order {invoice_no} added to shipment successfully', 'success')
    
    # Check if we came from the available orders page (with shipment context)
    referrer = request.referrer
    if referrer and '/orders/available' in referrer and f'shipment_id={shipment_id}' in referrer:
        # Stay on the available orders page to add more orders
        return redirect(url_for('shipments.available_orders', shipment_id=shipment_id))
    else:
        # Go back to shipment details
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))

@shipments_bp.route('/shipments/<int:shipment_id>/remove-order', methods=['POST'])
def remove_order_from_shipment(shipment_id):
    """Remove an order from a shipment"""
    # CSRF Protection
    if not validate_csrf_token():
        flash('Security error. Please try again.', 'error')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))
    
    invoice_no = request.form.get('invoice_no', '').strip()
    
    if not invoice_no:
        flash('Invoice number is required', 'error')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))
    
    # Find and remove the shipment order
    shipment_order = ShipmentOrder.query.filter_by(
        shipment_id=shipment_id, 
        invoice_no=invoice_no
    ).first()
    
    if not shipment_order:
        flash('Order not found in this shipment', 'error')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))
    
    # Update order status back to 'ready_for_dispatch' when removed from shipment
    invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if invoice:
        invoice.status = 'ready_for_dispatch'
    
    db.session.delete(shipment_order)
    db.session.commit()
    
    flash(f'Order {invoice_no} removed from shipment', 'success')
    return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))

@shipments_bp.route('/shipments/<int:shipment_id>/mark-delivered', methods=['POST'])
def mark_order_delivered(shipment_id):
    """Mark an order as delivered"""
    # CSRF Protection
    if not validate_csrf_token():
        flash('Security error. Please try again.', 'error')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))
    
    invoice_no = request.form.get('invoice_no', '').strip()
    
    if not invoice_no:
        flash('Invoice number is required', 'error')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))
    
    # Find the shipment order
    shipment_order = ShipmentOrder.query.filter_by(
        shipment_id=shipment_id, 
        invoice_no=invoice_no
    ).first()
    
    if not shipment_order:
        flash('Order not found in this shipment', 'error')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))
    
    # Update the main invoice status to 'delivered'
    invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if invoice:
        invoice.status = 'delivered'
    
    db.session.commit()
    
    flash(f'Order {invoice_no} marked as delivered', 'success')
    return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))

@shipments_bp.route('/shipments/<int:shipment_id>/mark-undelivered', methods=['POST'])
def mark_order_undelivered(shipment_id):
    """Mark an order as undelivered with reason"""
    # CSRF Protection
    if not validate_csrf_token():
        flash('Security error. Please try again.', 'error')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))
    
    invoice_no = request.form.get('invoice_no', '').strip()
    reason = request.form.get('reason', '').strip()
    
    if not invoice_no:
        flash('Invoice number is required', 'error')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))
    
    if not reason:
        flash('Reason for undelivered status is required', 'error')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))
    
    # Find the shipment order
    shipment_order = ShipmentOrder.query.filter_by(
        shipment_id=shipment_id, 
        invoice_no=invoice_no
    ).first()
    
    if not shipment_order:
        flash('Order not found in this shipment', 'error')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))
    
    # Update the main invoice status to 'delivery_failed'
    invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if invoice:
        invoice.status = 'delivery_failed'
    
    db.session.commit()
    
    flash(f'Order {invoice_no} marked as undelivered', 'success')
    return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))

@shipments_bp.route('/shipments/<int:shipment_id>/update-status', methods=['POST'])
def update_shipment_status(shipment_id):
    """Update shipment status (created, in_transit, completed, cancelled)"""
    # CSRF Protection
    if not validate_csrf_token():
        flash('Security error. Please try again.', 'error')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))
    
    shipment = Shipment.query.get_or_404(shipment_id)
    new_status = request.form.get('status', '').strip()
    
    if new_status not in ['created', 'in_transit', 'completed', 'cancelled']:
        flash('Invalid shipment status', 'error')
        return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))
    
    shipment.status = new_status
    db.session.commit()
    
    flash(f'Shipment status updated to {new_status.replace("_", " ").title()}', 'success')
    return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))

@shipments_bp.route('/shipments/bulk-add-orders', methods=['POST'])
def bulk_add_orders_to_shipment():
    """Add multiple orders to a shipment at once"""
    # CSRF Protection
    if not validate_csrf_token():
        flash('Security error. Please try again.', 'error')
        return redirect(url_for('shipments.available_orders'))
    
    shipment_id = request.form.get('shipment_id', '').strip()
    invoice_numbers_str = request.form.get('invoice_numbers', '').strip()
    
    if not shipment_id:
        flash('Shipment ID is required', 'error')
        return redirect(url_for('shipments.available_orders'))
    
    if not invoice_numbers_str:
        flash('No orders selected', 'error')
        return redirect(url_for('shipments.available_orders'))
    
    # Get the shipment
    shipment = Shipment.query.get_or_404(shipment_id)
    
    # Parse the invoice numbers
    invoice_numbers = [inv.strip() for inv in invoice_numbers_str.split(',') if inv.strip()]
    
    if not invoice_numbers:
        flash('No valid orders selected', 'error')
        return redirect(url_for('shipments.available_orders'))
    
    success_count = 0
    error_count = 0
    errors = []
    
    for invoice_no in invoice_numbers:
        try:
            # Check if invoice exists and is completed
            invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
            if not invoice:
                errors.append(f'Invoice {invoice_no} not found')
                error_count += 1
                continue
            
            if invoice.status != 'ready_for_dispatch':
                errors.append(f'Invoice {invoice_no} is not ready for dispatch')
                error_count += 1
                continue
            
            # Check if order is already assigned to a shipment
            existing = ShipmentOrder.query.filter_by(invoice_no=invoice_no).first()
            if existing:
                errors.append(f'Invoice {invoice_no} is already assigned to a shipment')
                error_count += 1
                continue
            
            # Add order to shipment
            shipment_order = ShipmentOrder(
                shipment_id=shipment_id,
                invoice_no=invoice_no
            )
            
            # Update order status to 'shipped' when added to shipment
            invoice.status = 'shipped'
            
            db.session.add(shipment_order)
            success_count += 1
            
        except Exception as e:
            errors.append(f'Error adding {invoice_no}: {str(e)}')
            error_count += 1
    
    # Commit all successful additions
    try:
        db.session.commit()
        
        # Create success message
        if success_count > 0:
            flash(f'Successfully added {success_count} order{"s" if success_count > 1 else ""} to shipment #{shipment_id}', 'success')
        
        # Show errors if any
        if error_count > 0:
            flash(f'{error_count} order{"s" if error_count > 1 else ""} could not be added: {"; ".join(errors[:3])}{"..." if len(errors) > 3 else ""}', 'error')
            
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving orders to shipment: {str(e)}', 'error')
    
    return redirect(url_for('shipments.shipment_details', shipment_id=shipment_id))

# API Endpoints for potential mobile app or AJAX calls
@shipments_bp.route('/api/shipments', methods=['GET'])
def api_get_shipments():
    """API endpoint to get all shipments"""
    shipments = Shipment.query.order_by(Shipment.delivery_date.desc()).all()
    
    shipments_data = []
    for shipment in shipments:
        shipments_data.append({
            'id': shipment.id,
            'driver_name': shipment.driver_name,
            'route_name': shipment.route_name,
            'status': shipment.status,
            'delivery_date': shipment.delivery_date.isoformat(),
            'created_at': shipment.created_at.isoformat(),
            'order_count': len(shipment.shipment_orders)
        })
    
    return jsonify(shipments_data)

@shipments_bp.route('/api/orders/available', methods=['GET'])
def api_get_available_orders():
    """API endpoint to get available orders"""
    # Get ready-for-dispatch orders that are not assigned to any shipment
    assigned_invoices = db.session.query(ShipmentOrder.invoice_no).distinct().subquery()
    
    available = db.session.query(Invoice).filter(
        and_(
            Invoice.status == 'ready_for_dispatch',
            ~Invoice.invoice_no.in_(assigned_invoices)
        )
    ).order_by(Invoice.invoice_no.desc()).all()
    
    orders_data = []
    for order in available:
        orders_data.append({
            'invoice_no': order.invoice_no,
            'customer_name': order.customer_name,
            'status': order.status,
            'completion_time': order.completion_time.isoformat() if order.completion_time else None
        })
    
    return jsonify(orders_data)