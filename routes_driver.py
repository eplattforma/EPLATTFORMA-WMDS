"""
Flask routes for Driver App
Handles route management, delivery workflow, COD, and POD collection
"""

from flask import Blueprint, render_template, request, jsonify, abort, redirect, url_for, flash, send_file, make_response
from flask_login import login_required, current_user
from functools import wraps
from decimal import Decimal
from datetime import datetime, timedelta
import json
import logging

from app import db
from models import (
    Shipment, RouteStop, RouteStopInvoice, Invoice, InvoiceItem,
    DeliveryEvent, DeliveryLine, CODReceipt, PODRecord,
    DeliveryDiscrepancy, DeliveryDiscrepancyEvent, User, CreditTerms, utc_now,
    InvoicePostDeliveryCase, InvoiceRouteHistory, DwInvoiceLine, RouteReturnHandover
)
from timezone_utils import utc_now_for_db, get_local_time
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
        'notes_for_driver': terms.notes_for_driver if terms.notes_for_driver and terms.notes_for_driver.strip() not in ('', 'None', 'null') else None
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
        
        # Idempotent: if already IN_TRANSIT, just redirect to stops
        if route.status == 'IN_TRANSIT':
            return jsonify({'success': True, 'status': 'IN_TRANSIT', 'already_started': True})

        # Update status
        if route.status != 'DISPATCHED':
            return jsonify({'error': f'Route must be DISPATCHED to start (current: {route.status})'}), 400
        
        route.status = 'IN_TRANSIT'
        route.started_at = utc_now()
        
        # Update all invoices to out_for_delivery via canonical RSI mapping
        stops = RouteStop.query.filter(
            RouteStop.shipment_id == route_id,
            RouteStop.deleted_at == None
        ).order_by(RouteStop.seq_no).all()
        for stop in stops:
            stop_invoices = RouteStopInvoice.query.filter_by(
                route_stop_id=stop.route_stop_id,
                is_active=True
            ).all()
            for rsi in stop_invoices:
                invoice = Invoice.query.get(rsi.invoice_no)
                if invoice and (invoice.status == 'shipped' or invoice.status == 'ready_for_dispatch'):
                    invoice.status = 'out_for_delivery'
                    invoice.status_updated_at = utc_now()
                    rsi.status = 'out_for_delivery'
        
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
                # Driver can collect any method allowed by terms
                methods = []
                if terms.get('allow_cash'): methods.append('Cash')
                if terms.get('allow_cheque'): methods.append('Cheque')
                if terms.get('allow_card_pos'): methods.append('Card')
                if terms.get('allow_bank_transfer'): methods.append('Transfer')
                
                if terms.get('is_credit'):
                    payment_method = terms.get('terms_code', 'Credit')
                elif methods:
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
            
            # Add items with line totals from DwInvoiceLine
            items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
            for item in items:
                # Try to get line total with VAT from DwInvoiceLine
                line_total_incl = None
                dw_line = DwInvoiceLine.query.filter_by(
                    invoice_no_365=invoice_no,
                    item_code_365=item.item_code
                ).first()
                if dw_line and dw_line.line_total_incl:
                    line_total_incl = float(dw_line.line_total_incl)
                
                items_data.append({
                    'invoice_no': invoice_no,
                    'item_code': item.item_code,
                    'item_name': item.item_name,
                    'qty_ordered': float(item.qty or 0),
                    'unit_type': item.unit_type,
                    'pack': item.pack,
                    'location': item.location,
                    'line_total_incl': line_total_incl
                })
    
    # Get existing exceptions/discrepancies for this stop
    from models import DeliveryDiscrepancy
    existing_discs = DeliveryDiscrepancy.query.filter(
        DeliveryDiscrepancy.invoice_no.in_(invoice_nos)
    ).all() if invoice_nos else []
    
    existing_exceptions_payload = []
    for disc in existing_discs:
        is_rebate = (disc.discrepancy_type == 'rebate')
        
        # Determine the 'qty' as it's used in the frontend (difference/variance)
        # Frontend: qty = missing qty. disc.qty_actual = delivered qty.
        # So missing = expected - actual
        missing_qty = float(Decimal(str(disc.qty_expected or 0)) - Decimal(str(disc.qty_actual or 0)))
        
        ex_payload = {
            'id': disc.id,
            'invoice_no': disc.invoice_no,
            'item_code': disc.item_code_expected,
            'item_name': disc.item_name,
            'type': disc.discrepancy_type.upper(),
            'qty': 1 if is_rebate else missing_qty,
            'amount': float(disc.reported_value or 0) if is_rebate else None,
            'qty_ordered': float(disc.qty_expected or 0),
            'unit_type': '',
            'notes': disc.note or '',
            'damagedAccepted': False,
            'actual': None,
            'exception_value': float(disc.reported_value or 0),
            'is_rebate': is_rebate,
            'from_db': True
        }
        existing_exceptions_payload.append(ex_payload)

    return render_template('driver/deliver_wizard.html',
                         route=route,
                         stop=stop,
                         items=items_data,
                         invoice_nos=invoice_nos,
                         invoices=invoices_data,
                         total_invoices_amount=float(total_invoices_amount),
                         terms=terms,
                         existing_exceptions=existing_exceptions_payload)

# --- Save Exceptions (called before printing exceptions proof) ---

