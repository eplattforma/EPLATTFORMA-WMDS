"""
Flask blueprint for route and stop management
"""
from flask import Blueprint, request, render_template, redirect, url_for, flash, jsonify, abort
from flask_login import login_required, current_user
from datetime import datetime
from functools import wraps
from models import Shipment, RouteStop, RouteStopInvoice, Invoice, User
from services_route_lifecycle import recompute_route_completion
from timezone_utils import utc_now_for_db, get_local_time
import services
import services_routing
import ps365_service

bp = Blueprint("routes", __name__)

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role not in ['admin', 'warehouse_manager']:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


@bp.route("/dashboard")
@login_required
def dashboard():
    """Display routes dashboard with three sections: In Progress, Pending Reconciliation, Archived"""
    from models import User
    from app import db
    
    date_str = request.args.get("date")
    view_mode = request.args.get("view", "active")  # active, pending, archived
    
    # Get all drivers for route creation dropdown
    drivers = User.query.filter_by(role='driver').all()
    
    # Build base query based on user role
    if current_user.role == 'driver':
        base_query = Shipment.query.filter(
            Shipment.driver_name == current_user.username,
            Shipment.deleted_at.is_(None)
        )
    else:
        base_query = Shipment.query.filter(Shipment.deleted_at.is_(None))
    
    # Apply date filter if provided
    if date_str:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
        base_query = base_query.filter(Shipment.delivery_date == day)
    else:
        day = None
    
    # Get counts for each section
    in_progress_count = base_query.filter(
        Shipment.is_archived == False,
        Shipment.status.in_(['PLANNED', 'DISPATCHED', 'IN_TRANSIT', 'created'])
    ).count()
    
    pending_count = base_query.filter(
        Shipment.is_archived == False,
        Shipment.status == 'COMPLETED',
        Shipment.reconciliation_status != 'RECONCILED'
    ).count()
    
    archived_count = base_query.filter(Shipment.is_archived == True).count()
    
    # Get routes based on view mode
    if view_mode == "pending":
        routes = base_query.filter(
            Shipment.is_archived == False,
            Shipment.status == 'COMPLETED',
            Shipment.reconciliation_status != 'RECONCILED'
        ).order_by(Shipment.completed_at.desc()).all()
    elif view_mode == "archived":
        # For archived view, only load results when search is performed
        search_route_id = request.args.get("route_id", "").strip()
        search_driver = request.args.get("driver", "").strip()
        search_date_from = request.args.get("date_from", "").strip()
        search_date_to = request.args.get("date_to", "").strip()
        
        archived_query = base_query.filter(Shipment.is_archived == True)
        
        # Check if any search filter is applied
        has_search = any([search_route_id, search_driver, search_date_from, search_date_to, date_str])
        
        if has_search:
            if search_route_id:
                try:
                    archived_query = archived_query.filter(Shipment.id == int(search_route_id))
                except ValueError:
                    pass
            if search_driver:
                archived_query = archived_query.filter(Shipment.driver_name.ilike(f"%{search_driver}%"))
            if search_date_from:
                try:
                    from_date = datetime.strptime(search_date_from, "%Y-%m-%d").date()
                    archived_query = archived_query.filter(Shipment.delivery_date >= from_date)
                except ValueError:
                    pass
            if search_date_to:
                try:
                    to_date = datetime.strptime(search_date_to, "%Y-%m-%d").date()
                    archived_query = archived_query.filter(Shipment.delivery_date <= to_date)
                except ValueError:
                    pass
            routes = archived_query.order_by(Shipment.archived_at.desc()).limit(50).all()
        else:
            routes = []  # Don't load any routes until search is performed
    else:
        # Default: active/in-progress
        routes = base_query.filter(
            Shipment.is_archived == False,
            Shipment.status.in_(['PLANNED', 'DISPATCHED', 'IN_TRANSIT', 'created'])
        ).order_by(Shipment.delivery_date.desc(), Shipment.driver_name).all()
    
    # Calculate progress for each route
    cards = []
    for route in routes:
        prog = services.route_progress(route.id)
        
        # For pending routes, calculate cash from COD receipts
        if view_mode == "pending":
            from models import CODReceipt
            cod_receipts = CODReceipt.query.filter_by(route_id=route.id).all()
            route.cash_expected = sum(float(r.expected_amount or 0) for r in cod_receipts)
            route.cash_handed_in = sum(float(r.received_amount or 0) for r in cod_receipts)
            route.cash_variance = route.cash_handed_in - route.cash_expected
        
        cards.append((route, prog))
    
    return render_template("routes_dashboard.html", 
                          day=day, 
                          cards=cards, 
                          drivers=drivers,
                          view_mode=view_mode,
                          in_progress_count=in_progress_count,
                          pending_count=pending_count,
                          archived_count=archived_count)


@bp.route("/upsert", methods=["POST"])
@login_required
@admin_required
def upsert():
    """Create or update a route"""
    data = request.form
    route_id = data.get("route_id")  # If provided, update existing route
    new_status = data.get("status", "PLANNED")
    
    # Validate status transitions
    if route_id:
        # Updating existing route - validate transition
        existing_route = Shipment.query.get_or_404(int(route_id))
        old_status = existing_route.status
        
        # Validate status transition
        valid_transitions = {
            'PLANNED': ['DISPATCHED', 'CANCELLED'],
            'DISPATCHED': ['IN_TRANSIT', 'CANCELLED'],
            'IN_TRANSIT': ['COMPLETED', 'CANCELLED'],
            'COMPLETED': [],  # Terminal state
            'CANCELLED': []   # Terminal state
        }
        
        if new_status != old_status:
            allowed = valid_transitions.get(old_status, [])
            if new_status not in allowed and new_status != 'CANCELLED':
                flash(f"Invalid status transition: Cannot change from {old_status} to {new_status}. Allowed: {', '.join(allowed) if allowed else 'none'}", "danger")
                return redirect(url_for("routes.detail", shipment_id=route_id))
        
        # Validate DISPATCHED requires all invoices ready
        if new_status == 'DISPATCHED':
            invoices = Invoice.query.filter_by(route_id=int(route_id)).all()
            if not invoices:
                flash("Cannot mark as DISPATCHED: Route has no invoices", "danger")
                return redirect(url_for("routes.detail", shipment_id=route_id))
            
            not_ready = [inv for inv in invoices if inv.status != 'ready_for_dispatch']
            if not_ready:
                flash(f"Cannot mark as DISPATCHED: {len(not_ready)} invoice(s) are not ready for dispatch. All invoices must be 'ready_for_dispatch'.", "danger")
                return redirect(url_for("routes.detail", shipment_id=route_id))
            
            # Validation: DISPATCHED requires all invoices to be synced from PS365
            not_synced = [inv for inv in invoices if inv.ps365_synced_at is None]
            if not_synced:
                invoice_list = ', '.join([inv.invoice_no for inv in not_synced[:5]])  # Show first 5
                if len(not_synced) > 5:
                    invoice_list += f" and {len(not_synced) - 5} more"
                flash(f"Cannot mark as DISPATCHED: {len(not_synced)} invoice(s) have not been synced from PS365 to get total amounts. Please sync these invoices first: {invoice_list}", "danger")
                return redirect(url_for("routes.detail", shipment_id=route_id))
    else:
        # Creating new route - only allow PLANNED or CANCELLED status
        if new_status not in ['PLANNED', 'CANCELLED']:
            flash(f"New routes can only be created with status PLANNED or CANCELLED. Cannot create with status {new_status}.", "danger")
            return redirect(url_for("routes.index"))
    
    route = services.upsert_route(
        driver_name=data["driver_name"],
        route_name=data.get("route_name") or "",
        delivery_date=datetime.strptime(data["delivery_date"], "%Y-%m-%d").date(),
        status=new_status,
        route_id=int(route_id) if route_id else None
    )
    
    action = "updated" if route_id else "created"
    flash(f"Route {action} for {route.driver_name} on {route.delivery_date} (ID: {route.id})", "success")
    return redirect(url_for("routes.detail", shipment_id=route.id))


