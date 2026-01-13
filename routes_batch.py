"""
Route handlers for the batch picking system
"""
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, session, current_app
from sqlalchemy import text
from flask_login import login_required, current_user
from sqlalchemy import and_, or_, func, desc, asc
from timezone_utils import get_local_time, utc_now_for_db

from app import db
from models import User, Invoice, InvoiceItem, PickingException, BatchPickingSession, BatchSessionInvoice, BatchPickedItem, Setting, ActivityLog, OrderTimeBreakdown, ItemTimeTracking
from sorting_utils import sort_batch_items, get_sorting_config

# Create a blueprint for batch picking routes
batch_bp = Blueprint('batch', __name__)

# Helper functions for sequential batch picking
def get_sorted_batch_invoices(batch_session):
    """Get all invoices in a batch sorted by routing number descending"""
    return db.session.query(BatchSessionInvoice).join(Invoice).filter(
        BatchSessionInvoice.batch_session_id == batch_session.id
    ).order_by(func.cast(Invoice.routing, db.Numeric).desc().nulls_last()).all()

def get_remaining_locked_items_count(batch_session, invoice_no):
    """Get count of remaining locked items for a specific invoice in this batch"""
    return db.session.query(InvoiceItem).filter(
        InvoiceItem.invoice_no == invoice_no,
        InvoiceItem.locked_by_batch_id == batch_session.id,
        InvoiceItem.is_picked == False,
        InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending'])
    ).count()

def find_next_incomplete_invoice_index(batch_session, invoice_order):
    """Find the index of the next invoice with remaining locked items - optimized with single query"""
    # Get all invoices with remaining items in ONE query instead of checking each one
    invoices_with_items = db.session.query(InvoiceItem.invoice_no).filter(
        InvoiceItem.locked_by_batch_id == batch_session.id,
        InvoiceItem.is_picked == False,
        InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending'])
    ).distinct().all()
    
    # Create a set for fast lookup
    invoices_with_remaining = {row[0] for row in invoices_with_items}
    
    # Find the first invoice in the order that has remaining items
    for idx, batch_inv in enumerate(invoice_order):
        if batch_inv.invoice_no in invoices_with_remaining:
            return idx
    return None

def clear_batch_cache(batch_id):
    """Clear session cache for a batch to force regeneration"""
    fixed_batch_key = f'batch_items_{batch_id}'
    if fixed_batch_key in session:
        session.pop(fixed_batch_key, None)
        current_app.logger.info(f"🧹 Cleared batch cache for batch {batch_id}")

@batch_bp.route('/admin/batch/manage')
@login_required
def batch_picking_manage():
    """Admin page to manage batch picking sessions"""
    # Only admin users can access this page
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))

    # Get active batch sessions
    active_sessions = BatchPickingSession.query.filter(
        BatchPickingSession.status.in_(['Created', 'picking', 'Active', 'Paused'])
    ).order_by(BatchPickingSession.created_at.desc()).all()

    # Get completed batch sessions
    completed_sessions = BatchPickingSession.query.filter_by(
        status='Completed'
    ).order_by(BatchPickingSession.created_at.desc()).limit(10).all()

    # Get pickers for the assign dropdown
    pickers = User.query.filter_by(role='picker').all()

    return render_template('batch_picking_manage.html',
                          active_sessions=active_sessions,
                          completed_sessions=completed_sessions,
                          pickers=pickers)

