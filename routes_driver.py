"""
Flask routes for Driver App
Handles route management, delivery workflow, COD, and POD collection
"""

from flask import Blueprint, render_template, request, jsonify, abort, redirect, url_for, flash, send_file
from flask_login import login_required, current_user
from functools import wraps
from decimal import Decimal
from datetime import datetime
import json
import logging

from app import db
from models import (
    Shipment, RouteStop, RouteStopInvoice, Invoice, InvoiceItem,
    DeliveryEvent, DeliveryLine, CODReceipt, PODRecord,
    DeliveryDiscrepancy, DeliveryDiscrepancyEvent, User, CreditTerms, utc_now,
    InvoicePostDeliveryCase, InvoiceRouteHistory
)
import services_warehouse_intake
from utils_pdf import generate_driver_receipt_pdf

driver_bp = Blueprint('driver', __name__, url_prefix='/driver')

# --- Decorators ---

def driver_required(f):
    """Decorator to require driver role"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role not in ['driver', 'admin']:
            flash('Access denied. Driver privileges required.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def require_in_transit(route):
    """Helper to enforce route must be IN_TRANSIT"""
    if route.status != 'IN_TRANSIT':
        abort(409, description="Route must be IN_TRANSIT")

def create_delivery_event(route_id, event_type, payload=None, stop_id=None, gps=None):
    """Helper to create a delivery event"""
    event = DeliveryEvent(
        route_id=route_id,
        route_stop_id=stop_id,
        event_type=event_type,
        payload=payload,
        gps_lat=gps.get('lat') if gps else None,
        gps_lng=gps.get('lng') if gps else None,
        actor_username=current_user.username,
        created_at=utc_now()
    )
    db.session.add(event)
    return event

def get_credit_terms(customer_code):
    """Fetch active credit terms for a customer, return sensible defaults if not found"""
    if not customer_code:
        # No customer code: default to POD with cash
        return {
            'is_credit': False,
            'allow_cash': True,
            'allow_cheque': False,
            'allow_bank_transfer': True,
            'allow_card_pos': False,
            'cheque_days_allowed': None,
            'notes_for_driver': None
        }
    
    # Get most recent active terms
    terms = CreditTerms.query.filter(
        CreditTerms.customer_code == customer_code,
        (CreditTerms.valid_to.is_(None)) | (CreditTerms.valid_to >= datetime.now().date())
    ).order_by(CreditTerms.valid_from.desc()).first()
    
    if not terms:
        # No terms found: default to POD with cash
        return {
            'is_credit': False,
            'allow_cash': True,
            'allow_cheque': False,
            'allow_bank_transfer': True,
            'allow_card_pos': False,
            'cheque_days_allowed': None,
            'notes_for_driver': None
        }
    
    return {
        'is_credit': terms.is_credit,
        'allow_cash': terms.allow_cash,
        'allow_cheque': terms.allow_cheque,
        'allow_bank_transfer': terms.allow_bank_transfer,
        'allow_card_pos': terms.allow_card_pos,
        'cheque_days_allowed': terms.cheque_days_allowed,
        'notes_for_driver': terms.notes_for_driver
    }

# --- Routes Dashboard ---

@driver_bp.route('/routes')
@driver_required
def routes_list():
    """Driver's routes dashboard"""
    # Get driver's routes:
    # 1. Active routes (IN_TRANSIT, DISPATCHED, PLANNED)
    # 2. Completed routes pending reconciliation
    routes = Shipment.query.filter_by(driver_name=current_user.username).filter(
        db.or_(
            Shipment.status.in_(['IN_TRANSIT', 'DISPATCHED', 'PLANNED']),
            db.and_(
                Shipment.status == 'COMPLETED',
                Shipment.reconciliation_status == 'PENDING'
            )
        )
    ).order_by(
        db.case(
            (Shipment.status == 'IN_TRANSIT', 1),
            (Shipment.status == 'DISPATCHED', 2),
            (Shipment.status == 'PLANNED', 3),
            (Shipment.status == 'COMPLETED', 4),
            else_=5
        ),
        Shipment.delivery_date.desc()
    ).all()
    
    # Calculate progress for each route
    route_data = []
    for route in routes:
        # Count delivered stops
        delivered_count = db.session.query(RouteStop).filter(
            RouteStop.shipment_id == route.id,
            RouteStop.delivered_at.isnot(None)
        ).count()
        
        total_stops = len(route.route_stops)
        
        # Count total items and weight (get stops in sequence order)
        stops = RouteStop.query.filter_by(shipment_id=route.id).order_by(RouteStop.seq_no).all()
        total_items = 0
        total_weight = 0
        for stop in stops:
            stop_invoices = RouteStopInvoice.query.filter_by(route_stop_id=stop.route_stop_id).all()
            for rsi in stop_invoices:
                invoice = Invoice.query.get(rsi.invoice_no)
                if invoice:
                    total_items += invoice.total_items or 0
                    total_weight += invoice.total_weight or 0
        
        route_data.append({
            'route': route,
            'delivered_count': delivered_count,
            'total_stops': total_stops,
            'total_items': total_items,
            'total_weight': round(total_weight, 2),
            'progress_percent': round((delivered_count / total_stops * 100) if total_stops > 0 else 0)
        })
    
    return render_template('driver/routes_list.html', route_data=route_data)