@bp.route("/<int:shipment_id>")
@login_required
def detail(shipment_id):
    """Display route details with all stops"""
    from models import PSCustomer, CreditTerms
    from app import db
    
    route = Shipment.query.get_or_404(shipment_id)
    
    # If user is a driver, verify they own this route
    if current_user.role == 'driver' and route.driver_name != current_user.username:
        abort(403)
    
    # Get progress
    progress = services.route_progress(shipment_id)
    
    # Get stops with their invoices and payment terms for this route
    stops_query = db.session.query(
        RouteStop, PSCustomer.website, CreditTerms
    ).outerjoin(
        PSCustomer, RouteStop.customer_code == PSCustomer.customer_code_365
    ).outerjoin(
        CreditTerms, db.and_(
            CreditTerms.customer_code == RouteStop.customer_code,
            CreditTerms.valid_to.is_(None)  # Get only active terms
        )
    ).filter(
        RouteStop.shipment_id == shipment_id
    ).order_by(RouteStop.seq_no).all()
    
    # Build enhanced stops list with payment terms and website
    stops = []
    for stop, website, payment_terms in stops_query:
        # Enhance the stop object with additional attributes
        stop.website = website
        stop.payment_terms = payment_terms
        stops.append(stop)
    
    # Build stop groups with invoices
    from models import ReceiptLog
    stop_groups = []
    for stop, website, payment_terms in stops_query:
        # Get all invoices for this stop
        invoices_for_stop = db.session.query(Invoice).join(
            RouteStopInvoice, Invoice.invoice_no == RouteStopInvoice.invoice_no
        ).filter(
            RouteStopInvoice.route_stop_id == stop.route_stop_id
        ).all()
        
        # Check if receipt exists for this stop
        receipt = ReceiptLog.query.filter_by(route_stop_id=stop.route_stop_id).first()
        
        stop_group = {
            'route_stop_id': stop.route_stop_id,
            'seq_no': stop.seq_no,
            'customer_name': stop.stop_name or stop.customer_code,
            'website': website,
            'payment_terms': payment_terms,
            'invoices': [],
            'total_items': 0,
            'total_weight': 0,
            'has_receipt': receipt is not None,
            'receipt_reference': receipt.reference_number if receipt else None
        }
        
        for invoice in invoices_for_stop:
            stop_group['invoices'].append({
                'invoice_no': invoice.invoice_no,
                'status': invoice.status,
                'total_items': invoice.total_items,
                'total_weight': invoice.total_weight
            })
            # Calculate totals
            stop_group['total_items'] += (invoice.total_items or 0)
            stop_group['total_weight'] += (invoice.total_weight or 0)
        
        stop_groups.append(stop_group)
    
    # Also get orders without stops (for backwards compatibility)
    orders = []
    orders_query = db.session.query(
        Invoice, PSCustomer.website, RouteStop.seq_no
    ).outerjoin(
        PSCustomer, Invoice.customer_name == PSCustomer.company_name
    ).outerjoin(
        RouteStopInvoice, Invoice.invoice_no == RouteStopInvoice.invoice_no
    ).outerjoin(
        RouteStop, RouteStopInvoice.route_stop_id == RouteStop.route_stop_id
    ).filter(Invoice.route_id == shipment_id).order_by(RouteStop.seq_no).all()
    
    for invoice, website, seq_no in orders_query:
        order_dict = {
            'invoice_no': invoice.invoice_no,
            'customer_name': invoice.customer_name,
            'status': invoice.status,
            'total_items': invoice.total_items,
            'total_weight': invoice.total_weight,
            'website': website,
            'seq_no': seq_no
        }
        orders.append(order_dict)
    
    # Check if all invoices are ready for dispatch (for showing "Mark as Shipped" button)
    all_invoices = Invoice.query.filter_by(route_id=shipment_id).all()
    all_ready_for_dispatch = len(all_invoices) > 0 and all(inv.status == 'ready_for_dispatch' for inv in all_invoices)
    
    # Debug logging
    import logging
    logging.debug(f"Route {shipment_id}: {len(all_invoices)} invoices, all_ready={all_ready_for_dispatch}, statuses={[inv.status for inv in all_invoices]}")
    
    # Compute KPIs for the new UI
    # "Picked" means warehouse work is complete (ready_for_dispatch or beyond)
    picked_statuses = ['ready_for_dispatch', 'SHIPPED', 'OUT_FOR_DELIVERY', 'DELIVERED', 'delivered']
    kpis = {
        'stops_total': len(stops),
        'invoices_total': len(all_invoices),
        'picked_count': sum(1 for inv in all_invoices if inv.status in picked_statuses),
        'ready_count': sum(1 for inv in all_invoices if inv.status == 'ready_for_dispatch'),
        'total_due': sum(float(inv.total_grand or 0) for inv in all_invoices)
    }
    
    # Compute dispatch blockers (issues preventing dispatch) - only for PLANNED routes
    dispatch_blockers = []
    blocked_stop_ids = set()
    
    if route.status == 'PLANNED':
        # Only check blockers for routes that haven't been dispatched yet
        # Problem statuses that block dispatch
        blocking_statuses = ['not_started', 'picking', 'awaiting_packing', 'awaiting_batch_items']
        
        not_ready_invoices = [inv for inv in all_invoices if inv.status in blocking_statuses]
        if not_ready_invoices:
            # Find which stops have these invoices
            for stop in stops:
                for rsi in stop.invoices:
                    if rsi.invoice.status in blocking_statuses:
                        blocked_stop_ids.add(stop.route_stop_id)
            
            not_picked = [inv for inv in not_ready_invoices if inv.status in ['not_started', 'picking']]
            awaiting = [inv for inv in not_ready_invoices if inv.status in ['awaiting_packing', 'awaiting_batch_items']]
            
            if not_picked:
                dispatch_blockers.append({
                    'type': 'not_picked',
                    'message': f"{len(not_picked)} invoice(s) not picked yet",
                    'count': len(not_picked)
                })
            if awaiting:
                dispatch_blockers.append({
                    'type': 'awaiting_packing',
                    'message': f"{len(awaiting)} invoice(s) awaiting packing",
                    'count': len(awaiting)
                })
        
        # Check for invoices not synced from PS365
        not_synced = [inv for inv in all_invoices if inv.ps365_synced_at is None]
        if not_synced:
            dispatch_blockers.append({
                'type': 'not_synced',
                'message': f"{len(not_synced)} invoice(s) not synced from PS365",
                'count': len(not_synced)
            })
    
    # Handle ?show=issues filter - only applies to PLANNED routes
    show_filter = request.args.get('show', '')
    if show_filter == 'issues' and blocked_stop_ids and route.status == 'PLANNED':
        # Filter stops to only those with issues
        stops = [s for s in stops if s.route_stop_id in blocked_stop_ids]
    
    # Get all drivers for edit route modal
    drivers = User.query.filter_by(role='driver').all() if current_user.role in ['admin', 'warehouse_manager'] else []
    
    # Use driver-specific template for drivers
    if current_user.role == 'driver':
        return render_template("driver_route_detail.html", route=route, stops=stops, progress=progress, orders=orders, stop_groups=stop_groups)
    else:
        return render_template("route_detail.html", 
                               route=route, 
                               stops=stops, 
                               progress=progress, 
                               orders=orders, 
                               all_ready_for_dispatch=all_ready_for_dispatch, 
                               drivers=drivers,
                               kpis=kpis,
                               dispatch_blockers=dispatch_blockers,
                               show_filter=show_filter,
                               blocked_stop_ids=blocked_stop_ids)


