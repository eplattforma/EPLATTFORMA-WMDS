"""
Route handlers for the batch picking system
"""
from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify, session, current_app
from sqlalchemy import text
from flask_login import login_required, current_user
from sqlalchemy import and_, func, desc, asc
from timezone_utils import utc_now_for_db

from app import db
from models import User, Invoice, InvoiceItem, PickingException, BatchPickingSession, BatchSessionInvoice, BatchPickedItem, Setting, ActivityLog

# Create a blueprint for batch picking routes
batch_bp = Blueprint('batch', __name__)

@batch_bp.route('/admin/batch/manage')
@login_required
def batch_picking_manage():
    """Admin page to manage batch picking sessions"""
    # Only admin users can access this page
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))

    # Get active batch sessions
    active_sessions = BatchPickingSession.query.filter(
        BatchPickingSession.status.in_(['Created', 'In Progress'])
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

@batch_bp.route('/admin/batch/simple', methods=['GET', 'POST'])
@login_required
def batch_picking_create_simple():
    """Simple admin page to create a new batch picking session"""
    # Only admin users can access this page
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        # Get form data
        name = request.form.get('name')
        zones = request.form.get('zones')
        picking_mode = request.form.get('picking_mode', 'Sequential')
        
        if not name or not zones:
            flash('Please provide a name and zones for the batch picking session.', 'warning')
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
        
        # Find invoices that have items in the specified zones
        invoices_with_items = db.session.query(Invoice.invoice_no).join(InvoiceItem).filter(
            InvoiceItem.zone.in_(zone_list),
            InvoiceItem.is_picked == False,
            InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending']),
            Invoice.status.in_(['Not Started', 'In Progress'])
        ).group_by(Invoice.invoice_no).all()
        
        # Extract invoice numbers
        invoice_numbers = [invoice[0] for invoice in invoices_with_items]
        
        if not invoice_numbers:
            flash('No invoices found with items in the specified zones.', 'warning')
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

@batch_bp.route('/admin/batch/filter', methods=['GET'])
@login_required
def batch_picking_filter():
    """Admin page to filter invoices for a batch"""
    # Only admin users can access this page
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get all zones for the filter dropdown
    zones = db.session.query(InvoiceItem.zone).filter(
        InvoiceItem.zone != None,
        InvoiceItem.zone != ''
    ).distinct().order_by(InvoiceItem.zone).all()
    
    # Extract zone values
    zone_list = [zone[0] for zone in zones if zone[0]]
    
    return render_template('batch_picking_filter.html', zones=zone_list)

@batch_bp.route('/admin/batch/filter-invoices', methods=['POST'])
@login_required
def filter_invoices_for_batch():
    """Filter invoices for batch picking and show selection interface"""
    # Only admin users can access this page
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get form data
    zones = request.form.getlist('zones')
    include_partial = request.form.get('include_partial') == 'on'
    
    if not zones:
        flash('Please select at least one zone to filter invoices.', 'warning')
        return redirect(url_for('batch.batch_picking_filter'))
    
    # Find invoices with items in selected zones
    query = db.session.query(
        Invoice
    ).join(
        InvoiceItem
    ).filter(
        InvoiceItem.zone.in_(zones),
        InvoiceItem.is_picked == False,
        InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending'])
    )
    
    if not include_partial:
        # Only include invoices that haven't been picked at all
        query = query.filter(Invoice.status == 'Not Started')
    else:
        # Include partially picked invoices too
        query = query.filter(Invoice.status.in_(['Not Started', 'In Progress']))
    
    # Only include each invoice once
    query = query.group_by(Invoice.invoice_no)
    
    # Execute the query
    invoices = query.all()
    
    if not invoices:
        flash('No invoices found with items in the selected zones.', 'warning')
        return redirect(url_for('batch.batch_picking_filter'))
    
    # Get picker list for assignment
    pickers = User.query.filter_by(role='picker').all()
    
    # Create a default session name based on zones and timestamp
    now = datetime.now().strftime('%Y-%m-%d_%H:%M')
    zone_prefix = zones[0] if len(zones) == 1 else f"{zones[0]}_Plus_{len(zones)-1}"
    default_name = f"{zone_prefix}_Batch_{now}"
    
    # Pass invoices to the create batch page for selection
    return render_template('batch_picking_create.html',
                          invoices=invoices,
                          selected_zones=','.join(zones),
                          pickers=pickers,
                          default_name=default_name)

@batch_bp.route('/admin/batch/create', methods=['POST'])
@login_required
def batch_picking_create():
    """Create a new batch picking session from selected invoices"""
    # Only admin users can access this page
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get form data
    name = request.form.get('name')
    zones = request.form.get('zones')
    picking_mode = request.form.get('picking_mode', 'Sequential')
    picker_username = request.form.get('picker')
    invoice_nos = request.form.getlist('invoices')
    
    if not name or not zones or not invoice_nos:
        flash('Please provide a name, zones, and select at least one invoice.', 'warning')
        return redirect(url_for('batch.batch_picking_filter'))
    
    # Generate a unique batch number
    from batch_utils import generate_batch_number
    batch_number = generate_batch_number()
    
    # Create a new batch picking session
    batch_session = BatchPickingSession(
        name=name,
        batch_number=batch_number,
        zones=zones,
        created_by=current_user.username,
        picking_mode=picking_mode,
        assigned_to=picker_username if picker_username else None
    )
    
    try:
        # Add the batch session to the database
        db.session.add(batch_session)
        db.session.flush()  # Get the ID without committing
        
        # Add invoices to the batch session
        for invoice_no in invoice_nos:
            batch_invoice = BatchSessionInvoice(
                batch_session_id=batch_session.id,
                invoice_no=invoice_no
            )
            db.session.add(batch_invoice)
        
        # Commit changes
        db.session.commit()
        
        flash(f'Batch picking session "{name}" created successfully with {len(invoice_nos)} invoices.', 'success')
        return redirect(url_for('batch.batch_picking_view', batch_id=batch_session.id))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error creating batch picking session: {str(e)}', 'danger')
        return redirect(url_for('batch.batch_picking_filter'))

@batch_bp.route('/admin/batch/filter-by-zone', methods=['POST'])
@login_required
def filter_invoices_by_zone():
    """API endpoint to filter invoices by zone"""
    # Only admin users can access this endpoint
    if current_user.role != 'admin':
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
        func.count(InvoiceItem.item_code).label('item_count')
    ).join(
        InvoiceItem
    ).filter(
        InvoiceItem.zone.in_(zones),
        InvoiceItem.is_picked == False,
        InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending'])
    )
    
    if not include_partial:
        # Only include invoices that haven't been picked at all
        query = query.filter(Invoice.status == 'Not Started')
    else:
        # Include partially picked invoices too
        query = query.filter(Invoice.status.in_(['Not Started', 'In Progress']))
    
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
            'item_count': invoice.item_count
        }
        for invoice in invoices
    ]
    
    return jsonify({'success': True, 'invoices': result})