@driver_bp.route('/stops/<int:stop_id>/save_exceptions', methods=['POST'])
@driver_required
def save_exceptions(stop_id):
    """Persist driver-entered exceptions to DB so the printed proof is a legal record."""
    try:
        data = request.get_json(force=True)
        exceptions_data = data.get('exceptions', [])

        stop = RouteStop.query.get_or_404(stop_id)
        route = Shipment.query.get(stop.shipment_id)

        if route.driver_name != current_user.username and current_user.role != 'admin':
            abort(403)

        # Get invoice nos for this stop
        stop_invoices = RouteStopInvoice.query.filter_by(route_stop_id=stop_id, is_active=True).all()
        invoice_nos = [rsi.invoice_no for rsi in stop_invoices]

        from services_discrepancy import process_discrepancy_for_settlement, create_or_update_post_delivery_case

        # --- Sync: delete removed exceptions ---
        # IDs that the driver still wants to keep
        kept_ids = {int(ex['id']) for ex in exceptions_data if ex.get('id')}

        existing_discs = DeliveryDiscrepancy.query.filter(
            DeliveryDiscrepancy.invoice_no.in_(invoice_nos),
            DeliveryDiscrepancy.reported_source == 'driver'
        ).all() if invoice_nos else []

        deleted_count = 0
        for disc in existing_discs:
            if disc.id not in kept_ids:
                db.session.delete(disc)
                deleted_count += 1

        # --- Sync: add new exceptions (those without an id) ---
        items_by_key = {}
        for invoice_no in invoice_nos:
            items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
            for item in items:
                items_by_key[(invoice_no, item.item_code)] = item

        saved_count = 0
        for ex in exceptions_data:
            if ex.get('id'):
                continue  # already in DB, not deleted above, so keep it
            ex_type = str(ex.get('type', '')).upper()
            invoice_no = ex.get('invoice_no')
            item_code = ex.get('item_code')
            qty = Decimal(str(ex.get('qty', 0) or 0))
            notes = ex.get('notes', '') or ''
            is_rebate = (item_code == 'REB-00')
            key = (invoice_no, item_code) if invoice_no and item_code else None
            item_obj = items_by_key.get(key) if key else None

            if not is_rebate and item_obj:
                ordered = Decimal(str(item_obj.qty or 0))
                already_in_db = sum(
                    Decimal(str(d.qty_expected or 0)) - Decimal(str(d.qty_actual or 0))
                    for d in existing_discs
                    if d.invoice_no == invoice_no and d.item_code_expected == item_code and d.id in kept_ids
                )
                already_new = sum(
                    Decimal(str(prev.get('qty', 0) or 0))
                    for prev in exceptions_data[:exceptions_data.index(ex)]
                    if not prev.get('id') and prev.get('invoice_no') == invoice_no
                    and prev.get('item_code') == item_code and prev.get('item_code') != 'REB-00'
                )
                if qty + already_in_db + already_new > ordered:
                    return jsonify({'error': f"Exception qty exceeds ordered qty {ordered} for {item_code} on {invoice_no}"}), 400

            disc = DeliveryDiscrepancy(
                invoice_no=invoice_no,
                item_code_expected=item_code or 'UNKNOWN',
                item_name='Rebate / General Discount' if is_rebate else (item_obj.item_name if item_obj else 'Unknown Item'),
                qty_expected=1 if is_rebate else (int(item_obj.qty or 0) if item_obj else 0),
                qty_actual=1 if is_rebate else (float((Decimal(str(item_obj.qty or 0)) - qty)) if item_obj else 0),
                discrepancy_type='rebate' if is_rebate else ex_type.lower(),
                reported_by=current_user.username,
                reported_at=utc_now(),
                reported_source='driver',
                status='reported',
                note=notes,
                reported_value=Decimal(str(ex.get('exception_value', 0) or 0))
            )
            db.session.add(disc)
            db.session.flush()

            process_discrepancy_for_settlement(disc)

            db.session.add(DeliveryDiscrepancyEvent(
                discrepancy_id=disc.id,
                event_type='created',
                actor=current_user.username,
                timestamp=utc_now(),
                note='Saved by driver'
            ))

            create_or_update_post_delivery_case(
                invoice_no=invoice_no,
                route_id=route.id,
                route_stop_id=stop.route_stop_id,
                created_by=current_user.username,
                reason=f"Discrepancy: {disc.discrepancy_type}"
            )
            saved_count += 1

        db.session.commit()

        # Return updated list with IDs so frontend can track them
        updated_discs = DeliveryDiscrepancy.query.filter(
            DeliveryDiscrepancy.invoice_no.in_(invoice_nos),
            DeliveryDiscrepancy.reported_source == 'driver'
        ).all() if invoice_nos else []

        updated_list = []
        for d in updated_discs:
            is_reb = (d.discrepancy_type == 'rebate')
            updated_list.append({
                'id': d.id,
                'invoice_no': d.invoice_no,
                'item_code': d.item_code_expected,
                'item_name': d.item_name,
                'type': d.discrepancy_type.upper(),
                'qty': 1 if is_reb else float(Decimal(str(d.qty_expected or 0)) - Decimal(str(d.qty_actual or 0))),
                'qty_ordered': float(d.qty_expected or 0),
                'notes': d.note or '',
                'exception_value': float(d.reported_value or 0),
                'is_rebate': is_reb,
                'from_db': True
            })

        return jsonify({'success': True, 'saved': saved_count, 'deleted': deleted_count, 'exceptions': updated_list})

    except Exception as e:
        db.session.rollback()
        import logging
        logging.exception("save_exceptions error")
        return jsonify({'success': False, 'error': str(e)}), 500


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
        
        has_signed_invoice = bool(pod.get('has_physical_signed_invoice', False))
        signature_required = False
        
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
        total_rebate_reduction = Decimal('0.00')
        # Track discrepancy value per invoice for recording on RouteStopInvoice
        discrepancy_values_by_invoice = {}

        # Skip re-saving exceptions if they were already committed at proof-print time
        exceptions_already_saved = bool(invoice_nos and DeliveryDiscrepancy.query.filter(
            DeliveryDiscrepancy.invoice_no.in_(invoice_nos),
            DeliveryDiscrepancy.reported_source == 'driver'
        ).first())
        if exceptions_already_saved:
            discrepancies = DeliveryDiscrepancy.query.filter(
                DeliveryDiscrepancy.invoice_no.in_(invoice_nos),
                DeliveryDiscrepancy.reported_source == 'driver'
            ).all()
            for disc in discrepancies:
                if disc.invoice_no:
                    val = Decimal(str(disc.reported_value or 0))
                    discrepancy_values_by_invoice[disc.invoice_no] = discrepancy_values_by_invoice.get(disc.invoice_no, Decimal('0')) + val
                    if disc.discrepancy_type == 'rebate':
                        total_rebate_reduction += val

        for ex in exceptions if not exceptions_already_saved else []:
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
            
            # Special handling for Rebate REB-00
            is_rebate = (item_code == 'REB-00')

            if not is_rebate and key and key in items_by_key:
                ordered = items_by_key[key]['ordered']
                already_excepted = sum(
                    Decimal(str(prev.get('qty', 0) or 0))
                    for prev in exceptions[:exceptions.index(ex)]
                    if prev.get('invoice_no') == invoice_no
                    and prev.get('item_code') == item_code
                    and prev.get('item_code') != 'REB-00'
                )
                if qty + already_excepted > ordered:
                    abort(400, description=f"Exception qty {qty + already_excepted} exceeds ordered qty {ordered} for item {item_code} on {invoice_no}")
            rebate_amount = Decimal(str(ex.get('amount', 0) or 0)) if is_rebate else Decimal(0)
            if is_rebate:
                total_rebate_reduction += rebate_amount

            # Create discrepancy record
            disc_data = {
                'invoice_no': invoice_no,
                'item_code_expected': item_code or 'UNKNOWN',
                'item_name': 'Rebate / General Discount' if is_rebate else (items_by_key[key]['item'].item_name if key and key in items_by_key else 'Unknown Item'),
                'qty_expected': 1 if is_rebate else (int(items_by_key[key]['ordered']) if key and key in items_by_key else 0),
                'qty_actual': 1 if is_rebate else (float(items_by_key[key]['ordered'] - qty) if key and key in items_by_key else 0),
                'discrepancy_type': 'rebate' if is_rebate else ex_type.lower(),
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
            
            # Process discrepancy for settlement (calculate deduct_amount, set CN required)
            from services_discrepancy import process_discrepancy_for_settlement, create_or_update_post_delivery_case
            process_discrepancy_for_settlement(disc)
            
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
            
            # Accumulate discrepancy value per invoice
            exception_value = Decimal(str(ex.get('exception_value', 0) or 0))
            if ex_type == 'DAMAGED' and damaged_accepted:
                exception_value = Decimal('0')
            if invoice_no:
                discrepancy_values_by_invoice[invoice_no] = discrepancy_values_by_invoice.get(invoice_no, Decimal('0')) + exception_value
            
            # Record reported value on discrepancy (driver-entered value)
            disc.reported_value = exception_value
            
            # Create/update post-delivery case for this invoice
            create_or_update_post_delivery_case(
                invoice_no=invoice_no,
                route_id=route.id,
                route_stop_id=stop.route_stop_id,
                created_by=current_user.username,
                reason=f"Discrepancy: {disc.discrepancy_type}"
            )
            
            if is_rebate:
                # Rebates don't affect physical quantities of actual items
                pass
            elif key and key in items_by_key:
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
        
        invoice_total_sum = Decimal('0.00')
        for invoice_no in invoice_nos:
            inv = Invoice.query.get(invoice_no)
            if inv and inv.total_grand:
                invoice_total_sum += Decimal(str(inv.total_grand))

        total_deductions = sum(discrepancy_values_by_invoice.values(), Decimal('0.00'))

        cod_expected = invoice_total_sum - total_deductions
        if cod_expected < 0:
            cod_expected = Decimal('0.00')
        
        # Process COD based on credit terms
        cod_receipt = None
        
        if is_credit:
            # Credit account - no COD collection
            received = Decimal('0')
            cod_method = None
            cod_note = 'Credit account - no payment collected'
            signature_required = True
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
            
            today_date = datetime.now().date()
            cheque_date_str = cod.get('cheque_date')
            cheque_date_parsed = datetime.strptime(cheque_date_str, '%Y-%m-%d').date() if cheque_date_str else None
            is_postdated = (cod_method == 'cheque' and cheque_date_parsed and cheque_date_parsed > today_date)

            if cod_method == 'online' or is_postdated:
                signature_required = True
            
            # For online payment, no cash collected now
            if cod_method == 'online':
                received = Decimal('0')
                cod_note = cod.get('note', '') or 'Pay Online - Pending Collection'
            else:
                received = Decimal(str(cod.get('received', 0) or 0))
                cod_note = cod.get('note', '')
            cod_variance = received - cod_expected
            
            existing_receipt = CODReceipt.query.filter(
                CODReceipt.route_stop_id == stop_id,
                db.or_(
                    CODReceipt.status.is_(None),
                    CODReceipt.status != 'VOIDED'
                )
            ).first()
            if existing_receipt:
                cod_receipt = existing_receipt
            else:
                cheque_date_val = datetime.strptime(cod.get('cheque_date'), '%Y-%m-%d').date() if cod.get('cheque_date') else None
                is_postdated_cheque = cod_method == 'cheque' and cheque_date_val and cheque_date_val > datetime.now().date()

                if cod_method == 'cash':
                    doc_type = 'official'
                elif cod_method == 'cheque' and not is_postdated_cheque:
                    doc_type = 'official'
                elif is_postdated_cheque or cod_method in ('postdated', 'post_dated'):
                    doc_type = 'pdc_ack'
                elif cod_method == 'online':
                    doc_type = 'online_notice'
                else:
                    doc_type = 'official'

                cod_receipt = CODReceipt(
                    route_id=route.id,
                    route_stop_id=stop_id,
                    driver_username=current_user.username,
                    invoice_nos=invoice_nos,
                    expected_amount=cod_expected,
                    received_amount=received,
                    variance=cod_variance,
                    payment_method=cod_method,
                    cheque_number=cod.get('cheque_number'),
                    cheque_date=cheque_date_val,
                    note=cod_note,
                    doc_type=doc_type,
                    status='DRAFT',
                    created_at=utc_now()
                )
                db.session.add(cod_receipt)
                db.session.flush()
            
            from models import CODInvoiceAllocation
            if not existing_receipt:
                cheque_date_alloc = datetime.strptime(cod.get('cheque_date'), '%Y-%m-%d').date() if cod.get('cheque_date') else None
                is_postdated_alloc = cod_method == 'cheque' and cheque_date_alloc and cheque_date_alloc > datetime.now().date()
                is_pending_method = cod_method in ('online', 'postdated', 'post_dated') or is_postdated_alloc

                inv_rows = []
                for invoice_no in invoice_nos:
                    inv = Invoice.query.get(invoice_no)
                    invoice_total = Decimal(str(inv.total_grand or 0)) if inv else Decimal('0')
                    invoice_deduct = discrepancy_values_by_invoice.get(invoice_no, Decimal('0'))
                    invoice_due = max(invoice_total - invoice_deduct, Decimal('0'))
                    inv_rows.append({
                        'invoice_no': invoice_no,
                        'invoice_total': invoice_total,
                        'invoice_deduct': invoice_deduct,
                        'invoice_due': invoice_due,
                    })

                inv_rows.sort(key=lambda r: r['invoice_due'])

                remaining = received
                for row in inv_rows:
                    if len(invoice_nos) == 1:
                        invoice_received = received
                    else:
                        invoice_received = min(row['invoice_due'], remaining)
                        remaining -= invoice_received

                    is_underpaid = (invoice_received + row['invoice_deduct']) < row['invoice_total']
                    is_pending = is_pending_method or is_underpaid

                    allocation = CODInvoiceAllocation(
                        cod_receipt_id=cod_receipt.id,
                        invoice_no=row['invoice_no'],
                        route_id=route.id,
                        expected_amount=row['invoice_total'],
                        received_amount=invoice_received,
                        deduct_amount=row['invoice_deduct'],
                        payment_method=cod_method,
                        is_pending=is_pending,
                        cheque_number=cod.get('cheque_number'),
                        cheque_date=cheque_date_alloc
                    )
                    db.session.add(allocation)
        
        if signature_required and not has_signed_invoice:
            abort(400, description="Physical signed invoice is required for Credit / Pay Online / Post-dated cheque")
        
        # Process POD
        pod_record = PODRecord(
            route_id=route.id,
            route_stop_id=stop_id,
            invoice_nos=invoice_nos,
            has_physical_signed_invoice=has_signed_invoice,
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
                    # Record the discrepancy value (monetary impact of exceptions)
                    if invoice_no in discrepancy_values_by_invoice:
                        rsi.discrepancy_value = discrepancy_values_by_invoice[invoice_no]
        
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
        
        print_png_url = None
        if cod_receipt:
            print_png_url = url_for('driver.print_receipt_png_by_id', receipt_id=cod_receipt.id)

        exceptions_print_url = None
        if discrepancies:
            exceptions_print_url = url_for('driver.print_exceptions_png', stop_id=stop_id)

        return jsonify({
            'success': True,
            'cod': {
                'expected': float(cod_expected),
                'received': float(received if not is_credit else 0),
                'method': cod_method
            },
            'receipt_id': cod_receipt.id if cod_receipt else None,
            'print_png_url': print_png_url,
            'exceptions_print_url': exceptions_print_url
        })
    
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error submitting delivery for stop {stop_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500

# --- Driver Correction Request ---

@driver_bp.route('/receipts/<int:receipt_id>/request_correction', methods=['POST'])
@driver_required
def request_receipt_correction(receipt_id):
    """Driver requests a correction on an ISSUED receipt.
    
    Creates a note against the receipt and flags it for admin review.
    The driver cannot void/reissue directly; an admin must act on the request.
    """
    try:
        receipt = CODReceipt.query.get_or_404(receipt_id)
        route = Shipment.query.get(receipt.route_id)

        if not route:
            return jsonify({'success': False, 'error': 'Route not found'}), 404

        if route.driver_name != current_user.username and current_user.role != 'admin':
            abort(403, description="Not your receipt")

        if receipt.status == 'VOIDED':
            return jsonify({'success': False, 'error': 'Receipt is already voided; contact admin for reissue'}), 400

        data = request.get_json(force=True) if request.is_json else {}
        reason = (data.get('reason') or '').strip()
        if not reason:
            return jsonify({'success': False, 'error': 'A correction reason is required'}), 400

        existing_note = receipt.note or ''
        correction_flag = f"[CORRECTION REQUEST by {current_user.username} at {utc_now().strftime('%Y-%m-%d %H:%M')} UTC]: {reason}"
        receipt.note = f"{existing_note}\n{correction_flag}".strip()

        db.session.commit()
        logging.info(f"Receipt {receipt_id} correction requested by {current_user.username}: {reason}")

        return jsonify({
            'success': True,
            'message': 'Correction request submitted. An admin will review and reissue if necessary.',
            'receipt_id': receipt_id
        })

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error requesting correction for receipt {receipt_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


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
        
        # Update invoices (only active RSI links)
        stop_invoices = RouteStopInvoice.query.filter_by(route_stop_id=stop_id, is_active=True).all()
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
    
    invoices = []
    for invoice_no in receipt.invoice_nos:
        inv = Invoice.query.get(invoice_no)
        if inv:
            invoices.append(inv)
    
    from models import DeliveryDiscrepancy
    exceptions = DeliveryDiscrepancy.query.filter(
        DeliveryDiscrepancy.invoice_no.in_(receipt.invoice_nos)
    ).all() if receipt.invoice_nos else []
    
    return render_template('driver/receipt_80mm.html',
                         receipt=receipt,
                         route=route,
                         stop=stop,
                         invoices=invoices,
                         exceptions=exceptions)

@driver_bp.route('/receipts/<int:receipt_id>/print.png')
@driver_required
def print_receipt_png_by_id(receipt_id):
    """Generate PNG receipt for a COD receipt (for BIXOLON mPrint via Share API)."""
    from services.receipt_render_png import render_receipt_png
    from io import BytesIO

    receipt = CODReceipt.query.get_or_404(receipt_id)
    stop = RouteStop.query.get(receipt.route_stop_id)
    route = Shipment.query.get(receipt.route_id)

    # For official receipts: call PS365 before printing.
    # Only print when a valid reference number is returned; use it as the receipt number.
    doc_type_val = (receipt.doc_type or 'official').lower()
    if doc_type_val == 'official' and receipt.status != 'VOIDED':
        if not receipt.ps365_reference_number:
            try:
                from routes_receipts import create_receipt_core
                invoice_nos_list = receipt.invoice_nos or []
                inv_list_str = ', '.join(invoice_nos_list[:5])
                if len(invoice_nos_list) > 5:
                    inv_list_str += f' +{len(invoice_nos_list)-5}'
                ok, ref_num, resp_id, _, _ = create_receipt_core(
                    customer_code=stop.customer_code if stop else '',
                    amount_val=float(receipt.received_amount or 0),
                    comments=inv_list_str,
                    driver_username=receipt.driver_username,
                    user_code=current_user.username,
                    route_stop_id=receipt.route_stop_id,
                    invoice_no=inv_list_str,
                    cheque_number=receipt.cheque_number or '',
                    cheque_date=receipt.cheque_date.strftime('%Y-%m-%d') if receipt.cheque_date else '',
                    allow_duplicate_stop=True
                )
                receipt.ps365_reference_number = ref_num
                receipt.ps365_receipt_id = str(resp_id) if resp_id else None
                receipt.ps365_synced_at = utc_now()
            except Exception as ps365_err:
                logging.error(f"PS365 receipt creation failed for receipt {receipt_id}: {ps365_err}")
                return jsonify({'error': f'Payment could not be registered in Powersoft: {ps365_err}. Receipt not printed.'}), 503

    if receipt.status != 'VOIDED':
        now = utc_now()
        if receipt.status != 'ISSUED':
            receipt.status = 'ISSUED'
            receipt.locked_at = now
            receipt.locked_by = current_user.username
            receipt.print_count = 1
            receipt.first_printed_at = now
            receipt.last_printed_at = now
        else:
            receipt.print_count = (receipt.print_count or 0) + 1
            receipt.last_printed_at = now
        db.session.commit()

    invoices_list = []
    invoice_total_sum = Decimal('0')
    invoice_nos_plain = []
    for inv_no in (receipt.invoice_nos or []):
        inv = Invoice.query.get(inv_no)
        inv_total = Decimal(str(inv.total_grand or 0)) if inv else Decimal('0')
        invoices_list.append({'invoice_no': inv_no, 'total': inv_total})
        invoice_total_sum += inv_total
        invoice_nos_plain.append(inv_no)

    from models import DeliveryDiscrepancy
    exceptions_raw = DeliveryDiscrepancy.query.filter(
        DeliveryDiscrepancy.invoice_no.in_(receipt.invoice_nos)
    ).all() if receipt.invoice_nos else []
    exceptions_list = [{
        'type': exc.discrepancy_type or '',
        'item_name': exc.item_name or '',
        'qty_expected': exc.qty_expected or '',
        'qty_actual': exc.qty_actual or '',
        'note': exc.note or ''
    } for exc in exceptions_raw]

    terms = get_credit_terms(stop.customer_code) if stop else {'is_credit': False}
    is_credit = terms['is_credit']

    expected_total = Decimal(str(receipt.expected_amount or 0)) or invoice_total_sum
    collected = Decimal(str(receipt.received_amount or 0))
    payment_method = receipt.payment_method or 'not_collected'
    is_collected = collected > 0 and payment_method.lower() not in ('not_collected', 'online')

    _METHOD_LABELS = {
        'cash': 'Cash',
        'cheque': 'Cheque',
        'post_dated_cheque': 'Post-Dated Cheque',
        'pdc': 'Post-Dated Cheque',
        'card': 'Card',
        'online': 'Bank Transfer',
        'credit': 'On Account',
    }
    method_label = _METHOD_LABELS.get(payment_method.lower(), payment_method.replace('_', ' ').title())
    payments_list = []
    if collected > 0 and payment_method.lower() not in ('not_collected',):
        payments_list.append({'method': method_label, 'amount': float(collected)})

    # Use PS365 reference number as receipt_no for official receipts; fall back to internal ID
    ps365_ref = (receipt.ps365_reference_number or '').strip()
    display_receipt_no = ps365_ref if ps365_ref else str(receipt.id)

    collector_name = (receipt.driver_username or (route.driver_name if route else '') or '').strip()

    png_data = {
        'is_collected': is_collected,
        'is_credit': is_credit,
        'is_preview': False,
        'is_amended': False,
        'receipt_no': display_receipt_no,
        'date_str': receipt.created_at.strftime('%Y-%m-%d %H:%M') if receipt.created_at else '',
        'route_no': route.id if route else '',
        'stop_no': str(stop.seq_no).zfill(3) if stop and stop.seq_no else '---',
        'driver_name': route.driver_name if route else '',
        'collector_name': collector_name,
        'customer_code': stop.customer_code or '' if stop else '',
        'customer_name': stop.stop_name or '' if stop else '',
        'customer_addr': stop.stop_addr or '' if stop else '',
        'invoices': invoices_list,
        'invoice_nos_plain': invoice_nos_plain,
        'payments': payments_list,
        'total_collected': float(collected),
        'expected': expected_total,
        'collected': collected,
        'balance_due': expected_total - collected,
        'payment_method': payment_method,
        'cheque_number': receipt.cheque_number,
        'cheque_date': receipt.cheque_date.strftime('%Y-%m-%d') if receipt.cheque_date else None,
        'notes': receipt.note or '',
        'exceptions': exceptions_list,
        'doc_type': getattr(receipt, 'doc_type', None) or 'official',
        'ps365_reference_number': getattr(receipt, 'ps365_reference_number', None) or '',
    }

    w = request.args.get('w', type=int)
    if w and w in (576, 640, 832):
        png_bytes = render_receipt_png(png_data, dot_width=w)
    else:
        png_bytes = render_receipt_png(png_data)

    resp = make_response(send_file(
        BytesIO(png_bytes),
        mimetype='image/png',
        download_name=f'receipt-{receipt.id}.png',
        as_attachment=False
    ))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp

@driver_bp.route('/stops/<int:stop_id>/print_exceptions.png')
@driver_required
def print_exceptions_png(stop_id):
    """Generate exceptions proof PNG for BIXOLON thermal printing via Share API."""
    from services.receipt_render_png import render_receipt_png
    from models import DeliveryDiscrepancy
    from io import BytesIO

    stop = RouteStop.query.get_or_404(stop_id)
    route = Shipment.query.get(stop.shipment_id)

    stop_invoices = RouteStopInvoice.query.filter_by(route_stop_id=stop_id, is_active=True).all()
    invoice_nos = [rsi.invoice_no for rsi in stop_invoices]

    exceptions_raw = DeliveryDiscrepancy.query.filter(
        DeliveryDiscrepancy.invoice_no.in_(invoice_nos)
    ).all() if invoice_nos else []

    if not exceptions_raw:
        return ('', 204)

    invoices_list = []
    invoice_total_sum = Decimal('0')
    for inv_no in invoice_nos:
        inv = Invoice.query.get(inv_no)
        inv_total = Decimal(str(inv.total_grand or 0)) if inv else Decimal('0')
        invoices_list.append({'invoice_no': inv_no, 'total': float(inv_total)})
        invoice_total_sum += inv_total

    exceptions_list = [{
        'type': exc.discrepancy_type or '',
        'item_name': exc.item_name or '',
        'qty_expected': exc.qty_expected or '',
        'qty_actual': exc.qty_actual or '',
        'note': exc.note or ''
    } for exc in exceptions_raw]

    stop_no = str(stop.seq_no).zfill(3) if stop.seq_no else '---'
    date_str = utc_now().strftime('%Y-%m-%d %H:%M')

    png_data = {
        'doc_mode': 'exceptions',
        'receipt_no': f'{route.id}-{stop_no}' if route else str(stop_id),
        'date_str': date_str,
        'route_no': route.id if route else '',
        'stop_no': stop_no,
        'driver_name': route.driver_name if route else current_user.username,
        'customer_code': stop.customer_code or '',
        'customer_name': stop.stop_name or '',
        'customer_addr': stop.stop_addr or '',
        'invoices': invoices_list,
        'exceptions': exceptions_list,
    }

    w = request.args.get('w', type=int)
    png_bytes = render_receipt_png(png_data, dot_width=w if w in (576, 640, 832) else None)

    resp = make_response(send_file(
        BytesIO(png_bytes),
        mimetype='image/png',
        download_name=f'exceptions-stop{stop_id}.png',
        as_attachment=False
    ))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp


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


@driver_bp.route('/print/receipt/<int:stop_id>.pdf')
def print_receipt_pdf(stop_id):
    """Tokenized 80mm thermal PDF receipt for seamless mobile printing."""
    from utils.print_token import verify_print_token
    from utils.thermal_receipt_pdf import build_delivery_receipt_pdf
    from io import BytesIO

    token = request.args.get('token', '')
    token_data = verify_print_token(token, max_age_seconds=300)
    if not token_data or token_data.get('stop_id') != stop_id:
        abort(403, description="Invalid or expired print token")

    stop = RouteStop.query.get_or_404(stop_id)
    route = Shipment.query.get(stop.shipment_id)

    cod_receipt = CODReceipt.query.filter_by(route_stop_id=stop_id).order_by(
        CODReceipt.created_at.desc()
    ).first()

    if cod_receipt and cod_receipt.status != 'VOIDED':
        doc_type_val = (cod_receipt.doc_type or 'official').lower()
        if doc_type_val == 'official' and not cod_receipt.ps365_reference_number:
            try:
                from routes_receipts import create_receipt_core
                invoice_nos_list = cod_receipt.invoice_nos or []
                inv_list_str = ', '.join(invoice_nos_list[:5])
                if len(invoice_nos_list) > 5:
                    inv_list_str += f' +{len(invoice_nos_list)-5}'
                candidate_user = token_data.get('username') or ''
                ok, ref_num, resp_id, _, _ = create_receipt_core(
                    customer_code=stop.customer_code if stop else '',
                    amount_val=float(cod_receipt.received_amount or 0),
                    comments=inv_list_str,
                    driver_username=cod_receipt.driver_username,
                    user_code=candidate_user,
                    route_stop_id=cod_receipt.route_stop_id,
                    invoice_no=inv_list_str,
                    cheque_number=cod_receipt.cheque_number or '',
                    cheque_date=cod_receipt.cheque_date.strftime('%Y-%m-%d') if cod_receipt.cheque_date else '',
                    allow_duplicate_stop=True
                )
                cod_receipt.ps365_reference_number = ref_num
                cod_receipt.ps365_receipt_id = str(resp_id) if resp_id else None
                cod_receipt.ps365_synced_at = utc_now()
            except Exception as ps365_err:
                logging.error(f"PS365 receipt creation failed for stop {stop_id}: {ps365_err}")

        now = utc_now()
        if cod_receipt.status != 'ISSUED':
            # Resolve valid locked_by BEFORE touching the model to prevent autoflush FK violation
            candidate = token_data.get('username') or ''
            with db.session.no_autoflush:
                valid_user = User.query.filter_by(username=candidate).first() if candidate else None
                if not valid_user:
                    valid_user = User.query.filter_by(role='admin').first()
                if not valid_user:
                    valid_user = User.query.first()
            locked_by_val = valid_user.username if valid_user else (candidate or 'unknown')

            cod_receipt.status = 'ISSUED'
            cod_receipt.locked_at = now
            cod_receipt.locked_by = locked_by_val
            cod_receipt.print_count = 1
            cod_receipt.first_printed_at = now
            cod_receipt.last_printed_at = now
        else:
            cod_receipt.print_count = (cod_receipt.print_count or 0) + 1
            cod_receipt.last_printed_at = now
        db.session.commit()

    stop_invoices = RouteStopInvoice.query.filter_by(
        route_stop_id=stop_id, is_active=True
    ).all()
    invoice_nos = [rsi.invoice_no for rsi in stop_invoices]

    invoices_list = []
    invoice_total_sum = Decimal('0')
    for inv_no in invoice_nos:
        inv = Invoice.query.get(inv_no)
        inv_total = Decimal(str(inv.total_grand or 0)) if inv else Decimal('0')
        invoices_list.append({'invoice_no': inv_no, 'total': inv_total})
        invoice_total_sum += inv_total

    from models import DeliveryDiscrepancy
    exceptions_raw = DeliveryDiscrepancy.query.filter(
        DeliveryDiscrepancy.invoice_no.in_(invoice_nos)
    ).all() if invoice_nos else []
    exceptions_list = [{
        'type': exc.discrepancy_type or '',
        'item_name': exc.item_name or '',
        'qty_expected': exc.qty_expected or '',
        'qty_actual': exc.qty_actual or '',
        'note': exc.note or ''
    } for exc in exceptions_raw]

    # If no DB exceptions (delivery not yet saved), fall back to JSON passed from driver app
    if not exceptions_list:
        import json as _json
        exc_param = request.args.get('exc', '')
        if exc_param:
            try:
                passed_exc = _json.loads(exc_param)
                if isinstance(passed_exc, list):
                    exceptions_list = passed_exc
            except Exception:
                pass

    terms = get_credit_terms(stop.customer_code)
    is_credit = terms['is_credit']

    is_preview = cod_receipt is None
    is_collected = False
    collected = Decimal('0')
    payment_method = 'not_collected'
    notes = ''
    cheque_number = None
    cheque_date = None
    receipt_no = f"PREVIEW-{stop_id}"
    date_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M')
    expected_total = invoice_total_sum

    if cod_receipt:
        expected_total = Decimal(str(cod_receipt.expected_amount or 0)) or invoice_total_sum
        collected = Decimal(str(cod_receipt.received_amount or 0))
        payment_method = cod_receipt.payment_method or 'not_collected'
        is_collected = collected > 0 and payment_method.lower() not in ('not_collected', 'online')
        notes = cod_receipt.note or ''
        cheque_number = cod_receipt.cheque_number
        cheque_date = cod_receipt.cheque_date.strftime('%Y-%m-%d') if cod_receipt.cheque_date else None
        ps365_ref = (cod_receipt.ps365_reference_number or '').strip()
        receipt_no = ps365_ref if ps365_ref else str(cod_receipt.id)
        date_str = cod_receipt.created_at.strftime('%Y-%m-%d %H:%M') if cod_receipt.created_at else date_str
        is_preview = False
    else:
        form_collected = request.args.get('collected')
        form_pm = request.args.get('payment_method')
        if form_collected:
            try:
                collected = Decimal(str(form_collected))
            except Exception:
                collected = Decimal('0')
        if form_pm:
            payment_method = form_pm
        is_collected = collected > 0 and payment_method.lower() not in ('not_collected', 'online')
        cheque_number = request.args.get('cheque_number') or None
        cheque_date = request.args.get('cheque_date') or None

        if is_credit:
            notes = 'Credit account - no payment required'
        elif not is_collected:
            notes = 'Payment not yet collected - PREVIEW'
        else:
            notes = 'PREVIEW - pending delivery confirmation'

    balance_due = expected_total - collected

    stop_no = str(stop.seq_no).zfill(3) if stop.seq_no else '---'

    pdf_data = {
        'is_collected': is_collected,
        'is_credit': is_credit,
        'is_preview': is_preview,
        'is_amended': False,
        'receipt_no': receipt_no,
        'date_str': date_str,
        'route_no': route.id if route else '',
        'stop_no': stop_no,
        'driver_name': route.driver_name if route else '',
        'customer_code': stop.customer_code or '',
        'customer_name': stop.stop_name or '',
        'customer_addr': stop.stop_addr or '',
        'invoices': invoices_list,
        'expected': expected_total,
        'collected': collected,
        'balance_due': balance_due,
        'payment_method': payment_method,
        'cheque_number': cheque_number,
        'cheque_date': cheque_date,
        'notes': notes,
        'exceptions': exceptions_list,
        'doc_type': getattr(cod_receipt, 'doc_type', None) or 'official' if cod_receipt else 'official',
        'ps365_reference_number': getattr(cod_receipt, 'ps365_reference_number', None) or '' if cod_receipt else '',
    }

    pdf_bytes = build_delivery_receipt_pdf(pdf_data)

    return send_file(
        BytesIO(pdf_bytes),
        mimetype='application/pdf',
        download_name=f'receipt-{receipt_no}.pdf',
        as_attachment=False
    ), 200, {
        'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
        'Pragma': 'no-cache',
        'Expires': '0',
        'X-Content-Type-Options': 'nosniff'
    }


@driver_bp.route('/api/stops/<int:stop_id>/print_token')
@driver_required
def get_print_token(stop_id):
    """Generate a short-lived print token for PDF receipt."""
    from utils.print_token import make_print_token

    stop = RouteStop.query.get_or_404(stop_id)
    route = Shipment.query.get(stop.shipment_id)
    if route.driver_name != current_user.username and current_user.role != 'admin':
        abort(403)

    token = make_print_token(stop_id, current_user.username)
    extra_params = {}
    for key in ('collected', 'payment_method', 'cheque_number', 'cheque_date', 'doc', 'exc'):
        val = request.args.get(key)
        if val:
            extra_params[key] = val
    fmt = request.args.get('fmt', 'png')
    if fmt == 'pdf':
        print_url = url_for('driver.print_receipt_pdf', stop_id=stop_id, token=token, **extra_params)
    else:
        print_url = url_for('driver.print_receipt_png', stop_id=stop_id, token=token, **extra_params)
    return jsonify({'print_url': print_url, 'token': token})


@driver_bp.route('/print/receipt/<int:stop_id>.png')
def print_receipt_png(stop_id):
    """Tokenized 576px-wide PNG receipt for BIXOLON thermal printing via Share API."""
    from utils.print_token import verify_print_token
    from services.receipt_render_png import render_receipt_png
    from io import BytesIO

    token = request.args.get('token', '')
    token_data = verify_print_token(token, max_age_seconds=300)
    if not token_data or token_data.get('stop_id') != stop_id:
        abort(403, description="Invalid or expired print token")

    stop = RouteStop.query.get_or_404(stop_id)
    route = Shipment.query.get(stop.shipment_id)

    cod_receipt = CODReceipt.query.filter_by(route_stop_id=stop_id).order_by(
        CODReceipt.created_at.desc()
    ).first()

    if cod_receipt and cod_receipt.status != 'VOIDED':
        doc_type_val = (cod_receipt.doc_type or 'official').lower()
        if doc_type_val == 'official' and not cod_receipt.ps365_reference_number:
            try:
                from routes_receipts import create_receipt_core
                invoice_nos_list = cod_receipt.invoice_nos or []
                inv_list_str = ', '.join(invoice_nos_list[:5])
                if len(invoice_nos_list) > 5:
                    inv_list_str += f' +{len(invoice_nos_list)-5}'
                candidate_user = token_data.get('username') or ''
                ok, ref_num, resp_id, _, _ = create_receipt_core(
                    customer_code=stop.customer_code if stop else '',
                    amount_val=float(cod_receipt.received_amount or 0),
                    comments=inv_list_str,
                    driver_username=cod_receipt.driver_username,
                    user_code=candidate_user,
                    route_stop_id=cod_receipt.route_stop_id,
                    invoice_no=inv_list_str,
                    cheque_number=cod_receipt.cheque_number or '',
                    cheque_date=cod_receipt.cheque_date.strftime('%Y-%m-%d') if cod_receipt.cheque_date else '',
                    allow_duplicate_stop=True
                )
                cod_receipt.ps365_reference_number = ref_num
                cod_receipt.ps365_receipt_id = str(resp_id) if resp_id else None
                cod_receipt.ps365_synced_at = utc_now()
            except Exception as ps365_err:
                logging.error(f"PS365 receipt creation failed for stop {stop_id}: {ps365_err}")

        now = utc_now()
        if cod_receipt.status != 'ISSUED':
            # Resolve valid locked_by BEFORE touching the model to prevent autoflush FK violation
            candidate = token_data.get('username') or ''
            with db.session.no_autoflush:
                valid_user = User.query.filter_by(username=candidate).first() if candidate else None
                if not valid_user:
                    valid_user = User.query.filter_by(role='admin').first()
                if not valid_user:
                    valid_user = User.query.first()
            locked_by_val = valid_user.username if valid_user else (candidate or 'unknown')

            cod_receipt.status = 'ISSUED'
            cod_receipt.locked_at = now
            cod_receipt.locked_by = locked_by_val
            cod_receipt.print_count = 1
            cod_receipt.first_printed_at = now
            cod_receipt.last_printed_at = now
        else:
            cod_receipt.print_count = (cod_receipt.print_count or 0) + 1
            cod_receipt.last_printed_at = now
        db.session.commit()

    stop_invoices = RouteStopInvoice.query.filter_by(
        route_stop_id=stop_id, is_active=True
    ).all()
    invoice_nos = [rsi.invoice_no for rsi in stop_invoices]

    invoices_list = []
    invoice_total_sum = Decimal('0')
    for inv_no in invoice_nos:
        inv = Invoice.query.get(inv_no)
        inv_total = Decimal(str(inv.total_grand or 0)) if inv else Decimal('0')
        invoices_list.append({'invoice_no': inv_no, 'total': inv_total})
        invoice_total_sum += inv_total

    from models import DeliveryDiscrepancy
    exceptions_raw = DeliveryDiscrepancy.query.filter(
        DeliveryDiscrepancy.invoice_no.in_(invoice_nos)
    ).all() if invoice_nos else []
    exceptions_list = [{
        'type': exc.discrepancy_type or '',
        'item_name': exc.item_name or '',
        'qty_expected': exc.qty_expected or '',
        'qty_actual': exc.qty_actual or '',
        'note': exc.note or ''
    } for exc in exceptions_raw]

    # If no DB exceptions (delivery not yet saved), fall back to JSON passed from driver app
    if not exceptions_list:
        import json as _json
        exc_param = request.args.get('exc', '')
        if exc_param:
            try:
                passed_exc = _json.loads(exc_param)
                if isinstance(passed_exc, list):
                    exceptions_list = passed_exc
            except Exception:
                pass

    terms = get_credit_terms(stop.customer_code)
    is_credit = terms['is_credit']

    is_preview = cod_receipt is None
    is_collected = False
    collected = Decimal('0')
    payment_method = 'not_collected'
    notes = ''
    cheque_number = None
    cheque_date = None
    receipt_no = f"PREVIEW-{stop_id}"
    date_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M')
    expected_total = invoice_total_sum

    if cod_receipt:
        expected_total = Decimal(str(cod_receipt.expected_amount or 0)) or invoice_total_sum
        collected = Decimal(str(cod_receipt.received_amount or 0))
        payment_method = cod_receipt.payment_method or 'not_collected'
        is_collected = collected > 0 and payment_method.lower() not in ('not_collected', 'online')
        notes = cod_receipt.note or ''
        cheque_number = cod_receipt.cheque_number
        cheque_date = cod_receipt.cheque_date.strftime('%Y-%m-%d') if cod_receipt.cheque_date else None
        ps365_ref = (cod_receipt.ps365_reference_number or '').strip()
        receipt_no = ps365_ref if ps365_ref else str(cod_receipt.id)
        date_str = cod_receipt.created_at.strftime('%Y-%m-%d %H:%M') if cod_receipt.created_at else date_str
        is_preview = False
    else:
        form_collected = request.args.get('collected')
        form_pm = request.args.get('payment_method')
        if form_collected:
            try:
                collected = Decimal(str(form_collected))
            except Exception:
                collected = Decimal('0')
        if form_pm:
            payment_method = form_pm
        is_collected = collected > 0 and payment_method.lower() not in ('not_collected', 'online')
        cheque_number = request.args.get('cheque_number') or None
        cheque_date = request.args.get('cheque_date') or None

        if is_credit:
            notes = 'Credit account - no payment required'
        elif not is_collected:
            notes = 'Payment not yet collected - PREVIEW'
        else:
            notes = 'PREVIEW - pending delivery confirmation'

    balance_due = expected_total - collected
    stop_no = str(stop.seq_no).zfill(3) if stop.seq_no else '---'

    png_data = {
        'is_collected': is_collected,
        'is_credit': is_credit,
        'is_preview': is_preview,
        'is_amended': False,
        'receipt_no': receipt_no,
        'date_str': date_str,
        'route_no': route.id if route else '',
        'stop_no': stop_no,
        'driver_name': route.driver_name if route else '',
        'customer_code': stop.customer_code or '',
        'customer_name': stop.stop_name or '',
        'customer_addr': stop.stop_addr or '',
        'invoices': invoices_list,
        'expected': expected_total,
        'collected': collected,
        'balance_due': balance_due,
        'payment_method': payment_method,
        'cheque_number': cheque_number,
        'cheque_date': cheque_date,
        'notes': notes,
        'exceptions': exceptions_list,
        'doc_type': getattr(cod_receipt, 'doc_type', None) or 'official' if cod_receipt else 'official',
        'ps365_reference_number': getattr(cod_receipt, 'ps365_reference_number', None) or '' if cod_receipt else '',
        'doc_mode': (request.args.get('doc') or '').strip().lower(),
    }

    w = request.args.get('w', type=int)
    if w and w in (576, 640, 832):
        png_bytes = render_receipt_png(png_data, dot_width=w)
    else:
        png_bytes = render_receipt_png(png_data)

    resp = make_response(send_file(
        BytesIO(png_bytes),
        mimetype='image/png',
        download_name=f'receipt-{receipt_no}.png',
        as_attachment=False
    ))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp


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
        'cleared_at': route.settlement_cleared_at,
        'cleared_by': route.settlement_cleared_by
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
        variance_note = data.get('variance_note', '').strip()
        
        route = Shipment.query.get_or_404(route_id)
        
        # Check permissions
        if route.driver_name != current_user.username and current_user.role != 'admin':
            abort(403)
        
        # Check if already submitted
        if route.driver_submitted_at:
            return jsonify({'error': 'Settlement already submitted'}), 400
        
        # Compute totals from CODReceipt (authoritative source)
        cod_receipts = CODReceipt.query.filter_by(route_id=route_id).all()
        
        def is_cashlike(m):
            m = (m or "cash").lower()
            return m in ("cash", "cheque")
        
        expected_cash = sum(Decimal(str(r.expected_amount or 0)) for r in cod_receipts if is_cashlike(r.payment_method))
        collected_cash = sum(Decimal(str(r.received_amount or 0)) for r in cod_receipts if is_cashlike(r.payment_method))
        
        # Store snapshots on the route at submission time
        route.cash_expected = expected_cash
        route.cash_collected = collected_cash
        
        # Variance is handed_in vs collected (not expected)
        variance = amount - collected_cash
        
        # Require variance note if variance != 0
        if variance != 0 and not variance_note:
            return jsonify({'error': 'Variance note is required when handed-in amount differs from collected COD totals.'}), 400
        
        # Update route
        route.driver_submitted_at = utc_now()
        route.cash_handed_in = amount
        route.cash_variance = variance
        route.cash_variance_note = variance_note if variance != 0 else None
        route.settlement_notes = notes
        route.settlement_status = 'DRIVER_SUBMITTED'
        
        # Create event
        create_delivery_event(
            route_id=route.id,
            event_type='settlement_submitted',
            payload={'amount': str(amount), 'notes': notes, 'expected': str(expected_cash), 'collected': str(collected_cash), 'variance': str(variance)}
        )
        
        db.session.commit()
        
        return jsonify({'success': True})
    
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error submitting settlement for route {route_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500

@driver_bp.route('/routes/<int:route_id>/settlement/clear', methods=['POST'])
@driver_required
def clear_settlement(route_id):
    """Admin clears settlement (does NOT change route status or completed_at)"""
    try:
        # Only admin can clear
        if current_user.role != 'admin':
            abort(403)
        
        route = Shipment.query.get_or_404(route_id)
        
        # Route must be COMPLETED before clearing settlement
        if route.status != 'COMPLETED':
            return jsonify({'error': 'Cannot clear settlement: route is not COMPLETED yet.'}), 400
        
        # Check if submitted
        if not route.driver_submitted_at:
            return jsonify({'error': 'Settlement not submitted yet'}), 400
        
        # Check if already cleared
        if route.settlement_status == 'SETTLED':
            return jsonify({'error': 'Settlement already cleared'}), 400
        
        # Update settlement status only - do NOT change route.status or completed_at
        route.settlement_status = 'SETTLED'
        route.settlement_cleared_at = utc_now()
        route.settlement_cleared_by = current_user.username
        
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


# --- Return Handover Workflow ---

@driver_bp.route('/routes/<int:route_id>/returns')
@driver_required
def returns_screen(route_id):
    """Driver returns screen for confirming handover of FAILED invoices"""
    route = Shipment.query.get_or_404(route_id)
    
    if route.driver_name != current_user.username and current_user.role != 'admin':
        flash('This route is not assigned to you.', 'error')
        return redirect(url_for('driver.my_routes'))
    
    failed_invoices = db.session.query(
        RouteStopInvoice.invoice_no,
        RouteStop.seq_no,
        RouteStop.stop_name,
        RouteStop.customer_code,
        Invoice.customer_name,
        Invoice.total_grand,
        RouteReturnHandover.id.label('handover_id'),
        RouteReturnHandover.driver_confirmed_at,
        RouteReturnHandover.packages_count,
        RouteReturnHandover.notes.label('handover_notes')
    ).join(
        RouteStop, RouteStop.route_stop_id == RouteStopInvoice.route_stop_id
    ).join(
        Invoice, Invoice.invoice_no == RouteStopInvoice.invoice_no
    ).outerjoin(
        RouteReturnHandover,
        db.and_(
            RouteReturnHandover.route_id == route_id,
            RouteReturnHandover.invoice_no == RouteStopInvoice.invoice_no
        )
    ).filter(
        RouteStop.shipment_id == route_id,
        db.or_(RouteStop.deleted_at == None, RouteStop.delivered_at.isnot(None), RouteStop.failed_at.isnot(None)),
        db.or_(RouteStopInvoice.is_active == True, db.func.lower(RouteStopInvoice.status).in_(['delivered', 'delivery_failed', 'returned_to_warehouse'])),
        db.func.lower(RouteStopInvoice.status).in_(['delivery_failed', 'returned_to_warehouse', 'failed'])
    ).order_by(RouteStop.seq_no).all()
    
    invoices_data = []
    for inv in failed_invoices:
        invoices_data.append({
            'invoice_no': inv.invoice_no,
            'stop_seq': inv.seq_no,
            'stop_name': inv.stop_name,
            'customer_code': inv.customer_code,
            'customer_name': inv.customer_name,
            'total': float(inv.total_grand) if inv.total_grand else 0,
            'handover_id': inv.handover_id,
            'confirmed': inv.driver_confirmed_at is not None,
            'confirmed_at': get_local_time(inv.driver_confirmed_at) if inv.driver_confirmed_at else None,
            'packages_count': inv.packages_count,
            'notes': inv.handover_notes
        })
    
    confirmed_count = sum(1 for inv in invoices_data if inv['confirmed'])
    
    return render_template(
        'driver/returns.html',
        route=route,
        invoices=invoices_data,
        confirmed_count=confirmed_count,
        total_count=len(invoices_data)
    )


@driver_bp.route('/routes/<int:route_id>/returns/confirm', methods=['POST'])
@driver_required
def confirm_return_handover(route_id):
    """Confirm return handover for a FAILED invoice"""
    route = Shipment.query.get_or_404(route_id)
    
    if route.driver_username != current_user.username and current_user.role != 'admin':
        return jsonify({'error': 'Not authorized'}), 403
    
    data = request.get_json()
    invoice_no = data.get('invoice_no')
    packages_count = data.get('packages_count', 1)
    notes = data.get('notes', '')
    
    if not invoice_no:
        return jsonify({'error': 'Invoice number required'}), 400
    
    rsi = RouteStopInvoice.query.join(RouteStop).filter(
        RouteStop.shipment_id == route_id,
        RouteStopInvoice.invoice_no == invoice_no,
        RouteStopInvoice.is_active == True
    ).first()
    
    if not rsi:
        return jsonify({'error': 'Invoice not found on this route'}), 404
    
    if rsi.status and rsi.status.lower() not in ('delivery_failed', 'returned_to_warehouse', 'failed'):
        return jsonify({'error': 'Only FAILED invoices can be returned'}), 400
    
    existing = RouteReturnHandover.query.filter_by(
        route_id=route_id,
        invoice_no=invoice_no
    ).first()
    
    now = utc_now_for_db()
    
    if existing:
        existing.driver_confirmed_at = now
        existing.driver_username = current_user.username
        existing.packages_count = packages_count
        existing.notes = notes
    else:
        handover = RouteReturnHandover(
            route_id=route_id,
            route_stop_id=rsi.route_stop_id,
            invoice_no=invoice_no,
            driver_confirmed_at=now,
            driver_username=current_user.username,
            packages_count=packages_count,
            notes=notes
        )
        db.session.add(handover)
    
    create_delivery_event(
        route_id=route_id,
        event_type='RETURN_HANDOVER_SUBMITTED',
        payload={'invoice_no': invoice_no, 'packages_count': packages_count},
        stop_id=rsi.route_stop_id
    )
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'Return handover confirmed for {invoice_no}'
    })


@driver_bp.route('/routes/<int:route_id>/returns/confirm-all', methods=['POST'])
@driver_required
def confirm_all_returns(route_id):
    """Confirm return handover for all FAILED invoices at once"""
    route = Shipment.query.get_or_404(route_id)
    
    if route.driver_username != current_user.username and current_user.role != 'admin':
        return jsonify({'error': 'Not authorized'}), 403
    
    failed_invoices = db.session.query(
        RouteStopInvoice.invoice_no,
        RouteStopInvoice.route_stop_id
    ).join(RouteStop).filter(
        RouteStop.shipment_id == route_id,
        db.or_(RouteStop.deleted_at == None, RouteStop.delivered_at.isnot(None), RouteStop.failed_at.isnot(None)),
        db.or_(RouteStopInvoice.is_active == True, db.func.lower(RouteStopInvoice.status).in_(['delivered', 'delivery_failed', 'returned_to_warehouse'])),
        db.func.lower(RouteStopInvoice.status).in_(['delivery_failed', 'returned_to_warehouse', 'failed'])
    ).all()
    
    now = utc_now_for_db()
    confirmed_count = 0
    
    for inv in failed_invoices:
        existing = RouteReturnHandover.query.filter_by(
            route_id=route_id,
            invoice_no=inv.invoice_no
        ).first()
        
        if existing and existing.driver_confirmed_at:
            continue
        
        if existing:
            existing.driver_confirmed_at = now
            existing.driver_username = current_user.username
            existing.packages_count = 1
        else:
            handover = RouteReturnHandover(
                route_id=route_id,
                route_stop_id=inv.route_stop_id,
                invoice_no=inv.invoice_no,
                driver_confirmed_at=now,
                driver_username=current_user.username,
                packages_count=1
            )
            db.session.add(handover)
        
        confirmed_count += 1
    
    if confirmed_count > 0:
        create_delivery_event(
            route_id=route_id,
            event_type='RETURN_HANDOVER_SUBMITTED',
            payload={'bulk_confirm': True, 'count': confirmed_count}
        )
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'Confirmed {confirmed_count} return(s)'
    })


@driver_bp.route('/api/receipts/<int:receipt_id>/correction-request', methods=['POST'])
@driver_required
def request_correction(receipt_id):
    """Driver requests correction on an ISSUED receipt (admin must void & reissue)"""
    try:
        receipt = CODReceipt.query.get_or_404(receipt_id)
        route = Shipment.query.get(receipt.route_id)

        if route.driver_name != current_user.username and current_user.role != 'admin':
            abort(403)

        if receipt.status == 'VOIDED':
            return jsonify({'error': 'Receipt is already voided'}), 400

        data = request.get_json(force=True)
        reason = data.get('reason', '').strip()
        if not reason:
            return jsonify({'error': 'A reason is required for the correction request'}), 400

        create_delivery_event(
            route_id=receipt.route_id,
            event_type='RECEIPT_CORRECTION_REQUESTED',
            payload={
                'receipt_id': receipt.id,
                'reason': reason,
                'requested_by': current_user.username
            },
            stop_id=receipt.route_stop_id
        )

        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Correction request submitted. An admin will review and reissue.'
        })
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error requesting correction for receipt {receipt_id}: {e}")
        return jsonify({'error': str(e)}), 500