@batch_bp.route('/admin/batch/edit/<int:batch_id>', methods=['GET', 'POST'])
@login_required
def batch_edit(batch_id):
    """Edit an existing batch picking session"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    batch = BatchPickingSession.query.get_or_404(batch_id)
    
    # Don't allow editing completed batches
    if batch.status == 'Completed':
        flash('Cannot edit completed batches.', 'warning')
        return redirect(url_for('batch.batch_picking_manage'))
    
    if request.method == 'POST':
        # Store old criteria for comparison
        old_zones = batch.zones
        old_corridors = batch.corridors
        old_unit_types = batch.unit_types
        old_picking_mode = batch.picking_mode
        
        # Update batch details
        batch.name = request.form.get('session_name', batch.name)
        batch.zones = request.form.get('zones', batch.zones)
        batch.corridors = request.form.get('corridors', batch.corridors)
        batch.unit_types = request.form.get('unit_types', batch.unit_types)
        batch.picking_mode = request.form.get('picking_mode', batch.picking_mode)
        
        # 🔧 FIXED: Clear cache when picking mode changes
        if old_picking_mode != batch.picking_mode:
            clear_batch_cache(batch_id)
            # Reset indices when switching modes
            batch.current_item_index = 0
            batch.current_invoice_index = 0
            current_app.logger.info(f"🔄 MODE SWITCH: Batch {batch_id} changed from {old_picking_mode} to {batch.picking_mode} - cache cleared and indices reset")
        
        # Handle assigned picker
        assigned_picker = request.form.get('assigned_to')
        if assigned_picker and assigned_picker != 'none':
            batch.assigned_to = assigned_picker
        else:
            batch.assigned_to = None
            
        try:
            # Check if criteria changed and update locks if needed
            criteria_changed = (
                old_zones != batch.zones or 
                old_corridors != batch.corridors or 
                old_unit_types != batch.unit_types or
                old_picking_mode != batch.picking_mode
            )
            
            db.session.commit()
            
            if criteria_changed:
                # 🔧 FIXED: Clear cache when criteria changes
                clear_batch_cache(batch_id)
                current_app.logger.info(f"🔄 CRITERIA CHANGED: Cleared cache for batch {batch_id}")
                
                # Update locks based on new criteria
                from batch_locking_utils import update_batch_locks_on_edit
                
                new_zones_list = batch.zones.split(',') if batch.zones else []
                new_corridors_list = batch.corridors.split(',') if batch.corridors and batch.corridors.strip() else []
                new_unit_types_list = batch.unit_types.split(',') if batch.unit_types and batch.unit_types.strip() else []
                
                # Get current batch invoices
                batch_invoices = [bi.invoice_no for bi in batch.invoices]
                
                locked_count = update_batch_locks_on_edit(
                    batch_id=batch.id,
                    new_zones_list=new_zones_list,
                    new_corridors_list=new_corridors_list if new_corridors_list else None,
                    new_unit_types_list=new_unit_types_list if new_unit_types_list else None,
                    new_invoice_nos=batch_invoices if batch_invoices else None
                )
                
                flash(f'Batch "{batch.name}" updated successfully! Updated locks for {locked_count} items.', 'success')
            else:
                flash(f'Batch "{batch.name}" updated successfully!', 'success')
            
            return redirect(url_for('batch.batch_picking_manage'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating batch: {str(e)}', 'danger')
    
    # Get available pickers for assignment
    pickers = User.query.filter_by(role='picker').all()
    
    # Get available zones
    zones_query = db.session.execute(text("""
        SELECT DISTINCT zone 
        FROM invoice_items 
        WHERE zone IS NOT NULL AND zone != ''
        ORDER BY zone
    """))
    available_zones = [row[0] for row in zones_query]
    
    # Get available corridors
    corridors_query = db.session.execute(text("""
        SELECT DISTINCT corridor 
        FROM invoice_items 
        WHERE corridor IS NOT NULL AND corridor != ''
        ORDER BY corridor
    """))
    available_corridors = [row[0] for row in corridors_query]
    
    # Get available unit types
    unit_types_query = db.session.execute(text("""
        SELECT DISTINCT unit_type 
        FROM invoice_items 
        WHERE unit_type IS NOT NULL AND unit_type != ''
        ORDER BY unit_type
    """))
    available_unit_types = [row[0] for row in unit_types_query]
    
    return render_template('batch_edit.html', 
                         batch=batch, 
                         pickers=pickers,
                         available_zones=available_zones,
                         available_corridors=available_corridors,
                         available_unit_types=available_unit_types)

@batch_bp.route('/admin/batch/add-invoices/<int:batch_id>', methods=['GET', 'POST'])
@login_required
def add_invoices_to_batch(batch_id):
    """Add invoices to an existing batch picking session"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    batch = BatchPickingSession.query.get_or_404(batch_id)
    
    # Don't allow adding to completed batches
    if batch.status == 'Completed':
        flash('Cannot add invoices to completed batches.', 'warning')
        return redirect(url_for('batch.batch_picking_manage'))
    
    if request.method == 'POST':
        # Get selected invoices to add
        invoice_nos = request.form.getlist('selected_invoices')
        
        if not invoice_nos:
            flash('Please select at least one invoice to add.', 'warning')
            return redirect(url_for('batch.add_invoices_to_batch', batch_id=batch_id))
        
        # Check for conflicts with other batches and validate corridor criteria
        from batch_locking_utils import check_batch_conflicts, lock_items_for_batch
        
        zones_list = batch.zones.split(',') if batch.zones else []
        corridors_list = batch.corridors.split(',') if batch.corridors else []
        
        # Validate that invoices have items matching batch corridor criteria
        valid_invoices = []
        invalid_invoices = []
        
        for invoice_no in invoice_nos:
            # Build filter conditions for matching items
            matching_filter_conditions = [
                InvoiceItem.invoice_no == invoice_no,
                InvoiceItem.zone.in_(zones_list)
            ]
            
            # Add corridor filter only if corridors are specified
            if corridors_list:
                matching_filter_conditions.append(InvoiceItem.corridor.in_(corridors_list))
            
            # Check if this invoice has items in the batch corridors
            matching_items = db.session.query(InvoiceItem).filter(
                and_(*matching_filter_conditions)
            ).count()
            
            # Get total items in invoice for this zone
            total_items = db.session.query(InvoiceItem).filter(
                InvoiceItem.invoice_no == invoice_no,
                InvoiceItem.zone.in_(zones_list)
            ).count()
            
            if matching_items > 0:
                valid_invoices.append({
                    'invoice_no': invoice_no, 
                    'matching_items': matching_items,
                    'total_items': total_items
                })
                if matching_items < total_items:
                    # This invoice has some items outside corridor criteria
                    outside_items = total_items - matching_items
                    flash(f'Warning: Invoice {invoice_no} has {outside_items} items outside batch corridors {corridors_list}. Only {matching_items} items will be included in batch.', 'warning')
            else:
                invalid_invoices.append(invoice_no)
        
        # Reject invoices with no matching items
        if invalid_invoices:
            flash(f'❌ Cannot add invoices {", ".join(invalid_invoices)} - they contain no items in batch corridors {corridors_list}.', 'danger')
        
        if not valid_invoices:
            flash('❌ None of the selected invoices contain items matching the batch corridor criteria.', 'danger')
            return redirect(url_for('batch.add_invoices_to_batch', batch_id=batch_id))
        
        # Update invoice_nos to only include valid ones
        invoice_nos = [inv['invoice_no'] for inv in valid_invoices]
        
        conflicts = check_batch_conflicts(
            zones_list=zones_list,
            corridors_list=corridors_list,
            invoice_nos=invoice_nos
        )
        
        if conflicts['has_conflicts']:
            warning_msg = f"Warning: {conflicts['total_conflicting_items']} items are already locked by other batches:"
            for conflict in conflicts['conflicts']:
                warning_msg += f"\n• {len(conflict['items'])} items locked by {conflict['batch_name']}"
            flash(warning_msg, 'warning')
        
        try:
            # Sort new invoices by routing number descending before adding
            new_invoice_data = db.session.query(Invoice.invoice_no, Invoice.routing).filter(
                Invoice.invoice_no.in_(invoice_nos)
            ).all()
            
            def get_routing_sort_key(invoice_tuple):
                invoice_no, routing = invoice_tuple
                if routing is None:
                    return -1
                try:
                    return float(routing)
                except (ValueError, TypeError):
                    return -1
            
            sorted_new_invoices = sorted(new_invoice_data, key=get_routing_sort_key, reverse=True)
            sorted_invoice_nos = [invoice[0] for invoice in sorted_new_invoices]
            
            # Add invoices to batch in sorted order
            added_count = 0
            for invoice_no in sorted_invoice_nos:
                # Check if invoice is already in this batch
                existing = BatchSessionInvoice.query.filter_by(
                    batch_session_id=batch_id,
                    invoice_no=invoice_no
                ).first()
                
                if not existing:
                    batch_invoice = BatchSessionInvoice(
                        batch_session_id=batch_id,
                        invoice_no=invoice_no
                    )
                    db.session.add(batch_invoice)
                    added_count += 1
            
            db.session.commit()
            
            # Lock items for the new invoices
            if added_count > 0:
                unit_types_list = batch.unit_types.split(',') if batch.unit_types else []
                locked_items_count = lock_items_for_batch(
                    batch_id=batch_id,
                    zones_list=zones_list,
                    corridors_list=corridors_list,
                    unit_types_list=unit_types_list if unit_types_list else None,
                    invoice_nos=sorted_invoice_nos
                )
                
                # Clear cache to force regeneration when adding invoices to existing batch
                if batch.picking_mode == 'Sequential':
                    from flask import session
                    fixed_batch_key = 'batch_items_' + str(batch_id)
                    if fixed_batch_key in session:
                        session.pop(fixed_batch_key, None)
                        current_app.logger.info(f"🔄 CACHE CLEARED: Added {added_count} invoices to sequential batch {batch_id}, cleared cache for regeneration")
                
                flash(f'Successfully added {added_count} invoices to batch "{batch.name}". Locked {locked_items_count} items.', 'success')
            else:
                flash('No new invoices were added (all selected invoices are already in this batch).', 'info')
                
            return redirect(url_for('batch.batch_picking_manage'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding invoices to batch: {str(e)}', 'danger')
    
    # GET request - show invoice selection interface
    # Get current batch zones and corridors
    zones_list = batch.zones.split(',') if batch.zones else []
    corridors_list = batch.corridors.split(',') if batch.corridors else []
    
    # Get invoices already in this batch
    existing_invoices = [bi.invoice_no for bi in batch.invoices]
    
    # Find available invoices that match batch criteria but aren't already in the batch
    filter_conditions = [
        InvoiceItem.zone.in_(zones_list),
        InvoiceItem.is_picked == False,
        InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending']),
        InvoiceItem.locked_by_batch_id == None,  # Only unlocked items
        ~Invoice.invoice_no.in_(existing_invoices)  # Exclude invoices already in batch
    ]
    
    if corridors_list:
        filter_conditions.append(InvoiceItem.corridor.in_(corridors_list))
    
    # Get available invoices with item counts and total quantities
    available_invoices = db.session.query(
        Invoice.invoice_no,
        Invoice.customer_name,
        Invoice.routing,
        func.count(InvoiceItem.item_code).label('item_count'),
        func.sum(InvoiceItem.qty).label('total_qty')
    ).join(InvoiceItem).filter(
        and_(*filter_conditions)
    ).group_by(
        Invoice.invoice_no, Invoice.customer_name, Invoice.routing
    ).order_by(func.cast(Invoice.routing, db.Numeric).desc().nulls_last()).all()
    
    return render_template('batch_add_invoices.html',
                         batch=batch,
                         available_invoices=available_invoices,
                         zones=zones_list,
                         corridors=corridors_list)

@batch_bp.route('/admin/batch/delete/<int:batch_id>', methods=['POST'])
@login_required
def batch_delete(batch_id):
    """Delete a batch picking session"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    batch = BatchPickingSession.query.get_or_404(batch_id)
    
    # Don't allow deleting completed batches with picked items
    if batch.status == 'Completed':
        flash('Cannot delete completed batches.', 'warning')
        return redirect(url_for('batch.batch_picking_manage'))
    
    try:
        # Delete related batch session invoices first
        BatchSessionInvoice.query.filter_by(batch_session_id=batch_id).delete()
        
        # Delete any picked items for this batch
        BatchPickedItem.query.filter_by(batch_session_id=batch_id).delete()
        
        # Delete the batch itself
        batch_name = batch.name
        db.session.delete(batch)
        db.session.commit()
        
        flash(f'Batch "{batch_name}" deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting batch: {str(e)}', 'danger')
    
    return redirect(url_for('batch.batch_picking_manage'))

@batch_bp.route('/admin/batch/simple', methods=['GET', 'POST'])
@login_required
def batch_picking_create_simple():
    """Simple admin page to create a new batch picking session"""
    # Only admin users can access this page
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        # Get form data
        name = request.form.get('name')
        zones = request.form.get('zones')
        picking_mode = request.form.get('picking_mode')
        
        if not name or not zones or not picking_mode:
            flash('Please provide a name, zones, and picking mode for the batch picking session.', 'warning')
            return redirect(url_for('batch.batch_picking_create_simple'))
        
        # Generate a unique batch number
        from batch_utils import generate_batch_number
        batch_number = generate_batch_number()
        
        # Create a new batch picking session
        batch_session = BatchPickingSession(
            name=name,
            batch_number=batch_number,
            zones=zones,
            created_by=current_user.username,
            picking_mode=picking_mode
        )
        
        # Find all invoices with items in these zones
        zone_list = [zone.strip() for zone in zones.split(',')]
        
        # Check for batch conflicts before creating the batch
        from batch_locking_utils import check_batch_conflicts
        conflict_check = check_batch_conflicts(zone_list, None, None)
        
        if conflict_check['has_conflicts']:
            conflict_msg = f"Cannot create batch: {conflict_check['total_conflicting_items']} items in these zones are already locked by other active batches."
            for conflict in conflict_check['conflicts']:
                conflict_msg += f" Batch #{conflict['batch_id']} ({conflict['batch_name']}) has locked {len(conflict['items'])} items."
            flash(conflict_msg, 'danger')
            return redirect(url_for('batch.batch_picking_create_simple'))
        
        # Find invoices that have items in the specified zones (only unlocked items)
        invoices_with_items = db.session.query(Invoice.invoice_no, Invoice.routing).join(InvoiceItem).filter(
            InvoiceItem.zone.in_(zone_list),
            InvoiceItem.is_picked == False,
            InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending']),
            InvoiceItem.locked_by_batch_id == None,  # Only unlocked items
            Invoice.status.in_(['not_started', 'picking'])
        ).group_by(Invoice.invoice_no, Invoice.routing).all()
        
        # Sort by routing number descending for sequential batch picking order
        # Handle numeric sorting properly in Python since PostgreSQL routing is numeric
        def get_routing_sort_key(invoice_tuple):
            invoice_no, routing = invoice_tuple
            if routing is None:
                return -1  # Put None values at the end
            try:
                return float(routing)
            except (ValueError, TypeError):
                return -1  # Put invalid values at the end
        
        sorted_invoices = sorted(invoices_with_items, key=get_routing_sort_key, reverse=True)
        
        # Extract invoice numbers in correct order
        invoice_numbers = [invoice[0] for invoice in sorted_invoices]
        
        # Debug logging to verify sort order
        current_app.logger.warning(f"🔍 SORTED INVOICE ORDER:")
        for invoice_no, routing in sorted_invoices:
            current_app.logger.warning(f"  {invoice_no}: routing {routing}")
        current_app.logger.warning(f"🔍 FINAL INVOICE LIST: {invoice_numbers}")
        
        if not invoice_numbers:
            flash('No invoices found with available (unlocked) items in the specified zones.', 'warning')
            return redirect(url_for('batch.batch_picking_create_simple'))
        
        try:
            # Add the batch session to the database
            db.session.add(batch_session)
            db.session.flush()  # Get the ID without committing
            
            # Add invoices to the batch session
            for invoice_no in invoice_numbers:
                batch_invoice = BatchSessionInvoice(
                    batch_session_id=batch_session.id,
                    invoice_no=invoice_no
                )
                db.session.add(batch_invoice)
            
            # Lock items for this batch to prevent conflicts
            from batch_locking_utils import lock_items_for_batch
            corridors_list = [c.strip() for c in batch_session.corridors.split(',')] if batch_session.corridors and batch_session.corridors.strip() else None
            unit_types_list = [u.strip() for u in batch_session.unit_types.split(',')] if batch_session.unit_types and batch_session.unit_types.strip() else None
            try:
                locked_count = lock_items_for_batch(
                    batch_session.id, 
                    zone_list, 
                    corridors_list,
                    unit_types_list,
                    invoice_numbers
                )
                current_app.logger.info(f"Locked {locked_count} items for batch {batch_session.id}")
            except Exception as lock_error:
                current_app.logger.error(f"Failed to lock items for batch {batch_session.id}: {str(lock_error)}")
                # Continue with batch creation even if locking fails, but log the error
            
            # Commit changes
            db.session.commit()
            
            flash(f'Batch picking session "{name}" created successfully with {len(invoice_numbers)} invoices.', 'success')
            return redirect(url_for('batch.batch_picking_view', batch_id=batch_session.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating batch picking session: {str(e)}', 'danger')
            return redirect(url_for('batch.batch_picking_create_simple'))
    
    # GET request - show the form
    return render_template('batch_picking_create.html')

@batch_bp.route('/admin/batch/filter', methods=['GET', 'POST'])
@login_required
def batch_picking_filter():
    """Admin page to filter invoices for a batch"""
    # Only admin users can access this page
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get all zones for the filter dropdown
    zones = db.session.query(InvoiceItem.zone).filter(
        InvoiceItem.zone != None,
        InvoiceItem.zone != ''
    ).distinct().order_by(InvoiceItem.zone).all()
    
    # Extract zone values
    zone_list = [zone[0] for zone in zones if zone[0]]
    
    # Complete list of allowed corridors as specified in requirements
    allowed_corridors = ["09", "10", "11", "12", "13", "14", "20", "30", "31", "40", "50", "70", "80", "90"]
    
    # Debug logging
    current_app.logger.info(f"Batch filter: Found {len(zone_list)} zones: {zone_list}")
    current_app.logger.info(f"Corridors being sent to template: {allowed_corridors}")
    current_app.logger.info(f"Corridor 70 included: {'70' in allowed_corridors}")
    
    # Get available unit types
    unit_types_query = db.session.execute(text("""
        SELECT DISTINCT unit_type 
        FROM invoice_items 
        WHERE unit_type IS NOT NULL AND unit_type != ''
        ORDER BY unit_type
    """))
    available_unit_types = [row[0] for row in unit_types_query]
    
    return render_template('batch_picking_filter.html', 
                          zones=zone_list, 
                          corridors=allowed_corridors, 
                          unit_types=available_unit_types)

@batch_bp.route('/admin/batch/filter-invoices', methods=['POST'])
@login_required
def filter_invoices_for_batch():
    """Filter invoices for batch picking and show selection interface"""
    # Only admin users can access this page
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get form data with proper zone, corridor, and unit type cleaning
    zones_raw = request.form.getlist('zones')
    corridors_raw = request.form.getlist('corridors')
    unit_types_raw = request.form.getlist('unit_types')
    picking_mode = request.form.get('picking_mode')
    session_name = request.form.get('session_name')
    include_partially_picked = request.form.get('include_partially_picked') == 'true'
    
    # Step 1: Clean zone format - remove any brackets or braces
    zones = []
    for zone in zones_raw:
        zone_cleaned = str(zone).replace("{", "").replace("}", "").replace("[", "").replace("]", "").strip().strip("'").strip('"')
        if zone_cleaned:
            zones.append(zone_cleaned)
    
    # Step 2: Clean corridor format
    corridors = []
    for corridor in corridors_raw:
        corridor_cleaned = str(corridor).replace("{", "").replace("}", "").replace("[", "").replace("]", "").strip().strip("'").strip('"')
        if corridor_cleaned:
            corridors.append(corridor_cleaned)
    
    # Step 3: Clean unit type format
    unit_types = []
    for unit_type in unit_types_raw:
        unit_type_cleaned = str(unit_type).replace("{", "").replace("}", "").replace("[", "").replace("]", "").strip().strip("'").strip('"')
        if unit_type_cleaned:
            unit_types.append(unit_type_cleaned)
    
    # Debug logging for zone, corridor, and unit type processing
    current_app.logger.info(f"Zones received: {zones_raw}")
    current_app.logger.info(f"Zones cleaned: {zones}")
    current_app.logger.info(f"Corridors received: {corridors_raw}")
    current_app.logger.info(f"Corridors cleaned: {corridors}")
    current_app.logger.info(f"Unit types received: {unit_types_raw}")
    current_app.logger.info(f"Unit types cleaned: {unit_types}")
    
    include_partial = request.form.get('include_partial') == 'on' or include_partially_picked
    
    current_app.logger.info(f"Include partial form value: '{request.form.get('include_partial')}'")
    current_app.logger.info(f"Include partially picked: {include_partially_picked}")
    current_app.logger.info(f"Final include_partial value: {include_partial}")
    
    if not zones:
        flash('Please select at least one zone to filter invoices.', 'warning')
        return redirect(url_for('batch.batch_picking_filter'))
    
    # Step 2: Get all invoices with items in selected zones first, then filter items later
    # This ensures we don't exclude entire invoices just because some items are locked
    active_batch_ids = [batch.id for batch in db.session.query(BatchPickingSession).filter(
        BatchPickingSession.status.in_(['Created', 'In Progress', 'Assigned'])
    ).all()]
    
    current_app.logger.info(f"Active batches: {len(active_batch_ids)} batches: {active_batch_ids}")
    
    # Find ALL invoices that have items in selected zones (regardless of lock status)
    base_filter_conditions = [
        InvoiceItem.zone.in_(zones)
    ]
    
    # Add corridor filter if corridors were selected
    if corridors:
        base_filter_conditions.append(InvoiceItem.corridor.in_(corridors))
    
    # Add unit type filter if unit types were selected
    if unit_types:
        base_filter_conditions.append(InvoiceItem.unit_type.in_(unit_types))
    
    query = db.session.query(
        Invoice
    ).join(
        InvoiceItem
    ).filter(
        and_(*base_filter_conditions)
    )
    
    if not include_partial:
        # Only include invoices that haven't been picked at all
        query = query.filter(Invoice.status == 'not_started')
    else:
        # Include partially picked invoices too
        query = query.filter(Invoice.status.in_(['not_started', 'picking', 'awaiting_batch_items']))
    
    # Only include each invoice once
    query = query.group_by(Invoice.invoice_no)
    
    # Execute the query
    invoices = query.all()
    
    # Calculate eligible item counts for each invoice based on selected zones and corridors
    # Filter out invoices that have no eligible items in the selected corridors
    # Also exclude items locked by other active batches
    filtered_invoices = []
    for invoice in invoices:
        item_filter_conditions = [
            InvoiceItem.invoice_no == invoice.invoice_no,
            InvoiceItem.zone.in_(zones),
            InvoiceItem.is_picked == False,
            InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending'])
        ]
        
        # Add corridor filter if corridors were selected
        if corridors:
            item_filter_conditions.append(InvoiceItem.corridor.in_(corridors))
        
        # Exclude items locked by active batches in item count calculation
        if active_batch_ids:
            item_filter_conditions.append(
                db.or_(
                    InvoiceItem.locked_by_batch_id.is_(None),
                    ~InvoiceItem.locked_by_batch_id.in_(active_batch_ids)
                )
            )
        
        # Get both count and total quantity of eligible items
        eligible_items_query = db.session.query(
            func.count(InvoiceItem.item_code).label('item_count'),
            func.sum(InvoiceItem.qty).label('total_qty')
        ).filter(
            and_(*item_filter_conditions)
        ).first()
        
        eligible_items = eligible_items_query.item_count or 0
        total_qty = eligible_items_query.total_qty or 0
        
        current_app.logger.info(f"Invoice {invoice.invoice_no}: {eligible_items} eligible items ({total_qty} total quantity)")
        
        # Only include invoices that have eligible items in the selected corridors
        if eligible_items > 0:
            # Add both eligible item count and total quantity as dynamic attributes
            invoice.eligible_item_count = eligible_items
            invoice.eligible_total_qty = total_qty
            filtered_invoices.append(invoice)
    
    # Use the filtered list of invoices
    invoices = filtered_invoices
    
    # Debug: Log the final invoice list being passed to template
    current_app.logger.info(f"Final invoice list for template: {len(invoices)} invoices")
    for inv in invoices:
        current_app.logger.info(f"  - {inv.invoice_no}: {inv.customer_name}, items: {getattr(inv, 'eligible_item_count', 'Unknown')}")
    
    if not invoices:
        if corridors:
            flash(f'No invoices found with items in zones {", ".join(zones)} and corridors {", ".join(corridors)}.', 'warning')
        else:
            flash(f'No invoices found with items in the selected zones {", ".join(zones)}.', 'warning')
        return redirect(url_for('batch.batch_picking_filter'))
    
    # Get picker list for assignment
    pickers = User.query.filter_by(role='picker').all()
    
    # Create a default session name based on zones and timestamp
    now = get_local_time().strftime('%Y-%m-%d_%H:%M')
    zone_prefix = zones[0] if len(zones) == 1 else f"{zones[0]}_Plus_{len(zones)-1}"
    default_name = f"{zone_prefix}_Batch_{now}"
    
    # Pass invoices to the create batch page for selection
    return render_template('batch_picking_create.html',
                          invoices=invoices,
                          selected_zones=zones,
                          selected_corridors=corridors,
                          selected_unit_types=unit_types,
                          picking_mode=picking_mode,
                          session_name=session_name,
                          include_partially_picked=include_partially_picked,
                          pickers=pickers,
                          default_name=default_name)

@batch_bp.route('/admin/batch/create', methods=['POST'])
@login_required
def batch_picking_create():
    """Create a new batch picking session from selected invoices"""
    # Only admin users can access this page
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get form data
    name = request.form.get('session_name')  # Fix: use correct field name
    zones = request.form.getlist('zones')  # Fix: get zones as list
    corridors = request.form.getlist('corridors')  # Get corridors as list
    unit_types = request.form.getlist('unit_types')  # Get unit types as list
    picking_mode = request.form.get('picking_mode')
    # Picker assignment removed from creation - can be assigned later
    invoice_nos = request.form.getlist('selected_invoices')  # Fix: use correct field name
    
    # Debug logging
    current_app.logger.warning(f"🔍 BATCH CREATE START: name={name}, zones={zones}, corridors={corridors}, invoices={len(invoice_nos) if invoice_nos else 0}")
    
    # If name is empty, generate a default one
    if not name:
        now = get_local_time()
        name = f"BATCH_{now.strftime('%Y-%m-%d-%H:%M:%S')}"
        current_app.logger.warning(f"🔧 AUTO-GENERATED NAME: {name}")
    
    if not zones or not invoice_nos or not picking_mode:
        flash('Please select zones, picking mode, and at least one invoice.', 'warning')
        return redirect(url_for('batch.batch_picking_filter'))
    
    # Generate a unique batch number
    from batch_utils import generate_batch_number
    batch_number = generate_batch_number()
    
    # Fix zone format - ensure clean string storage
    clean_zones = []
    for zone in zones:
        # Strip any curly braces or brackets and clean the zone name
        clean_zone = str(zone).strip('{}[]').strip()
        if clean_zone:
            clean_zones.append(clean_zone)
    
    zones_string = ','.join(clean_zones)
    
    # Fix corridor format - ensure clean string storage
    clean_corridors = []
    for corridor in corridors:
        # Strip any curly braces or brackets and clean the corridor name
        clean_corridor = str(corridor).strip('{}[]').strip()
        if clean_corridor:
            clean_corridors.append(clean_corridor)
    
    corridors_string = ','.join(clean_corridors)
    
    # Fix unit type format - ensure clean string storage
    clean_unit_types = []
    for unit_type in unit_types:
        # Strip any curly braces or brackets and clean the unit type name
        clean_unit_type = str(unit_type).strip('{}[]').strip()
        if clean_unit_type:
            clean_unit_types.append(clean_unit_type)
    
    unit_types_string = ','.join(clean_unit_types) if clean_unit_types else None
    
    # Check for batch conflicts and get available item count before creating the batch
    from batch_locking_utils import check_batch_conflicts, get_available_items_count
    
    current_app.logger.warning(f"🔍 CONFLICT CHECK: zones={clean_zones}, corridors={clean_corridors}, invoices={invoice_nos}")
    
    conflicts = check_batch_conflicts(
        zones_list=clean_zones,
        corridors_list=clean_corridors if clean_corridors else None,
        invoice_nos=invoice_nos
    )
    
    current_app.logger.warning(f"🔍 CONFLICT RESULT: {conflicts}")
    
    # Get count of actually available items (excluding locked ones)
    available_items_count = get_available_items_count(
        zones_list=clean_zones,
        corridors_list=clean_corridors if clean_corridors else None,
        unit_types_list=clean_unit_types if clean_unit_types else None,
        invoice_nos=invoice_nos
    )
    
    current_app.logger.warning(f"🔍 AVAILABLE ITEMS: {available_items_count}")
    
    if available_items_count == 0:
        flash("Cannot create batch: All matching items are already locked by other active batches.", 'danger')
        return redirect(url_for('batch.batch_picking_filter'))
    
    if conflicts['has_conflicts']:
        # For partial conflicts, warn user but proceed with available items only
        warning_msg = f"Note: {conflicts['total_conflicting_items']} items are already locked by other batches and will be excluded:"
        for conflict in conflicts['conflicts']:
            warning_msg += f"\n• {len(conflict['items'])} items locked by {conflict['batch_name']}"
        warning_msg += f"\n\nBatch will be created with {available_items_count} available items only."
        
        current_app.logger.warning(f"⚠️ PARTIAL CONFLICT DETECTED: {warning_msg}")
        flash(warning_msg, 'warning')
    
    # Create a new batch picking session
    batch_session = BatchPickingSession(
        name=name,
        batch_number=batch_number,
        zones=zones_string,  # Store as clean comma-separated string
        corridors=corridors_string,  # Store corridors as clean comma-separated string
        unit_types=unit_types_string,  # Store unit types as clean comma-separated string
        created_by=current_user.username,
        picking_mode=picking_mode,
        assigned_to=None  # No picker assigned during creation
    )
    
    try:
        # Add the batch session to the database
        db.session.add(batch_session)
        db.session.flush()  # Get the ID without committing
        
        # Sort invoices by routing number descending before adding to batch
        sorted_invoice_data = db.session.query(Invoice.invoice_no, Invoice.routing).filter(
            Invoice.invoice_no.in_(invoice_nos)
        ).all()
        
        def get_routing_sort_key(invoice_tuple):
            invoice_no, routing = invoice_tuple
            if routing is None:
                return -1  # Put None values at the end
            try:
                return float(routing)
            except (ValueError, TypeError):
                return -1  # Put invalid values at the end
        
        sorted_invoices = sorted(sorted_invoice_data, key=get_routing_sort_key, reverse=True)
        sorted_invoice_nos = [invoice[0] for invoice in sorted_invoices]
        
        current_app.logger.warning(f"🔍 ORIGINAL ORDER: {invoice_nos}")
        current_app.logger.warning(f"🔍 SORTED ORDER:")
        for invoice_no, routing in sorted_invoices:
            current_app.logger.warning(f"  {invoice_no}: routing {routing}")
        current_app.logger.warning(f"🔍 FINAL ORDER: {sorted_invoice_nos}")
        
        # Add invoices to the batch session in sorted order
        for invoice_no in sorted_invoice_nos:
            batch_invoice = BatchSessionInvoice(
                batch_session_id=batch_session.id,
                invoice_no=invoice_no
            )
            db.session.add(batch_invoice)
        
        # Calculate actual item count that will be processed based on filters
        # IMPORTANT: Only count items that are NOT already locked by other active batches
        zones_list = clean_zones
        corridors_list = clean_corridors
        
        # Get active batch IDs to exclude locked items
        active_batch_ids = [batch.id for batch in db.session.query(BatchPickingSession).filter(
            BatchPickingSession.status.in_(['Created', 'In Progress', 'Assigned'])
        ).all()]
        
        item_filter_conditions = [
            InvoiceItem.invoice_no.in_(invoice_nos),
            InvoiceItem.zone.in_(zones_list),
            InvoiceItem.is_picked == False,
            InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending'])
        ]
        
        # Add corridor filter if corridors were selected
        if corridors_list:
            item_filter_conditions.append(InvoiceItem.corridor.in_(corridors_list))
        
        # Add unit type filter if unit types were selected
        if clean_unit_types:
            item_filter_conditions.append(InvoiceItem.unit_type.in_(clean_unit_types))
        
        # Exclude items locked by other active batches (not including this new batch)
        if active_batch_ids:
            item_filter_conditions.append(
                db.or_(
                    InvoiceItem.locked_by_batch_id.is_(None),
                    ~InvoiceItem.locked_by_batch_id.in_(active_batch_ids)
                )
            )
        
        total_filtered_items = db.session.query(InvoiceItem).filter(
            and_(*item_filter_conditions)
        ).count()
        
        # Commit changes
        db.session.commit()
        
        # Lock items for the new batch
        from batch_locking_utils import lock_items_for_batch
        
        current_app.logger.warning(f"🔒 LOCKING ITEMS for batch {batch_session.id}: zones={clean_zones}, corridors={clean_corridors}, invoices={invoice_nos}")
        
        locked_items_count = lock_items_for_batch(
            batch_id=batch_session.id,
            zones_list=clean_zones,
            corridors_list=clean_corridors if clean_corridors else None,
            unit_types_list=clean_unit_types if clean_unit_types else None,
            invoice_nos=invoice_nos
        )
        
        current_app.logger.warning(f"🔒 LOCKED {locked_items_count} items for batch {batch_session.id}")
        
        # Create appropriate success message based on filtering
        if corridors_list:
            flash(f'Batch picking session "{name}" created successfully with {len(invoice_nos)} invoices. Locked {locked_items_count} items from corridors {", ".join(corridors_list)}.', 'success')
        else:
            flash(f'Batch picking session "{name}" created successfully with {len(invoice_nos)} invoices. Locked {locked_items_count} items.', 'success')
        
        return redirect(url_for('batch.batch_picking_manage'))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error creating batch picking session: {str(e)}', 'danger')
        return redirect(url_for('batch.batch_picking_filter'))

@batch_bp.route('/admin/batch/filter-by-zone', methods=['POST'])
@login_required
def filter_invoices_by_zone():
    """API endpoint to filter invoices by zone"""
    # Only admin users can access this endpoint
    if current_user.role not in ['admin', 'warehouse_manager']:
        return jsonify({'success': False, 'message': 'Access denied'})
    
    # Get the selected zones from the request
    zones = request.json.get('zones', [])
    include_partial = request.json.get('include_partial', False)
    
    if not zones:
        return jsonify({'success': False, 'message': 'No zones selected'})
    
    # Find invoices with items in selected zones
    query = db.session.query(
        Invoice.invoice_no,
        Invoice.customer_name,
        Invoice.routing,
        func.count(InvoiceItem.item_code).label('item_count'),
        func.sum(InvoiceItem.qty).label('total_qty')
    ).join(
        InvoiceItem
    ).filter(
        InvoiceItem.zone.in_(zones),
        InvoiceItem.is_picked == False,
        InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending'])
    )
    
    if not include_partial:
        # Only include invoices that haven't been picked at all
        query = query.filter(Invoice.status == 'not_started')
    else:
        # Include partially picked invoices too
        query = query.filter(Invoice.status.in_(['not_started', 'picking', 'awaiting_batch_items']))
    
    # Group by invoice
    query = query.group_by(Invoice.invoice_no, Invoice.customer_name, Invoice.routing)
    
    # Execute the query
    invoices = query.all()
    
    # Format the results
    result = [
        {
            'invoice_no': invoice.invoice_no,
            'customer_name': invoice.customer_name,
            'routing': invoice.routing,
            'item_count': invoice.item_count,
            'total_qty': invoice.total_qty or 0
        }
        for invoice in invoices
    ]
    
    return jsonify({'success': True, 'invoices': result})

# DISABLED: Use print reports instead of batch view
# @batch_bp.route('/admin/batch/view/<int:batch_id>')
# @login_required
# def batch_picking_view(batch_id):
    """Admin page to view a batch picking session"""
    # Only admin users can access this page
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Get the invoices in this batch
    batch_invoices = BatchSessionInvoice.query.filter_by(
        batch_session_id=batch_id
    ).all()
    
    # Get the invoice details
    invoices = []
    for bi in batch_invoices:
        invoice = Invoice.query.get(bi.invoice_no)
        if invoice:
            # Count items that match the batch zones
            zones = batch_session.zones.split(',')
            total_items = InvoiceItem.query.filter(
                InvoiceItem.invoice_no == invoice.invoice_no,
                InvoiceItem.zone.in_(zones)
            ).count()
            
            # Count picked items in this batch
            picked_items = BatchPickedItem.query.filter_by(
                batch_session_id=batch_id,
                invoice_no=invoice.invoice_no
            ).count()
            
            invoices.append({
                'invoice': invoice,
                'is_completed': bi.is_completed,
                'total_items': total_items,
                'picked_items': picked_items
            })
    
    # Get pickers for the assign dropdown
    pickers = User.query.filter_by(role='picker').all()
    
    # Calculate completion stats for template
    total_items = sum(inv.get('total_items', 0) for inv in invoices)
    picked_items = sum(inv.get('picked_items', 0) for inv in invoices)
    completion_percentage = (picked_items / total_items * 100) if total_items > 0 else 0
    
    # Calculate invoice completion stats
    total_invoices = len(invoices)
    completed_invoices = sum(1 for inv in invoices if inv.get('is_completed', False))
    invoice_completion_percentage = (completed_invoices / total_invoices * 100) if total_invoices > 0 else 0
    
    # Format batch_invoices data for template compatibility
    batch_invoices_data = []
    invoice_items_data = {}
    
    for inv_data in invoices:
        invoice = inv_data['invoice']
        bi = next((bi for bi in batch_invoices if bi.invoice_no == invoice.invoice_no), None)
        
        # Get items for this invoice in the batch zones
        zones = batch_session.zones.split(',')
        items = InvoiceItem.query.filter(
            InvoiceItem.invoice_no == invoice.invoice_no,
            InvoiceItem.zone.in_(zones)
        ).all()
        
        batch_invoices_data.append((bi, invoice))
        invoice_items_data[invoice.invoice_no] = items
    
    return render_template('batch_picking_view.html',
                          batch_session=batch_session,
                          batch=batch_session,  # Add this for template compatibility
                          batch_invoices=batch_invoices_data,
                          invoice_items=invoice_items_data,
                          invoices=invoices,
                          pickers=pickers,
                          total_items=total_items,
                          picked_items=picked_items,
                          completion_percentage=completion_percentage,
                          total_invoices=total_invoices,
                          completed_invoices=completed_invoices,
                          invoice_completion_percentage=invoice_completion_percentage)

@batch_bp.route('/admin/batch/assign/<int:batch_id>', methods=['POST'])
@login_required
def batch_picking_assign(batch_id):
    """Assign a picker to a batch picking session"""
    # Only admin users can access this endpoint
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get the picker username from the form (check both possible field names)
    picker_username = request.form.get('picker') or request.form.get('assigned_picker')
    
    # Debug: Log all form data to see what's being submitted
    current_app.logger.warning(f"🔍 BATCH ASSIGN DEBUG: Form data = {dict(request.form)}")
    current_app.logger.warning(f"🔍 BATCH ASSIGN DEBUG: Picker value = '{picker_username}'")
    
    if not picker_username:
        flash('Please select a picker to assign.', 'warning')
        return redirect(url_for('batch.batch_picking_manage'))
    
    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Assign the picker and activate the batch
    batch_session.assigned_to = picker_username
    batch_session.status = 'Active'  # Automatically activate when assigned
    
    # Save changes to database
    db.session.commit()
    
    flash(f'Picker {picker_username} assigned to batch picking session and activated.', 'success')
    return redirect(url_for('batch.batch_picking_manage'))

@batch_bp.route('/admin/batch/unassign/<int:batch_id>', methods=['POST'])
@login_required
def batch_picking_unassign(batch_id):
    """Unassign a picker from a batch picking session"""
    # Only admin users can access this endpoint
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Unassign the picker
    batch_session.assigned_to = None
    
    # Save changes to database
    db.session.commit()
    
    flash('Picker unassigned from batch picking session.', 'success')
    return redirect(url_for('batch.batch_picking_manage'))

@batch_bp.route('/picker/batch/list')
@login_required
def picker_batch_list():
    """Picker page to view assigned batch picking sessions"""
    # Only picker users can access this page
    if current_user.role not in ['admin', 'warehouse_manager'] and current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get batch sessions assigned to this picker (exclude completed batches)
    batch_sessions = BatchPickingSession.query.filter(
        BatchPickingSession.assigned_to == current_user.username,
        BatchPickingSession.status != 'Completed'
    ).order_by(BatchPickingSession.created_at.desc()).all()
    
    return render_template('batch_picking_list.html',
                          batch_sessions=batch_sessions)

@batch_bp.route('/picker/batch/clear_cache/<int:batch_id>')
@login_required
def clear_batch_cache(batch_id):
    """Clear the session cache for a specific batch"""
    if current_user.role not in ['admin', 'warehouse_manager'] and current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Clear all batch-related session data
    fixed_batch_key = 'batch_items_' + str(batch_id)
    batch_start_key = 'batch_start_' + str(batch_id)
    
    if fixed_batch_key in session:
        session.pop(fixed_batch_key, None)
        current_app.logger.info(f"🔄 CACHE CLEARED: Manually cleared batch items cache for batch {batch_id}")
    
    if batch_start_key in session:
        session.pop(batch_start_key, None)
        current_app.logger.info(f"🔄 CACHE CLEARED: Manually cleared batch start cache for batch {batch_id}")
    
    flash(f'Cache cleared for batch {batch_id}. The picking sequence has been refreshed.', 'success')
    return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))

@batch_bp.route('/picker/batch/start/<int:batch_id>')
@login_required
def start_batch_picking(batch_id):
    """Start picking a batch"""
    # Only picker users can access this endpoint
    if current_user.role not in ['admin', 'warehouse_manager'] and current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Check if this picker is assigned to this batch
    if current_user.role not in ['admin', 'warehouse_manager'] and batch_session.assigned_to != current_user.username:
        flash('You are not assigned to this batch picking session.', 'danger')
        return redirect(url_for('batch.picker_batch_list'))
    
    # Update the status to picking
    batch_session.status = 'picking'
    
    # Calculate ALL items in the batch - all zones for all invoices
    zone_list = [zone.strip() for zone in batch_session.zones.split(',')]
    all_batch_items = []
    
    current_app.logger.warning(f"🔍 BATCH START DEBUG: Zone list = {zone_list}")
    
    # Get all invoices in this batch - sort by routing number descending to maintain picking order
    # Cast routing to numeric for proper sorting (not string sorting)
    batch_invoices = BatchSessionInvoice.query.join(Invoice).filter(
        BatchSessionInvoice.batch_session_id == batch_id
    ).order_by(func.cast(Invoice.routing, db.Numeric).desc().nulls_last()).all()
    
    for bi in batch_invoices:
        # Get items that are actually locked by THIS batch
        invoice_items = InvoiceItem.query.filter(
            InvoiceItem.invoice_no == bi.invoice_no,
            InvoiceItem.locked_by_batch_id == batch_session.id,
            InvoiceItem.is_picked == False,
            InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending'])
        ).all()
        
        current_app.logger.info(f"Invoice {bi.invoice_no}: Found {len(invoice_items)} items locked by batch {batch_session.id}")
        
        # Add items to the list
        for item in invoice_items:
            # For Sequential mode, each item is processed separately
            if batch_session.picking_mode == 'Sequential':
                all_batch_items.append({
                    'item_code': item.item_code,
                    'item_name': item.item_name,
                    'location': item.location,
                    'zone': item.zone,
                    'barcode': item.barcode,
                    'unit_type': item.unit_type,
                    'pack': item.pack,
                    'total_qty': item.expected_pick_pieces if item.expected_pick_pieces else item.qty,
                    'source_items': [
                        {
                            'invoice_no': item.invoice_no,
                            'item_code': item.item_code,
                            'qty': item.qty,
                            'expected_pick_pieces': item.expected_pick_pieces if item.expected_pick_pieces else item.qty
                        }
                    ]
                })
            else:
                # For Consolidated mode, we need to group items by item_code and location
                # This is more complex and would be implemented here
                # For now, we'll just add each item separately
                all_batch_items.append({
                    'item_code': item.item_code,
                    'item_name': item.item_name,
                    'location': item.location,
                    'zone': item.zone,
                    'barcode': item.barcode,
                    'unit_type': item.unit_type,
                    'pack': item.pack,
                    'total_qty': item.expected_pick_pieces if item.expected_pick_pieces else item.qty,
                    'source_items': [
                        {
                            'invoice_no': item.invoice_no,
                            'item_code': item.item_code,
                            'qty': item.qty,
                            'expected_pick_pieces': item.expected_pick_pieces if item.expected_pick_pieces else item.qty
                        }
                    ]
                })
    
    # Sort items using admin configurable sorting settings
    all_batch_items = sort_batch_items(all_batch_items)
    
    # Serialize the batch items list to store in the session
    serialized_items = []
    for item in all_batch_items:
        # Extract only the necessary data to keep the session size manageable
        serialized_item = {
            'item_code': item['item_code'],
            'item_name': item.get('item_name', 'Unknown Item'),
            'location': item['location'],
            'zone': item['zone'],
            'barcode': item.get('barcode', ''),
            'unit_type': item.get('unit_type', ''),
            'pack': item.get('pack', ''),
            'total_qty': item['total_qty'],
            'source_items': [
                {'invoice_no': s['invoice_no'], 'item_code': s['item_code'], 'qty': s['qty'], 'expected_pick_pieces': s.get('expected_pick_pieces', s['qty'])} 
                for s in item['source_items']
            ]
        }
        serialized_items.append(serialized_item)
    
    # Store in the session
    session['batch_items_' + str(batch_id)] = serialized_items
    
    # Reset the current index to 0 to start from the beginning
    batch_session.current_item_index = 0
    
    # For sequential mode, also reset invoice index
    if batch_session.picking_mode == 'Sequential':
        batch_session.current_invoice_index = 0
        
    # Set a flag to indicate we're starting a new batch
    # This ensures the batch_picking_item route uses our fixed item list
    session['batch_start_' + str(batch_id)] = True
    
    current_app.logger.info(f"Starting batch {batch_id} with fixed list of {len(all_batch_items) if all_batch_items else 0} items at index 0")
    
    # Record picking start time for time tracking
    # Get all invoices in this batch to create/update OrderTimeBreakdown records
    batch_invoices = BatchSessionInvoice.query.filter_by(batch_session_id=batch_id).all()
    for bi in batch_invoices:
        existing_breakdown = OrderTimeBreakdown.query.filter_by(
            invoice_no=bi.invoice_no,
            picker_username=current_user.username
        ).first()
        
        if not existing_breakdown:
            breakdown = OrderTimeBreakdown(
                invoice_no=bi.invoice_no,
                picker_username=current_user.username,
                picking_started=datetime.utcnow()
            )
            db.session.add(breakdown)
        elif not existing_breakdown.picking_started:
            # Only set if not already set
            existing_breakdown.picking_started = datetime.utcnow()
    
    db.session.commit()
    
    # Store the batch id in the session
    session['current_batch_id'] = batch_id
    
    # Redirect to the batch picking page
    return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))

@batch_bp.route('/picker/batch/item/<int:batch_id>')
@login_required
def batch_picking_item(batch_id):
    """Display the current item to pick in a batch"""
    # Only picker users can access this page
    if current_user.role not in ['admin', 'warehouse_manager'] and current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))

    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Check if batch is already completed
    if batch_session.status == 'Completed':
        flash('This batch has been completed successfully!', 'success')
        return redirect(url_for('batch.batch_completion_summary', batch_id=batch_id))
    
    # Check if this picker is assigned to this batch
    if current_user.role not in ['admin', 'warehouse_manager'] and batch_session.assigned_to != current_user.username:
        flash('You are not assigned to this batch picking session.', 'danger')
        return redirect(url_for('batch.picker_batch_list'))
    
    # Reset item index if requested (for debugging)
    if request.args.get('reset') == 'true' and current_user.role in ['admin', 'warehouse_manager']:
        batch_session.current_item_index = 0
        batch_session.current_invoice_index = 0
        # Clear the session cache to force regeneration
        fixed_batch_key = 'batch_items_' + str(batch_id)
        if fixed_batch_key in session:
            session.pop(fixed_batch_key, None)
        session['batch_start_' + str(batch_id)] = True
        db.session.commit()
        current_app.logger.info(f"Reset batch indices to 0 and cleared cache for debugging")
        
    # COMPLETE BATCH PICKING FIX - USE A FIXED LIST THROUGHOUT THE ENTIRE PROCESS
    # Check if we already have a fixed items list for this batch in the session
    fixed_batch_key = 'batch_items_' + str(batch_id)
    
    # If we don't have a fixed list yet, or if we're restarting the batch, create one
    # FORCE REGENERATION when switching to Sequential mode
    force_regenerate = (batch_session.picking_mode == 'Sequential' and 
                       batch_session.current_item_index == 0 and 
                       batch_session.current_invoice_index == 0)
    
    if fixed_batch_key not in session or session.pop('batch_start_' + str(batch_id), False) or force_regenerate:
        # Clear any existing cached data when switching modes
        if force_regenerate and fixed_batch_key in session:
            session.pop(fixed_batch_key, None)
            current_app.logger.warning(f"🔄 FORCING REGENERATION: Cleared cached data for Sequential mode")
        
        # Generate the complete list of ALL items in the batch
        all_batch_items = batch_session.get_grouped_items()
        
        # Save these items in the session
        if all_batch_items:
            # Serialize the batch items for session storage
            serialized_items = []
            for item in all_batch_items:
                # Get order details for the first invoice in this item's source items
                first_invoice_no = item['source_items'][0]['invoice_no'] if item['source_items'] else None
                current_invoice = None
                customer_name = None
                order_total_items = None
                order_total_weight = None
                routing = None
                
                if first_invoice_no:
                    current_invoice = first_invoice_no
                    # Get invoice details
                    invoice = Invoice.query.filter_by(invoice_no=first_invoice_no).first()
                    if invoice:
                        customer_name = invoice.customer_name
                        order_total_items = invoice.total_items
                        order_total_weight = invoice.total_weight
                        routing = invoice.routing
                
                # Include ALL necessary data fields needed for display
                serialized_item = {
                    'item_code': item['item_code'],
                    'item_name': item.get('item_name', 'Unknown Item'),
                    'location': item['location'],
                    'zone': item['zone'],
                    'barcode': item.get('barcode', ''),
                    'unit_type': item.get('unit_type', ''),
                    'pack': item.get('pack', ''),
                    'total_qty': item['total_qty'],
                    'current_invoice': current_invoice,
                    'customer_name': customer_name,
                    'routing': routing,
                    'source_items': [
                        {'invoice_no': s['invoice_no'], 'item_code': s['item_code'], 'qty': s['qty']} 
                        for s in item['source_items']
                    ],
                    # Order details for display
                    'order_total_items': order_total_items,
                    'order_total_weight': order_total_weight
                }
                serialized_items.append(serialized_item)
            
            # Store in session
            session[fixed_batch_key] = serialized_items
            current_app.logger.info(f"Created batch item list: {len(serialized_items)} items for batch {batch_id}")
            
            # Always start at the beginning 
            batch_session.current_item_index = 0
            db.session.commit()
    
    # Now use our fixed item list
    if fixed_batch_key in session:
        items = session[fixed_batch_key]
        current_app.logger.info(f"Using fixed batch list: {len(items)} items, current index: {batch_session.current_item_index}")
    else:
        # Fallback to database query if session data is missing
        items = batch_session.get_grouped_items()
        current_app.logger.info(f"Using database query, found {len(items if items else [])} items")
    
    # CRITICAL DEBUG CHECK - Verify all items are in the fixed list
    if current_user.role in ['admin', 'warehouse_manager'] and request.args.get('debug') == 'items':
        # Get list of specific item codes to check for
        check_items = request.args.get('items', '').split(',')
        if check_items:
            current_app.logger.info(f"Checking for items: {', '.join(check_items)}")
            
            # Check if these items exist in our fixed list
            found_items = [item['item_code'] for item in items if item['item_code'] in check_items]
            missing_items = [item for item in check_items if item not in found_items]
            
            if missing_items:
                current_app.logger.error(f"CRITICAL MISSING ITEMS: {', '.join(missing_items)}")
            else:
                current_app.logger.warning(f"ALL ITEMS FOUND IN LIST: {', '.join(check_items)}")
                
        # Dump the entire batch picking queue for debugging
        current_app.logger.info(f"BATCH PICKING QUEUE DUMP FOR BATCH {batch_id}:")
        for i, item in enumerate(items):
            status = "CURRENT" if i == batch_session.current_item_index else "PICKED" if i < batch_session.current_item_index else "PENDING"
            current_app.logger.info(f"  Item #{i}: {item['item_code']} (Location: {item['location']}) - {status}")
    
    # Check batch completion status
    current_app.logger.info(f"Batch check: items={len(items) if items else 0}, current_index={batch_session.current_item_index}")
    
    # Safety check if we're at the end of the batch
    if not items or batch_session.current_item_index >= len(items):
        # Check for skipped items that need to be collected later
        zones_list = batch_session.zones.split(',')
        batch_invoices = BatchSessionInvoice.query.filter_by(batch_session_id=batch_id).all()
        invoice_nos = [bi.invoice_no for bi in batch_invoices]
        
        # Find skipped items that need to be resolved - only in scope for this batch
        skipped_filter_conditions = [
            InvoiceItem.invoice_no.in_(invoice_nos),
            InvoiceItem.zone.in_(zones_list),
            InvoiceItem.pick_status == 'skipped_pending'
        ]
        
        # Add corridor filter if corridors are specified for this batch
        if batch_session.corridors:
            corridors_list = [c.strip() for c in batch_session.corridors.split(',')]
            skipped_filter_conditions.append(InvoiceItem.corridor.in_(corridors_list))
        
        skipped_items = InvoiceItem.query.filter(
            and_(*skipped_filter_conditions)
        ).all()
        
        if skipped_items:
            # There are skipped items - add them back to the batch for resolution
            current_app.logger.info(f"Skip and collect: Found {len(skipped_items)} skipped items to resolve")
            
            # Create a new list with skipped items grouped like the original batch
            skipped_batch_items = []
            item_groups = {}
            
            for item in skipped_items:
                key = item.item_code
                if key not in item_groups:
                    item_groups[key] = {
                        'item_code': item.item_code,
                        'item_name': item.item_name,
                        'location': item.location,
                        'zone': item.zone,
                        'barcode': item.barcode,
                        'unit_type': item.unit_type,
                        'pack': item.pack,
                        'total_qty': 0,
                        'source_items': []
                    }
                
                item_groups[key]['total_qty'] += (item.expected_pick_pieces if item.expected_pick_pieces else item.qty)
                item_groups[key]['source_items'].append({
                    'invoice_no': item.invoice_no,
                    'item_code': item.item_code,
                    'qty': item.qty,
                    'expected_pick_pieces': item.expected_pick_pieces if item.expected_pick_pieces else item.qty
                })
            
            # Convert to list and sort by location
            skipped_batch_items = list(item_groups.values())
            skipped_batch_items.sort(key=lambda x: x['location'] if x['location'] else 'ZZZ')
            
            # Update the session with skipped items only
            session[fixed_batch_key] = skipped_batch_items
            batch_session.current_item_index = 0
            db.session.commit()
            
            flash(f'Please resolve {len(skipped_items)} skipped item(s) before completing the batch.', 'warning')
            return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
        
        # No skipped items - batch can be completed
        # Use the batch's own method to get remaining item count (this excludes items locked by other batches)
        remaining_items_count = batch_session.get_filtered_item_count()
        
        # Count picked items that belong to this batch
        picked_items_count = 0
        if batch_session.zones:
            zones_list = batch_session.zones.split(',')
            batch_invoices = BatchSessionInvoice.query.filter_by(batch_session_id=batch_id).all()
            invoice_nos = [bi.invoice_no for bi in batch_invoices]
            
            # Build filter conditions for picked items that are actually in scope for this batch
            filter_conditions = [
                InvoiceItem.invoice_no.in_(invoice_nos),
                InvoiceItem.zone.in_(zones_list),
                InvoiceItem.is_picked == True,
                or_(
                    InvoiceItem.locked_by_batch_id.is_(None),
                    InvoiceItem.locked_by_batch_id == batch_id
                )
            ]
            
            # Add corridor filter if corridors are specified for this batch
            if batch_session.corridors:
                corridors_list = [c.strip() for c in batch_session.corridors.split(',')]
                filter_conditions.append(InvoiceItem.corridor.in_(corridors_list))
            
            picked_items_count = InvoiceItem.query.filter(
                and_(*filter_conditions)
            ).count()
            
            current_app.logger.info(f"Batch {batch_id} completion check: {picked_items_count} picked, {remaining_items_count} remaining")
        
        if remaining_items_count == 0 and batch_session.current_item_index >= 0:
            # All items are picked - complete the batch
            if batch_session.status != 'Completed':
                batch_session.status = 'Completed'
                
                # Update order statuses for all invoices in this batch
                from batch_aware_order_status import update_all_orders_after_batch_completion
                updated_orders = update_all_orders_after_batch_completion(batch_id)
                
                db.session.commit()
                current_app.logger.info(f"Batch {batch_id} completed: All items picked successfully")
                
            flash('All items in this batch have been picked!', 'success')
            return redirect(url_for('batch.batch_completion_summary', batch_id=batch_id))
        elif not items and batch_session.current_item_index == 0:
            # No items were ever found - configuration issue
            current_app.logger.error(f"Critical error: Batch {batch_id} found 0 items but should have found items in zones: {batch_session.zones}")
            flash('No items found in this batch. Please check the zone configuration.', 'warning')
            return redirect(url_for('batch.picker_batch_list'))
        else:
            # Unexpected state - log for debugging
            current_app.logger.error(f"Unexpected state: Batch {batch_id} - items={len(items) if items else 0}, index={batch_session.current_item_index}, remaining={remaining_items_count}")
            flash('Batch completion check failed. Please contact support.', 'warning')
            return redirect(url_for('batch.picker_batch_list'))
    
    # Get the current item to pick
    current_item = items[batch_session.current_item_index]
    
    # Debug logging for batch picking
    current_app.logger.info(f"Batch {batch_id}: Processing item {batch_session.current_item_index + 1}/{len(items)} - {current_item['item_code']}")
    
    # Get the next item's location (if available) to show to the picker
    show_next_location = False
    next_location = None
    
    if batch_session.current_item_index + 1 < len(items):
        next_item = items[batch_session.current_item_index + 1]
        if next_item and 'location' in next_item and next_item['location']:
            show_next_location = True
            next_location = next_item['location']
    
    # Get skip reason settings
    try:
        require_skip_reason = Setting.get_json(db.session, 'require_skip_reason', default=True)
        skip_reasons_setting = Setting.get_json(db.session, 'skip_reasons', default=["Item not found", "Damaged item", "Wrong location"])
        skip_reasons = skip_reasons_setting if isinstance(skip_reasons_setting, list) else []
    except:
        require_skip_reason = True
        skip_reasons = ["Item not found", "Damaged item", "Wrong location"]
    
    # Check if we need to show product images
    try:
        show_product_image = Setting.get_json(db.session, 'show_product_image', default=True)
    except:
        show_product_image = True
    
    # Get the multi-quantity warning setting
    show_multi_qty_warning = Setting.get(db.session, 'show_multi_qty_warning', 'true')
    
    # Get exception reasons for the issue modal (same as regular order picking)
    default_exception_reasons = "Item not found\nInsufficient quantity\nDamaged product\nWrong location\nOther"
    exception_reasons_text = Setting.get(db.session, 'exception_reasons', default_exception_reasons)
    exception_reasons_list = [reason.strip() for reason in exception_reasons_text.split('\n') if reason.strip()]
    
    # Start per-product time tracking for each source item in this consolidated batch item
    # We create tracking records now but don't set item_started until picker clicks "Proceed to Pick"
    tracking_ids = []
    try:
        from item_tracking import start_item_tracking
        source_items = current_item.get('source_items', [])
        
        for source_item in source_items:
            tracking = start_item_tracking(
                invoice_no=source_item.get('invoice_no'),
                item_code=current_item.get('item_code'),
                picker_username=current_user.username,
                previous_location=None,  # Could track this for walking time
                start_immediately=False,
                batch_id=batch_id
            )
            if tracking:
                tracking_ids.append(tracking.id)
    except Exception as e:
        current_app.logger.warning(f"Error starting batch item tracking: {e}")
    
    # Render the picking page
    return render_template('batch_picking_item.html',
                          batch_session=batch_session,
                          item=current_item,
                          show_next_location=show_next_location,
                          next_location=next_location,
                          show_product_image=show_product_image,
                          require_skip_reason=require_skip_reason,
                          skip_reasons=skip_reasons,
                          exception_reasons_list=exception_reasons_list,
                          total_items=len(items),
                          current_index=batch_session.current_item_index,
                          show_multi_qty_warning=show_multi_qty_warning,
                          tracking_ids=tracking_ids)

@batch_bp.route('/api/picker/batch/<int:batch_id>/arrived', methods=['POST'])
@login_required
def api_batch_arrived(batch_id):
    """Sets item_started timestamp for all tracking IDs when picker clicks 'Proceed to Pick'"""
    if current_user.role not in ['admin', 'warehouse_manager', 'picker']:
        return jsonify({'ok': False, 'error': 'Access denied'}), 403

    # Get tracking_ids from request
    if request.is_json:
        tracking_ids = (request.json or {}).get('tracking_ids', [])
    else:
        tracking_ids_str = request.form.get('tracking_ids', '[]')
        try:
            import json
            tracking_ids = json.loads(tracking_ids_str)
        except:
            tracking_ids = []

    if not tracking_ids:
        return jsonify({'ok': False, 'error': 'Missing tracking_ids'}), 400

    from timezone_utils import get_utc_now
    now = get_utc_now()

    # Find the most recent completed tracking record for walking_time calculation
    # (Use first tracking_id as representative for the batch)
    prev = ItemTimeTracking.query.filter(
        ItemTimeTracking.picker_username == current_user.username,
        ItemTimeTracking.item_completed.isnot(None),
        ItemTimeTracking.id.notin_(tracking_ids)
    ).order_by(ItemTimeTracking.item_completed.desc()).first()

    walking_time = max((now - prev.item_completed).total_seconds(), 0) if prev else 0.0
    
    # For batch picks, divide walking_time by number of source invoices
    num_sources = len(tracking_ids) if tracking_ids else 1
    walking_time_per_source = walking_time / num_sources if num_sources > 0 else 0.0

    # Update all tracking records
    for tid in tracking_ids:
        tracking = ItemTimeTracking.query.filter_by(
            id=int(tid),
            picker_username=current_user.username
        ).first()

        if tracking and tracking.item_started is None:
            tracking.item_started = now
            tracking.walking_time = walking_time_per_source

    db.session.commit()
    return jsonify({'ok': True})


@batch_bp.route('/picker/batch/confirm/<int:batch_id>', methods=['POST'])
@login_required
def confirm_batch_item(batch_id):
    """Confirm a picked item in a batch - redirects to confirmation screen"""
    # Only picker users can access this endpoint
    if current_user.role not in ['admin', 'warehouse_manager'] and current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))

    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Check if this picker is assigned to this batch
    if current_user.role not in ['admin', 'warehouse_manager'] and batch_session.assigned_to != current_user.username:
        flash('You are not assigned to this batch picking session.', 'danger')
        return redirect(url_for('batch.picker_batch_list'))

    # CRUCIAL FIX: Use our fixed item list from the session
    # This prevents the list from changing during the batch picking process
    fixed_batch_key = 'batch_items_' + str(batch_id)
    
    # Get the items from our fixed list instead of recalculating
    if fixed_batch_key in session:
        items = session[fixed_batch_key]
        current_app.logger.info(f"Confirming batch item using fixed list: {len(items)} items, current index: {batch_session.current_item_index}")
    else:
        # Fallback to database query if session data is missing
        items = batch_session.get_grouped_items()
        
        # Store for future access
        if items:
            # Create a fully detailed serialized list for session storage
            serialized_items = []
            for item in items:
                serialized_item = {
                    'item_code': item['item_code'],
                    'item_name': item.get('item_name', 'Unknown Item'),
                    'location': item['location'],
                    'zone': item['zone'],
                    'barcode': item.get('barcode', ''),
                    'unit_type': item.get('unit_type', ''),
                    'pack': item.get('pack', ''),
                    'total_qty': item['total_qty'],
                    'source_items': [
                        {'invoice_no': s['invoice_no'], 'item_code': s['item_code'], 'qty': s['qty']} 
                        for s in item['source_items']
                    ]
                }
                serialized_items.append(serialized_item)
            
            # Store in session to prevent further recalculation
            session[fixed_batch_key] = serialized_items
            items = serialized_items
            
        current_app.logger.info(f"Fallback in confirm: Using database query, found {len(items if items else [])} items")
    
    # CRITICAL DEBUG INFO
    current_app.logger.info(f"Confirm: Processing item at index {batch_session.current_item_index} of {len(items)} total")
    
    if not items or batch_session.current_item_index >= len(items):
        flash('No more items to pick in this batch session.', 'info')
        return redirect(url_for('batch.picker_batch_list'))
    
    # Get the current item
    current_item = items[batch_session.current_item_index]
    
    # Get the picked quantity from the form
    try:
        picked_qty = int(request.form.get('picked_qty', 0))
    except ValueError:
        picked_qty = 0
    
    if picked_qty <= 0:
        flash('Please enter a valid picked quantity.', 'danger')
        return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
    
    # Get the source items for this batch item
    source_items = current_item['source_items']
    total_required = current_item['total_qty']
    
    # Check the setting for showing product images on the confirmation screen
    try:
        show_product_image = Setting.get_json(db.session, 'show_product_image_confirmation', default=False)
    except Exception as e:
        current_app.logger.error(f"Error loading product image settings: {str(e)}")
        show_product_image = False
    
    # Redirect to the confirmation screen
    return render_template('batch_picking_confirm.html',
                          batch_session=batch_session,
                          item=current_item,
                          picked_qty=picked_qty,
                          total_items=len(items),
                          current_index=batch_session.current_item_index,
                          show_product_image=show_product_image,
                          show_product_image_confirmation=show_product_image)

@batch_bp.route('/picker/batch/complete-confirm/<int:batch_id>', methods=['POST'])
@login_required
def complete_batch_confirm(batch_id):
    """Process the confirmation of a picked item and complete the database updates"""
    # Only picker users can access this endpoint
    if current_user.role not in ['admin', 'warehouse_manager'] and current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))

    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Check if this picker is assigned to this batch
    if current_user.role not in ['admin', 'warehouse_manager'] and batch_session.assigned_to != current_user.username:
        flash('You are not assigned to this batch picking session.', 'danger')
        return redirect(url_for('batch.picker_batch_list'))

    # CRUCIAL FIX: Use our fixed item list from the session
    fixed_batch_key = 'batch_items_' + str(batch_id)
    
    if fixed_batch_key in session:
        items = session[fixed_batch_key]
        current_app.logger.info(f"Complete confirm using fixed list: {len(items)} items, current index: {batch_session.current_item_index}")
    else:
        # Fallback to database query if session data is missing
        items = batch_session.get_grouped_items()
        current_app.logger.info(f"Fallback in complete confirm: Using database query, found {len(items if items else [])} items")
    
    if not items or batch_session.current_item_index >= len(items):
        flash('No more items to pick in this batch session.', 'info')
        return redirect(url_for('batch.picker_batch_list'))
    
    # Get the current item
    current_item = items[batch_session.current_item_index]
    
    # Get the picked quantity from the form
    try:
        picked_qty = int(request.form.get('picked_qty', 0))
    except ValueError:
        picked_qty = 0
    
    # Get exception reason from form (for exception reports)
    exception_reason = request.form.get("reason", "")
    if picked_qty <= 0:
        flash('Please enter a valid picked quantity.', 'danger')
        return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
    
    # Get the source items for this batch item
    source_items = current_item['source_items']
    total_required = current_item['total_qty']
    
    try:
        # Process the picked items
        if batch_session.picking_mode == 'Sequential':
            # Sequential mode - one invoice at a time
            invoice_no = source_items[0]['invoice_no']
            item_code = source_items[0]['item_code']
            required_qty = source_items[0].get('expected_pick_pieces', source_items[0]['qty'])
            
            # Update the invoice item
            invoice_item = InvoiceItem.query.filter_by(
                invoice_no=invoice_no,
                item_code=item_code
            ).first()
            
            if invoice_item:
                # Record any exceptions if there's a discrepancy or if a reason is provided
                if picked_qty != required_qty or exception_reason:
                    # Use the provided reason or create a default one for quantity discrepancies
                    if exception_reason:
                        reason_text = exception_reason
                    else:
                        reason_text = f"Quantity discrepancy: {picked_qty} picked, {required_qty} required"
                    
                    exception = PickingException(
                        invoice_no=invoice_no,
                        item_code=item_code,
                        expected_qty=required_qty,
                        picked_qty=picked_qty,
                        picker_username=current_user.username,
                        reason=reason_text
                    )
                    db.session.add(exception)
                
                # Update the invoice item
                invoice_item.picked_qty = picked_qty
                invoice_item.is_picked = True
                invoice_item.pick_status = 'picked'
                
                # Record the batch picked item (prevent duplicates)
                existing_batch_picked = BatchPickedItem.query.filter_by(
                    batch_session_id=batch_id,
                    invoice_no=invoice_no,
                    item_code=item_code
                ).first()
                
                if existing_batch_picked:
                    # Update existing record instead of creating duplicate
                    existing_batch_picked.picked_qty = picked_qty
                else:
                    # Create new record
                    batch_picked = BatchPickedItem(
                        batch_session_id=batch_id,
                        invoice_no=invoice_no,
                        item_code=item_code,
                        picked_qty=picked_qty
                    )
                    db.session.add(batch_picked)
            
            # Check if all items for this invoice in the batch are picked
            zones = batch_session.zones.split(',')
            unpicked_count = InvoiceItem.query.filter(
                InvoiceItem.invoice_no == invoice_no,
                InvoiceItem.zone.in_(zones),
                InvoiceItem.is_picked == False,
                InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending'])
            ).count()
            
            if unpicked_count == 0:
                # Mark this invoice as completed in the batch
                batch_invoice = BatchSessionInvoice.query.filter_by(
                    batch_session_id=batch_id,
                    invoice_no=invoice_no
                ).first()
                
                if batch_invoice:
                    batch_invoice.is_completed = True
        
        else:  # Consolidated mode
            # Need to allocate the picked quantity across invoices
            # We'll prioritize allocating to the earliest invoices first
            
            # Sort source items by invoice number (assuming invoice numbers are chronological)
            sorted_sources = sorted(source_items, key=lambda x: x['invoice_no'])
            
            remaining_qty = picked_qty
            
            for source in sorted_sources:
                invoice_no = source['invoice_no']
                item_code = source['item_code']
                required_qty = source['qty']
                
                # Allocate as much as possible to this invoice
                allocated_qty = min(remaining_qty, required_qty)
                
                if allocated_qty > 0:
                    # Update the invoice item
                    invoice_item = InvoiceItem.query.filter_by(
                        invoice_no=invoice_no,
                        item_code=item_code
                    ).first()
                    
                    if invoice_item:
                        # Record any exceptions if there's a discrepancy
                        if allocated_qty != required_qty:
                            exception = PickingException(
                                invoice_no=invoice_no,
                                item_code=item_code,
                                expected_qty=required_qty,
                                picked_qty=allocated_qty,
                                picker_username=current_user.username,
                                reason=f"Batch picking (consolidated): {allocated_qty} allocated, {required_qty} required"
                            )
                            db.session.add(exception)
                        
                        # Update the invoice item
                        invoice_item.picked_qty = allocated_qty
                        invoice_item.is_picked = True
                        invoice_item.pick_status = 'picked'
                        
                        # Record the batch picked item (prevent duplicates)
                        existing_batch_picked = BatchPickedItem.query.filter_by(
                            batch_session_id=batch_id,
                            invoice_no=invoice_no,
                            item_code=item_code
                        ).first()
                        
                        if existing_batch_picked:
                            # Update existing record instead of creating duplicate
                            existing_batch_picked.picked_qty = allocated_qty
                        else:
                            # Create new record
                            batch_picked = BatchPickedItem(
                                batch_session_id=batch_id,
                                invoice_no=invoice_no,
                                item_code=item_code,
                                picked_qty=allocated_qty
                            )
                            db.session.add(batch_picked)
                        
                        # Reduce the remaining quantity
                        remaining_qty -= allocated_qty
                        
                        if remaining_qty <= 0:
                            break  # No more quantity to allocate
        
        # Record an activity
        activity = ActivityLog(
            picker_username=current_user.username,
            activity_type='batch_item_pick',
            details=f"Batch {batch_id}: Picked {picked_qty} of {current_item['item_code']} (total required: {total_required})"
        )
        db.session.add(activity)
        
        # Record item-level time tracking for each source item - OPTIMIZED with batch query
        current_time = datetime.utcnow()
        
        # Build list of (invoice_no, item_code) tuples for batch lookup
        source_keys = [(s['invoice_no'], s['item_code']) for s in source_items]
        
        # Fetch ALL existing tracking records in ONE query instead of N queries
        existing_tracking = {}
        if source_keys:
            tracking_records = ItemTimeTracking.query.filter(
                ItemTimeTracking.picker_username == current_user.username,
                or_(*[and_(ItemTimeTracking.invoice_no == inv, ItemTimeTracking.item_code == code) 
                      for inv, code in source_keys])
            ).all()
            for rec in tracking_records:
                existing_tracking[(rec.invoice_no, rec.item_code)] = rec
        
        # Now process each source item using the pre-fetched data
        for source_item in source_items:
            invoice_no = source_item['invoice_no']
            item_code = source_item['item_code']
            required_qty = source_item['qty']
            
            # Check if ItemTimeTracking already exists (from pre-fetched data)
            item_tracking = existing_tracking.get((invoice_no, item_code))
            
            if not item_tracking:
                # Create new item tracking record
                item_tracking = ItemTimeTracking(
                    invoice_no=invoice_no,
                    item_code=item_code,
                    picker_username=current_user.username,
                    item_started=current_time,
                    item_completed=current_time,
                    picking_time=0.0,
                    quantity_expected=required_qty,
                    quantity_picked=picked_qty
                )
                db.session.add(item_tracking)
            else:
                # Update completion time and picking time
                item_tracking.item_completed = current_time
                if item_tracking.item_started:
                    picking_seconds = (current_time - item_tracking.item_started).total_seconds()
                    item_tracking.picking_time = max(picking_seconds, 0)
                item_tracking.quantity_picked = picked_qty
        
        # For Sequential mode, check if current order is complete and move to next order
        if batch_session.picking_mode == 'Sequential':
            # Check if current order is complete
            current_invoice = current_item['current_invoice']
            zones = batch_session.zones.split(',')
            
            # 🔧 FIXED: Use DB-backed completion check that only considers batch-locked items
            remaining_items_in_order = get_remaining_locked_items_count(batch_session, current_invoice)
            current_app.logger.info(f"Sequential mode: Order {current_invoice} has {remaining_items_in_order} remaining batch-locked items")
            
            if remaining_items_in_order == 0:
                # Current order is complete, mark it and find next incomplete order
                current_app.logger.info(f"🔄 SEQUENTIAL: Order {current_invoice} completed (was at index {batch_session.current_invoice_index})")
                
                # Mark this order as completed in the batch
                batch_invoice = BatchSessionInvoice.query.filter_by(
                    batch_session_id=batch_id,
                    invoice_no=current_invoice
                ).first()
                
                if batch_invoice:
                    batch_invoice.is_completed = True
                
                # 🔧 FIXED: Use helper functions for proper completion checking
                all_batch_invoices = get_sorted_batch_invoices(batch_session)
                next_index = find_next_incomplete_invoice_index(batch_session, all_batch_invoices)
                
                if next_index is not None:
                    batch_session.current_invoice_index = next_index
                    batch_session.current_item_index = 0  # Reset item index for new order
                    next_invoice_no = all_batch_invoices[next_index].invoice_no
                    current_app.logger.info(f"🔄 SEQUENTIAL: Advanced to invoice index {next_index} (invoice {next_invoice_no})")
                else:
                    # No more incomplete invoices - batch is done
                    current_app.logger.info(f"🔄 SEQUENTIAL: No more incomplete invoices - batch completion check will handle this")
                
                # 🔧 FIXED: Use helper function for cache clearing
                clear_batch_cache(batch_id)
            else:
                # More items in current order, continue normally
                batch_session.current_item_index += 1
        else:
            # Consolidated mode - continue normally
            batch_session.current_item_index += 1
        
        # Check if entire batch is complete
        if batch_session.current_item_index >= len(items):
            # In sequential mode, we need to check if there are more invoices to process
            if batch_session.picking_mode == 'Sequential':
                # Get all invoices in this batch to check if we have more to process
                batch_invoices = db.session.query(BatchSessionInvoice).filter_by(
                    batch_session_id=batch_id
                ).all()
                invoice_nos = [bi.invoice_no for bi in batch_invoices]
                
                # Build filter conditions for items in remaining invoices
                zones_list = batch_session.zones.split(',')
                corridors_list = batch_session.corridors.split(',') if batch_session.corridors else []
                
                # 🔧 OPTIMIZED: Use .count() instead of .all() - faster when we only need to check existence
                remaining_items_count = db.session.query(InvoiceItem).filter(
                    InvoiceItem.invoice_no.in_(invoice_nos),
                    InvoiceItem.locked_by_batch_id == batch_id,
                    InvoiceItem.is_picked == False,
                    InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending'])
                ).count()
                
                if remaining_items_count > 0:
                    # There are more items in other invoices but we've reached the end of current invoice items
                    # The invoice advancement should have already been handled when the order completed
                    # Just redirect to continue - don't double increment!
                    current_app.logger.info(f"Sequential mode: Items remaining in other invoices, continuing at current index {batch_session.current_invoice_index}")
                    
                    # Clear the fixed item list to force regeneration
                    fixed_batch_key = 'batch_items_' + str(batch_id)
                    if fixed_batch_key in session:
                        session.pop(fixed_batch_key, None)
                        current_app.logger.info(f"Sequential mode: Cleared cache to regenerate items")
                    
                    db.session.commit()
                    
                    # Redirect back to continue with current/next invoice
                    return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
                else:
                    # No more items in any invoice - complete the batch
                    batch_session.status = 'Completed'
                    # Record picking completion time
                    batch_invoices = BatchSessionInvoice.query.filter_by(batch_session_id=batch_id).all()
                    for bi in batch_invoices:
                        breakdown = OrderTimeBreakdown.query.filter_by(
                            invoice_no=bi.invoice_no,
                            picker_username=current_user.username
                        ).first()
                        if breakdown:
                            breakdown.picking_completed = datetime.utcnow()
            else:
                # Consolidated mode - complete the batch
                batch_session.status = 'Completed'
                # Record picking completion time
                batch_invoices = BatchSessionInvoice.query.filter_by(batch_session_id=batch_id).all()
                for bi in batch_invoices:
                    breakdown = OrderTimeBreakdown.query.filter_by(
                        invoice_no=bi.invoice_no,
                        picker_username=current_user.username
                    ).first()
                    if breakdown:
                        breakdown.picking_completed = datetime.utcnow()
            
            # Count final stats for the completion message - only items in batch scope
            total_items_in_batch = 0
            picked_items_in_batch = 0
            
            for invoice_no in [bi.invoice_no for bi in batch_session.invoices]:
                # Build filter conditions for items that are actually in scope for this batch
                filter_conditions = [
                    InvoiceItem.invoice_no == invoice_no,
                    InvoiceItem.zone.in_(batch_session.zones.split(',')),
                    InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending', 'picked'])
                ]
                
                # Add corridor filter if corridors are specified for this batch
                if batch_session.corridors:
                    corridors_list = [c.strip() for c in batch_session.corridors.split(',')]
                    filter_conditions.append(InvoiceItem.corridor.in_(corridors_list))
                
                batch_zone_items = db.session.query(InvoiceItem).filter(
                    and_(*filter_conditions)
                ).all()
                
                for item in batch_zone_items:
                    total_items_in_batch += 1
                    if item.pick_status == 'picked':
                        picked_items_in_batch += 1
            
            if picked_items_in_batch >= total_items_in_batch and total_items_in_batch > 0:
                flash('All items in this batch have been picked!', 'success')
                current_app.logger.warning(f"✅ BATCH {batch_id} COMPLETED: All {total_items_in_batch} items picked")
            else:
                unpicked_count = total_items_in_batch - picked_items_in_batch
                flash(f'Batch completed! {picked_items_in_batch}/{total_items_in_batch} items picked. {unpicked_count} items left as exceptions.', 'success')
                current_app.logger.warning(f"✅ BATCH {batch_id} COMPLETED: {picked_items_in_batch}/{total_items_in_batch} items picked, {unpicked_count} exceptions")
            
            db.session.commit()
            return redirect(url_for('batch.picker_batch_list'))
        
        # Save changes to database
        db.session.commit()
        
        # Flash success message
        # Item picked - no flash message needed
        
        # Redirect to the next item
        return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
        
    except Exception as e:
        # Roll back the transaction
        db.session.rollback()
        flash(f'Error processing this item: {str(e)}', 'danger')
        current_app.logger.error(f"Error processing batch item: {str(e)}")
        return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))


@batch_bp.route('/batch/<int:batch_id>/force_complete')
@login_required
def force_complete_batch(batch_id):
    """Force complete a batch even if items remain unpicked"""
    try:
        batch_session = db.session.get(BatchPickingSession, batch_id)
        if not batch_session:
            flash('Batch session not found', 'danger')
            return redirect(url_for('batch.picker_batch_list'))
        
        # Check if user has permission to access this batch
        if batch_session.assigned_to != current_user.username:
            flash('You are not assigned to this batch', 'danger')
            return redirect(url_for('batch.picker_batch_list'))
        
        # Count all items that should be in this batch based on selected zones and corridors
        total_items_in_batch = 0
        picked_items_in_batch = 0
        
        for invoice_no in [bi.invoice_no for bi in batch_session.invoices]:
            # Build filter conditions for items that are actually in scope for this batch
            filter_conditions = [
                InvoiceItem.invoice_no == invoice_no,
                InvoiceItem.zone.in_(batch_session.zones.split(',')),
                InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending', 'picked'])
            ]
            
            # Add corridor filter if corridors are specified for this batch
            if batch_session.corridors:
                corridors_list = [c.strip() for c in batch_session.corridors.split(',')]
                filter_conditions.append(InvoiceItem.corridor.in_(corridors_list))
            
            batch_zone_items = db.session.query(InvoiceItem).filter(
                and_(*filter_conditions)
            ).all()
            
            for item in batch_zone_items:
                total_items_in_batch += 1
                if item.pick_status == 'picked':
                    picked_items_in_batch += 1
        
        current_app.logger.warning(f"🔍 FORCE BATCH COMPLETION: {picked_items_in_batch}/{total_items_in_batch} items picked")
        
        # Mark batch as complete regardless of unpicked items
        batch_session.status = 'Completed'
        
        # Update order statuses for all invoices in this batch
        from batch_aware_order_status import update_all_orders_after_batch_completion
        updated_orders = update_all_orders_after_batch_completion(batch_id)
        
        # Record an activity
        activity = ActivityLog(
            picker_username=current_user.username,
            activity_type='batch_force_complete',
            details=f"Force completed batch {batch_id}: {picked_items_in_batch}/{total_items_in_batch} items picked"
        )
        db.session.add(activity)
        
        if picked_items_in_batch >= total_items_in_batch and total_items_in_batch > 0:
            flash('Batch completed! All items were picked.', 'success')
            current_app.logger.warning(f"✅ BATCH {batch_id} FORCE COMPLETED: All {total_items_in_batch} items picked")
        else:
            unpicked_count = total_items_in_batch - picked_items_in_batch
            flash(f'Batch completed! {picked_items_in_batch}/{total_items_in_batch} items picked. {unpicked_count} items not found/available.', 'success')
            current_app.logger.warning(f"✅ BATCH {batch_id} FORCE COMPLETED: {picked_items_in_batch}/{total_items_in_batch} items picked, {unpicked_count} not available")
        
        db.session.commit()
        return redirect(url_for('batch.picker_batch_list'))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error completing batch: {str(e)}', 'danger')
        current_app.logger.error(f"Error force completing batch: {str(e)}")
        return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))

@batch_bp.route('/batch/<int:batch_id>/report_issue', methods=['POST'])
@login_required
def batch_report_issue(batch_id):
    """Handle reporting an issue with a batch item"""
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Check access
    if current_user.role not in ['admin', 'warehouse_manager'] and batch_session.assigned_to != current_user.username:
        flash('Access denied.', 'danger')
        return redirect(url_for('batch.picker_batch_list'))
    
    # Get form data
    issue_type = request.form.get('issueType', '')
    picked_qty = int(request.form.get('pickedQuantity', 0))
    notes = request.form.get('issueNotes', '')
    
    # Get current item info
    fixed_batch_key = 'batch_items_' + str(batch_id)
    if fixed_batch_key in session:
        items = session[fixed_batch_key]
        if batch_session.current_item_index < len(items):
            current_item = items[batch_session.current_item_index]
            
            # If any quantity was picked, allocate it to customers
            if picked_qty > 0:
                # Use the same allocation logic as normal picking
                source_items = current_item['source_items']
                sorted_sources = sorted(source_items, key=lambda x: x['invoice_no'])
                remaining_qty = picked_qty
                
                for source in sorted_sources:
                    if remaining_qty <= 0:
                        break
                        
                    invoice_no = source['invoice_no']
                    item_code = source['item_code']
                    required_qty = source['qty']
                    
                    # Allocate as much as possible to this invoice
                    allocated_qty = min(remaining_qty, required_qty)
                    
                    if allocated_qty > 0:
                        # Update the invoice item
                        invoice_item = InvoiceItem.query.filter_by(
                            invoice_no=invoice_no,
                            item_code=item_code
                        ).first()
                        
                        if invoice_item:
                            # Update the invoice item
                            invoice_item.picked_qty = allocated_qty
                            invoice_item.is_picked = True
                            invoice_item.pick_status = 'picked'
                            
                            # Record the batch picked item (prevent duplicates)
                            existing_batch_picked = BatchPickedItem.query.filter_by(
                                batch_session_id=batch_id,
                                invoice_no=invoice_no,
                                item_code=item_code
                            ).first()
                            
                            if existing_batch_picked:
                                # Update existing record instead of creating duplicate
                                existing_batch_picked.picked_qty = allocated_qty
                            else:
                                # Create new record
                                batch_picked = BatchPickedItem(
                                    batch_session_id=batch_id,
                                    invoice_no=invoice_no,
                                    item_code=item_code,
                                    picked_qty=allocated_qty
                                )
                                db.session.add(batch_picked)
                            
                            # Create exception if partially allocated
                            if allocated_qty != required_qty:
                                exception = PickingException(
                                    invoice_no=invoice_no,
                                    item_code=item_code,
                                    expected_qty=required_qty,
                                    picked_qty=allocated_qty,
                                    picker_username=current_user.username,
                                    reason=f"Batch picking (issue reported): {allocated_qty} allocated, {required_qty} required. Issue: {issue_type}. Notes: {notes}"
                                )
                                db.session.add(exception)
                            
                            # Reduce the remaining quantity
                            remaining_qty -= allocated_qty
                
                # Record activity for allocated items
                activity = ActivityLog(
                    picker_username=current_user.username,
                    activity_type='batch_item_issue',
                    details=f"Batch {batch_id}: Issue reported for {current_item['item_code']} - {picked_qty} allocated to customers. Issue: {issue_type}"
                )
                db.session.add(activity)
            
            # Create exceptions for customers who didn't receive their items
            total_required = current_item['total_qty']
            if picked_qty < total_required:
                # Find customers who didn't get their items
                source_items = current_item['source_items']
                sorted_sources = sorted(source_items, key=lambda x: x['invoice_no'])
                allocated_so_far = picked_qty
                
                for source in sorted_sources:
                    invoice_no = source['invoice_no']
                    item_code = source['item_code']
                    required_qty = source['qty']
                    
                    if allocated_so_far >= required_qty:
                        # This customer got their full allocation
                        allocated_so_far -= required_qty
                    else:
                        # This customer is short
                        shortage = required_qty - allocated_so_far
                        if allocated_so_far > 0:
                            # Partial allocation already recorded above
                            allocated_so_far = 0
                        else:
                            # No allocation for this customer
                            exception = PickingException(
                                invoice_no=invoice_no,
                                item_code=item_code,
                                expected_qty=required_qty,
                                picked_qty=0,
                                picker_username=current_user.username,
                                reason=f"Batch picking (issue reported): No items available. Issue: {issue_type}. Notes: {notes}"
                            )
                            db.session.add(exception)
            
            # Move to next item
            batch_session.current_item_index += 1
            db.session.commit()
    
    return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))

def delete_batch_comprehensive(batch_id, batch_name, admin_username):
    """
    Comprehensive batch deletion that cleans up all related data
    
    Args:
        batch_id: ID of the batch to delete
        batch_name: Name of the batch for logging
        admin_username: Username of admin performing deletion
    
    Returns:
        Summary string of what was deleted
    """
    deletion_counts = {
        'batch_picked_items': 0,
        'batch_session_invoices': 0,
        'activity_logs': 0,
        'picking_exceptions': 0,
        'unlocked_items': 0
    }
    
    # 1. Unlock items that were locked by this batch
    from batch_locking_utils import unlock_items_for_batch
    deletion_counts['unlocked_items'] = unlock_items_for_batch(batch_id, preserve_picked=False)
    
    # 2. Delete batch picked items
    deletion_counts['batch_picked_items'] = BatchPickedItem.query.filter_by(batch_session_id=batch_id).count()
    BatchPickedItem.query.filter_by(batch_session_id=batch_id).delete()
    
    # 3. Delete picking exceptions related to this batch
    batch_exceptions = PickingException.query.filter(
        (PickingException.reason.contains(f'batch {batch_id}')) |
        (PickingException.reason.contains(f'Batch {batch_id}')) |
        (PickingException.reason.contains(batch_name))
    ).all()
    deletion_counts['picking_exceptions'] = len(batch_exceptions)
    for exception in batch_exceptions:
        db.session.delete(exception)
    
    # 4. Delete activity logs related to this batch
    batch_logs = ActivityLog.query.filter(
        (ActivityLog.details.contains(f'batch {batch_id}')) |
        (ActivityLog.details.contains(f'Batch {batch_id}')) |
        (ActivityLog.details.contains(batch_name))
    ).all()
    deletion_counts['activity_logs'] = len(batch_logs)
    for log in batch_logs:
        db.session.delete(log)
    
    # 5. Delete batch session invoices
    deletion_counts['batch_session_invoices'] = BatchSessionInvoice.query.filter_by(batch_session_id=batch_id).count()
    BatchSessionInvoice.query.filter_by(batch_session_id=batch_id).delete()
    
    # 6. Delete the batch session itself
    batch_session = BatchPickingSession.query.get(batch_id)
    if batch_session:
        db.session.delete(batch_session)
    
    # 7. Create deletion activity log (use valid username from users table)
    deletion_log = ActivityLog(
        picker_username='administrator',
        activity_type='BATCH_DELETED',
        details=f'Comprehensive deletion of batch {batch_name} (ID: {batch_id}) by {admin_username}. '
               f'Cleaned up: {deletion_counts["batch_picked_items"]} picked items, '
               f'{deletion_counts["batch_session_invoices"]} invoice links, '
               f'{deletion_counts["activity_logs"]} activity logs, '
               f'{deletion_counts["picking_exceptions"]} exceptions, '
               f'{deletion_counts["unlocked_items"]} unlocked items'
    )
    db.session.add(deletion_log)
    
    # Commit all changes
    db.session.commit()
    
    # Return summary
    return (f"Removed {deletion_counts['batch_picked_items']} picked items, "
           f"{deletion_counts['activity_logs']} logs, "
           f"{deletion_counts['picking_exceptions']} exceptions, "
           f"unlocked {deletion_counts['unlocked_items']} items")

@batch_bp.route('/batch/delete/<int:batch_id>', methods=['POST'])
@login_required
def delete_batch(batch_id):
    """Delete a batch that hasn't had any items picked yet (admin only)"""
    # Only admin users can delete batches
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('batch.picker_batch_list'))
    
    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Check if any items have been picked in this batch
    picked_items_count = BatchPickedItem.query.filter_by(batch_session_id=batch_id).count()
    
    if picked_items_count > 0:
        flash(f'Cannot delete batch "{batch_session.batch_number or "BATCH-" + str(batch_session.id)}" - it has {picked_items_count} picked items.', 'warning')
        return redirect(url_for('batch.picker_batch_list'))
    
    # Check if batch is in "Created" status (safeguard)
    if batch_session.status not in ['Created']:
        flash(f'Cannot delete batch "{batch_session.batch_number or "BATCH-" + str(batch_session.id)}" - it is not in "Created" status.', 'warning')
        return redirect(url_for('batch.picker_batch_list'))
    
    batch_name = batch_session.batch_number or f"BATCH-{batch_session.id}"
    
    try:
        # Comprehensive batch deletion with proper cleanup
        deletion_summary = delete_batch_comprehensive(batch_id, batch_name, current_user.username)
        
        flash(f'Batch "{batch_name}" and all related data have been successfully deleted. {deletion_summary}', 'success')
        current_app.logger.info(f"Admin {current_user.username} deleted batch {batch_name} (ID: {batch_id}) - {deletion_summary}")
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting batch {batch_id}: {str(e)}")
        flash('An error occurred while deleting the batch. Please try again.', 'danger')
    
    return redirect(url_for('batch.picker_batch_list'))

@batch_bp.route('/batch/summary/<int:batch_id>')
@login_required
def batch_picking_summary(batch_id):
    """Mobile-optimized picking summary for pickers"""
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Check if user has access to this batch
    if current_user.role == 'picker' and batch_session.assigned_to != current_user.username:
        flash('Access denied. You can only view batches assigned to you.', 'danger')
        return redirect(url_for('batch.picker_batch_list'))
    
    # Get all items that match batch criteria (zones, corridors, locks) using consistent filtering
    filtered_items = batch_session.get_filtered_items(include_picked=True)
    
    # Preload invoice data to avoid N+1 queries
    invoice_nos = list(set(item.invoice_no for item in filtered_items))
    invoices_dict = {inv.invoice_no: inv for inv in Invoice.query.filter(Invoice.invoice_no.in_(invoice_nos)).all()}
    
    items = []
    picked_count = 0
    skipped_count = 0
    total_weight = 0
    
    # Process all filtered items consistently regardless of mode
    for item in filtered_items:
        # Get invoice details for routing and customer name from preloaded data
        invoice = invoices_dict.get(item.invoice_no)
        routing = invoice.routing if invoice else None
        customer_name = invoice.customer_name if invoice else 'Unknown Customer'
        
        # Check if this item has been picked via batch
        picked_item = BatchPickedItem.query.filter_by(
            batch_session_id=batch_session.id,
            item_code=item.item_code,
            invoice_no=item.invoice_no
        ).first()
        
        # Determine status based on actual item state
        if item.pick_status == 'picked' or picked_item:
            status = 'picked'
            picked_count += 1
        elif item.pick_status == 'skipped':
            status = 'skipped'
            skipped_count += 1
        else:
            status = 'pending'
            
        total_weight += (item.item_weight or 0) * (item.qty or 0)
        
        items.append({
            'item_code': item.item_code,
            'item_name': item.item_name,
            'location': item.location,
            'qty': item.qty,
            'picked_qty': picked_item.picked_qty if picked_item else (item.picked_qty if item.pick_status == 'picked' else 0),
            'pick_status': status,
            'skip_reason': item.skip_reason if item.pick_status == 'skipped' else None,
            'invoice_no': item.invoice_no,
            'routing': routing,
            'customer_name': customer_name
        })
    
    # If Consolidated mode, group identical items for cleaner display
    if batch_session.picking_mode == 'Consolidated':
        # Group items by item_code for consolidated display
        consolidated_items = {}
        for item in items:
            key = item['item_code']
            if key in consolidated_items:
                # Combine quantities for same item code
                consolidated_items[key]['qty'] += item['qty'] or 0
                consolidated_items[key]['picked_qty'] += item['picked_qty'] or 0
                # Keep the most relevant status (picked > skipped > pending)
                if item['pick_status'] == 'picked' or consolidated_items[key]['pick_status'] != 'picked':
                    consolidated_items[key]['pick_status'] = item['pick_status']
            else:
                consolidated_items[key] = item.copy()
        
        items = list(consolidated_items.values())
        
        # Recalculate counts for consolidated view
        picked_count = sum(1 for item in items if item['pick_status'] == 'picked')
        skipped_count = sum(1 for item in items if item['pick_status'] == 'skipped')
    # Group items by routing and customer, then sort by picking sequence
    # Get the actual picking sequence from get_grouped_items
    actual_picking_sequence = batch_session.get_grouped_items(include_picked=True)
    
    # Create a canonical minimum position mapping using unique (invoice_no, item_code, location) keys
    from collections import defaultdict
    min_pos = defaultdict(lambda: 10**9)
    
    for pos, picking_item in enumerate(actual_picking_sequence):
        if 'source_items' in picking_item and picking_item['source_items']:
            # Consolidated mode: assign minimum position to all source items
            for source_item in picking_item['source_items']:
                # Use simpler key without location to avoid format mismatches
                base = (source_item['invoice_no'], source_item['item_code'])
                min_pos[base] = min(min_pos[base], pos)
        else:
            # Sequential mode or simple item structure - handle direct item
            invoice_no = picking_item.get('invoice_no', '')
            # Use simpler key without location to avoid format mismatches
            base = (invoice_no, picking_item['item_code'])
            min_pos[base] = min(min_pos[base], pos)
    
    # Sort items individually by picking sequence first
    def get_item_order(item):
        # Use simpler key without location to avoid format mismatches
        base = (item['invoice_no'], item['item_code'])
        position = min_pos.get(base, 10**9)
        
        # Log any items that don't have a position in the sequence for debugging
        if position == 10**9:
            from flask import current_app
            current_app.logger.debug(f"Item not found in picking sequence: {base}")
        
        # Use location as secondary sort key to maintain consistent ordering within position
        location = item.get('location') or ''
        return (position, item.get('routing') or '', location, item['item_code'])
    
    items.sort(key=get_item_order)
    
    # Group items by routing and customer
    grouped_items = []
    current_group = None
    
    for item in items:
        routing_customer_key = (item.get('routing'), item['customer_name'], item['invoice_no'])
        
        # Start a new group if this is a different routing/customer combination
        if current_group is None or current_group['key'] != routing_customer_key:
            if current_group is not None:
                grouped_items.append(current_group)
            
            current_group = {
                'key': routing_customer_key,
                'routing': item.get('routing'),
                'customer_name': item['customer_name'],
                'invoice_no': item['invoice_no'],
                'group_items': [],
                'min_position': get_item_order(item)[0]  # Use first item's position for group sorting
            }
        
        current_group['group_items'].append(item)
    
    # Add the last group
    if current_group is not None:
        grouped_items.append(current_group)
    
    # Sort groups by routing number numerically (same logic as batch creation)
    def get_routing_sort_key(group):
        routing = group.get('routing')
        if routing is None:
            return -1
        try:
            return float(routing)
        except (ValueError, TypeError):
            return -1
    
    # Sort in descending order (highest routing first, like delivery routes)
    grouped_items.sort(key=get_routing_sort_key, reverse=True)
    
    # Update groups to be the grouped structure
    groups = grouped_items
    
    # Calculate total items from all groups
    total_items = sum(len(group['group_items']) for group in groups) if groups else 0
    
    # Defensive fix: ensure group_items is always a list
    for group in groups:
        group_items = group.get('group_items', [])
        if callable(group_items):
            try:
                group['group_items'] = list(group_items())
            except:
                group['group_items'] = []
        elif not isinstance(group_items, list):
            group['group_items'] = list(group_items or [])
    
    return render_template('batch_picking_summary.html',
                          batch_session=batch_session,
                          groups=groups,
                          picked_count=picked_count,
                          skipped_count=skipped_count,
                          total_items=total_items,
                          total_weight=total_weight)

@batch_bp.route('/picker/batch/skip/<int:batch_id>', methods=['POST'])
@login_required
def skip_batch_item(batch_id):
    """Skip an item in a batch for later picking"""
    # Only picker users can access this endpoint
    if current_user.role not in ['admin', 'warehouse_manager'] and current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))

    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Check if this picker is assigned to this batch
    if current_user.role not in ['admin', 'warehouse_manager'] and batch_session.assigned_to != current_user.username:
        flash('You are not assigned to this batch picking session.', 'danger')
        return redirect(url_for('batch.picker_batch_list'))
    
    # CRUCIAL FIX: Use our fixed item list from the session
    fixed_batch_key = 'batch_items_' + str(batch_id)
    
    if fixed_batch_key in session:
        items = session[fixed_batch_key]
        # Log the state
        current_app.logger.warning(f"🔄 SKIP: Using fixed list with {len(items)} items, current index: {batch_session.current_item_index}")
    else:
        # Fallback to database query if session data is missing
        items = batch_session.get_grouped_items()
        current_app.logger.warning(f"⚠️ FALLBACK IN SKIP: Using database query, found {len(items if items else [])} items")
    
    if not items or batch_session.current_item_index >= len(items):
        flash('No more items to pick in this batch session.', 'info')
        return redirect(url_for('batch.picker_batch_list'))
    
    # Get the current item
    current_item = items[batch_session.current_item_index]
    
    # Get the skip reason, if provided
    skip_reason = request.form.get('skip_reason', 'Item skipped')
    
    # If "Other" was selected and a custom reason provided, use that
    if skip_reason == 'Other' and request.form.get('other_skip_reason'):
        skip_reason = request.form.get('other_skip_reason')
    
    try:
        # Process all source items
        for source in current_item['source_items']:
            invoice_no = source['invoice_no']
            item_code = source['item_code']
            
            # Mark the invoice item as skipped_pending
            invoice_item = InvoiceItem.query.filter_by(
                invoice_no=invoice_no,
                item_code=item_code
            ).first()
            
            if invoice_item:
                invoice_item.pick_status = 'skipped_pending'
                invoice_item.skip_reason = skip_reason
                invoice_item.skip_timestamp = utc_now_for_db()
                invoice_item.skip_count += 1
        
        # Move to the next item
        batch_session.current_item_index += 1
        
        # Save changes to database
        db.session.commit()
        
        # Flash success message
        flash(f'Item {current_item["item_code"]} skipped and will be picked later.', 'info')
        
        # Redirect to the next item or completion
        if batch_session.current_item_index >= len(items):
            flash('All other items have been processed. Please resolve the skipped items.', 'warning')
            # TODO: Create a dedicated "resolve skipped items" interface
            return redirect(url_for('batch.batch_picking_view', batch_id=batch_id))
        
        return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
        
    except Exception as e:
        # Roll back the transaction
        db.session.rollback()
        flash(f'Error skipping this item: {str(e)}', 'danger')
        current_app.logger.error(f"Error skipping batch item: {str(e)}")
        return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))