@batch_bp.route('/admin/batch/view/<int:batch_id>')
@login_required
def batch_picking_view(batch_id):
    """Admin page to view a batch picking session"""
    # Only admin users can access this page
    if current_user.role != 'admin':
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
    
    return render_template('batch_picking_view.html',
                          batch_session=batch_session,
                          invoices=invoices,
                          pickers=pickers)

@batch_bp.route('/admin/batch/assign/<int:batch_id>', methods=['POST'])
@login_required
def batch_picking_assign(batch_id):
    """Assign a picker to a batch picking session"""
    # Only admin users can access this endpoint
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get the picker username from the form
    picker_username = request.form.get('picker')
    
    if not picker_username:
        flash('Please select a picker to assign.', 'warning')
        return redirect(url_for('batch.batch_picking_view', batch_id=batch_id))
    
    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Assign the picker
    batch_session.assigned_to = picker_username
    
    # Save changes to database
    db.session.commit()
    
    flash(f'Picker {picker_username} assigned to batch picking session.', 'success')
    return redirect(url_for('batch.batch_picking_view', batch_id=batch_id))

@batch_bp.route('/admin/batch/unassign/<int:batch_id>', methods=['POST'])
@login_required
def batch_picking_unassign(batch_id):
    """Unassign a picker from a batch picking session"""
    # Only admin users can access this endpoint
    if current_user.role != 'admin':
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Unassign the picker
    batch_session.assigned_to = None
    
    # Save changes to database
    db.session.commit()
    
    flash('Picker unassigned from batch picking session.', 'success')
    return redirect(url_for('batch.batch_picking_view', batch_id=batch_id))