# --- Start Route ---

@driver_bp.route('/routes/<int:route_id>/start', methods=['POST'])
@driver_required
def start_route(route_id):
    """Start a route (DISPATCHED → IN_TRANSIT)"""
    try:
        route = Shipment.query.get_or_404(route_id)
        
        # Check permissions
        if route.driver_name != current_user.username and current_user.role != 'admin':
            abort(403, description="Not your route")
        
        # Check if driver has another active route
        active_route = Shipment.query.filter_by(
            driver_name=current_user.username,
            status='IN_TRANSIT'
        ).first()
        
        if active_route and active_route.id != route_id:
            return jsonify({
                'error': 'You already have an active route. Please finish or pause it first.',
                'active_route_id': active_route.id
            }), 409
        
        # Update status
        if route.status != 'DISPATCHED':
            return jsonify({'error': f'Route must be DISPATCHED to start (current: {route.status})'}), 400
        
        route.status = 'IN_TRANSIT'
        route.started_at = utc_now()
        
        # Update all invoices to out_for_delivery (process in sequence order)
        stops = RouteStop.query.filter_by(shipment_id=route_id).order_by(RouteStop.seq_no).all()
        for stop in stops:
            stop_invoices = RouteStopInvoice.query.filter_by(route_stop_id=stop.route_stop_id).all()
            for rsi in stop_invoices:
                invoice = Invoice.query.get(rsi.invoice_no)
                if invoice and (invoice.status == 'shipped' or invoice.status == 'ready_for_dispatch'):
                    invoice.status = 'out_for_delivery'
                    invoice.status_updated_at = utc_now()
                    # Sync to RouteStopInvoice
                    rsi.status = 'OUT_FOR_DELIVERY'
        
        # Create event
        create_delivery_event(
            route_id=route.id,
            event_type='start',
            payload={'started_by': current_user.username},
            gps=request.json.get('gps') if request.json else None
        )
        
        db.session.commit()
        
        return jsonify({'success': True, 'status': 'IN_TRANSIT'})
    
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error starting route {route_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500

# --- Stops List ---

@driver_bp.route('/routes/<int:route_id>/stops')
@driver_required
def stops_list(route_id):
    """Show stops for a route"""
    route = Shipment.query.get_or_404(route_id)
    
    # Check permissions
    if route.driver_name != current_user.username and current_user.role != 'admin':
        abort(403, description="Not your route")
    
    # Get stops with invoice details, sorted by:
    # 1. Status (pending first, then delivered/failed)
    # 2. Sequence number (for stops with the same status)
    stops = RouteStop.query.filter_by(shipment_id=route_id).order_by(
        db.case(
            (RouteStop.delivered_at.isnot(None), 1),
            (RouteStop.failed_at.isnot(None), 1),
            else_=0
        ),
        RouteStop.seq_no
    ).all()
    stops_data = []
    for stop in stops:
        # Get invoices for this stop
        stop_invoices = RouteStopInvoice.query.filter_by(route_stop_id=stop.route_stop_id).all()
        
        items_count = 0
        total_weight = 0
        total_gross = Decimal('0.00')
        invoice_list = []
        
        for rsi in stop_invoices:
            invoice = Invoice.query.get(rsi.invoice_no)
            if invoice:
                items_count += invoice.total_items or 0
                total_weight += invoice.total_weight or 0
                if invoice.total_grand:
                    total_gross += Decimal(str(invoice.total_grand))
                
                # Get credit terms for payment method
                # CRITICAL: Always use the customer_code from the invoice to fetch terms
                customer_code = invoice.customer_code
                if not customer_code and stop.customer_code:
                    customer_code = stop.customer_code
                    
                terms = get_credit_terms(customer_code)
                
                # Logic for display:
                # If is_credit is False, show allowed methods (Cash/Cheque/etc)
                # If is_credit is True, show the terms_code (e.g. NET30)
                if terms.get('is_credit'):
                    payment_method = terms.get('terms_code', 'Credit')
                else:
                    methods = []
                    if terms.get('allow_cash'): methods.append('Cash')
                    if terms.get('allow_cheque'): methods.append('Cheque')
                    if terms.get('allow_card_pos'): methods.append('Card')
                    if terms.get('allow_bank_transfer'): methods.append('Transfer')
                    
                    if methods:
                        payment_method = "/".join(methods)
                    else:
                        payment_method = terms.get('terms_code', 'COD')
                
                invoice_list.append({
                    'invoice_no': invoice.invoice_no,
                    'status': invoice.status,
                    'items': invoice.total_items or 0,
                    'weight': invoice.total_weight or 0,
                    'total_grand': invoice.total_grand,
                    'payment_method': payment_method
                })
        
        # Determine primary payment method for the stop
        unique_methods = list(set([inv['payment_method'] for inv in invoice_list if inv.get('payment_method')]))
        stop_payment_method = ", ".join(unique_methods) if unique_methods else "N/A"
        
        # Determine stop status
        if stop.delivered_at:
            stop_status = 'delivered'
        elif stop.failed_at:
            stop_status = 'failed'
        else:
            stop_status = 'pending'
        
        stops_data.append({
            'stop': stop,
            'items_count': items_count,
            'total_weight': round(total_weight, 2),
            'total_gross': float(total_gross),
            'status': stop_status,
            'invoices': invoice_list,
            'payment_method': stop_payment_method
        })
    
    return render_template('driver/stops_list.html', route=route, stops_data=stops_data)

# --- Stop Detail ---

@driver_bp.route('/stops/<int:stop_id>')
@driver_required
def stop_detail(stop_id):
    """Show stop details with customer info and invoices"""
    stop = RouteStop.query.get_or_404(stop_id)
    route = Shipment.query.get(stop.shipment_id)
    
    # Check permissions
    if route.driver_name != current_user.username and current_user.role != 'admin':
        abort(403, description="Not your route")
    
    # Get invoices for this stop
    stop_invoices = RouteStopInvoice.query.filter_by(route_stop_id=stop_id).all()
    
    invoices_data = []
    cod_total = Decimal('0.00')
    
    for rsi in stop_invoices:
        invoice = Invoice.query.get(rsi.invoice_no)
        if invoice:
            invoices_data.append({
                'invoice_no': invoice.invoice_no,
                'customer_name': invoice.customer_name,
                'items': invoice.total_items or 0,
                'weight': invoice.total_weight or 0,
                'status': invoice.status
            })
            # COD calculation (placeholder - will be enhanced with customer terms)
            # For now, assume total_weight * some price or fixed amount
    
    return render_template('driver/stop_detail.html', 
                         route=route, 
                         stop=stop, 
                         invoices=invoices_data,
                         cod_total=float(cod_total))

# --- Delivery Wizard (Exception-Only) ---

@driver_bp.route('/stops/<int:stop_id>/deliver')
@driver_required
def deliver_wizard(stop_id):
    """Show exception-only delivery wizard"""
    stop = RouteStop.query.get_or_404(stop_id)
    route = Shipment.query.get(stop.shipment_id)
    
    require_in_transit(route)
    
    # Check permissions
    if route.driver_name != current_user.username and current_user.role != 'admin':
        abort(403, description="Not your route")
    
    # Get customer credit terms
    terms = get_credit_terms(stop.customer_code)
    
    # Get all invoice items for this stop
    stop_invoices = RouteStopInvoice.query.filter_by(route_stop_id=stop_id).all()
    invoice_nos = [rsi.invoice_no for rsi in stop_invoices]
    
    items_data = []
    invoices_data = []
    total_invoices_amount = Decimal('0.00')
    
    for invoice_no in invoice_nos:
        invoice = Invoice.query.get(invoice_no)
        if invoice:
            # Add to invoices data with total
            invoice_total = invoice.total_grand or 0
            invoices_data.append({
                'invoice_no': invoice_no,
                'total_grand': float(invoice_total) if invoice_total else 0
            })
            if invoice_total:
                total_invoices_amount += Decimal(str(invoice_total))
            
            # Add items
            items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
            for item in items:
                items_data.append({
                    'invoice_no': invoice_no,
                    'item_code': item.item_code,
                    'item_name': item.item_name,
                    'qty_ordered': float(item.qty or 0),
                    'unit_type': item.unit_type,
                    'pack': item.pack,
                    'location': item.location
                })
    
    return render_template('driver/deliver_wizard.html',
                         route=route,
                         stop=stop,
                         items=items_data,
                         invoice_nos=invoice_nos,
                         invoices=invoices_data,
                         total_invoices_amount=float(total_invoices_amount),
                         terms=terms)

# --- Submit Delivery (Exception-Only) ---

@driver_bp.route('/stops/<int:stop_id>/deliver', methods=['POST'])
@driver_required
def submit_delivery(stop_id):
    """Process delivery with exception-only data"""
    try:
        data = request.get_json(force=True)
        exceptions = data.get('exceptions', [])
        invoice_nos = data.get('invoice_nos', [])
        cod = data.get('cod', {}) or {}
        pod = data.get('pod', {}) or {}
        gps = data.get('gps', {}) or {}
        
        stop = RouteStop.query.get_or_404(stop_id)
        route = Shipment.query.get(stop.shipment_id)
        
        require_in_transit(route)
        
        # Check permissions
        if route.driver_name != current_user.username and current_user.role != 'admin':
            abort(403)
        
        # Get customer credit terms
        terms = get_credit_terms(stop.customer_code)
        is_credit = terms['is_credit']
        
        # Load all invoice items
        items_by_key = {}
        for invoice_no in invoice_nos:
            items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
            for item in items:
                key = (invoice_no, item.item_code)
                items_by_key[key] = {
                    'item': item,
                    'ordered': Decimal(str(item.qty or 0)),
                    'delivered': Decimal(str(item.qty or 0)),  # Assume full delivery
                    'short': Decimal('0'),
                    'damaged_rejected': Decimal('0')
                }
        
        # Process exceptions
        discrepancies = []
        for ex in exceptions:
            ex_type = ex.get('type', '').upper()
            invoice_no = ex.get('invoice_no')
            item_code = ex.get('item_code')
            qty = Decimal(str(ex.get('qty', 0) or 0))
            notes = ex.get('notes', '')
            damaged_accepted = bool(ex.get('damagedAccepted', False))
            
            # Extract substitution/actual item data for WRONG type
            actual = ex.get('actual') or {}
            actual_barcode = actual.get('barcode')
            actual_item_code = actual.get('item_code')
            actual_qty = Decimal(str(actual.get('qty', qty))) if ex_type == 'WRONG' else None
            
            key = (invoice_no, item_code) if invoice_no and item_code else None
            
            # Create discrepancy record
            disc_data = {
                'invoice_no': invoice_no,
                'item_code_expected': item_code or 'UNKNOWN',
                'item_name': items_by_key[key]['item'].item_name if key and key in items_by_key else 'Unknown Item',
                'qty_expected': int(items_by_key[key]['ordered']) if key and key in items_by_key else 0,
                'qty_actual': float(items_by_key[key]['ordered'] - qty) if key and key in items_by_key else 0,
                'discrepancy_type': ex_type.lower(),
                'reported_by': current_user.username,
                'reported_at': utc_now(),
                'reported_source': 'driver',
                'status': 'reported',
                'note': notes
            }
            
            # Add actual item fields for WRONG/substitution type
            if ex_type == 'WRONG':
                disc_data.update({
                    'actual_item_code': actual_item_code,
                    'actual_barcode': actual_barcode,
                    'actual_qty': float(actual_qty) if actual_qty else None
                })
            
            disc = DeliveryDiscrepancy(**disc_data)
            db.session.add(disc)
            db.session.flush()
            
            # Create event
            event_note = 'Reported by driver during delivery'
            if ex_type == 'WRONG' and (actual_barcode or actual_item_code):
                actual_info = actual_item_code or actual_barcode
                event_note += f' - Substitution: Expected {item_code} → Actual {actual_info}'
            
            db.session.add(DeliveryDiscrepancyEvent(
                discrepancy_id=disc.id,
                event_type='created',
                actor=current_user.username,
                timestamp=utc_now(),
                note=event_note
            ))
            
            discrepancies.append(disc)
            
            # Adjust delivered quantities
            if key and key in items_by_key:
                if ex_type == 'SHORT':
                    items_by_key[key]['short'] += qty
                elif ex_type == 'DAMAGED' and not damaged_accepted:
                    items_by_key[key]['damaged_rejected'] += qty
                elif ex_type == 'WRONG':
                    # Treat as short on expected line (expected item was not delivered)
                    items_by_key[key]['short'] += qty
        
        # Calculate delivered quantities
        for key, data in items_by_key.items():
            ordered = data['ordered']
            not_delivered = min(ordered, data['short'] + data['damaged_rejected'])
            delivered = max(Decimal('0'), ordered - not_delivered)
            data['delivered'] = delivered
            
            # Create delivery line
            dl = DeliveryLine(
                route_id=route.id,
                route_stop_id=stop_id,
                invoice_no=key[0],
                item_code=key[1],
                qty_ordered=ordered,
                qty_delivered=delivered,
                created_at=utc_now()
            )
            db.session.add(dl)
        
        # Calculate COD expected amount from invoice totals (for POD customers)
        cod_expected = Decimal('0.00')
        for invoice_no in invoice_nos:
            invoice = Invoice.query.get(invoice_no)
            if invoice and invoice.total_grand:
                cod_expected += Decimal(str(invoice.total_grand))
        
        # Process COD based on credit terms
        cod_receipt = None
        
        if is_credit:
            # Credit account - no COD collection
            received = Decimal('0')
            cod_method = None
            cod_note = 'Credit account - no payment collected'
        else:
            # Non-credit - process COD
            if not cod:
                abort(400, description="Payment info required for non-credit customer")
            
            cod_method = cod.get('method', '').lower()
            
            # Validate payment method is allowed by terms
            allowed_methods = {
                'cash': terms['allow_cash'],
                'cheque': terms['allow_cheque'],
                'online': terms['allow_bank_transfer'],
                'card': terms['allow_card_pos']
            }
            
            if cod_method not in allowed_methods or not allowed_methods.get(cod_method, False):
                abort(400, description=f"Payment method '{cod_method}' not allowed by customer terms")
            
            # For online payment, no cash collected now
            if cod_method == 'online':
                received = Decimal('0')
                cod_note = cod.get('note', '') or 'Pay Online - Pending Collection'
            else:
                received = Decimal(str(cod.get('received', 0) or 0))
                cod_note = cod.get('note', '')
            cod_variance = received - cod_expected
            
            # Note is optional - driver can explain variance if needed
            # if cod_variance != 0 and not cod_note:
            #     abort(422, description="Variance note is required when variance ≠ 0")
            
            # Create COD receipt
            cod_receipt = CODReceipt(
                route_id=route.id,
                route_stop_id=stop_id,
                driver_username=current_user.username,
                invoice_nos=invoice_nos,
                expected_amount=cod_expected,
                received_amount=received,
                variance=cod_variance,
                payment_method=cod_method,
                note=cod_note,
                created_at=utc_now()
            )
            db.session.add(cod_receipt)
            db.session.flush()
        
        # Process POD
        pod_record = PODRecord(
            route_id=route.id,
            route_stop_id=stop_id,
            invoice_nos=invoice_nos,
            has_physical_signed_invoice=bool(pod.get('has_physical_signed_invoice', True)),
            receiver_name=pod.get('receiver_name', ''),
            receiver_relationship=pod.get('receiver_relationship', ''),
            photo_paths=pod.get('photos', []),
            gps_lat=gps.get('lat'),
            gps_lng=gps.get('lng'),
            collected_at=utc_now(),
            collected_by=current_user.username,
            notes=pod.get('notes', '')
        )
        db.session.add(pod_record)
        
        # Update invoices status
        for invoice_no in invoice_nos:
            invoice = Invoice.query.get(invoice_no)
            if invoice:
                invoice.status = 'delivered'
                invoice.delivered_at = utc_now()
                invoice.status_updated_at = utc_now()
                # Sync to RouteStopInvoice
                rsi = RouteStopInvoice.query.filter_by(route_stop_id=stop_id, invoice_no=invoice_no).first()
                if rsi:
                    rsi.status = 'delivered'
        
        # Update stop status
        stop.delivered_at = utc_now()
        
        # Create delivery event
        create_delivery_event(
            route_id=route.id,
            event_type='deliver',
            stop_id=stop_id,
            payload={
                'invoice_nos': invoice_nos,
                'exceptions_count': len(exceptions),
                'cod_expected': str(cod_expected),
                'cod_received': str(received)
            },
            gps=gps
        )
        
        # Check for partial deliveries and create warehouse intake cases
        for invoice_no in invoice_nos:
            if len(exceptions) > 0:
                # Has exceptions - may have partial delivery
                reason = f"Partial delivery - {len(exceptions)} exception(s) reported"
                services_warehouse_intake.open_post_delivery_case_if_needed(
                    invoice_no=invoice_no,
                    route_id=route.id,
                    route_stop_id=stop_id,
                    reason=reason,
                    notes=f"Driver: {current_user.username}. Exceptions: {len(exceptions)}"
                )
        
        # Check if route is now complete (all stops delivered/failed)
        from services_route_lifecycle import recompute_route_completion
        recompute_route_completion(route.id)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'cod': {
                'expected': float(cod_expected),
                'received': float(received if not is_credit else 0),
                'method': cod_method
            },
            'receipt_id': cod_receipt.id if cod_receipt else None
        })
    
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error submitting delivery for stop {stop_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500

# --- Fail Delivery ---

@driver_bp.route('/stops/<int:stop_id>/fail', methods=['POST'])
@driver_required
def fail_delivery(stop_id):
    """Mark delivery as failed"""
    try:
        data = request.get_json(force=True)
        reason = data.get('reason', 'Unknown')
        notes = data.get('notes', '')
        gps = data.get('gps', {})
        
        stop = RouteStop.query.get_or_404(stop_id)
        route = Shipment.query.get(stop.shipment_id)
        
        require_in_transit(route)
        
        # Update stop
        stop.failed_at = utc_now()
        stop.failure_reason = reason
        
        # Update invoices
        stop_invoices = RouteStopInvoice.query.filter_by(route_stop_id=stop_id).all()
        for rsi in stop_invoices:
            invoice = Invoice.query.get(rsi.invoice_no)
            if invoice:
                invoice.status = 'delivery_failed'
                invoice.undelivered_reason = f"{reason}: {notes}"
                invoice.status_updated_at = utc_now()
            # Sync to RouteStopInvoice
            rsi.status = 'delivery_failed'
        
        # Create event
        create_delivery_event(
            route_id=route.id,
            event_type='fail',
            stop_id=stop_id,
            payload={'reason': reason, 'notes': notes},
            gps=gps
        )
        
        # Check if route is now complete (all stops delivered/failed)
        from services_route_lifecycle import recompute_route_completion
        recompute_route_completion(route.id)
        
        db.session.commit()
        
        return jsonify({'success': True})
    
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error failing delivery for stop {stop_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500

# --- COD Receipt Print ---

@driver_bp.route('/receipts/<int:receipt_id>/print')
@driver_required
def print_receipt(receipt_id):
    """Print 58mm COD receipt"""
    receipt = CODReceipt.query.get_or_404(receipt_id)
    route = Shipment.query.get(receipt.route_id)
    stop = RouteStop.query.get(receipt.route_stop_id)
    
    # Get invoices
    invoices = []
    for invoice_no in receipt.invoice_nos:
        inv = Invoice.query.get(invoice_no)
        if inv:
            invoices.append(inv)
    
    return render_template('driver/receipt_58mm.html',
                         receipt=receipt,
                         route=route,
                         stop=stop,
                         invoices=invoices)

@driver_bp.route('/receipts/<int:receipt_id>/print_80mm')
@driver_required
def print_receipt_80mm(receipt_id):
    """Print 80mm COD receipt for POS printer"""
    receipt = CODReceipt.query.get_or_404(receipt_id)
    route = Shipment.query.get(receipt.route_id)
    stop = RouteStop.query.get(receipt.route_stop_id)
    
    # Get invoices
    invoices = []
    for invoice_no in receipt.invoice_nos:
        inv = Invoice.query.get(invoice_no)
        if inv:
            invoices.append(inv)
    
    # Get delivery discrepancies (exceptions) for this receipt's invoices
    from models import DeliveryDiscrepancy
    exceptions = DeliveryDiscrepancy.query.filter(
        DeliveryDiscrepancy.invoice_no.in_(receipt.invoice_nos)
    ).all() if receipt.invoice_nos else []
    
    logging.info(f"Receipt {receipt_id}: Found {len(exceptions)} exceptions for invoices {receipt.invoice_nos}")
    for exc in exceptions:
        logging.info(f"  - {exc.discrepancy_type}: {exc.item_name} (Expected: {exc.qty_expected}, Actual: {exc.qty_actual})")
    
    return render_template('driver/receipt_80mm.html',
                         receipt=receipt,
                         route=route,
                         stop=stop,
                         invoices=invoices,
                         exceptions=exceptions)

@driver_bp.route('/stops/<int:stop_id>/print_receipt')
@driver_required
def print_stop_receipt(stop_id):
    """Generate A5 PDF receipt for a route stop with COD information"""
    stop = RouteStop.query.get_or_404(stop_id)
    route = Shipment.query.get(stop.shipment_id)
    
    # Check permissions
    if route.driver_name != current_user.username and current_user.role != 'admin':
        abort(403, description="Not your route")
    
    # Get the most recent COD receipt for this stop
    cod_receipt = CODReceipt.query.filter_by(route_stop_id=stop_id).order_by(
        CODReceipt.created_at.desc()
    ).first()
    
    if not cod_receipt:
        # No COD receipt yet - get invoices for this stop
        stop_invoices = RouteStopInvoice.query.filter_by(route_stop_id=stop_id).all()
        invoice_numbers = [rsi.invoice_no for rsi in stop_invoices]
        
        expected_amount = Decimal('0.00')
        for invoice_no in invoice_numbers:
            inv = Invoice.query.get(invoice_no)
            if inv and inv.total_grand:
                expected_amount += Decimal(str(inv.total_grand))
        
        # Create receipt data showing NOT COLLECTED status
        receipt_data = {
            'receipt_id': f"PREVIEW-{stop_id}",
            'stop_name': stop.stop_name or 'N/A',
            'stop_addr': stop.stop_addr or '',
            'driver_name': route.driver_name,
            'delivered_at': stop.delivered_at or utc_now(),
            'payment_method': 'NOT COLLECTED',
            'expected_amount': expected_amount,
            'received_amount': None,  # None means not collected yet
            'variance': None,  # None means not collected yet
            'note': 'Payment not yet collected - this is a PREVIEW receipt',
            'exceptions_count': 0,
            'invoice_numbers': invoice_numbers,
            'is_preview': True  # Flag to indicate this is a preview
        }
    else:
        # Use actual COD receipt data
        receipt_data = {
            'receipt_id': f"COD-{cod_receipt.id}",
            'stop_name': stop.stop_name or 'N/A',
            'stop_addr': stop.stop_addr or '',
            'driver_name': route.driver_name,
            'delivered_at': stop.delivered_at or cod_receipt.created_at,
            'payment_method': cod_receipt.payment_method.replace('_', ' ').title(),
            'expected_amount': cod_receipt.expected_amount,
            'received_amount': cod_receipt.received_amount,
            'variance': cod_receipt.variance or Decimal('0.00'),
            'note': cod_receipt.note or '',
            'exceptions_count': 0,  # Could count delivery discrepancies if needed
            'invoice_numbers': cod_receipt.invoice_nos
        }
    
    # Generate PDF
    try:
        pdf_buffer = generate_driver_receipt_pdf(receipt_data)
        
        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=False,
            download_name=f'receipt_stop_{stop_id}.pdf'
        )
    except Exception as e:
        logging.error(f"Error generating receipt PDF for stop {stop_id}: {str(e)}")
        abort(500, description="Failed to generate receipt")

@driver_bp.route('/stops/<int:stop_id>/print_receipt_80mm')
@driver_required
def print_stop_receipt_80mm(stop_id):
    """Generate 80mm thermal receipt for a route stop"""
    stop = RouteStop.query.get_or_404(stop_id)
    route = Shipment.query.get(stop.shipment_id)
    
    # Check permissions
    if route.driver_name != current_user.username and current_user.role != 'admin':
        abort(403, description="Not your route")
    
    # Get the most recent COD receipt for this stop
    cod_receipt = CODReceipt.query.filter_by(route_stop_id=stop_id).order_by(
        CODReceipt.created_at.desc()
    ).first()
    
    # Get invoices for this stop
    stop_invoices = RouteStopInvoice.query.filter_by(route_stop_id=stop_id).all()
    invoice_numbers = [rsi.invoice_no for rsi in stop_invoices]
    
    invoices = []
    for invoice_no in invoice_numbers:
        inv = Invoice.query.get(invoice_no)
        if inv:
            invoices.append(inv)
    
    # Get delivery discrepancies (exceptions) for this stop's invoices
    from models import DeliveryDiscrepancy
    exceptions = DeliveryDiscrepancy.query.filter(
        DeliveryDiscrepancy.invoice_no.in_(invoice_numbers)
    ).all() if invoice_numbers else []
    
    logging.info(f"Stop {stop_id}: Found {len(exceptions)} exceptions for invoices {invoice_numbers}")
    for exc in exceptions:
        logging.info(f"  - {exc.discrepancy_type}: {exc.item_name} (Expected: {exc.qty_expected}, Actual: {exc.qty_actual})")
    
    # If we have a COD receipt, use it; otherwise create preview data
    if not cod_receipt:
        # Create a mock receipt object for preview
        from datetime import datetime
        
        expected_amount = Decimal('0.00')
        for inv in invoices:
            if inv.total_grand:
                expected_amount += Decimal(str(inv.total_grand))
        
        class MockReceipt:
            def __init__(self):
                self.id = f"PREVIEW-{stop_id}"
                self.expected_amount = expected_amount
                self.received_amount = Decimal('0.00')
                self.variance = Decimal('0.00')
                self.payment_method = 'not_collected'
                self.note = 'Payment not yet collected - this is a PREVIEW'
                self.created_at = datetime.utcnow()
                self.driver_username = route.driver_name
                self.invoice_nos = invoice_numbers
        
        receipt = MockReceipt()
    else:
        receipt = cod_receipt
    
    return render_template('driver/receipt_80mm.html',
                         receipt=receipt,
                         route=route,
                         stop=stop,
                         invoices=invoices,
                         exceptions=exceptions)

# --- API Endpoints ---

@driver_bp.route('/api/routes/<int:route_id>/progress')
@driver_required
def route_progress(route_id):
    """Get route progress data"""
    route = Shipment.query.get_or_404(route_id)
    
    delivered = db.session.query(RouteStop).filter(
        RouteStop.shipment_id == route_id,
        RouteStop.delivered_at.isnot(None)
    ).count()
    
    failed = db.session.query(RouteStop).filter(
        RouteStop.shipment_id == route_id,
        RouteStop.failed_at.isnot(None)
    ).count()
    
    total = len(route.route_stops)
    
    return jsonify({
        'total': total,
        'delivered': delivered,
        'failed': failed,
        'pending': total - delivered - failed,
        'progress_percent': round((delivered / total * 100) if total > 0 else 0)
    })

# --- Settlement Flow ---

@driver_bp.route('/routes/<int:route_id>/settlement')
@driver_required
def settlement_form(route_id):
    """Show settlement form for driver"""
    route = Shipment.query.get_or_404(route_id)
    
    # Check permissions
    if route.driver_name != current_user.username and current_user.role != 'admin':
        abort(403, description="Not your route")
    
    # Get all COD receipts for this route
    cod_receipts = CODReceipt.query.filter_by(route_id=route_id).all()
    
    # Calculate totals
    total_expected = sum(r.expected_amount for r in cod_receipts)
    total_received = sum(r.received_amount for r in cod_receipts)
    total_variance = total_received - total_expected
    
    # Get settlement info if exists
    settlement_info = {
        'submitted': route.driver_submitted_at is not None,
        'submitted_at': route.driver_submitted_at,
        'submitted_amount': route.cash_handed_in,
        'cleared': route.settlement_status == 'SETTLED',
        'cleared_at': route.completed_at,
        'cleared_by': None  # Not tracked separately in current model
    }
    
    return render_template('driver/settlement_form.html',
                         route=route,
                         cod_receipts=cod_receipts,
                         total_expected=float(total_expected),
                         total_received=float(total_received),
                         total_variance=float(total_variance),
                         settlement_info=settlement_info)

@driver_bp.route('/routes/<int:route_id>/settlement/submit', methods=['POST'])
@driver_required
def submit_settlement(route_id):
    """Driver submits settlement for admin clearance"""
    try:
        data = request.get_json(force=True)
        amount = Decimal(str(data.get('amount', 0) or 0))
        notes = data.get('notes', '')
        
        route = Shipment.query.get_or_404(route_id)
        
        # Check permissions
        if route.driver_name != current_user.username and current_user.role != 'admin':
            abort(403)
        
        # Check if already submitted
        if route.driver_submitted_at:
            return jsonify({'error': 'Settlement already submitted'}), 400
        
        # Calculate variance
        expected = route.cash_expected or Decimal('0.00')
        variance = amount - expected
        
        # Update route
        route.driver_submitted_at = utc_now()
        route.cash_handed_in = amount
        route.cash_variance = variance
        route.settlement_notes = notes
        route.settlement_status = 'DRIVER_SUBMITTED'
        
        # Create event
        create_delivery_event(
            route_id=route.id,
            event_type='settlement_submitted',
            payload={'amount': str(amount), 'notes': notes}
        )
        
        db.session.commit()
        
        return jsonify({'success': True})
    
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error submitting settlement for route {route_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500

@driver_bp.route('/routes/<int:route_id>/settlement/clear', methods=['POST'])
@login_required
def clear_settlement(route_id):
    """Admin clears settlement and completes route"""
    try:
        # Only admin can clear
        if current_user.role != 'admin':
            abort(403)
        
        route = Shipment.query.get_or_404(route_id)
        
        # Check if submitted
        if not route.driver_submitted_at:
            return jsonify({'error': 'Settlement not submitted yet'}), 400
        
        # Check if already cleared
        if route.settlement_status == 'SETTLED':
            return jsonify({'error': 'Settlement already cleared'}), 400
        
        # Update route
        route.settlement_status = 'SETTLED'
        route.status = 'COMPLETED'
        route.completed_at = utc_now()
        
        # Create event
        create_delivery_event(
            route_id=route.id,
            event_type='settlement_cleared',
            payload={'cleared_by': current_user.username}
        )
        
        # Automatically send all failed deliveries to Warehouse Intake
        failed_invoices = Invoice.query.filter_by(
            route_id=route_id,
            status='delivery_failed'
        ).all()
        
        intake_cases_created = 0
        for invoice in failed_invoices:
            # Get the stop for this invoice
            stop_invoice = RouteStopInvoice.query.filter_by(invoice_no=invoice.invoice_no).first()
            stop_id = stop_invoice.route_stop_id if stop_invoice else None
            
            # Get failure reason from delivery events
            failure_event = DeliveryEvent.query.filter_by(
                route_id=route_id,
                event_type='fail'
            ).filter(
                DeliveryEvent.payload.contains(invoice.invoice_no)
            ).first()
            
            failure_reason = "Delivery failed"
            if failure_event and failure_event.payload:
                failure_reason = failure_event.payload.get('reason', 'Delivery failed')
            
            # Check if intake case already exists
            existing_case = InvoicePostDeliveryCase.query.filter_by(
                invoice_no=invoice.invoice_no
            ).filter(
                InvoicePostDeliveryCase.status.in_(['OPEN', 'INTAKE_RECEIVED', 'REROUTE_QUEUED'])
            ).first()
            
            if not existing_case:
                # Create warehouse intake case
                intake_case = InvoicePostDeliveryCase(
                    invoice_no=invoice.invoice_no,
                    route_id=route_id,
                    route_stop_id=stop_id,
                    status='OPEN',
                    reason=failure_reason,
                    notes=f'Auto-created on route closure by {current_user.username}',
                    created_by=current_user.username
                )
                db.session.add(intake_case)
                
                # Log to invoice history
                history_entry = InvoiceRouteHistory(
                    invoice_no=invoice.invoice_no,
                    route_id=route_id,
                    route_stop_id=stop_id,
                    action='SENT_TO_WAREHOUSE',
                    reason=failure_reason,
                    notes=f'Auto-sent to warehouse intake on route closure',
                    actor_username=current_user.username
                )
                db.session.add(history_entry)
                
                intake_cases_created += 1
        
        db.session.commit()
        
        response_data = {'success': True}
        if intake_cases_created > 0:
            response_data['message'] = f'Settlement cleared. {intake_cases_created} failed delivery(s) sent to Warehouse Intake.'
        
        return jsonify(response_data)
    
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error clearing settlement for route {route_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500