@batch_bp.route('/admin/batch/verify/<int:batch_id>')
@login_required
def manual_verify_batch(batch_id):
    """Manually verify specific items are included in a batch"""
    # Only admin users can access this page
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Get the item codes to check from query parameter
    items_to_check = request.args.get('items', '').split(',')
    
    results = {}
    
    # Check if these items exist in the batch session
    if items_to_check and items_to_check[0]:  # Check if first item is empty string
        # Get the zones for this batch
        zones = batch_session.zones.split(',')
        
        # Get the invoices in this batch
        batch_invoices = BatchSessionInvoice.query.filter_by(
            batch_session_id=batch_id
        ).all()
        
        invoice_nos = [bi.invoice_no for bi in batch_invoices]
        
        # Check for each item
        for item_code in items_to_check:
            # Try to find the item in any invoice that's part of this batch
            item = InvoiceItem.query.filter(
                InvoiceItem.invoice_no.in_(invoice_nos),
                InvoiceItem.item_code == item_code,
                InvoiceItem.zone.in_(zones)
            ).first()
            
            if item:
                results[item_code] = {
                    'found': True,
                    'invoice_no': item.invoice_no,
                    'zone': item.zone,
                    'location': item.location,
                    'is_picked': item.is_picked,
                    'pick_status': item.pick_status
                }
            else:
                results[item_code] = {
                    'found': False
                }
    
    return render_template('batch_debug.html',
                          batch_session=batch_session,
                          results=results,
                          items_checked=items_to_check)