@batch_bp.route('/picker/batch/list')
@login_required
def picker_batch_list():
    """Picker page to view assigned batch picking sessions"""
    # Only picker users can access this page
    if current_user.role != 'admin' and current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get batch sessions assigned to this picker
    batch_sessions = BatchPickingSession.query.filter(
        BatchPickingSession.assigned_to == current_user.username,
        BatchPickingSession.status.in_(['Created', 'In Progress'])
    ).order_by(BatchPickingSession.created_at.desc()).all()
    
    # Get recently completed batch sessions
    completed_sessions = BatchPickingSession.query.filter(
        BatchPickingSession.assigned_to == current_user.username,
        BatchPickingSession.status == 'Completed'
    ).order_by(BatchPickingSession.created_at.desc()).limit(5).all()
    
    return render_template('batch_picking_list.html',
                          batch_sessions=batch_sessions,
                          completed_sessions=completed_sessions)

@batch_bp.route('/picker/batch/start/<int:batch_id>')
@login_required
def start_batch_picking(batch_id):
    """Start picking a batch"""
    # Only picker users can access this endpoint
    if current_user.role != 'admin' and current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Check if this picker is assigned to this batch
    if current_user.role != 'admin' and batch_session.assigned_to != current_user.username:
        flash('You are not assigned to this batch picking session.', 'danger')
        return redirect(url_for('batch.picker_batch_list'))
    
    # Update the status to In Progress
    batch_session.status = 'In Progress'
    
    # Calculate ALL items in the batch - all zones for all invoices
    zone_list = batch_session.zones.split(',')
    all_batch_items = []
    
    # Get all invoices in this batch
    batch_invoices = BatchSessionInvoice.query.filter_by(
        batch_session_id=batch_id
    ).all()
    
    for bi in batch_invoices:
        # Get items for this invoice in the batch zones
        invoice_items = InvoiceItem.query.filter(
            InvoiceItem.invoice_no == bi.invoice_no,
            InvoiceItem.zone.in_(zone_list),
            InvoiceItem.is_picked == False,
            InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending'])
        ).all()
        
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
                    'total_qty': item.qty,
                    'source_items': [
                        {
                            'invoice_no': item.invoice_no,
                            'item_code': item.item_code,
                            'qty': item.qty
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
                    'total_qty': item.qty,
                    'source_items': [
                        {
                            'invoice_no': item.invoice_no,
                            'item_code': item.item_code,
                            'qty': item.qty
                        }
                    ]
                })
    
    # Sort items by location to optimize picking route
    all_batch_items.sort(key=lambda x: x['location'] if x['location'] else 'ZZZ')
    
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
                {'invoice_no': s['invoice_no'], 'item_code': s['item_code'], 'qty': s['qty']} 
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
    
    current_app.logger.warning(f"🚀 COMPLETE BATCH FIX: Starting batch {batch_id} with fixed list of {len(all_batch_items) if all_batch_items else 0} items at index 0")
    
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
    if current_user.role != 'admin' and current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))

    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Check if this picker is assigned to this batch
    if current_user.role != 'admin' and batch_session.assigned_to != current_user.username:
        flash('You are not assigned to this batch picking session.', 'danger')
        return redirect(url_for('batch.picker_batch_list'))
    
    # Reset item index if requested (for debugging)
    if request.args.get('reset') == 'true' and current_user.role == 'admin':
        batch_session.current_item_index = 0
        db.session.commit()
        current_app.logger.info(f"Reset batch item index to 0 for debugging")
        
    # COMPLETE BATCH PICKING FIX - USE A FIXED LIST THROUGHOUT THE ENTIRE PROCESS
    # Check if we already have a fixed items list for this batch in the session
    fixed_batch_key = 'batch_items_' + str(batch_id)
    
    # If we don't have a fixed list yet, or if we're restarting the batch, create one
    if fixed_batch_key not in session or session.pop('batch_start_' + str(batch_id), False):
        # Generate the complete list of ALL items in the batch
        all_batch_items = batch_session.get_grouped_items()
        
        # Save these items in the session
        if all_batch_items:
            # Serialize the batch items for session storage
            serialized_items = []
            for item in all_batch_items:
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
                    'source_items': [
                        {'invoice_no': s['invoice_no'], 'item_code': s['item_code'], 'qty': s['qty']} 
                        for s in item['source_items']
                    ]
                }
                serialized_items.append(serialized_item)
            
            # Store in session
            session[fixed_batch_key] = serialized_items
            current_app.logger.warning(f"📋 FIXED LIST CREATED: {len(serialized_items)} items for batch {batch_id}")
            
            # Always start at the beginning 
            batch_session.current_item_index = 0
            db.session.commit()
    
    # Now use our fixed item list
    if fixed_batch_key in session:
        items = session[fixed_batch_key]
        current_app.logger.warning(f"📋 USING FIXED LIST: {len(items)} items, current index: {batch_session.current_item_index}")
    else:
        # Fallback to database query if session data is missing
        items = batch_session.get_grouped_items()
        current_app.logger.warning(f"⚠️ FALLBACK: Using database query, found {len(items if items else [])} items")
    
    # CRITICAL DEBUG CHECK - Verify all items are in the fixed list
    if current_user.role == 'admin' and request.args.get('debug') == 'items':
        # Get list of specific item codes to check for
        check_items = request.args.get('items', '').split(',')
        if check_items:
            current_app.logger.warning(f"CHECKING FOR PROBLEMATIC ITEMS: {', '.join(check_items)}")
            
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
    
    # Safety check if we're at the end of the batch
    if not items or batch_session.current_item_index >= len(items):
        # Check if all items are truly picked
        if batch_session.status != 'Completed':
            batch_session.status = 'Completed'
            db.session.commit()
            
        flash('All items in this batch have been picked!', 'success')
        return redirect(url_for('batch.batch_print_reports', batch_id=batch_id))
    
    # Get the current item to pick
    current_item = items[batch_session.current_item_index]
    
    # CRITICAL DEBUG LOG
    current_app.logger.warning(f"🔍 BEFORE PICK: Batch {batch_id} item index is {batch_session.current_item_index}/{len(items)}")
    current_app.logger.warning(f"🔍 Item code at current index: {current_item['item_code']}")
    
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
    
    # Render the picking page
    return render_template('batch_picking_item.html',
                          batch_session=batch_session,
                          item=current_item,
                          show_next_location=show_next_location,
                          next_location=next_location,
                          show_product_image=show_product_image,
                          require_skip_reason=require_skip_reason,
                          skip_reasons=skip_reasons,
                          total_items=len(items),
                          current_index=batch_session.current_item_index)

