import os
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from app import db
from models import DeliveryDiscrepancy, DeliveryDiscrepancyEvent, Invoice, InvoiceItem, User, ItemTimeTracking, DiscrepancyType, StockResolution
from functools import wraps
from werkzeug.utils import secure_filename
from datetime import datetime
import logging

delivery_issues_bp = Blueprint('delivery_issues', __name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
UPLOAD_FOLDER = 'uploads/delivery_issues'

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role not in ['admin', 'warehouse_manager']:
            flash('Access denied. Admin privileges required.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def admin_only_required(f):
    """Decorator for admin-only operations (not warehouse_manager)"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != 'admin':
            flash('Access denied. Admin-only privileges required.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def create_event(discrepancy_id, event_type, actor, note=None, old_value=None, new_value=None):
    """Helper to create a discrepancy event"""
    event = DeliveryDiscrepancyEvent(
        discrepancy_id=discrepancy_id,
        event_type=event_type,
        actor=actor,
        note=note,
        old_value=old_value,
        new_value=new_value
    )
    db.session.add(event)
    return event

@delivery_issues_bp.route('/admin/delivery-issues/new', methods=['GET', 'POST'])
@admin_required
def record_issue():
    """Record a new delivery discrepancy"""
    if request.method == 'POST':
        try:
            invoice_no = request.form.get('invoice_no')
            item_code = request.form.get('item_code')
            custom_item_code = request.form.get('custom_item_code', '').strip()
            qty_expected_str = request.form.get('qty_expected', '').strip()
            qty_expected = int(qty_expected_str) if qty_expected_str else 0
            qty_actual = request.form.get('qty_actual')
            qty_actual = float(qty_actual) if qty_actual else None
            discrepancy_type = request.form.get('discrepancy_type')
            reported_source = request.form.get('reported_source', 'admin')
            note = request.form.get('note', '')
            
            invoice = Invoice.query.get(invoice_no)
            if not invoice:
                flash('Invoice not found', 'error')
                return redirect(url_for('delivery_issues.record_issue'))
            
            # Handle custom item code
            if item_code == 'OTHER':
                if not custom_item_code:
                    flash('Custom item code is required when "Other" is selected', 'error')
                    return redirect(url_for('delivery_issues.record_issue'))
                item_code = custom_item_code
                item = None
                item_name = 'Custom Item'
                item_location = None
            else:
                item = InvoiceItem.query.filter_by(
                    invoice_no=invoice_no,
                    item_code=item_code
                ).first()
                
                if not item:
                    flash('Item not found in invoice', 'error')
                    return redirect(url_for('delivery_issues.record_issue'))
                item_name = item.item_name
                item_location = item.location
            
            # Get picker and pick time from ItemTimeTracking or invoice
            picker_username = None
            picked_at = None
            tracking_record = ItemTimeTracking.query.filter_by(
                invoice_no=invoice_no,
                item_code=item_code
            ).order_by(ItemTimeTracking.item_completed.desc()).first()
            
            if tracking_record:
                picker_username = tracking_record.picker_username
                picked_at = tracking_record.item_completed
            elif invoice.assigned_to:
                picker_username = invoice.assigned_to
                # Use invoice picking completion time if available
                if invoice.picking_complete_time:
                    picked_at = invoice.picking_complete_time
            
            # Get delivery date from invoice
            delivery_date = None
            if invoice.delivered_at:
                delivery_date = invoice.delivered_at.date()
            elif invoice.shipped_at:
                delivery_date = invoice.shipped_at.date()
            
            # Lookup shelf location from PS365
            shelf_code_365 = None
            if item_code:
                try:
                    from ps365_util import find_shelf_for_item_ps365
                    store_code = "777"  # Default store, adjust as needed
                    shelf_code_365 = find_shelf_for_item_ps365(store=store_code, item=item_code)
                    if shelf_code_365:
                        logging.info(f"PS365: Found shelf {shelf_code_365} for item {item_code}")
                except Exception as e:
                    logging.warning(f"PS365 lookup failed for {item_code}: {e}")
            
            photo_paths = []
            if 'photos' in request.files:
                files = request.files.getlist('photos')
                for file in files:
                    if file and file.filename and allowed_file(file.filename):
                        filename = secure_filename(file.filename)
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        filename = f"{timestamp}_{filename}"
                        filepath = os.path.join(UPLOAD_FOLDER, filename)
                        file.save(filepath)
                        photo_paths.append(filepath)
            
            discrepancy = DeliveryDiscrepancy(
                invoice_no=invoice_no,
                item_code_expected=item_code,
                item_name=item_name,
                qty_expected=qty_expected,
                qty_actual=qty_actual,
                discrepancy_type=discrepancy_type,
                reported_by=current_user.username,
                reported_source=reported_source,
                note=note,
                photo_paths=json.dumps(photo_paths) if photo_paths else None,
                status='reported',
                picker_username=picker_username,
                picked_at=picked_at,
                delivery_date=delivery_date,
                shelf_code_365=shelf_code_365,
                location=item_location
            )
            
            db.session.add(discrepancy)
            db.session.flush()
            
            create_event(
                discrepancy_id=discrepancy.id,
                event_type='created',
                actor=current_user.username,
                note=f'Discrepancy reported: {discrepancy_type}'
            )
            
            db.session.commit()
            
            flash(f'Delivery issue #{discrepancy.id} recorded successfully', 'success')
            return redirect(url_for('delivery_issues.review_issues'))
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Error recording delivery issue: {str(e)}")
            flash(f'Error recording issue: {str(e)}', 'error')
            return redirect(url_for('delivery_issues.record_issue'))
    
    # Only load essential fields for dropdown - improves performance
    # Show last 40 invoices initially, search endpoint handles the rest
    # Sort by upload_date (invoice date) first, then invoice number (both descending)
    # Filter to only include production invoices (start with IN1) - exclude test data
    invoice_query = Invoice.query.with_entities(
        Invoice.invoice_no,
        Invoice.customer_name,
        Invoice.routing
    ).filter(
        Invoice.status.in_(['shipped', 'out_for_delivery', 'delivered']),
        Invoice.invoice_no.like('IN1%')
    ).order_by(
        db.func.to_date(Invoice.upload_date, 'YYYY-MM-DD').desc(),
        Invoice.invoice_no.desc()
    ).limit(40).all()
    
    # Convert to dict format for template
    invoices = [{'invoice_no': inv[0], 'customer_name': inv[1], 'routing': inv[2]} 
                for inv in invoice_query]
    
    # Get active discrepancy types from database
    discrepancy_types = DiscrepancyType.query.filter_by(is_active=True).order_by(DiscrepancyType.sort_order).all()
    
    # Debug logging
    logging.info(f"Loading record_issue form - Found {len(discrepancy_types)} discrepancy types")
    for dt in discrepancy_types:
        logging.info(f"  - {dt.name}: {dt.display_name}")
    
    sources = ['admin', 'driver', 'customer', 'warehouse']
    
    return render_template('admin/record_delivery_issue.html',
                         invoices=invoices,
                         discrepancy_types=discrepancy_types,
                         sources=sources)

@delivery_issues_bp.route('/api/delivery-issues/search-invoices')
@admin_required
def search_invoices():
    """
    Search for invoices with statuses: shipped, out_for_delivery, delivered
    Requires minimum search term length for performance
    Returns max 100 results
    """
    search_term = request.args.get('q', '').strip()
    
    if not search_term:
        return jsonify([])
    
    # Build base query for eligible statuses
    # Filter to only include production invoices (start with IN1) - exclude test data
    query = Invoice.query.with_entities(
        Invoice.invoice_no,
        Invoice.customer_name,
        Invoice.routing,
        Invoice.upload_date
    ).filter(
        Invoice.status.in_(['shipped', 'out_for_delivery', 'delivered']),
        Invoice.invoice_no.like('IN1%')
    )
    
    # Apply search filters
    search_lower = search_term.lower()
    
    # Invoice number search (min 5 chars)
    if len(search_term) >= 5:
        query = query.filter(
            db.or_(
                Invoice.invoice_no.ilike(f'%{search_term.upper()}%'),
                Invoice.customer_name.ilike(f'%{search_term}%')
            )
        )
    # Customer name search (min 3 chars)
    elif len(search_term) >= 3:
        query = query.filter(Invoice.customer_name.ilike(f'%{search_term}%'))
    else:
        # Too short - return empty
        return jsonify([])
    
    # Order by most recent (upload_date = invoice date) and limit results
    results = query.order_by(
        db.func.to_date(Invoice.upload_date, 'YYYY-MM-DD').desc(),
        Invoice.invoice_no.desc()
    ).limit(100).all()
    
    # Format response
    invoices = [
        {
            'invoice_no': inv[0],
            'customer_name': inv[1],
            'routing': inv[2],
            'upload_date': inv[3]  # Already in YYYY-MM-DD string format
        }
        for inv in results
    ]
    
    return jsonify(invoices)

@delivery_issues_bp.route('/admin/delivery-issues')
@admin_required
def review_issues():
    """Review and manage delivery discrepancies"""
    status_filter = request.args.get('status', 'all')
    resolved_filter = request.args.get('filter', 'all')  # 'unresolved', 'all'
    
    query = DeliveryDiscrepancy.query
    
    if status_filter != 'all':
        query = query.filter_by(status=status_filter)
    
    # Apply resolved filter if specified
    if resolved_filter == 'unresolved':
        query = query.filter_by(is_resolved=False)
    
    issues = query.order_by(DeliveryDiscrepancy.reported_at.desc()).all()
    
    # Get all discrepancy types and resolutions for dynamic resolution dropdown
    discrepancy_types = DiscrepancyType.query.filter_by(is_active=True).order_by(DiscrepancyType.sort_order).all()
    
    # Build a dict of resolutions by discrepancy type
    resolutions_by_type = {}
    for dtype in discrepancy_types:
        resolutions = StockResolution.query.filter_by(
            discrepancy_type=dtype.name,
            is_active=True
        ).order_by(StockResolution.sort_order).all()
        resolutions_by_type[dtype.name] = resolutions
    
    return render_template('admin/review_delivery_issues.html',
                         issues=issues,
                         status_filter=status_filter,
                         resolved_filter=resolved_filter,
                         resolutions_by_type=resolutions_by_type)

@delivery_issues_bp.route('/admin/delivery-issues/<int:issue_id>/validate', methods=['POST'])
@admin_only_required
def validate_issue(issue_id):
    """Validate a reported delivery discrepancy"""
    try:
        discrepancy = DeliveryDiscrepancy.query.get_or_404(issue_id)
        
        if discrepancy.is_validated:
            flash(f'Issue #{issue_id} has already been validated', 'warning')
            return redirect(url_for('delivery_issues.review_issues'))
        
        note = request.form.get('note', '')
        
        # Mark as validated
        discrepancy.is_validated = True
        discrepancy.validated_by = current_user.username
        discrepancy.validated_at = datetime.utcnow()
        
        # Update status to 'review' only if BOTH validated AND resolved
        old_status = discrepancy.status
        if discrepancy.is_validated and discrepancy.is_resolved:
            discrepancy.status = 'review'
            new_status = 'review'
        else:
            discrepancy.status = 'reported'
            new_status = 'reported (validated)'
        
        create_event(
            discrepancy_id=discrepancy.id,
            event_type='validated',
            actor=current_user.username,
            note=note,
            old_value=old_status,
            new_value=new_status
        )
        
        db.session.commit()
        
        if discrepancy.status == 'review':
            flash(f'Issue #{issue_id} validated. Both validation and resolution complete - moved to REVIEW', 'success')
        else:
            flash(f'Issue #{issue_id} validated successfully. Awaiting resolution to complete.', 'success')
        return redirect(url_for('delivery_issues.review_issues'))
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error validating issue {issue_id}: {str(e)}")
        flash(f'Error validating issue: {str(e)}', 'error')
        return redirect(url_for('delivery_issues.review_issues'))

@delivery_issues_bp.route('/admin/delivery-issues/<int:issue_id>/resolve', methods=['POST'])
@admin_required
def resolve_issue(issue_id):
    """Resolve a delivery discrepancy"""
    try:
        discrepancy = DeliveryDiscrepancy.query.get_or_404(issue_id)
        
        if discrepancy.is_resolved:
            flash(f'Issue #{issue_id} has already been resolved', 'warning')
            if current_user.role == 'warehouse_manager':
                return redirect(url_for('delivery_issues.review_issues', filter='unresolved'))
            return redirect(url_for('delivery_issues.review_issues'))
        
        resolution_action = request.form.get('resolution_action')
        note = request.form.get('note', '')
        
        if not resolution_action:
            flash('Resolution action is required', 'error')
            if current_user.role == 'warehouse_manager':
                return redirect(url_for('delivery_issues.review_issues', filter='unresolved'))
            return redirect(url_for('delivery_issues.review_issues'))
        
        # Mark as resolved
        old_status = discrepancy.status
        discrepancy.is_resolved = True
        discrepancy.resolved_by = current_user.username
        discrepancy.resolved_at = datetime.utcnow()
        discrepancy.resolution_action = resolution_action
        
        # Update status to 'review' only if BOTH validated AND resolved
        if discrepancy.is_validated and discrepancy.is_resolved:
            discrepancy.status = 'review'
            new_status = 'review'
        else:
            discrepancy.status = 'reported'
            new_status = 'reported (resolved)'
        
        if note:
            if discrepancy.note:
                discrepancy.note += f"\n\nResolution Note: {note}"
            else:
                discrepancy.note = f"Resolution Note: {note}"
        
        create_event(
            discrepancy_id=discrepancy.id,
            event_type='resolved',
            actor=current_user.username,
            note=f'Resolution: {resolution_action}. {note}',
            old_value=old_status,
            new_value=new_status
        )
        
        db.session.commit()
        
        if discrepancy.status == 'review':
            flash(f'Issue #{issue_id} resolved with action: {resolution_action}. Both validation and resolution complete - moved to REVIEW', 'success')
        else:
            flash(f'Issue #{issue_id} resolved with action: {resolution_action}. Awaiting validation to complete.', 'success')
        
        # Redirect warehouse managers back to unresolved issues
        if current_user.role == 'warehouse_manager':
            return redirect(url_for('delivery_issues.review_issues', filter='unresolved'))
        return redirect(url_for('delivery_issues.review_issues'))
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error resolving issue {issue_id}: {str(e)}")
        flash(f'Error resolving issue: {str(e)}', 'error')
        # Redirect warehouse managers back to unresolved issues
        if current_user.role == 'warehouse_manager':
            return redirect(url_for('delivery_issues.review_issues', filter='unresolved'))
        return redirect(url_for('delivery_issues.review_issues'))

@delivery_issues_bp.route('/admin/delivery-issues/<int:issue_id>/close', methods=['POST'])
@admin_only_required
def close_issue(issue_id):
    """Close a delivery discrepancy that is in review status"""
    try:
        discrepancy = DeliveryDiscrepancy.query.get_or_404(issue_id)
        
        if discrepancy.status != 'review':
            flash(f'Issue #{issue_id} must be in review status to close', 'error')
            return redirect(url_for('delivery_issues.review_issues'))
        
        note = request.form.get('note', '')
        
        old_status = discrepancy.status
        discrepancy.status = 'closed'
        
        if note:
            if discrepancy.note:
                discrepancy.note += f"\n\nClosed Note: {note}"
            else:
                discrepancy.note = f"Closed Note: {note}"
        
        create_event(
            discrepancy_id=discrepancy.id,
            event_type='closed',
            actor=current_user.username,
            note=note,
            old_value=old_status,
            new_value='closed'
        )
        
        db.session.commit()
        
        flash(f'Issue #{issue_id} has been closed', 'success')
        return redirect(url_for('delivery_issues.review_issues'))
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error closing issue {issue_id}: {str(e)}")
        flash(f'Error closing issue: {str(e)}', 'error')
        return redirect(url_for('delivery_issues.review_issues'))

@delivery_issues_bp.route('/admin/delivery-issues/<int:issue_id>/delete', methods=['POST'])
@admin_only_required
def delete_issue(issue_id):
    """Delete a delivery discrepancy (admin only)"""
    try:
        discrepancy = DeliveryDiscrepancy.query.get_or_404(issue_id)
        
        # Delete all associated events first
        DeliveryDiscrepancyEvent.query.filter_by(discrepancy_id=issue_id).delete()
        
        # Store info for flash message before deleting
        invoice_no = discrepancy.invoice_no
        item_code = discrepancy.item_code_expected
        
        # Delete the discrepancy
        db.session.delete(discrepancy)
        db.session.commit()
        
        flash(f'Issue #{issue_id} (Invoice: {invoice_no}, Item: {item_code}) has been permanently deleted', 'success')
        return redirect(url_for('delivery_issues.review_issues'))
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error deleting issue {issue_id}: {str(e)}")
        flash(f'Error deleting issue: {str(e)}', 'error')
        return redirect(url_for('delivery_issues.review_issues'))

@delivery_issues_bp.route('/api/invoices/<invoice_no>/items')
@admin_required
def get_invoice_items(invoice_no):
    """API endpoint to get items for an invoice"""
    try:
        invoice = Invoice.query.get(invoice_no)
        items = InvoiceItem.query.filter_by(invoice_no=invoice_no).order_by(InvoiceItem.item_name).all()
        
        # Get delivery date
        delivery_date = None
        if invoice:
            if invoice.delivered_at:
                delivery_date = invoice.delivered_at.strftime('%d/%m/%Y')
            elif invoice.shipped_at:
                delivery_date = invoice.shipped_at.strftime('%d/%m/%Y')
        
        items_list = []
        for item in items:
            # Get picker and time from tracking
            tracking = ItemTimeTracking.query.filter_by(
                invoice_no=invoice_no,
                item_code=item.item_code
            ).order_by(ItemTimeTracking.item_completed.desc()).first()
            
            picker = None
            picked_time = None
            if tracking:
                picker = tracking.picker_username
                if tracking.item_completed:
                    import pytz
                    athens_tz = pytz.timezone('Europe/Athens')
                    utc_dt = pytz.UTC.localize(tracking.item_completed)
                    athens_dt = utc_dt.astimezone(athens_tz)
                    picked_time = athens_dt.strftime('%d/%m/%Y %H:%M')
            elif invoice and invoice.assigned_to:
                picker = invoice.assigned_to
            
            items_list.append({
                'item_code': item.item_code,
                'item_name': item.item_name,
                'qty': item.qty,
                'picked_qty': item.picked_qty,
                'location': item.location,
                'unit_type': item.unit_type,
                'pack': item.pack,
                'picker': picker,
                'picked_time': picked_time
            })
        
        return jsonify({
            'items': items_list,
            'delivery_date': delivery_date
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@delivery_issues_bp.route('/api/resolutions/<discrepancy_type>')
@admin_required
def get_resolutions(discrepancy_type):
    """API endpoint to get resolutions for a discrepancy type"""
    try:
        resolutions = StockResolution.query.filter_by(
            discrepancy_type=discrepancy_type,
            is_active=True
        ).order_by(StockResolution.sort_order).all()
        
        return jsonify({
            'resolutions': [{'name': r.resolution_name, 'value': r.resolution_name} for r in resolutions]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@delivery_issues_bp.route('/admin/delivery-issues/<int:issue_id>')
@admin_required
def view_issue(issue_id):
    """View detailed information about a specific delivery discrepancy"""
    discrepancy = DeliveryDiscrepancy.query.get_or_404(issue_id)
    
    photos = []
    if discrepancy.photo_paths:
        try:
            photos = json.loads(discrepancy.photo_paths)
        except:
            photos = []
    
    # Get available resolutions for this discrepancy type
    resolutions = StockResolution.query.filter_by(
        discrepancy_type=discrepancy.discrepancy_type,
        is_active=True
    ).order_by(StockResolution.sort_order).all()
    
    return render_template('admin/view_delivery_issue.html',
                         discrepancy=discrepancy,
                         photos=photos,
                         resolutions=resolutions)

@delivery_issues_bp.route('/picker/delivery-issues')
@login_required
def picker_review_issues():
    """Allow pickers to review delivery issues for orders they picked"""
    if current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'error')
        return redirect(url_for('index'))
    
    # Get validated filter (default to unvalidated)
    validated_filter = request.args.get('filter', 'unvalidated')
    
    query = DeliveryDiscrepancy.query.filter_by(picker_username=current_user.username)
    
    # Apply validated filter
    if validated_filter == 'unvalidated':
        query = query.filter_by(is_validated=False)
    elif validated_filter == 'validated':
        query = query.filter_by(is_validated=True)
    # 'all' shows everything
    
    issues = query.order_by(DeliveryDiscrepancy.reported_at.desc()).all()
    
    return render_template('picker/review_delivery_issues.html',
                         issues=issues,
                         validated_filter=validated_filter)

@delivery_issues_bp.route('/picker/delivery-issues/<int:issue_id>/validate')
@login_required
def picker_validate_form(issue_id):
    """Show validation form for picker"""
    if current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'error')
        return redirect(url_for('index'))
    
    discrepancy = DeliveryDiscrepancy.query.get_or_404(issue_id)
    
    # Verify this issue belongs to the current picker
    if discrepancy.picker_username != current_user.username:
        flash('You can only validate issues for orders you picked.', 'error')
        return redirect(url_for('delivery_issues.picker_review_issues'))
    
    if discrepancy.is_validated:
        flash(f'Issue #{issue_id} has already been validated', 'warning')
        return redirect(url_for('delivery_issues.picker_review_issues'))
    
    return render_template('picker/validate_issue.html', issue=discrepancy)

@delivery_issues_bp.route('/picker/delivery-issues/<int:issue_id>/validate', methods=['POST'])
@login_required
def picker_validate_submit(issue_id):
    """Submit picker validation with reason and notes"""
    if current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'error')
        return redirect(url_for('index'))
    
    try:
        discrepancy = DeliveryDiscrepancy.query.get_or_404(issue_id)
        
        # Verify this issue belongs to the current picker
        if discrepancy.picker_username != current_user.username:
            flash('You can only validate issues for orders you picked.', 'error')
            return redirect(url_for('delivery_issues.picker_review_issues'))
        
        if discrepancy.is_validated:
            flash(f'Issue #{issue_id} has already been validated', 'warning')
            return redirect(url_for('delivery_issues.picker_review_issues'))
        
        reason = request.form.get('reason', '').strip()
        note = request.form.get('note', '').strip()
        
        if not reason:
            flash('Validation reason is required', 'error')
            return redirect(url_for('delivery_issues.picker_validate_form', issue_id=issue_id))
        
        # Mark as validated
        discrepancy.is_validated = True
        discrepancy.validated_by = current_user.username
        discrepancy.validated_at = datetime.utcnow()
        
        # Append validation notes to the discrepancy note field
        validation_note = f"\n\n{current_user.username} - Validation Reason: {reason}"
        if note:
            validation_note += f"\nNotes: {note}"
        
        if discrepancy.note:
            discrepancy.note += validation_note
        else:
            discrepancy.note = validation_note.strip()
        
        # Update status to 'review' only if BOTH validated AND resolved
        old_status = discrepancy.status
        if discrepancy.is_validated and discrepancy.is_resolved:
            discrepancy.status = 'review'
            new_status = 'review'
        else:
            discrepancy.status = 'reported'
            new_status = 'reported (validated)'
        
        # Create event with reason and note
        event_note = f"Reason: {reason}"
        if note:
            event_note += f"\nNotes: {note}"
        
        create_event(
            discrepancy_id=discrepancy.id,
            event_type='validated',
            actor=current_user.username,
            note=event_note,
            old_value=old_status,
            new_value=new_status
        )
        
        db.session.commit()
        
        flash(f'Issue #{issue_id} validated successfully', 'success')
        return redirect(url_for('delivery_issues.picker_review_issues'))
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error validating issue {issue_id}: {str(e)}")
        flash(f'Error validating issue: {str(e)}', 'error')
        return redirect(url_for('delivery_issues.picker_review_issues'))