@bp.route("/<int:shipment_id>/stops/new", methods=["GET", "POST"])
@login_required
@admin_required
def new_stop(shipment_id):
    """Create a new stop in the route"""
    route = Shipment.query.get_or_404(shipment_id)
    
    if request.method == "POST":
        from decimal import Decimal
        seq_no = Decimal(str(request.form.get("seq_no") or services.get_next_seq_no(shipment_id)))
        
        stop = services.create_stop(
            shipment_id,
            seq_no,
            stop_name=request.form.get("stop_name"),
            stop_addr=request.form.get("stop_addr"),
            stop_city=request.form.get("stop_city"),
            stop_postcode=request.form.get("stop_postcode"),
            notes=request.form.get("notes")
        )
        
        # Attach invoices if provided
        invoice_nos = request.form.get("invoice_nos", "").strip()
        if invoice_nos:
            invoice_list = [inv.strip() for inv in invoice_nos.split(",") if inv.strip()]
            services.attach_invoices_to_stop(stop.route_stop_id, invoice_list)
        
        flash(f"Stop #{stop.seq_no} created successfully", "success")
        return redirect(url_for("routes.detail", shipment_id=shipment_id))
    
    # GET: show form
    next_seq = services.get_next_seq_no(shipment_id)
    return render_template("stop_form.html", route=route, seq_no=next_seq, stop=None)