@batch_bp.route('/picker/batch/confirm/<int:batch_id>', methods=['POST'])
@login_required
def confirm_batch_item(batch_id):
    """Confirm a picked item in a batch - redirects to confirmation screen"""
    # Only picker users can access this endpoint
    if current_user.role != 'admin' and current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))

    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Check if this picker is assigned to this batch
    if current_user.role != 'admin' and batch_session.assigned_to != current_user.username:
        flash('You are not assigned to this batch picking session.', 'danger')
        return redirect(url_for('batch.picker_batch_list'))

    # CRUCIAL FIX: Use our fixed item list from the session
    # This prevents the list from changing during the batch picking process
    fixed_batch_key = 'batch_items_' + str(batch_id)
    
    # Get the items from our fixed list instead of recalculating
    if fixed_batch_key in session:
        items = session[fixed_batch_key]
        current_app.logger.warning(f"📋 CONFIRM USING FIXED LIST: {len(items)} items, current index: {batch_session.current_item_index}")
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
            
        current_app.logger.warning(f"⚠️ FALLBACK IN CONFIRM: Using database query, found {len(items if items else [])} items")
    
    # CRITICAL DEBUG INFO
    current_app.logger.warning(f"🔄 CONFIRM: Processing item at index {batch_session.current_item_index} of {len(items)} total")
    
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
                          show_product_image=show_product_image)