@batch_bp.route('/batch/completion-summary/<int:batch_id>')
@login_required
def batch_completion_summary(batch_id):
    """Show batch completion summary with print options for pickers"""
    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Check if this user has access to this batch
    if (current_user.role not in ['admin', 'warehouse_manager'] and 
        batch_session.assigned_to != current_user.username):
        flash('You do not have access to this batch picking session.', 'danger')
        return redirect(url_for('batch.picker_batch_list'))
    
    # Check if batch is actually completed
    if batch_session.status != 'Completed':
        flash('This batch is not yet completed.', 'warning')
        return redirect(url_for('batch.picker_batch_list'))
    
    return render_template('batch_completion_summary.html',
                          batch_session=batch_session)

@batch_bp.route('/batch/print-reports/<int:batch_id>')
@login_required
def batch_print_reports(batch_id):
    """Print reports for a batch picking session"""
    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Check if this user has access to this batch
    # Allow access for: admin, assigned picker, or any picker if batch is completed
    if (current_user.role not in ['admin', 'warehouse_manager'] and 
        batch_session.assigned_to != current_user.username and 
        not (current_user.role == 'picker' and batch_session.status == 'Completed')):
        flash('You do not have access to this batch picking session.', 'danger')
        return redirect(url_for('index'))
    
    # Use the exact same sequence as actual picking by getting the canonical order from get_grouped_items
    # This ensures 100% parity between report and actual picking sequence for both sequential and consolidated modes
    actual_picking_sequence = batch_session.get_grouped_items(include_picked=True)
    
    # Create a canonical minimum position mapping using unique (invoice_no, item_code, location) keys
    from collections import defaultdict
    min_pos = defaultdict(lambda: 10**9)
    
    for pos, picking_item in enumerate(actual_picking_sequence):
        # For consolidated mode, items have source_items; for sequential mode, they might not
        if 'source_items' in picking_item and picking_item['source_items']:
            # Consolidated mode: assign minimum position to all source items
            for source_item in picking_item['source_items']:
                base = (source_item['invoice_no'], source_item['item_code'], source_item.get('location') or '')
                min_pos[base] = min(min_pos[base], pos)
        else:
            # Sequential mode or simple item structure - handle direct item
            base = (picking_item.get('invoice_no'), picking_item['item_code'], picking_item.get('location') or '')
            min_pos[base] = min(min_pos[base], pos)
    
    # Pre-load all invoice items and batch picked items to avoid N+1 queries
    batch_invoices = BatchSessionInvoice.query.filter_by(batch_session_id=batch_id).all()
    invoice_nos = [bi.invoice_no for bi in batch_invoices]
    
    # Load all items for this batch (both picked and unpicked)
    all_invoice_items = InvoiceItem.query.filter(
        InvoiceItem.invoice_no.in_(invoice_nos),
        InvoiceItem.locked_by_batch_id == batch_id
    ).all()
    
    all_batch_picked = BatchPickedItem.query.filter_by(batch_session_id=batch_id).all()
    
    # Create lookups for fast access using composite keys that match the sequencing logic
    invoice_items_lookup = {}
    batch_picked_lookup = {}
    
    for item in all_invoice_items:
        key = (item.invoice_no, item.item_code, item.location or '')
        invoice_items_lookup[key] = item
        
    for batch_item in all_batch_picked:
        # Need to get the location for batch picked items by looking up the invoice item
        invoice_item = next((item for item in all_invoice_items 
                           if item.invoice_no == batch_item.invoice_no and item.item_code == batch_item.item_code), None)
        location = invoice_item.location if invoice_item else ''
        key = (batch_item.invoice_no, batch_item.item_code, location or '')
        batch_picked_lookup[key] = batch_item.picked_qty
    
    # Sort batch invoices by routing number descending (for invoice-level organization)
    def get_routing_key(bi):
        routing = bi.invoice.routing
        if routing is None or routing == '':
            return -1
        try:
            return float(routing)
        except (ValueError, TypeError):
            return -1
    
    sorted_batch_invoices = sorted(batch_invoices, key=get_routing_key, reverse=True)
    
    # Build invoices data using the canonical order from actual picking
    invoices_data = []
    
    for bi in sorted_batch_invoices:
        invoice = Invoice.query.get(bi.invoice_no)
        if not invoice:
            continue
        
        # Get all items for this invoice that are in the batch
        invoice_items = [item for item in all_invoice_items if item.invoice_no == invoice.invoice_no]
        
        # Sort items using the canonical minimum position mapping from actual picking sequence
        def get_item_order(item):
            base = (item.invoice_no, item.item_code, item.location or '')
            return (min_pos.get(base, 10**9), item.zone or '', item.corridor or '', item.location or '', item.item_code)
        
        invoice_items.sort(key=get_item_order)
        
        batch_picked = []
        manually_picked = []
        unpicked = []
        
        for item in invoice_items:
            item_key = (item.invoice_no, item.item_code, item.location or '')
            batch_picked_qty = batch_picked_lookup.get(item_key)
            
            if batch_picked_qty is not None:
                # Item was picked through batch
                batch_picked.append({
                    'item': item,
                    'picked_qty': batch_picked_qty
                })
            elif item.is_picked:
                # Item was picked manually (not through batch)
                manually_picked.append({
                    'item': item,
                    'picked_qty': item.picked_qty
                })
            else:
                # Item is unpicked
                unpicked.append(item)
        
        # Add to the list
        invoices_data.append({
            'invoice': invoice,
            'batch_picked': batch_picked,
            'manually_picked': manually_picked,
            'unpicked': unpicked
        })
    
    # Get batch invoices with proper data structure for template
    batch_invoices_with_data = []
    invoice_items = {}  # Dictionary expected by template
    
    for bi in batch_invoices:
        invoice = Invoice.query.get(bi.invoice_no)
        if invoice:
            # Calculate actual picked vs required for this invoice
            batch_picked_items = BatchPickedItem.query.filter_by(
                batch_session_id=batch_id,
                invoice_no=invoice.invoice_no
            ).all()
            
            # Create lookup for allocated quantities
            batch_picked_lookup = {item.item_code: item.picked_qty for item in batch_picked_items}
            
            # Get original items that are actually locked by this batch
            original_items = InvoiceItem.query.filter(
                InvoiceItem.invoice_no == invoice.invoice_no,
                InvoiceItem.locked_by_batch_id == batch_session.id
            ).all()
            
            # Calculate actual status based on allocations
            total_required = len(original_items)
            total_allocated = len(batch_picked_items)
            
            # Update invoice completion status
            if total_allocated == total_required:
                # Check if all allocations match requirements
                full_completion = True
                for item in original_items:
                    allocated = batch_picked_lookup.get(item.item_code, 0)
                    if allocated != item.qty:
                        full_completion = False
                        break
                bi.completion_status = "Completed" if full_completion else "In Progress"
            elif total_allocated > 0:
                bi.completion_status = "In Progress"
            else:
                bi.completion_status = "Not Started"
            
            batch_invoices_with_data.append((bi, invoice))
            invoice_items[invoice.invoice_no] = original_items
    
    # Render the print reports page
    return render_template('batch_print_reports.html',
                          batch=batch_session,
                          batch_session=batch_session,
                          batch_invoices=batch_invoices_with_data,
                          invoice_items=invoice_items,
                          invoices_data=invoices_data)

