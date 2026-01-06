"""
Operational dashboard routes for the three-view workflow:
1. Open Orders - Warehouse operations
2. On Shipment - Logistics tracking  
3. Archive - Historical records
"""

from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from models import Invoice, User, PickingException, InvoiceItem, ActivityLog, BatchPickedItem
from app import app, db
from datetime import datetime, timedelta
from routes import validate_csrf_token
from sqlalchemy import or_, and_


@app.route('/operations/open-orders')
@login_required
def open_orders():
    """Open Orders view - Warehouse operations"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get all invoices with warehouse statuses
    warehouse_statuses = ['not_started', 'picking', 'awaiting_batch_items', 'ready_for_dispatch', 'returned_to_warehouse']
    open_orders = Invoice.query.filter(Invoice.status.in_(warehouse_statuses)).all()
    
    # Extract invoice numbers for bulk queries
    invoice_nos = [inv.invoice_no for inv in open_orders]
    
    # Bulk query for exceptions - single query instead of N queries
    from sqlalchemy import func
    exception_counts = db.session.query(
        PickingException.invoice_no,
        func.count(PickingException.id).label('count')
    ).filter(
        PickingException.invoice_no.in_(invoice_nos)
    ).group_by(PickingException.invoice_no).all()
    invoice_exceptions = {row.invoice_no: row.count for row in exception_counts}
    
    # Bulk query for item counts - single query instead of N queries
    item_counts = db.session.query(
        InvoiceItem.invoice_no,
        func.count(InvoiceItem.item_code).label('total'),
        func.sum(db.case((InvoiceItem.is_picked == True, 1), else_=0)).label('picked')
    ).filter(
        InvoiceItem.invoice_no.in_(invoice_nos)
    ).group_by(InvoiceItem.invoice_no).all()
    
    total_lines_count = {row.invoice_no: int(row.total) for row in item_counts}
    picked_lines_count = {row.invoice_no: int(row.picked or 0) for row in item_counts}
    
    # Fill in zeros for invoices with no items/exceptions
    for invoice_no in invoice_nos:
        invoice_exceptions.setdefault(invoice_no, 0)
        total_lines_count.setdefault(invoice_no, 0)
        picked_lines_count.setdefault(invoice_no, 0)
    
    # Get all pickers for assignment
    pickers = User.query.filter_by(role='picker').all()
    
    # Group orders by status for better organization
    orders_by_status = {
        'not_started': [o for o in open_orders if o.status == 'not_started'],
        'picking': [o for o in open_orders if o.status == 'picking'],
        'awaiting_batch_items': [o for o in open_orders if o.status == 'awaiting_batch_items'],
        'ready_for_dispatch': [o for o in open_orders if o.status == 'ready_for_dispatch'],
        'returned_to_warehouse': [o for o in open_orders if o.status == 'returned_to_warehouse']
    }
    
    return render_template('operations_open_orders.html',
                         orders_by_status=orders_by_status,
                         pickers=pickers,
                         invoice_exceptions=invoice_exceptions,
                         picked_lines_count=picked_lines_count,
                         total_lines_count=total_lines_count)


@app.route('/operations/shipments')
@login_required  
def on_shipment():
    """On Shipment view - Logistics tracking (Refactored to use direct Invoice shipping fields)"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Note: This page shows direct Invoice shipping data, not complex Shipment assignments
    # It's always available regardless of the shipments feature flag since we use direct Invoice fields
    
    # Get all shipped orders using direct Invoice fields (no complex JOINs)
    shipment_statuses = ['shipped', 'delivery_failed']
    
    try:
        # Direct query on Invoice model using new shipping audit fields
        shipment_orders = Invoice.query.filter(
            Invoice.status.in_(shipment_statuses)
        ).order_by(
            Invoice.shipped_at.desc().nulls_last(),
            Invoice.invoice_no.desc()
        ).limit(100).all()
        
        # Build shipping info using direct Invoice fields instead of complex JOINs
        shipments_info = {}
        for invoice in shipment_orders:
            # Use the direct shipping fields from Invoice model
            shipments_info[invoice.invoice_no] = {
                'shipment_id': 'Direct', # No longer using Shipment model
                'courier': invoice.shipped_by or 'Unknown',  # Use shipped_by field instead of shipper relationship
                'ship_date': invoice.shipped_at.date() if invoice.shipped_at else None,
                'tracking_number': 'N/A',  # Can be enhanced with tracking numbers later
                'shipped_by': invoice.shipped_by,
                'delivered_at': invoice.delivered_at
            }
                
    except Exception as e:
        # Fallback in case of issues - return empty data rather than crashing
        shipment_orders = []
        shipments_info = {}
        flash(f'Error loading shipment data: {str(e)}', 'warning')
    
    return render_template('operations_shipments.html',
                         shipment_orders=shipment_orders,
                         shipments_info=shipments_info)


