"""
Flask routes for Warehouse Intake System
Handles post-delivery cases, reroute requests, and return to stock
"""

from flask import Blueprint, render_template, request, jsonify, abort, flash, redirect, url_for
from flask_login import login_required, current_user
from functools import wraps

from app import db
from models import InvoicePostDeliveryCase, InvoiceRouteHistory, RerouteRequest, Invoice
import services_warehouse_intake

warehouse_bp = Blueprint('warehouse', __name__, url_prefix='/warehouse')


def admin_required(f):
    """Decorator to require admin/warehouse_manager role"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role not in ['admin', 'warehouse_manager']:
            flash('Access denied. Admin privileges required.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


@warehouse_bp.route('/intake')
@admin_required
def intake_dashboard():
    """Display warehouse intake dashboard with failed deliveries only"""
    from models import Shipment, User
    
    # Get all open warehouse intake cases (failed deliveries)
    # Join with invoices to get customer details
    intake_cases = db.session.query(InvoicePostDeliveryCase, Invoice).join(
        Invoice, InvoicePostDeliveryCase.invoice_no == Invoice.invoice_no
    ).filter(
        InvoicePostDeliveryCase.status.in_(['OPEN', 'INTAKE_RECEIVED'])
    ).order_by(InvoicePostDeliveryCase.created_at.desc()).all()
    
    # Transform to list of invoice objects with case info attached
    failed_invoices = []
    for case, invoice in intake_cases:
        # Attach case info to invoice for template access
        invoice._intake_case = case
        failed_invoices.append(invoice)
    
    # Also include invoices with delivery_failed status that don't have a case yet
    # so they can be processed through the intake workflow
    delivery_failed_invoices = Invoice.query.filter_by(status='delivery_failed').all()
    for invoice in delivery_failed_invoices:
        # Only add if not already in the list (to avoid duplicates)
        if not any(inv.invoice_no == invoice.invoice_no for inv in failed_invoices):
            invoice._intake_case = None
            failed_invoices.append(invoice)
    
    # Get all reroute requests
    reroute_requests = RerouteRequest.query.filter_by(status='OPEN').all()
    
    # Get available routes (PLANNED and DISPATCHED only for assignment)
    available_routes = Shipment.query.filter(
        Shipment.status.in_(['PLANNED', 'DISPATCHED'])
    ).order_by(Shipment.delivery_date, Shipment.driver_name).all()
    
    # Get all drivers
    drivers = User.query.filter_by(role='driver').all()
    
    return render_template('warehouse/intake_dashboard.html',
                         failed_invoices=failed_invoices,
                         reroute_requests=reroute_requests,
                         available_routes=available_routes,
                         drivers=drivers)


@warehouse_bp.route('/cases/<int:case_id>/intake', methods=['POST'])
@admin_required
def mark_intake_received(case_id):
    """Mark case as physically received at warehouse"""
    try:
        notes = request.form.get('notes', '')
        services_warehouse_intake.mark_intake_received(case_id, notes)
        flash('Case marked as received at warehouse', 'success')
        return redirect(url_for('warehouse.intake_dashboard'))
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('warehouse.intake_dashboard'))


@warehouse_bp.route('/cases/<int:case_id>/reroute', methods=['POST'])
@admin_required
def queue_reroute(case_id):
    """Queue invoice for reroute (re-delivery)"""
    try:
        notes = request.form.get('notes', '')
        reroute_request = services_warehouse_intake.queue_for_reroute(case_id, notes)
        flash(f'Invoice queued for reroute (Request #{reroute_request.id})', 'success')
        return redirect(url_for('warehouse.intake_dashboard'))
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('warehouse.intake_dashboard'))


@warehouse_bp.route('/cases/<int:case_id>/return-to-stock', methods=['POST'])
@admin_required
def return_to_stock(case_id):
    """Return remainder to stock and close case - redirect to printable picking list"""
    try:
        notes = request.form.get('notes', '')
        case = services_warehouse_intake.return_to_stock(case_id, notes)
        # Redirect to printable put-away list
        return redirect(url_for('warehouse.print_putaway_list', invoice_no=case.invoice_no))
    except Exception as e:
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('warehouse.intake_dashboard'))


@warehouse_bp.route('/putaway/<invoice_no>')
@admin_required
def print_putaway_list(invoice_no):
    """Display printable put-away list sorted by shelf location"""
    from models import InvoiceItem
    from sqlalchemy import text
    
    invoice = Invoice.query.get_or_404(invoice_no)
    
    # Get all items for this invoice with shelf locations, sorted by location
    items_query = db.session.execute(text("""
        SELECT 
            ii.item_code,
            ii.item_name,
            ii.qty as quantity,
            ii.location as shelf_location
        FROM invoice_items ii
        WHERE ii.invoice_no = :invoice_no
        ORDER BY 
            CASE 
                WHEN ii.location IS NULL OR ii.location = '' THEN 'ZZZZZ'
                ELSE ii.location 
            END,
            ii.item_code
    """), {"invoice_no": invoice_no}).fetchall()
    
    items = []
    for row in items_query:
        items.append({
            'item_code': row.item_code,
            'item_name': row.item_name or '',
            'quantity': float(row.quantity) if row.quantity else 0,
            'shelf_location': row.shelf_location or 'NO LOCATION'
        })
    
    from datetime import datetime
    
    return render_template('warehouse/putaway_list.html',
                         invoice=invoice,
                         items=items,
                         current_date=datetime.now().strftime('%Y-%m-%d %H:%M'))


@warehouse_bp.route('/invoice/<invoice_no>/history')
@admin_required
def invoice_history(invoice_no):
    """Get invoice routing history"""
    history = InvoiceRouteHistory.query.filter_by(
        invoice_no=invoice_no
    ).order_by(InvoiceRouteHistory.created_at.desc()).all()
    
    return jsonify({
        'invoice_no': invoice_no,
        'history': [{
            'action': h.action,
            'reason': h.reason,
            'notes': h.notes,
            'actor': h.actor_username,
            'created_at': h.created_at.isoformat() if h.created_at else None
        } for h in history]
    })


@warehouse_bp.route('/invoice/<invoice_no>/remainder')
@admin_required
def invoice_remainder(invoice_no):
    """Get invoice remainder info"""
    remainder_info = services_warehouse_intake.compute_invoice_remainder(invoice_no)
    return jsonify(remainder_info)


@warehouse_bp.route('/invoice/<invoice_no>/return', methods=['POST'])
@admin_required
def return_invoice_to_warehouse(invoice_no):
    """Return a failed delivery invoice back to warehouse"""
    try:
        from datetime import datetime
        
        data = request.get_json()
        notes = data.get('notes', '')
        
        # Get invoice
        invoice = Invoice.query.get_or_404(invoice_no)
        
        # Update status to returned_to_warehouse
        invoice.status = 'returned_to_warehouse'
        invoice.status_updated_at = datetime.utcnow()
        
        # Log history
        from models import InvoiceRouteHistory
        history_entry = InvoiceRouteHistory(
            invoice_no=invoice_no,
            route_id=invoice.route_id,
            action='RETURNED_TO_WAREHOUSE',
            reason='Failed delivery',
            notes=notes,
            actor_username=current_user.username
        )
        db.session.add(history_entry)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Invoice {invoice_no} returned to warehouse'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@warehouse_bp.route('/assign-to-route', methods=['POST'])
@admin_required
def assign_to_route():
    """Assign reroute invoice to a route (existing or new) or mark for warehouse collection"""
    from models import Shipment
    from datetime import datetime
    import services_routing
    
    try:
        data = request.get_json()
        invoice_no = data.get('invoice_no')
        reroute_request_id = data.get('reroute_request_id')
        case_id = data.get('case_id')
        warehouse_collection = data.get('warehouse_collection', False)
        
        if not invoice_no:
            return jsonify({'error': 'Invoice number required'}), 400
        
        # Handle warehouse collection (customer pickup)
        if warehouse_collection:
            invoice = Invoice.query.get(invoice_no)
            if not invoice:
                return jsonify({'error': 'Invoice not found'}), 404
            
            # Mark as delivered
            invoice.status = 'delivered'
            invoice.status_updated_at = datetime.utcnow()
            invoice.delivered_at = datetime.utcnow()
            
            # Update reroute request status if provided
            if reroute_request_id:
                reroute_req = RerouteRequest.query.get(reroute_request_id)
                if reroute_req:
                    reroute_req.status = 'COMPLETED'
                    reroute_req.completed_at = datetime.utcnow()
                    reroute_req.notes = (reroute_req.notes or '') + ' | Collected from warehouse'
            
            # Close the case if it exists
            if case_id:
                case = InvoicePostDeliveryCase.query.get(case_id)
                if case:
                    case.status = 'CLOSED'
                    case.updated_at = datetime.utcnow()
                    case.resolution_notes = (case.resolution_notes or '') + ' | Customer collected from warehouse'
            
            # Log history
            from models import InvoiceRouteHistory
            history_entry = InvoiceRouteHistory(
                invoice_no=invoice_no,
                route_id=None,
                action='WAREHOUSE_COLLECTION',
                reason='Customer pickup',
                notes='Marked as delivered - collected from warehouse',
                actor_username=current_user.username
            )
            db.session.add(history_entry)
            
            db.session.commit()
            
            return jsonify({
                'success': True,
                'message': f'Invoice {invoice_no} marked as delivered (warehouse collection)'
            })
        
        # Get or create route
        if data.get('create_new_route'):
            # Create new route
            driver_name = data.get('driver_name')
            delivery_date_str = data.get('delivery_date')
            route_name = data.get('route_name', '')
            
            if not driver_name or not delivery_date_str:
                return jsonify({'error': 'Driver and delivery date required'}), 400
            
            delivery_date = datetime.strptime(delivery_date_str, '%Y-%m-%d').date()
            
            # Create route
            route = Shipment(
                driver_name=driver_name,
                route_name=route_name,
                delivery_date=delivery_date,
                status='PLANNED'
            )
            db.session.add(route)
            db.session.flush()  # Get route ID
            route_id = route.id
        else:
            # Use existing route
            route_id = data.get('route_id')
            if not route_id:
                return jsonify({'error': 'Route ID required'}), 400
        
        # If case_id is provided, create a reroute request if it doesn't exist
        if case_id and not reroute_request_id:
            case = InvoicePostDeliveryCase.query.get(case_id)
            if case:
                # Create reroute request
                reroute_req = RerouteRequest(
                    invoice_no=invoice_no,
                    requested_by=current_user.username,
                    status='OPEN',
                    notes=f'Auto-created from case #{case_id}'
                )
                db.session.add(reroute_req)
                db.session.flush()
                reroute_request_id = reroute_req.id
                
                # Update case status
                case.status = 'REROUTE_QUEUED'
        
        # Assign invoice to route
        result = services_routing.assign_invoices_to_route_grouped_by_customer(
            route_id, [invoice_no]
        )
        
        if not result.get('ok'):
            return jsonify({'error': result.get('message', 'Assignment failed')}), 400
        
        # Update reroute request status if provided
        if reroute_request_id:
            reroute_req = RerouteRequest.query.get(reroute_request_id)
            if reroute_req:
                reroute_req.status = 'ASSIGNED'
                reroute_req.assigned_route_id = route_id
                reroute_req.completed_at = datetime.utcnow()
        
        # Close the case if it exists
        if case_id:
            case = InvoicePostDeliveryCase.query.get(case_id)
            if case:
                case.status = 'CLOSED'
                case.updated_at = datetime.utcnow()
        
        # Update invoice status to ready_for_dispatch
        invoice = Invoice.query.get(invoice_no)
        if invoice:
            invoice.status = 'ready_for_dispatch'
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Invoice {invoice_no} assigned to route #{route_id}',
            'route_id': route_id
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@warehouse_bp.route('/receipt-sample')
@login_required
def receipt_sample():
    """Display sample stop receipt for 70mm thermal printer"""
    return render_template('warehouse/stop_receipt_sample.html')