@batch_bp.route('/admin/batch/print-report/<int:batch_id>')
@login_required
def batch_admin_print_report(batch_id):
    """Admin picking report for completed batches"""
    # Allow admin and picker users to access print reports
    if current_user.role not in ['admin', 'picker']:
        flash('Access denied. Insufficient privileges.', 'danger')
        return redirect(url_for('index'))
    
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    current_time = get_local_time()
    
    # Prepare invoice data for the report
    invoices = []
    
    # Sort batch invoices by routing number descending for print report order
    # Convert routing to float for proper numeric sorting
    def get_routing_key(bi):
        routing = bi.invoice.routing
        if routing is None or routing == '':
            return -1  # Put empty routing at the end
        try:
            return float(routing)
        except (ValueError, TypeError):
            return -1  # Put invalid routing at the end
    
    sorted_batch_invoices = sorted(batch_session.invoices, 
                                 key=get_routing_key, 
                                 reverse=True)
    

    
    for bi in sorted_batch_invoices:
        invoice = bi.invoice
        
        # Get picked and exception items based on actual allocations
        picked_items = []
        exception_items = []
        total_lines = 0
        total_units = 0
        total_weight = 0
        picked_count = 0
        
        # Get items that were actually allocated to this invoice in the batch
        batch_picked_items = BatchPickedItem.query.filter_by(
            batch_session_id=batch_session.id,
            invoice_no=invoice.invoice_no
        ).all()
        
        # Create a lookup for batch picked quantities
        batch_picked_lookup = {item.item_code: item.picked_qty for item in batch_picked_items}
        
        for item in invoice.items:
            # Only include items that are actually locked by THIS specific batch
            # Check if this item is locked by this batch
            item_in_batch_scope = (item.locked_by_batch_id == batch_session.id)
            
            if item_in_batch_scope:
                total_lines += 1
                original_qty = item.qty or 0
                allocated_qty = batch_picked_lookup.get(item.item_code, 0)
                
                total_units += original_qty
                total_weight += (item.item_weight or 0) * original_qty
                
                # Create a copy of the item with correct allocated quantity
                item_data = {
                    'item_code': item.item_code,
                    'item_name': item.item_name,
                    'location': item.location,
                    'qty': original_qty,  # Required quantity
                    'picked_qty': allocated_qty,  # Actually allocated quantity
                    'unit_type': item.unit_type,
                    'pack': item.pack,
                    'item_weight': item.item_weight,
                    'barcode': item.barcode
                }
                
                if allocated_qty > 0:
                    picked_items.append(item_data)
                    picked_count += 1
                    
                    # If allocated less than required, also add to exceptions
                    if allocated_qty < original_qty:
                        exception_data = item_data.copy()
                        exception_data['shortage_qty'] = original_qty - allocated_qty
                        exception_data['reason'] = f"Shortage: {allocated_qty} allocated, {original_qty} required"
                        exception_items.append(exception_data)
                elif original_qty > 0:
                    # No allocation but was required - full exception
                    exception_data = item_data.copy()
                    exception_data['shortage_qty'] = original_qty
                    exception_data['reason'] = f"Not allocated: 0 received, {original_qty} required"
                    exception_items.append(exception_data)
        
        # Also check for picking exceptions in the database, but only for items in batch scope
        picking_exceptions = PickingException.query.filter_by(
            invoice_no=invoice.invoice_no
        ).filter(
            PickingException.reason.contains('Batch picking')
        ).all()
        
        for exc in picking_exceptions:
            # Only include this exception if the item was actually in the batch scope
            item_in_batch_scope = False
            
            # Check if this item was in the selected zones and corridors for this batch
            batch_item = InvoiceItem.query.filter_by(
                invoice_no=invoice.invoice_no,
                item_code=exc.item_code
            ).first()
            
            if batch_item:
                # Check if this item is actually locked by this batch
                item_in_batch_scope = (batch_item.locked_by_batch_id == batch_session.id)
            
            # Only add this exception if the item was actually supposed to be picked in this batch
            if item_in_batch_scope:
                existing = next((item for item in exception_items if item['item_code'] == exc.item_code), None)
                if not existing:
                    # Get the actual item details instead of "Unknown"
                    if batch_item:
                        exception_items.append({
                            'item_code': exc.item_code,
                            'item_name': batch_item.item_name or 'Unknown',
                            'location': batch_item.location or 'Unknown',
                            'qty': exc.expected_qty,
                            'picked_qty': exc.picked_qty,
                            'shortage_qty': exc.expected_qty - exc.picked_qty,
                            'reason': exc.reason,
                            'unit_type': batch_item.unit_type or '',
                            'pack': batch_item.pack or '',
                            'item_weight': batch_item.item_weight or 0,
                            'barcode': batch_item.barcode or ''
                        })
                    else:
                        exception_items.append({
                            'item_code': exc.item_code,
                            'item_name': 'Unknown',
                            'location': 'Unknown',
                            'qty': exc.expected_qty,
                            'picked_qty': exc.picked_qty,
                            'shortage_qty': exc.expected_qty - exc.picked_qty,
                            'reason': exc.reason,
                            'unit_type': '',
                            'pack': '',
                            'item_weight': 0,
                            'barcode': ''
                        })
        
        # Calculate completion based on ALL picked items (batch + manual) for items in batch scope
        total_picked_items = 0
        for item in invoice.items:
            if item.locked_by_batch_id == batch_session.id:
                # Item was in batch scope - check if picked (either batch or manual)
                if item.is_picked and item.picked_qty and item.picked_qty > 0:
                    total_picked_items += 1
        
        completion_percentage = (total_picked_items / total_lines * 100) if total_lines > 0 else 0
        
        # Include ALL invoices that are part of this batch (total_lines > 0)
        # This ensures the sort order is preserved
        if total_lines > 0:
            invoices.append({
                'invoice': invoice,
                'picked_items': picked_items,
                'exception_items': exception_items,
                'unpicked_items': [],  # We'll handle unpicked items separately
                'total_lines': total_lines,
                'total_units': total_units,
                'total_weight': total_weight,
                'completion_percentage': completion_percentage
            })
    
    return render_template('batch_admin_print_report.html',
                          batch_session=batch_session,
                          invoices=invoices,
                          current_time=current_time)