@app.route('/operations/archive')
@login_required
def archive():
    """Archive view - Historical records"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get filter parameters
    status_filter = request.args.get('status', '')
    customer_filter = request.args.get('customer', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    # Base query for archived orders
    archive_statuses = ['shipped', 'delivered', 'cancelled']
    query = Invoice.query.filter(Invoice.status.in_(archive_statuses))
    
    # Apply filters
    if status_filter:
        query = query.filter(Invoice.status == status_filter)
    if customer_filter:
        query = query.filter(Invoice.customer_name.ilike(f'%{customer_filter}%'))
    if date_from:
        try:
            from_date = datetime.strptime(date_from, '%Y-%m-%d')
            # Filter by delivered_at, shipped_at, or updated_at for cancelled orders
            query = query.filter(
                or_(
                    Invoice.delivered_at >= from_date,
                    and_(Invoice.delivered_at.is_(None), Invoice.shipped_at >= from_date),
                    and_(Invoice.delivered_at.is_(None), Invoice.shipped_at.is_(None), 
                         Invoice.status == 'cancelled', Invoice.updated_at >= from_date)
                )
            )
        except ValueError:
            flash('Invalid start date format', 'warning')
    if date_to:
        try:
            to_date = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)  # Include full day
            # Filter by delivered_at, shipped_at, or updated_at for cancelled orders
            query = query.filter(
                or_(
                    Invoice.delivered_at < to_date,
                    and_(Invoice.delivered_at.is_(None), Invoice.shipped_at < to_date),
                    and_(Invoice.delivered_at.is_(None), Invoice.shipped_at.is_(None), 
                         Invoice.status == 'cancelled', Invoice.updated_at < to_date)
                )
            )
        except ValueError:
            flash('Invalid end date format', 'warning')
    
    # Order by most recent first - delivered_at, then shipped_at, then invoice_no
    archived_orders = query.order_by(
        Invoice.delivered_at.desc().nulls_last(),
        Invoice.shipped_at.desc().nulls_last(), 
        Invoice.invoice_no.desc()
    ).all()
    
    # Get unique customers for filter dropdown
    all_customers = db.session.query(Invoice.customer_name).filter(
        Invoice.status.in_(archive_statuses)
    ).distinct().all()
    customers = [c[0] for c in all_customers if c[0]]
    
    return render_template('operations_archive.html',
                         archived_orders=archived_orders,
                         customers=customers,
                         filters={
                             'status': status_filter,
                             'customer': customer_filter,
                             'date_from': date_from,
                             'date_to': date_to
                         })


@app.route('/operations/shipped-orders-report')
@login_required
def shipped_orders_report():
    """Shipped Orders Report - View completed orders with picking details"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get filter parameters
    status_filter = request.args.get('status', '')
    customer_filter = request.args.get('customer', '')
    picker_filter = request.args.get('picker', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    # Define shipped/completed statuses
    shipped_statuses = ['shipped', 'delivered', 'delivery_failed', 'returned_to_warehouse', 'cancelled']
    
    # Base query for shipped orders
    query = Invoice.query.filter(Invoice.status.in_(shipped_statuses))
    
    # Apply filters
    if status_filter:
        query = query.filter(Invoice.status == status_filter)
    if customer_filter:
        query = query.filter(Invoice.customer_name.ilike(f'%{customer_filter}%'))
    if picker_filter:
        query = query.filter(Invoice.assigned_to == picker_filter)
    
    # Apply date filters using shipping audit fields
    if date_from and date_to:
        from datetime import datetime, timedelta
        from sqlalchemy import or_, and_
        try:
            date_from_dt = datetime.strptime(date_from, '%Y-%m-%d')
            date_to_dt = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            # Correct logic: (shipped_at ∈ [from, to)) OR (delivered_at ∈ [from, to))
            query = query.filter(
                or_(
                    and_(
                        Invoice.shipped_at >= date_from_dt,
                        Invoice.shipped_at < date_to_dt
                    ),
                    and_(
                        Invoice.delivered_at >= date_from_dt,
                        Invoice.delivered_at < date_to_dt
                    )
                )
            )
        except ValueError:
            pass  # Invalid date format, ignore filter
    elif date_from:
        from datetime import datetime
        from sqlalchemy import or_
        try:
            date_from_dt = datetime.strptime(date_from, '%Y-%m-%d')
            query = query.filter(
                or_(
                    Invoice.shipped_at >= date_from_dt,
                    Invoice.delivered_at >= date_from_dt
                )
            )
        except ValueError:
            pass
    elif date_to:
        from datetime import datetime, timedelta
        from sqlalchemy import or_
        try:
            date_to_dt = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(
                or_(
                    Invoice.shipped_at < date_to_dt,
                    Invoice.delivered_at < date_to_dt
                )
            )
        except ValueError:
            pass
    
    # Order by most recent first using proper audit fields
    shipped_orders = query.order_by(
        Invoice.delivered_at.desc().nulls_last(),
        Invoice.shipped_at.desc().nulls_last(), 
        Invoice.invoice_no.desc()
    ).limit(100).all()
    
    # Get basic stats for each order (optimized for speed)
    orders_data = []
    for invoice in shipped_orders:
        # Get only basic item count and completion stats
        from sqlalchemy import func
        item_stats = db.session.query(
            func.count(InvoiceItem.id).label('total_items'),
            func.count(InvoiceItem.id).filter(InvoiceItem.is_picked == True).label('picked_items')
        ).filter_by(invoice_no=invoice.invoice_no).first()
        
        # Get exception count only
        exception_count = PickingException.query.filter_by(invoice_no=invoice.invoice_no).count()
        
        # Calculate completion rate
        total_items = item_stats.total_items or 0
        picked_items = item_stats.picked_items or 0
        completion_rate = round((picked_items / total_items * 100) if total_items > 0 else 0, 1)
        
        orders_data.append({
            'invoice': invoice,
            'exceptions': [],  # Empty list, will show count only
            'stats': {
                'total_items': total_items,
                'picked_items': picked_items,
                'total_exceptions': exception_count,
                'completion_rate': completion_rate
            }
        })
    
    # Get unique values for filter dropdowns
    all_customers = db.session.query(Invoice.customer_name).filter(
        Invoice.status.in_(shipped_statuses),
        Invoice.customer_name.isnot(None)
    ).distinct().all()
    customers = [c[0] for c in all_customers if c[0]]
    
    all_pickers = db.session.query(Invoice.assigned_to).filter(
        Invoice.status.in_(shipped_statuses),
        Invoice.assigned_to.isnot(None)
    ).distinct().all()
    pickers = [p[0] for p in all_pickers if p[0]]
    
    return render_template('shipped_orders_report.html',
                         orders_data=orders_data,
                         shipped_statuses=shipped_statuses,
                         customers=customers,
                         pickers=pickers,
                         filters={
                             'status': status_filter,
                             'customer': customer_filter,
                             'picker': picker_filter,
                             'date_from': date_from,
                             'date_to': date_to
                         })


@app.route('/operations/order-picking-details/<invoice_no>')
@login_required
def order_picking_details(invoice_no):
    """Detailed picking report for a specific order - similar to print report with timing"""
    from models import Invoice, InvoiceItem, BatchPickedItem, PickingException, ActivityLog, ItemTimeTracking
    
    # Get the invoice
    invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if not invoice:
        flash('Order not found', 'error')
        return redirect(url_for('shipped_orders_report'))
    
    # Get all items for this invoice with picking details
    items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
    
    # Get batch picked items for this invoice
    batch_items = BatchPickedItem.query.filter_by(invoice_no=invoice_no).all()
    batch_info = {}
    for batch_item in batch_items:
        batch_info[batch_item.item_code] = {
            'picked_qty': batch_item.picked_qty,
            'batch_id': batch_item.batch_session_id,
            'picked_at': batch_item.created_at
        }
    
    # Get time tracking data for each item
    time_tracking = ItemTimeTracking.query.filter_by(invoice_no=invoice_no).all()
    time_info = {}
    for track in time_tracking:
        time_info[track.item_code] = {
            'walking_time': track.walking_time or 0,
            'picking_time': track.picking_time or 0,
            'confirmation_time': track.confirmation_time or 0,
            'picked_at': track.timestamp,
            'total_time': (track.walking_time or 0) + (track.picking_time or 0) + (track.confirmation_time or 0)
        }
    
    # Get exceptions for this invoice
    exceptions = PickingException.query.filter_by(invoice_no=invoice_no).all()
    
    # Get picking activities for this invoice
    pick_activities = ActivityLog.query.filter_by(
        invoice_no=invoice_no,
        activity_type='item_pick'
    ).order_by(ActivityLog.timestamp.asc()).all()
    
    # Separate items by picking method
    manual_items = []
    batch_items_list = []
    
    for item in items:
        item_data = {
            'item': item,
            'batch_info': batch_info.get(item.item_code),
            'time_info': time_info.get(item.item_code),
            'picked_method': 'Batch' if item.item_code in batch_info else 'Manual'
        }
        
        if item.item_code in batch_info:
            batch_items_list.append(item_data)
        else:
            manual_items.append(item_data)
    
    return render_template('order_picking_details.html',
                         invoice=invoice,
                         manual_items=manual_items,
                         batch_items_list=batch_items_list,
                         exceptions=exceptions,
                         pick_activities=pick_activities,
                         time_info=time_info)


@app.route('/operations/shipped-orders-report.csv')
@login_required
def shipped_orders_report_csv():
    """CSV export for shipped orders with fixed 29-column schema"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))

    # Fixed 29-column schema as specified by architect
    HEADERS_29 = [
        'invoice_no', 'customer_name', 'status', 'shipped_at', 'delivered_at',          # 1-5
        'total_items', 'picked_items', 'completion_rate_percent', 'total_exceptions',    # 6-9
        'routing', 'assigned_to', 'total_weight_kg', 'upload_date',                     # 10-13
        'total_walking_time_s', 'total_picking_time_s', 'total_confirmation_time_s',    # 14-16
        'total_item_time_s', 'avg_walking_time_s', 'avg_picking_time_s',               # 17-19
        'avg_confirmation_time_s', 'avg_total_time_s', 'items_tracked',                # 20-22
        'batch_ids', 'batch_statuses', 'batch_total_items', 'batch_started_at',       # 23-26
        'zones_picked', 'corridors_picked', 'exception_codes'                         # 27-29
    ]

    # Reuse same filter logic as HTML report
    status_filter = request.args.get('status', '')
    customer_filter = request.args.get('customer', '')
    picker_filter = request.args.get('picker', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    shipped_statuses = ['shipped', 'delivered', 'delivery_failed', 'returned_to_warehouse', 'cancelled']
    query = Invoice.query.filter(Invoice.status.in_(shipped_statuses))
    
    # Apply same filters as HTML report
    if status_filter:
        query = query.filter(Invoice.status == status_filter)
    if customer_filter:
        query = query.filter(Invoice.customer_name.ilike(f'%{customer_filter}%'))
    if picker_filter:
        query = query.filter(Invoice.assigned_to == picker_filter)
    
    # Apply date filters with correct logic
    if date_from and date_to:
        from datetime import datetime, timedelta
        from sqlalchemy import or_, and_
        try:
            date_from_dt = datetime.strptime(date_from, '%Y-%m-%d')
            date_to_dt = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(
                or_(
                    and_(Invoice.shipped_at >= date_from_dt, Invoice.shipped_at < date_to_dt),
                    and_(Invoice.delivered_at >= date_from_dt, Invoice.delivered_at < date_to_dt)
                )
            )
        except ValueError:
            pass
    elif date_from:
        from datetime import datetime
        from sqlalchemy import or_
        try:
            date_from_dt = datetime.strptime(date_from, '%Y-%m-%d')
            query = query.filter(or_(Invoice.shipped_at >= date_from_dt, Invoice.delivered_at >= date_from_dt))
        except ValueError:
            pass
    elif date_to:
        from datetime import datetime, timedelta
        from sqlalchemy import or_
        try:
            date_to_dt = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(or_(Invoice.shipped_at < date_to_dt, Invoice.delivered_at < date_to_dt))
        except ValueError:
            pass

    shipped_orders = query.order_by(
        Invoice.delivered_at.desc().nulls_last(),
        Invoice.shipped_at.desc().nulls_last(), 
        Invoice.invoice_no.desc()
    ).limit(100).all()

    # Build CSV rows with deterministic field mapping
    csv_rows = [HEADERS_29]  # Header row
    
    for invoice in shipped_orders:
        # Get same detailed data as HTML report
        items = InvoiceItem.query.filter_by(invoice_no=invoice.invoice_no).all()
        batch_items = BatchPickedItem.query.filter_by(invoice_no=invoice.invoice_no).all()
        exceptions = PickingException.query.filter_by(invoice_no=invoice.invoice_no).all()
        
        from models import ItemTimeTracking, BatchPickingSession
        time_tracking = ItemTimeTracking.query.filter_by(invoice_no=invoice.invoice_no).all()
        
        # Calculate same stats as HTML report
        total_items = len(items)
        picked_items = sum(1 for item in items if item.is_picked)
        total_exceptions = len(exceptions)
        completion_rate = round((picked_items / total_items * 100) if total_items > 0 else 0, 1)
        
        # Time tracking calculations
        total_walking_time = sum(t.walking_time or 0 for t in time_tracking)
        total_picking_time = sum(t.picking_time or 0 for t in time_tracking)
        total_confirmation_time = sum(t.confirmation_time or 0 for t in time_tracking)
        total_item_time = total_walking_time + total_picking_time + total_confirmation_time
        items_tracked = len(time_tracking)
        
        avg_walking_time = round(total_walking_time / items_tracked, 1) if items_tracked > 0 else 0
        avg_picking_time = round(total_picking_time / items_tracked, 1) if items_tracked > 0 else 0
        avg_confirmation_time = round(total_confirmation_time / items_tracked, 1) if items_tracked > 0 else 0
        avg_total_time = round(total_item_time / items_tracked, 1) if items_tracked > 0 else 0
        
        # Batch information
        batch_info = {}
        for batch_item in batch_items:
            batch_info[batch_item.item_code] = {'batch_id': batch_item.batch_session_id}
        batch_ids = sorted(list(set(bi['batch_id'] for bi in batch_info.values()))) if batch_info else []
        batch_sessions = BatchPickingSession.query.filter(BatchPickingSession.id.in_(batch_ids)).all() if batch_ids else []
        
        # Zone and corridor data  
        zones_picked = sorted(list(set(item.zone for item in items if item.zone and item.is_picked)))
        corridors_picked = []
        for item in items:
            if item.is_picked and item.location:
                import re
                location = item.location
                if ', ' in location:
                    parts = [part.strip() for part in location.split(', ') if part.strip().isdigit()]
                    corridors_picked.extend(parts)
                elif '-' in location:
                    first_part = location.split('-')[0]
                    corridor_match = re.search(r'(\d+)', first_part)
                    if corridor_match:
                        corridors_picked.append(corridor_match.group(1))
                else:
                    corridor_match = re.search(r'^[A-Z]*(\d+)', location)
                    if corridor_match:
                        corridors_picked.append(corridor_match.group(1))
        corridors_picked = sorted(list(set(corridors_picked)))
        
        # Build deterministic 29-field row
        row = [
            invoice.invoice_no,                                                         # 1: invoice_no
            invoice.customer_name or '',                                               # 2: customer_name
            invoice.status,                                                            # 3: status
            invoice.shipped_at.strftime('%Y-%m-%d %H:%M') if invoice.shipped_at else '',  # 4: shipped_at
            invoice.delivered_at.strftime('%Y-%m-%d %H:%M') if invoice.delivered_at else '',  # 5: delivered_at
            total_items,                                                               # 6: total_items
            picked_items,                                                              # 7: picked_items
            completion_rate,                                                           # 8: completion_rate_percent
            total_exceptions,                                                          # 9: total_exceptions
            invoice.routing or '',                                                     # 10: routing
            invoice.assigned_to or '',                                                 # 11: assigned_to
            f'{invoice.total_weight:.1f}' if invoice.total_weight else '0.0',        # 12: total_weight_kg
            invoice.upload_date or '',                                                 # 13: upload_date
            f'{total_walking_time:.1f}',                                              # 14: total_walking_time_s
            f'{total_picking_time:.1f}',                                              # 15: total_picking_time_s
            f'{total_confirmation_time:.1f}',                                         # 16: total_confirmation_time_s
            f'{total_item_time:.1f}',                                                 # 17: total_item_time_s
            f'{avg_walking_time:.1f}',                                                # 18: avg_walking_time_s
            f'{avg_picking_time:.1f}',                                                # 19: avg_picking_time_s
            f'{avg_confirmation_time:.1f}',                                           # 20: avg_confirmation_time_s
            f'{avg_total_time:.1f}',                                                  # 21: avg_total_time_s
            items_tracked,                                                             # 22: items_tracked
            ';'.join(map(str, batch_ids)),                                            # 23: batch_ids
            ';'.join(bs.status for bs in batch_sessions),                             # 24: batch_statuses
            ';'.join(str(bs.get_filtered_item_count()) for bs in batch_sessions),     # 25: batch_total_items
            ';'.join(bs.created_at.strftime('%Y-%m-%d %H:%M') for bs in batch_sessions),  # 26: batch_started_at
            ';'.join(zones_picked),                                                    # 27: zones_picked
            ';'.join(corridors_picked),                                               # 28: corridors_picked
            ';'.join(e.item_code for e in exceptions)                                 # 29: exception_codes
        ]
        
        csv_rows.append(row)
    
    # Generate CSV response
    import csv
    from io import StringIO
    output = StringIO()
    writer = csv.writer(output)
    for row in csv_rows:
        writer.writerow(row)
    
    csv_content = output.getvalue()
    output.close()
    
    # Return CSV file download
    from flask import Response
    from datetime import datetime
    response = Response(
        csv_content,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=shipped_orders_report_{datetime.now().strftime("%Y%m%d")}.csv'}
    )
    return response


@app.route('/operations/cancel-shipment', methods=['POST'])
@login_required
def cancel_shipment():
    """Cancel shipment and return order to ready_for_dispatch status"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    # CSRF Protection
    if not validate_csrf_token():
        return jsonify({'success': False, 'message': 'CSRF token validation failed'}), 403
    
    invoice_no = request.form.get('invoice_no')
    reason = request.form.get('reason', '').strip()
    
    if not invoice_no:
        return jsonify({'success': False, 'message': 'Invoice number is required'}), 400
    
    if not reason:
        return jsonify({'success': False, 'message': 'Cancellation reason is required'}), 400
    
    # Import and use the unship functionality
    from utils.shipping_utils import unship_invoice
    
    success = unship_invoice(
        invoice_no=invoice_no,
        cancelled_by=current_user.username,
        reason=reason
    )
    
    if success:
        return jsonify({
            'success': True, 
            'message': f'Shipment cancelled for order {invoice_no}. Order returned to Ready for Dispatch.'
        })
    else:
        return jsonify({
            'success': False, 
            'message': f'Failed to cancel shipment for order {invoice_no}. Please check logs.'
        }), 500


@app.route('/operations/update-status', methods=['POST'])
@login_required
def update_order_status():
    """Update order status from operations views"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    # CSRF Protection
    if not validate_csrf_token():
        return jsonify({'success': False, 'message': 'CSRF token validation failed'}), 403
    
    invoice_no = request.form.get('invoice_no')
    new_status = request.form.get('new_status')
    
    if not invoice_no or not new_status:
        return jsonify({'success': False, 'message': 'Missing parameters'}), 400
    
    invoice = Invoice.query.filter_by(invoice_no=invoice_no).first()
    if not invoice:
        return jsonify({'success': False, 'message': 'Invoice not found'}), 404
    
    # Validate status transitions
    valid_statuses = ['not_started', 'picking', 'ready_for_dispatch', 'shipped', 
                     'delivered', 'delivery_failed', 'returned_to_warehouse', 'cancelled']
    
    if new_status not in valid_statuses:
        return jsonify({'success': False, 'message': 'Invalid status'}), 400
    
    # Update status
    old_status = invoice.status
    invoice.status = new_status
    invoice.status_updated_at = datetime.utcnow()
    
    try:
        db.session.commit()
        
        # Log the status change
        from models import ActivityLog
        activity = ActivityLog(
            invoice_no=invoice_no,
            activity_type='status_change',
            details=f'Status changed from {old_status} to {new_status}',
            picker_username=current_user.username,
            timestamp=datetime.utcnow()
        )
        db.session.add(activity)
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'Status updated to {new_status}'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500