@batch_bp.route('/picker/batch/complete-confirm/<int:batch_id>', methods=['POST'])
@login_required
def complete_batch_confirm(batch_id):
    """Process the confirmation of a picked item and complete the database updates"""
    # Only picker users can access this endpoint
    if current_user.role != 'admin' and current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))

    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Check if this picker is assigned to this batch
    if current_user.role != 'admin' and batch_session.assigned_to != current_user.username:
        flash('You are not assigned to this batch picking session.', 'danger')
        return redirect(url_for('batch.picker_batch_list'))

    # CRUCIAL FIX: Use our fixed item list from the session
    fixed_batch_key = 'batch_items_' + str(batch_id)
    
    if fixed_batch_key in session:
        items = session[fixed_batch_key]
        current_app.logger.warning(f"📋 COMPLETE CONFIRM USING FIXED LIST: {len(items)} items, current index: {batch_session.current_item_index}")
    else:
        # Fallback to database query if session data is missing
        items = batch_session.get_grouped_items()
        current_app.logger.warning(f"⚠️ FALLBACK IN COMPLETE CONFIRM: Using database query, found {len(items if items else [])} items")
    
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
    
    try:
        # Process the picked items
        if batch_session.picking_mode == 'Sequential':
            # Sequential mode - one invoice at a time
            invoice_no = source_items[0]['invoice_no']
            item_code = source_items[0]['item_code']
            required_qty = source_items[0]['qty']
            
            # Update the invoice item
            invoice_item = InvoiceItem.query.filter_by(
                invoice_no=invoice_no,
                item_code=item_code
            ).first()
            
            if invoice_item:
                # Record any exceptions if there's a discrepancy
                if picked_qty != required_qty:
                    exception = PickingException(
                        invoice_no=invoice_no,
                        item_code=item_code,
                        expected_qty=required_qty,
                        picked_qty=picked_qty,
                        picker_username=current_user.username,
                        reason=f"Batch picking: {picked_qty} picked, {required_qty} required"
                    )
                    db.session.add(exception)
                
                # Update the invoice item
                invoice_item.picked_qty = picked_qty
                invoice_item.is_picked = True
                invoice_item.pick_status = 'picked'
                
                # Record the batch picked item
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
                        
                        # Record the batch picked item
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
        
        # Log the current state before making any changes
        current_app.logger.warning(f"🔄 CRITICAL FIX (confirm_batch_item): Before processing - index: {batch_session.current_item_index}, item: {current_item['item_code']}")
        current_app.logger.warning(f"🔄 Total items in batch: {len(items)}")
        
        # Only increment after processing the current item completely
        # This is the ONLY place where the index should be incremented
        batch_session.current_item_index += 1
        
        current_app.logger.warning(f"🔄 CRITICAL FIX: After incrementing - new index: {batch_session.current_item_index}")
        if batch_session.current_item_index < len(items):
            current_app.logger.warning(f"🔄 Next item will be: {items[batch_session.current_item_index]['item_code']}")
        
        # Record an activity
        activity = ActivityLog(
            picker_username=current_user.username,
            activity_type='batch_item_pick',
            details=f"Batch {batch_id}: Picked {picked_qty} of {current_item['item_code']} (total required: {total_required})"
        )
        db.session.add(activity)
        
        # Check if all items are picked
        if batch_session.current_item_index >= len(items):
            # All items have been picked, update the status
            batch_session.status = 'Completed'
            flash('All items in this batch have been picked!', 'success')
            db.session.commit()
            return redirect(url_for('batch.picker_batch_list'))
        
        # Save changes to database
        db.session.commit()
        
        # Flash success message
        flash(f'Item {current_item["item_code"]} successfully picked!', 'success')
        
        # Redirect to the next item
        return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
        
    except Exception as e:
        # Roll back the transaction
        db.session.rollback()
        flash(f'Error processing this item: {str(e)}', 'danger')
        current_app.logger.error(f"Error processing batch item: {str(e)}")
        return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))