@bp.route("/stops/<int:route_stop_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_stop(route_stop_id):
    """Edit an existing stop"""
    stop = RouteStop.query.get_or_404(route_stop_id)
    route = stop.shipment
    
    if request.method == "POST":
        from decimal import Decimal
        stop.seq_no = Decimal(str(request.form.get("seq_no", stop.seq_no)))
        stop.stop_name = request.form.get("stop_name")
        stop.stop_addr = request.form.get("stop_addr")
        stop.stop_city = request.form.get("stop_city")
        stop.stop_postcode = request.form.get("stop_postcode")
        stop.notes = request.form.get("notes")
        
        from app import db
        db.session.commit()
        
        flash(f"Stop #{stop.seq_no} updated successfully", "success")
        return redirect(url_for("routes.detail", shipment_id=route.id))
    
    # GET: show form
    return render_template("stop_form.html", route=route, seq_no=stop.seq_no, stop=stop)


@bp.route("/api/update-stop-sequence", methods=["POST"])
@login_required
@admin_required
def api_update_stop_sequence():
    """API endpoint to update stop sequence number with validation"""
    from decimal import Decimal
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "No data provided"}), 400
        
    stop_id = data.get("stop_id")
    new_sequence = data.get("new_sequence")
    
    if stop_id is None or new_sequence is None:
        return jsonify({"success": False, "message": "stop_id and new_sequence are required"}), 400
        
    stop = RouteStop.query.get_or_404(stop_id)
    shipment_id = stop.shipment_id
    new_seq_decimal = Decimal(str(new_sequence))
    
    # Check if sequence number already exists in this shipment
    existing_stop = RouteStop.query.filter_by(
        shipment_id=shipment_id, 
        seq_no=new_seq_decimal
    ).filter(RouteStop.route_stop_id != stop_id).first()
    
    if existing_stop:
        return jsonify({
            "success": False, 
            "message": f"Sequence number {new_sequence} is already in use by stop '{existing_stop.stop_name or existing_stop.customer_code or existing_stop.route_stop_id}'. Please choose a different number."
        }), 400

    stop.seq_no = new_seq_decimal
    from app import db
    try:
        db.session.commit()
        return jsonify({"success": True, "message": f"Stop sequence updated to #{stop.seq_no}"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Error updating sequence: {str(e)}"}), 500


@bp.route("/stops/<int:route_stop_id>/update-notes", methods=["POST"])
@login_required
@admin_required
def update_stop_notes(route_stop_id):
    """Quick update stop notes"""
    stop = RouteStop.query.get_or_404(route_stop_id)
    shipment_id = stop.shipment_id
    
    notes = request.form.get("notes", "").strip()
    stop.notes = notes if notes else None
    
    from app import db
    db.session.commit()
    flash("Notes updated successfully", "success")
    
    return redirect(url_for("routes.detail", shipment_id=shipment_id))


@bp.route("/payment-terms/<customer_code>/update", methods=["POST"])
@login_required
@admin_required
def update_payment_terms(customer_code):
    """Update payment terms for a customer"""
    from app import db
    from models import PaymentCustomer, CreditTerms
    from decimal import Decimal
    
    # Get return shipment_id from referrer
    referrer = request.referrer or ''
    shipment_id = referrer.split('/')[-1] if '/routes/' in referrer else None
    
    # Get or create PaymentCustomer
    customer = PaymentCustomer.query.filter_by(code=customer_code).first()
    if not customer:
        # Create customer entry if it doesn't exist
        customer = PaymentCustomer(code=customer_code, name=customer_code, group='')
        db.session.add(customer)
        db.session.flush()
    
    # Get existing active terms
    existing_terms = CreditTerms.query.filter_by(
        customer_code=customer_code, 
        valid_to=None
    ).first()
    
    today = datetime.now().date()
    
    # Check if there's already a record for today
    today_terms = CreditTerms.query.filter_by(
        customer_code=customer_code,
        valid_from=today
    ).first()
    
    if today_terms:
        # Update the existing record from today
        terms_to_update = today_terms
    elif existing_terms and existing_terms.valid_from == today:
        # The active term already starts today, update it
        terms_to_update = existing_terms
    else:
        # Expire old terms and create new one
        if existing_terms:
            existing_terms.valid_to = today
        terms_to_update = CreditTerms()
        terms_to_update.customer_code = customer_code
        terms_to_update.valid_from = today
        terms_to_update.valid_to = None
        db.session.add(terms_to_update)
    
    # Update/set all fields
    terms_to_update.terms_code = request.form.get('terms_code', 'COD').strip()
    terms_to_update.due_days = int(request.form.get('due_days', 0))
    terms_to_update.is_credit = 'is_credit' in request.form
    
    # Credit limit
    credit_limit_str = request.form.get('credit_limit', '').strip()
    if credit_limit_str:
        terms_to_update.credit_limit = Decimal(credit_limit_str)
    else:
        terms_to_update.credit_limit = None
    
    # Payment methods
    terms_to_update.allow_cash = 'allow_cash' in request.form
    terms_to_update.allow_card_pos = 'allow_card_pos' in request.form
    terms_to_update.allow_bank_transfer = 'allow_bank_transfer' in request.form
    terms_to_update.allow_cheque = 'allow_cheque' in request.form
    
    # Cheque days
    cheque_days_str = request.form.get('cheque_days_allowed', '').strip()
    if cheque_days_str:
        terms_to_update.cheque_days_allowed = int(cheque_days_str)
    else:
        terms_to_update.cheque_days_allowed = None
    
    # Notes
    terms_to_update.notes_for_driver = request.form.get('notes_for_driver', '').strip() or None
    db.session.commit()
    
    flash(f"Payment terms updated for {customer_code}", "success")
    
    # Redirect back to route detail if we have shipment_id
    if shipment_id and shipment_id.isdigit():
        return redirect(url_for("routes.detail", shipment_id=int(shipment_id)))
    else:
        return redirect(request.referrer or url_for("routes.dashboard"))


@bp.route("/stops/<int:route_stop_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_stop(route_stop_id):
    """Delete a stop"""
    from models import DeliveryEvent, PODRecord, CODReceipt, ReceiptLog
    
    stop = RouteStop.query.get_or_404(route_stop_id)
    shipment_id = stop.shipment_id
    
    # Check if stop has been delivered (has delivery records)
    has_delivery_events = DeliveryEvent.query.filter_by(route_stop_id=route_stop_id).count() > 0
    has_pod = PODRecord.query.filter_by(route_stop_id=route_stop_id).count() > 0
    has_cod = CODReceipt.query.filter_by(route_stop_id=route_stop_id).count() > 0
    has_receipt_logs = ReceiptLog.query.filter_by(route_stop_id=route_stop_id).count() > 0
    
    if has_delivery_events or has_pod or has_cod or has_receipt_logs:
        flash("Cannot delete this stop - it has already been delivered and has delivery records (POD, COD receipts, or PS365 receipt logs). Delivered stops cannot be removed.", "error")
        return redirect(url_for("routes.detail", shipment_id=shipment_id))
    
    services.delete_stop(route_stop_id)
    flash("Stop deleted successfully", "success")
    return redirect(url_for("routes.detail", shipment_id=shipment_id))


@bp.route("/stops/<int:route_stop_id>/invoices/add", methods=["POST"])
@login_required
@admin_required
def add_invoices_to_stop(route_stop_id):
    """Add invoices to a stop"""
    stop = RouteStop.query.get_or_404(route_stop_id)
    
    invoice_nos = request.form.get("invoice_nos", "").strip()
    if invoice_nos:
        invoice_list = [inv.strip() for inv in invoice_nos.split(",") if inv.strip()]
        attached = services.attach_invoices_to_stop(route_stop_id, invoice_list)
        flash(f"Added {len(attached)} invoice(s) to stop", "success")
    else:
        flash("No invoices provided", "warning")
    
    return redirect(url_for("routes.detail", shipment_id=stop.shipment_id))


@bp.route("/stops/<int:route_stop_id>/invoices/<invoice_no>/remove", methods=["POST"])
@login_required
@admin_required
def remove_invoice_from_stop(route_stop_id, invoice_no):
    """Remove an invoice from a stop and delete stop if empty"""
    rsi = RouteStopInvoice.query.filter_by(
        route_stop_id=route_stop_id,
        invoice_no=invoice_no
    ).first_or_404()
    
    shipment_id = rsi.stop.shipment_id
    stop = rsi.stop
    
    from app import db
    from sqlalchemy import delete, update
    
    # Clear the invoice's route and stop assignment using bulk update
    db.session.execute(
        update(Invoice)
        .where(Invoice.invoice_no == invoice_no)
        .values(route_id=None, stop_id=None)
    )
    
    # Delete ALL route_stop_invoice links for this invoice (in case there are duplicates)
    db.session.execute(
        delete(RouteStopInvoice).where(
            RouteStopInvoice.invoice_no == invoice_no
        )
    )
    
    db.session.commit()
    
    # Check if stop is now empty
    remaining_invoices = RouteStopInvoice.query.filter_by(route_stop_id=route_stop_id).count()
    
    if remaining_invoices == 0:
        # Check if stop has delivery records before deleting
        from models import DeliveryEvent, PODRecord, CODReceipt, ReceiptLog
        has_delivery_events = DeliveryEvent.query.filter_by(route_stop_id=route_stop_id).count() > 0
        has_pod = PODRecord.query.filter_by(route_stop_id=route_stop_id).count() > 0
        has_cod = CODReceipt.query.filter_by(route_stop_id=route_stop_id).count() > 0
        has_receipt_logs = ReceiptLog.query.filter_by(route_stop_id=route_stop_id).count() > 0
        
        if has_delivery_events or has_pod or has_cod or has_receipt_logs:
            flash(f"Invoice {invoice_no} removed, but cannot delete the empty stop - it has already been delivered and has delivery records. The stop will remain empty.", "warning")
        else:
            # Delete the empty stop using the service to handle all FK constraints
            services.delete_stop(route_stop_id)
            flash(f"Invoice {invoice_no} removed. Stop #{stop.seq_no} deleted (no invoices remaining).", "success")
    else:
        flash(f"Invoice {invoice_no} removed from stop", "success")
    return redirect(url_for("routes.detail", shipment_id=shipment_id))


@bp.route("/<int:shipment_id>/run-sheet")
@login_required
def run_sheet(shipment_id):
    """Display printable run sheet for drivers"""
    route = Shipment.query.get_or_404(shipment_id)
    stops = RouteStop.query.filter_by(shipment_id=shipment_id).order_by(RouteStop.seq_no).all()
    
    return render_template("run_sheet.html", route=route, stops=stops)


@bp.route("/auto-assign", methods=["POST"])
@login_required
@admin_required
def auto_assign():
    """
    Auto-assign invoices to a route with automatic customer grouping.
    
    Expected JSON body:
    {
        "driver_name": "Driver Name",
        "delivery_date": "YYYY-MM-DD",
        "invoice_nos": ["INV001", "INV002", ...],
        "route_id": 123  // Optional: use existing route instead of creating new
    }
    
    Returns:
    {
        "ok": true,
        "route_id": 123,
        "created_stops": 2,
        "total_invoices": 5,
        ...
    }
    """
    data = request.get_json(force=True)
    
    # Validate input
    driver_name = data.get("driver_name")
    delivery_date_str = data.get("delivery_date")
    invoice_nos = data.get("invoice_nos", [])
    route_id = data.get("route_id")  # Optional: existing route ID
    
    if not driver_name:
        return jsonify({"ok": False, "message": "driver_name is required"}), 400
    
    if not delivery_date_str:
        return jsonify({"ok": False, "message": "delivery_date is required"}), 400
    
    if not invoice_nos:
        return jsonify({"ok": False, "message": "invoice_nos list is required"}), 400
    
    try:
        delivery_date = datetime.strptime(delivery_date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"ok": False, "message": "Invalid date format. Use YYYY-MM-DD"}), 400
    
    # Use existing route or create new one
    if route_id:
        # Use existing route
        route = Shipment.query.get(route_id)
        if not route:
            return jsonify({"ok": False, "message": f"Route {route_id} not found"}), 404
    else:
        # Create new route
        route = services.upsert_route(
            driver_name=driver_name,
            route_name=data.get("route_name", f"{driver_name} Route"),
            delivery_date=delivery_date,
            status="PLANNED"
        )
    
    # Auto-assign invoices grouped by customer
    result = services_routing.assign_invoices_to_route_grouped_by_customer(
        route.id,
        invoice_nos
    )
    
    if result.get("ok"):
        result["route_id"] = route.id
        result["driver_name"] = route.driver_name
        result["delivery_date"] = route.delivery_date.isoformat()
        return jsonify(result), 200
    else:
        return jsonify(result), 400


@bp.route("/unassign-from-route", methods=["POST"])
@login_required
@admin_required
def unassign_from_route():
    """
    Unassign invoices from their routes.
    
    Expected JSON body:
    {
        "invoice_nos": ["INV001", "INV002", ...]
    }
    
    Returns:
    {
        "ok": true,
        "total_invoices": 2,
        "message": "..."
    }
    """
    from app import db
    
    data = request.get_json(force=True)
    invoice_nos = data.get("invoice_nos", [])
    
    if not invoice_nos:
        return jsonify({"ok": False, "message": "invoice_nos list is required"}), 400
    
    # Find all invoices
    invoices = Invoice.query.filter(Invoice.invoice_no.in_(invoice_nos)).all()
    
    if not invoices:
        return jsonify({"ok": False, "message": "No invoices found"}), 404
    
    # Unassign each invoice from its route
    from sqlalchemy import delete
    for invoice in invoices:
        # Delete route_stop_invoice records
        if invoice.stop_id:
            db.session.execute(
                delete(RouteStopInvoice).where(
                    RouteStopInvoice.invoice_no == invoice.invoice_no
                )
            )
        invoice.route_id = None
        invoice.stop_id = None
    
    db.session.commit()
    
    # Clean up any empty stops after unassigning invoices
    empty_stops = db.session.execute(
        db.select(RouteStop.route_stop_id).outerjoin(
            RouteStopInvoice
        ).group_by(RouteStop.route_stop_id).having(
            db.func.count(RouteStopInvoice.invoice_no) == 0
        )
    ).scalars().all()
    
    for stop_id in empty_stops:
        services.delete_stop(stop_id)
    
    return jsonify({
        "ok": True,
        "total_invoices": len(invoices),
        "message": f"{len(invoices)} invoice(s) removed from route"
    }), 200


@bp.route("/<int:shipment_id>/mark-shipped", methods=["POST"])
@login_required
@admin_required
def mark_shipped(shipment_id):
    """Mark route as shipped and update all invoices to shipped status"""
    from app import db
    from models import ActivityLog
    from timezone_utils import get_local_time
    
    route = Shipment.query.get_or_404(shipment_id)
    
    # Get all invoices on this route
    invoices = Invoice.query.filter_by(route_id=shipment_id).all()
    
    # Check if route has invoices
    if not invoices:
        flash("Cannot ship route. Route has no invoices.", "danger")
        if request.referrer and 'delivery-dashboard' in request.referrer:
            return redirect(url_for("delivery_dashboard.dashboard"))
        return redirect(url_for("routes.detail", shipment_id=shipment_id))
    
    # Check if all invoices are picked (ready_for_dispatch)
    unpicked_invoices = [inv for inv in invoices if inv.status != 'ready_for_dispatch']
    
    if unpicked_invoices:
        unpicked_list = ', '.join([inv.invoice_no for inv in unpicked_invoices])
        flash(f"Cannot ship route. The following invoices are not picked yet: {unpicked_list}", "danger")
        
        # Check if request came from delivery dashboard
        if request.referrer and 'delivery-dashboard' in request.referrer:
            return redirect(url_for("delivery_dashboard.dashboard"))
        
        return redirect(url_for("routes.detail", shipment_id=shipment_id))
    
    # Check if all invoices are synced from PS365 (have total amounts)
    not_synced = [inv for inv in invoices if inv.total_grand is None]
    if not_synced:
        invoice_list = ', '.join([inv.invoice_no for inv in not_synced[:5]])
        if len(not_synced) > 5:
            invoice_list += f" and {len(not_synced) - 5} more"
        flash(f"Cannot ship route. {len(not_synced)} invoice(s) have not been synced from PS365 to get total amounts. Please sync these invoices first: {invoice_list}", "danger")
        
        if request.referrer and 'delivery-dashboard' in request.referrer:
            return redirect(url_for("delivery_dashboard.dashboard"))
        
        return redirect(url_for("routes.detail", shipment_id=shipment_id))
    
    # Keep route status as DISPATCHED (drivers need to see it)
    # Only update invoices to shipped status
    route.status = "DISPATCHED"
    
    # Update all invoices to shipped status
    for invoice in invoices:
        old_status = invoice.status
        invoice.status = "shipped"
        
        # Log the status change
        log = ActivityLog()
        log.invoice_no = invoice.invoice_no
        log.activity_type = "status_change"
        log.details = f"Route marked as shipped - status changed from {old_status} to shipped by {current_user.username}"
        log.picker_username = current_user.username
        log.timestamp = utc_now_for_db()
        db.session.add(log)
    
    db.session.commit()
    
    flash(f"Route marked as SHIPPED. {len(invoices)} invoices updated to shipped status.", "success")
    
    # Check if request came from delivery dashboard
    if request.referrer and 'delivery-dashboard' in request.referrer:
        return redirect(url_for("delivery_dashboard.dashboard"))
    
    return redirect(url_for("routes.detail", shipment_id=shipment_id))


@bp.route("/<int:shipment_id>/start-route", methods=["POST"])
@login_required
def start_route(shipment_id):
    """Start a route - changes status to in_progress and all orders to out_for_delivery"""
    from app import db
    from models import ActivityLog
    from timezone_utils import get_local_time
    
    route = Shipment.query.get_or_404(shipment_id)
    
    # Verify driver owns this route
    if current_user.role == 'driver' and route.driver_name != current_user.username:
        abort(403)
    
    # Verify route is in DISPATCHED status (ready to start)
    if route.status != 'DISPATCHED':
        flash(f"Cannot start route. Route status is {route.status}. Only DISPATCHED routes can be started.", "danger")
        return redirect(url_for("routes.detail", shipment_id=shipment_id))
    
    # Update route status to IN_TRANSIT
    route.status = "IN_TRANSIT"
    route.started_at = utc_now_for_db()
    
    # Get all invoices on this route
    invoices = Invoice.query.filter_by(route_id=shipment_id).all()
    
    # Update all invoices to out_for_delivery status
    for invoice in invoices:
        old_status = invoice.status
        invoice.status = "out_for_delivery"
        
        # Sync to RouteStopInvoice
        db.session.query(RouteStopInvoice).filter(
            RouteStopInvoice.invoice_no == invoice.invoice_no
        ).update({RouteStopInvoice.status: 'OUT_FOR_DELIVERY'}, synchronize_session=False)
        
        # Log the status change
        log = ActivityLog()
        log.invoice_no = invoice.invoice_no
        log.activity_type = "status_change"
        log.details = f"Route started - status changed from {old_status} to out_for_delivery by {current_user.username}"
        log.picker_username = current_user.username
        log.timestamp = utc_now_for_db()
        db.session.add(log)
    
    db.session.commit()
    
    flash(f"Route started! {len(invoices)} orders are now OUT FOR DELIVERY.", "success")
    return redirect(url_for("routes.detail", shipment_id=shipment_id))


@bp.route("/<int:shipment_id>/orders/<invoice_no>/deliver", methods=["POST"])
@login_required
def deliver_order(shipment_id, invoice_no):
    """Mark an order as delivered"""
    from app import db
    from models import ActivityLog
    from timezone_utils import get_local_time
    
    route = Shipment.query.get_or_404(shipment_id)
    
    # Verify driver owns this route
    if current_user.role == 'driver' and route.driver_name != current_user.username:
        abort(403)
    
    # Get the order
    order = Invoice.query.filter_by(invoice_no=invoice_no, route_id=shipment_id).first_or_404()
    
    # Verify order is out for delivery
    if order.status != 'out_for_delivery':
        flash(f"Cannot deliver order. Order status is {order.status}.", "danger")
        return redirect(url_for("routes.detail", shipment_id=shipment_id))
    
    # Update order status
    order.status = "delivered"
    order.delivered_at = utc_now_for_db()
    
    # Update ALL RouteStopInvoice rows for this invoice to match
    db.session.query(RouteStopInvoice).join(RouteStop).filter(
        RouteStop.shipment_id == shipment_id,
        RouteStopInvoice.invoice_no == invoice_no
    ).update({RouteStopInvoice.status: 'DELIVERED'}, synchronize_session=False)
    
    # Log the status change
    log = ActivityLog()
    log.invoice_no = order.invoice_no
    log.activity_type = "status_change"
    log.details = f"Order delivered by {current_user.username}"
    log.picker_username = current_user.username
    log.timestamp = utc_now_for_db()
    db.session.add(log)
    
    # Recompute route completion
    recompute_route_completion(shipment_id)
    
    db.session.commit()
    
    flash(f"Order {invoice_no} marked as DELIVERED.", "success")
    return redirect(url_for("routes.detail", shipment_id=shipment_id))


@bp.route("/<int:shipment_id>/orders/<invoice_no>/return", methods=["POST"])
@login_required
def return_order(shipment_id, invoice_no):
    """Mark an order as returned"""
    from app import db
    from models import ActivityLog
    from timezone_utils import get_local_time
    
    route = Shipment.query.get_or_404(shipment_id)
    
    # Verify driver owns this route
    if current_user.role == 'driver' and route.driver_name != current_user.username:
        abort(403)
    
    # Get the order
    order = Invoice.query.filter_by(invoice_no=invoice_no, route_id=shipment_id).first_or_404()
    
    # Verify order is out for delivery
    if order.status != 'out_for_delivery':
        flash(f"Cannot return order. Order status is {order.status}.", "danger")
        return redirect(url_for("routes.detail", shipment_id=shipment_id))
    
    # Update order status
    order.status = "returned"
    
    # Update ALL RouteStopInvoice rows for this invoice (returned = FAILED for route completion)
    db.session.query(RouteStopInvoice).join(RouteStop).filter(
        RouteStop.shipment_id == shipment_id,
        RouteStopInvoice.invoice_no == invoice_no
    ).update({RouteStopInvoice.status: 'FAILED'}, synchronize_session=False)
    
    # Log the status change
    log = ActivityLog()
    log.invoice_no = order.invoice_no
    log.activity_type = "status_change"
    log.details = f"Order returned by {current_user.username}"
    log.picker_username = current_user.username
    log.timestamp = utc_now_for_db()
    db.session.add(log)
    
    # Recompute route completion
    recompute_route_completion(shipment_id)
    
    db.session.commit()
    
    flash(f"Order {invoice_no} marked as RETURNED.", "warning")
    return redirect(url_for("routes.detail", shipment_id=shipment_id))


@bp.route("/<int:shipment_id>/orders/<invoice_no>/fail", methods=["POST"])
@login_required
def fail_order(shipment_id, invoice_no):
    """Mark an order as delivery failed"""
    from app import db
    from models import ActivityLog
    from timezone_utils import get_local_time
    
    route = Shipment.query.get_or_404(shipment_id)
    
    # Verify driver owns this route
    if current_user.role == 'driver' and route.driver_name != current_user.username:
        abort(403)
    
    # Get the order
    order = Invoice.query.filter_by(invoice_no=invoice_no, route_id=shipment_id).first_or_404()
    
    # Verify order is out for delivery
    if order.status != 'out_for_delivery':
        flash(f"Cannot fail order. Order status is {order.status}.", "danger")
        return redirect(url_for("routes.detail", shipment_id=shipment_id))
    
    # Update order status
    order.status = "delivery_failed"
    
    # Update ALL RouteStopInvoice rows for this invoice
    db.session.query(RouteStopInvoice).join(RouteStop).filter(
        RouteStop.shipment_id == shipment_id,
        RouteStopInvoice.invoice_no == invoice_no
    ).update({RouteStopInvoice.status: 'FAILED'}, synchronize_session=False)
    
    # Log the status change
    log = ActivityLog()
    log.invoice_no = order.invoice_no
    log.activity_type = "status_change"
    log.details = f"Order delivery failed - marked by {current_user.username}"
    log.picker_username = current_user.username
    log.timestamp = utc_now_for_db()
    db.session.add(log)
    
    # Recompute route completion
    recompute_route_completion(shipment_id)
    
    db.session.commit()
    
    flash(f"Order {invoice_no} marked as DELIVERY FAILED.", "danger")
    return redirect(url_for("routes.detail", shipment_id=shipment_id))


@bp.route("/<int:shipment_id>/change-status", methods=["POST"])
@login_required
@admin_required
def change_route_status(shipment_id):
    """Change route status and update order statuses accordingly"""
    from app import db
    from models import ActivityLog
    from timezone_utils import get_local_time
    
    route = Shipment.query.get_or_404(shipment_id)
    new_status = request.form.get("new_status")
    
    if not new_status:
        flash("Status is required", "danger")
        return redirect(url_for("routes.detail", shipment_id=shipment_id))
    
    old_status = route.status
    
    # Get all invoices on this route
    invoices = Invoice.query.filter_by(route_id=shipment_id).all()
    
    # Validation: Check if status transition is allowed
    valid_transitions = {
        'PLANNED': ['DISPATCHED', 'CANCELLED'],
        'DISPATCHED': ['IN_TRANSIT', 'CANCELLED'],
        'IN_TRANSIT': ['COMPLETED', 'CANCELLED'],
        'COMPLETED': [],  # Terminal state
        'CANCELLED': []   # Terminal state
    }
    
    # Allow transition if: same status, valid transition, or going to CANCELLED
    if new_status != old_status:
        allowed = valid_transitions.get(old_status, [])
        if new_status not in allowed and new_status != 'CANCELLED':
            flash(f"Invalid status transition: Cannot change from {old_status} to {new_status}. Allowed: {', '.join(allowed) if allowed else 'none'}", "danger")
            return redirect(url_for("routes.detail", shipment_id=shipment_id))
    
    # Validation: DISPATCHED requires all invoices to be ready_for_dispatch
    if new_status == 'DISPATCHED':
        if not invoices:
            flash("Cannot mark as DISPATCHED: Route has no invoices", "danger")
            return redirect(url_for("routes.detail", shipment_id=shipment_id))
        
        not_ready = [inv for inv in invoices if inv.status != 'ready_for_dispatch']
        if not_ready:
            flash(f"Cannot mark as DISPATCHED: {len(not_ready)} invoice(s) are not ready for dispatch. All invoices must be 'ready_for_dispatch'.", "danger")
            return redirect(url_for("routes.detail", shipment_id=shipment_id))
        
        # Validation: DISPATCHED requires all invoices to be synced from PS365
        not_synced = [inv for inv in invoices if inv.ps365_synced_at is None]
        if not_synced:
            invoice_list = ', '.join([inv.invoice_no for inv in not_synced[:5]])  # Show first 5
            if len(not_synced) > 5:
                invoice_list += f" and {len(not_synced) - 5} more"
            flash(f"Cannot mark as DISPATCHED: {len(not_synced)} invoice(s) have not been synced from PS365 to get total amounts. Please sync these invoices first: {invoice_list}", "danger")
            return redirect(url_for("routes.detail", shipment_id=shipment_id))
    
    # Determine what order status should be based on new route status
    order_status_map = {
        'PLANNED': 'ready_for_dispatch',      # Route planned - orders ready to be shipped
        'DISPATCHED': 'shipped',               # Route dispatched - orders shipped
        'IN_TRANSIT': 'out_for_delivery',      # Route in transit - orders out for delivery
        'CANCELLED': 'ready_for_dispatch',     # Route cancelled - orders back to ready
        'COMPLETED': None                      # Route completed - keep current status (delivered/returned/failed)
    }
    
    new_order_status = order_status_map.get(new_status)
    
    # Update route status
    route.status = new_status
    
    # Update order statuses if applicable
    # Terminal statuses that should never be changed
    terminal_statuses = {'delivered', 'returned', 'delivery_failed'}
    updated_count = 0
    
    if new_order_status:
        for invoice in invoices:
            # Skip orders in terminal states to preserve delivery outcomes
            if invoice.status in terminal_statuses:
                continue
            
            old_inv_status = invoice.status
            invoice.status = new_order_status
            updated_count += 1
            
            # Log the status change
            log = ActivityLog()
            log.invoice_no = invoice.invoice_no
            log.activity_type = "status_change"
            log.details = f"Route status changed to {new_status} - order status changed from {old_inv_status} to {new_order_status} by {current_user.username}"
            log.picker_username = current_user.username
            log.timestamp = utc_now_for_db()
            db.session.add(log)
    
    db.session.commit()
    
    if updated_count > 0:
        flash(f"Route status changed from {old_status} to {new_status}. {updated_count} orders updated to {new_order_status}.", "success")
    else:
        flash(f"Route status changed from {old_status} to {new_status}. Order statuses unchanged.", "success")
    
    return redirect(url_for("routes.detail", shipment_id=shipment_id))


@bp.route("/<int:shipment_id>/orders/<invoice_no>/remove", methods=["POST"])
@login_required
@admin_required
def remove_order_from_route(shipment_id, invoice_no):
    """Remove an order from a route and optionally mark it as unassigned"""
    from app import db
    from models import ActivityLog
    from timezone_utils import get_local_time
    
    route = Shipment.query.get_or_404(shipment_id)
    invoice = Invoice.query.filter_by(invoice_no=invoice_no, route_id=shipment_id).first_or_404()
    
    # Check if should mark as unassigned
    mark_unassigned = request.form.get("mark_unassigned") == "true"
    
    # Get the stop_id before removing the invoice
    affected_stop_id = invoice.stop_id
    
    # Remove from route stop invoice associations
    # Need to join through RouteStop to filter by shipment_id
    from sqlalchemy import delete
    delete_stmt = delete(RouteStopInvoice).where(
        RouteStopInvoice.invoice_no == invoice_no,
        RouteStopInvoice.route_stop_id.in_(
            db.session.query(RouteStop.route_stop_id).filter_by(shipment_id=shipment_id)
        )
    )
    db.session.execute(delete_stmt)
    
    # Clear route assignment
    old_status = invoice.status
    invoice.route_id = None
    invoice.stop_id = None
    
    # Optionally mark as unassigned (not_started)
    if mark_unassigned:
        invoice.status = "not_started"
        status_msg = " and marked as unassigned (not_started)"
    else:
        status_msg = f" (status remains {invoice.status})"
    
    # Log the removal
    log = ActivityLog()
    log.invoice_no = invoice.invoice_no
    log.activity_type = "route_change"
    log.details = f"Removed from route {shipment_id}{status_msg} by {current_user.username}"
    log.picker_username = current_user.username
    log.timestamp = utc_now_for_db()
    db.session.add(log)
    
    db.session.commit()
    
    # Check if the stop is now empty and delete if so
    if affected_stop_id:
        remaining_invoices = RouteStopInvoice.query.filter_by(route_stop_id=affected_stop_id).count()
        if remaining_invoices == 0:
            stop = RouteStop.query.get(affected_stop_id)
            if stop:
                services.delete_stop(affected_stop_id)
                flash(f"Order {invoice_no} removed from route{status_msg}. Stop #{stop.seq_no} deleted (no invoices remaining).", "info")
                return redirect(url_for("routes.detail", shipment_id=shipment_id))
    
    flash(f"Order {invoice_no} removed from route{status_msg}.", "info")
    return redirect(url_for("routes.detail", shipment_id=shipment_id))


@bp.route("/<int:shipment_id>/sync-ps365", methods=["POST"])
@login_required
@admin_required
def sync_ps365_totals(shipment_id):
    """Sync all invoice totals from Powersoft365 API"""
    route = Shipment.query.get_or_404(shipment_id)
    
    # Trigger sync
    result = ps365_service.sync_route_invoices(shipment_id)
    
    if result["success"]:
        flash(f"✅ Successfully synced {result['synced']} invoice(s) from Powersoft365", "success")
    else:
        flash(f"⚠️ Synced {result['synced']}/{result['total_invoices']} invoices. {result['failed']} failed.", "warning")
        for error in result.get("errors", [])[:3]:  # Show first 3 errors
            flash(f"• {error['invoice_no']}: {error['error']}", "warning")
    
    return redirect(url_for("routes.detail", shipment_id=shipment_id))


@bp.route("/api/update-stop-sequence", methods=["POST"])
@login_required
@admin_required
def api_update_stop_sequence():
    """Update the sequence number for a route stop"""
    from app import db
    from decimal import Decimal
    
    try:
        data = request.get_json()
        stop_id = data.get('stop_id')
        new_sequence = data.get('new_sequence')
        
        if not stop_id:
            return jsonify({'success': False, 'message': 'Stop ID is required'}), 400
        
        if new_sequence is None or new_sequence < 0:
            return jsonify({'success': False, 'message': 'Invalid sequence number'}), 400
        
        # Convert to Decimal
        new_sequence = Decimal(str(new_sequence))
        
        # Find the stop
        stop = RouteStop.query.get(stop_id)
        if not stop:
            return jsonify({'success': False, 'message': 'Stop not found'}), 404
        
        # Check if sequence number is already in use
        existing_stop = RouteStop.query.filter_by(
            shipment_id=stop.shipment_id,
            seq_no=new_sequence
        ).first()
        
        if existing_stop and existing_stop.route_stop_id != stop_id:
            # Swap sequences
            old_sequence = stop.seq_no
            existing_stop.seq_no = old_sequence
            stop.seq_no = new_sequence
        else:
            # Just update the sequence
            stop.seq_no = new_sequence
        
        db.session.commit()
        return jsonify({'success': True, 'message': 'Sequence updated successfully'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@bp.route("/<int:shipment_id>/reconcile")
@login_required
@admin_required
def reconcile_view(shipment_id):
    """View route details for reconciliation and allow reconciliation action"""
    from services_route_lifecycle import get_route_reconciliation_summary
    
    route = Shipment.query.get_or_404(shipment_id)
    summary = get_route_reconciliation_summary(shipment_id)
    
    return render_template('route_reconcile.html', route=route, summary=summary)


@bp.route("/<int:shipment_id>/reconcile", methods=["POST"])
@login_required
@admin_required
def reconcile_action(shipment_id):
    """Perform route reconciliation"""
    from services_route_lifecycle import reconcile_route
    
    force = request.form.get('force') == 'true'
    
    success, message = reconcile_route(shipment_id, current_user.username, force=force)
    
    if success:
        flash(f"Route #{shipment_id} has been reconciled and archived.", "success")
        return redirect(url_for("routes.dashboard", view="archived"))
    else:
        flash(f"Cannot reconcile: {message}", "danger")
        return redirect(url_for("routes.reconcile_view", shipment_id=shipment_id))


@bp.route("/<int:shipment_id>/reconciliation")
@login_required
@admin_required
def reconciliation_report(shipment_id):
    """Comprehensive route reconciliation report for admin review"""
    from models import (DeliveryEvent, DeliveryLine, CODReceipt, PODRecord, 
                       DeliveryDiscrepancy, ReceiptLog, Invoice)
    from app import db
    
    route = Shipment.query.get_or_404(shipment_id)
    
    # Get all delivery events
    delivery_events = DeliveryEvent.query.filter_by(
        route_id=shipment_id
    ).order_by(DeliveryEvent.created_at.desc()).all()
    
    # Get all stops with delivery details
    stops = RouteStop.query.filter_by(
        shipment_id=shipment_id
    ).order_by(RouteStop.seq_no).all()
    
    # Create stop lookup for delivery events (use primitives to avoid DetachedInstanceError)
    stop_lookup = {
        stop.route_stop_id: {
            'seq_no': stop.seq_no,
            'stop_name': stop.stop_name,
            'customer_code': stop.customer_code
        } for stop in stops
    }
    
    # Organize data by stop
    stops_data = []
    for stop in stops:
        # Get invoices for this stop
        stop_invoices = db.session.query(Invoice).join(
            RouteStopInvoice, Invoice.invoice_no == RouteStopInvoice.invoice_no
        ).filter(
            RouteStopInvoice.route_stop_id == stop.route_stop_id
        ).all()
        
        # Get delivery lines (exceptions)
        delivery_lines = DeliveryLine.query.filter_by(
            route_stop_id=stop.route_stop_id
        ).all()
        
        # Get COD receipt
        cod_receipt = CODReceipt.query.filter_by(
            route_stop_id=stop.route_stop_id
        ).first()
        
        # Get POD record
        pod_record = PODRecord.query.filter_by(
            route_stop_id=stop.route_stop_id
        ).first()
        
        # Get discrepancies for this stop's invoices
        invoice_nos = [inv.invoice_no for inv in stop_invoices]
        discrepancies = DeliveryDiscrepancy.query.filter(
            DeliveryDiscrepancy.invoice_no.in_(invoice_nos)
        ).all() if invoice_nos else []
        
        # Get PS365 receipt log if COD receipt was sent
        ps365_receipt = None
        if cod_receipt and cod_receipt.ps365_receipt_id:
            ps365_receipt = ReceiptLog.query.filter_by(
                reference_number=cod_receipt.ps365_receipt_id
            ).first()
        
        stops_data.append({
            'stop': stop,
            'invoices': stop_invoices,
            'delivery_lines': delivery_lines,
            'cod_receipt': cod_receipt,
            'pod_record': pod_record,
            'discrepancies': discrepancies,
            'ps365_receipt': ps365_receipt
        })
    
    # Calculate totals
    all_cod_receipts = CODReceipt.query.filter_by(route_id=shipment_id).all()
    total_expected = sum(r.expected_amount for r in all_cod_receipts)
    total_received = sum(r.received_amount for r in all_cod_receipts)
    total_variance = total_received - total_expected
    
    # Settlement info
    settlement_info = {
        'submitted': route.driver_submitted_at is not None,
        'submitted_at': route.driver_submitted_at,
        'submitted_amount': route.cash_handed_in,
        'cleared': route.settlement_status == 'SETTLED',
        'cleared_at': route.completed_at,
        'status': route.settlement_status
    }
    
    return render_template('route_reconciliation.html',
                         route=route,
                         stops_data=stops_data,
                         delivery_events=delivery_events,
                         stop_lookup=stop_lookup,
                         total_expected=float(total_expected),
                         total_received=float(total_received),
                         total_variance=float(total_variance),
                         settlement_info=settlement_info)