@batch_bp.route('/picker/batch/skip/<int:batch_id>', methods=['POST'])
@login_required
def skip_batch_item(batch_id):
    """Skip an item in a batch for later picking"""
    # Only picker users can access this endpoint
    if current_user.role != 'admin' and current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))

    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Check if this picker is assigned to this batch
    if current_user.role != 'admin' and batch_session.assigned_to != current_user.username:
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
    if current_user.role != 'admin':
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

@batch_bp.route('/batch/print-reports/<int:batch_id>')
@login_required
def batch_print_reports(batch_id):
    """Print reports for a batch picking session"""
    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    # Check if this user has access to this batch
    if current_user.role != 'admin' and batch_session.assigned_to != current_user.username:
        flash('You do not have access to this batch picking session.', 'danger')
        return redirect(url_for('index'))
    
    # Get the invoices in this batch
    batch_invoices = BatchSessionInvoice.query.filter_by(
        batch_session_id=batch_id
    ).all()
    
    # Get the invoice details and picked items
    invoices_data = []
    for bi in batch_invoices:
        invoice = Invoice.query.get(bi.invoice_no)
        if invoice:
            # Get items picked in this batch for this invoice
            batch_items = BatchPickedItem.query.filter_by(
                batch_session_id=batch_id,
                invoice_no=invoice.invoice_no
            ).all()
            
            # Get the full details for each picked item
            picked_items = []
            for batch_item in batch_items:
                invoice_item = InvoiceItem.query.filter_by(
                    invoice_no=invoice.invoice_no,
                    item_code=batch_item.item_code
                ).first()
                
                if invoice_item:
                    picked_items.append({
                        'item': invoice_item,
                        'picked_qty': batch_item.picked_qty
                    })
            
            # Also get items that were manually picked (not through batch)
            manual_items = InvoiceItem.query.filter(
                InvoiceItem.invoice_no == invoice.invoice_no,
                InvoiceItem.is_picked == True,
                ~InvoiceItem.item_code.in_([i.item_code for i in batch_items])
            ).all()
            
            # Format for the template
            manual_picked = []
            for item in manual_items:
                manual_picked.append({
                    'item': item,
                    'picked_qty': item.picked_qty
                })
            
            # Get any remaining unpicked items
            unpicked_items = InvoiceItem.query.filter(
                InvoiceItem.invoice_no == invoice.invoice_no,
                InvoiceItem.is_picked == False,
                InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending'])
            ).all()
            
            # Add to the list
            invoices_data.append({
                'invoice': invoice,
                'batch_picked': picked_items,
                'manually_picked': manual_picked,
                'unpicked': unpicked_items
            })
    
    # Render the print reports page
    return render_template('batch_print_reports.html',
                          batch_session=batch_session,
                          invoices_data=invoices_data)