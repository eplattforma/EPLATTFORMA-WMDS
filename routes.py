from app import app, db
from flask import render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import current_user, login_required
from sqlalchemy import func
from datetime import datetime


def validate_csrf_token():
    token = session.get('csrf_token')
    return bool(token)

@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    # Get sorting parameters
    sort_by = request.args.get('sort', 'status')
    sort_dir = request.args.get('dir', 'asc')
    
    # Filter for open warehouse orders only (Not Started, Picking, Awaiting Batch Items, Awaiting Packing, Ready for Dispatch)
    # Exclude returned_to_warehouse - those orders should not appear on picking dashboard
    open_warehouse_statuses = ['not_started', 'picking', 'awaiting_batch_items', 'awaiting_packing', 'ready_for_dispatch']
    all_invoices = Invoice.query.filter(Invoice.status.in_(open_warehouse_statuses)).all()
    
    # Separate warehouse orders (not assigned to routes) from route-assigned orders
    # An invoice is assigned to a route if it has a route_id set
    warehouse_invoices = [inv for inv in all_invoices if not inv.route_id]
    route_assigned_invoices = [inv for inv in all_invoices if inv.route_id]
    
    # Define proper status sequence for sorting
    status_order = {
        'not_started': 0,
        'picking': 1,
        'awaiting_batch_items': 2,
        'awaiting_packing': 3,
        'ready_for_dispatch': 4,
        'shipped': 5,
        'delivered': 6,
        'delivery_failed': 6,
        'returned_to_warehouse': 7,
        'cancelled': 8
    }
    
    def multi_sort_key(invoice):
        # Primary sort: status
        status_priority = status_order.get(invoice.status, 999)
        
        # Secondary sort: routing number (as numeric, with empty values last)
        routing = invoice.routing
        if not routing or routing.strip() == '':
            routing_priority = (1, 999999)
        else:
            try:
                routing_num = float(routing.strip())
                routing_priority = (0, routing_num)
            except (ValueError, TypeError):
                routing_priority = (0.5, routing.strip())
        
        return (status_priority, routing_priority)
    
    route_groups = {}
    unassigned_invoices = warehouse_invoices
    
    for inv in route_assigned_invoices:
        if inv.route_id:
            if inv.route_id not in route_groups:
                route_groups[inv.route_id] = []
            route_groups[inv.route_id].append(inv)
    
    from models import RouteStop
    stop_ids = [inv.stop_id for inv in route_assigned_invoices if inv.stop_id]
    route_stops_cache = {}
    if stop_ids:
        route_stops = RouteStop.query.filter(RouteStop.route_stop_id.in_(stop_ids)).all()
        for stop in route_stops:
            route_stops_cache[stop.route_stop_id] = stop
    
    for route_id in route_groups:
        def stop_sort_key(invoice):
            if invoice.stop_id and invoice.stop_id in route_stops_cache:
                stop = route_stops_cache[invoice.stop_id]
                return (0, stop.seq_no, invoice.status, invoice.routing or '')
            return (1, 999999, invoice.status, invoice.routing or '')
        
        route_groups[route_id] = sorted(route_groups[route_id], key=stop_sort_key)
    
    unassigned_invoices = sorted(unassigned_invoices, key=multi_sort_key)
    
    invoices = []
    route_info = {}
    
    from models import Shipment
    route_ids = list(route_groups.keys())
    shipment_cache = {}
    if route_ids:
        shipments = Shipment.query.filter(Shipment.id.in_(route_ids)).all()
        for shipment in shipments:
            shipment_cache[shipment.id] = shipment
    
    def shipment_sort_key(rid):
        shipment = shipment_cache.get(rid)
        if shipment:
            return (shipment.delivery_date if shipment.delivery_date else datetime.max.date(), shipment.driver_name if shipment.driver_name else '')
        return (datetime.max.date(), '')
    
    sorted_route_ids = sorted(route_groups.keys(), key=shipment_sort_key)
    
    for route_id in sorted_route_ids:
        route = shipment_cache.get(route_id)
        if route:
            route_info[route_id] = route
        invoices.extend(route_groups[route_id])
    
    invoices.extend(unassigned_invoices)
    
    all_active_invoices = list(warehouse_invoices) + list(route_assigned_invoices)
    completed_invoices = [invoice for invoice in all_active_invoices if invoice.status == 'Completed']
    
    pickers = get_picking_eligible_users()
    drivers = User.query.filter_by(role='driver').all()
    from models import Shipment
    planned_routes = Shipment.query.filter_by(status='PLANNED').order_by(Shipment.delivery_date.desc(), Shipment.driver_name).all()
    
    invoice_exceptions = {}
    batch_picked_info = {}
    picked_lines_count = {}
    total_lines_count = {}
    picking_times = {}
    
    from models import BatchPickedItem, BatchPickingSession
    
    invoice_nos = [inv.invoice_no for inv in invoices]
    exception_counts = db.session.query(
        PickingException.invoice_no,
        func.count(PickingException.id).label('count')
    ).filter(PickingException.invoice_no.in_(invoice_nos)).group_by(PickingException.invoice_no).all()
    
    for invoice_no, count in exception_counts:
        invoice_exceptions[invoice_no] = count
    for invoice in invoices:
        if invoice.invoice_no not in invoice_exceptions:
            invoice_exceptions[invoice.invoice_no] = 0
    
    all_items = InvoiceItem.query.filter(InvoiceItem.invoice_no.in_(invoice_nos)).all()
    items_by_invoice = {}
    for item in all_items:
        if item.invoice_no not in items_by_invoice:
            items_by_invoice[item.invoice_no] = []
        items_by_invoice[item.invoice_no].append(item)
    
    from models import OrderTimeBreakdown, ItemTimeTracking
    from timezone_utils import get_utc_now
    
    try:
        time_breakdowns = OrderTimeBreakdown.query.filter(
            OrderTimeBreakdown.invoice_no.in_(invoice_nos)
        ).all()
        breakdown_by_invoice = {tb.invoice_no: tb for tb in time_breakdowns}
        
        actual_tracking = db.session.query(
            ItemTimeTracking.invoice_no,
            func.sum(ItemTimeTracking.total_item_time).label('total_time')
        ).filter(
            ItemTimeTracking.invoice_no.in_(invoice_nos)
        ).group_by(ItemTimeTracking.invoice_no).all()
        actual_by_invoice = {t.invoice_no: t.total_time for t in actual_tracking}
    except Exception as e:
        import logging
        logging.error(f"Error fetching time tracking data: {e}")
        breakdown_by_invoice = {}
        actual_by_invoice = {}
    
    now_utc = get_utc_now()
    
    for invoice in invoices:
        items = items_by_invoice.get(invoice.invoice_no, [])
        total_lines_count[invoice.invoice_no] = len(items)
        picked_lines_count[invoice.invoice_no] = sum(1 for item in items if item.is_picked)
        
        actual_seconds = actual_by_invoice.get(invoice.invoice_no)
        if actual_seconds and actual_seconds > 0:
            total_seconds = actual_seconds
            if invoice.picking_complete_time and invoice.packing_complete_time:
                packing_seconds = (invoice.packing_complete_time - invoice.picking_complete_time).total_seconds()
                total_seconds += packing_seconds
            picking_times[invoice.invoice_no] = f"{round(total_seconds / 60, 2)}m"
        else:
            breakdown = breakdown_by_invoice.get(invoice.invoice_no)
            if breakdown:
                if breakdown.picking_started and breakdown.picking_completed:
                    duration = (breakdown.picking_completed - breakdown.picking_started).total_seconds() / 60
                    picking_times[invoice.invoice_no] = f"{int(duration)}m"
                elif breakdown.picking_started and invoice.status == 'picking':
                    elapsed = (now_utc - breakdown.picking_started).total_seconds() / 60
                    picking_times[invoice.invoice_no] = f"{int(elapsed)}m"
            else:
                picking_times[invoice.invoice_no] = "—"
    
    batch_items_list = BatchPickedItem.query.filter(BatchPickedItem.invoice_no.in_(invoice_nos)).all()
    batch_sessions_by_id = {}
    if batch_items_list:
        batch_ids = set(item.batch_session_id for item in batch_items_list)
        if batch_ids:
            batch_sessions = BatchPickingSession.query.filter(BatchPickingSession.id.in_(batch_ids)).all()
            for batch in batch_sessions:
                batch_sessions_by_id[batch.id] = batch
    
    batch_items_by_invoice = {}
    for batch_item in batch_items_list:
        if batch_item.invoice_no not in batch_items_by_invoice:
            batch_items_by_invoice[batch_item.invoice_no] = []
        batch_items_by_invoice[batch_item.invoice_no].append(batch_item)
    
    for invoice in invoices:
        batch_items = batch_items_by_invoice.get(invoice.invoice_no, [])
        if batch_items:
            batch_sessions = {}
            for batch_item in batch_items:
                batch_id = batch_item.batch_session_id
                if batch_id not in batch_sessions:
                    batch = batch_sessions_by_id.get(batch_id)
                    batch_name = batch.name if batch else f"Batch #{batch_id}"
                    batch_number = batch.batch_number if batch and batch.batch_number else f"BATCH-{batch_id}"
                    batch_status = batch.status if batch else None
                    batch_sessions[batch_id] = {
                        'id': batch_id,
                        'name': batch_name,
                        'batch_number': batch_number,
                        'status': batch_status,
                        'count': 0,
                        'items': []
                    }
                
                batch_sessions[batch_id]['count'] += 1
                items_for_invoice = items_by_invoice.get(invoice.invoice_no, [])
                item = next((i for i in items_for_invoice if i.item_code == batch_item.item_code), None)
                if item:
                    batch_sessions[batch_id]['items'].append({
                        'code': item.item_code,
                        'name': item.item_name,
                        'qty': batch_item.picked_qty
                    })
            
            batch_picked_info[invoice.invoice_no] = batch_sessions
    
    batch_fully_picked_invoice_nos = set()
    for inv_no, sessions in batch_picked_info.items():
        if sessions and all(s.get('status') == 'Completed' for s in sessions.values()):
            batch_fully_picked_invoice_nos.add(inv_no)
    
    cooler_invoice_nos = set()
    cooler_fully_picked_invoice_nos = set()
    if invoice_nos:
        try:
            cooler_rows = db.session.execute(
                db.text(
                    "SELECT DISTINCT bpq.invoice_no "
                    "FROM batch_pick_queue bpq "
                    "JOIN batch_picking_sessions bps ON bps.id = bpq.batch_session_id "
                    "WHERE bps.session_type = 'cooler_route' "
                    "AND bpq.invoice_no = ANY(:invoice_nos)"
                ),
                {"invoice_nos": list(invoice_nos)}
            ).fetchall()
            cooler_invoice_nos = {row[0] for row in cooler_rows}
        except Exception as _e:
            import logging as _log
            _log.warning(f"admin_dashboard: cooler indicator query failed: {_e}")

        if cooler_invoice_nos:
            try:
                unpicked_cooler = db.session.execute(
                    db.text(
                        "SELECT DISTINCT bpq.invoice_no "
                        "FROM batch_pick_queue bpq "
                        "JOIN batch_picking_sessions bps ON bps.id = bpq.batch_session_id "
                        "WHERE bps.session_type = 'cooler_route' "
                        "AND bpq.invoice_no = ANY(:invoice_nos) "
                        "AND (bpq.qty_picked IS NULL OR bpq.qty_picked = 0)"
                    ),
                    {"invoice_nos": list(cooler_invoice_nos)}
                ).fetchall()
                has_unpicked = {row[0] for row in unpicked_cooler}
                cooler_fully_picked_invoice_nos = cooler_invoice_nos - has_unpicked
            except Exception as _e:
                import logging as _log
                _log.warning(f"admin_dashboard: cooler fully-picked query failed: {_e}")
    
    open_batch_statuses = ['Created', 'In Progress', 'Active', 'Paused']
    open_batch_sessions = BatchPickingSession.query.filter(
        BatchPickingSession.status.in_(open_batch_statuses),
        BatchPickingSession.archived_at.is_(None),
        BatchPickingSession.cancelled_at.is_(None),
    ).order_by(BatchPickingSession.created_at.desc()).all()
    
    cooler_route_ids = [s.route_id for s in open_batch_sessions if s.session_type == 'cooler_route' and s.route_id]
    route_date_map = {}
    if cooler_route_ids:
        from models import Shipment
        _rows = Shipment.query.filter(Shipment.id.in_(cooler_route_ids)).with_entities(
            Shipment.id, Shipment.delivery_date).all()
        route_date_map = {r.id: r.delivery_date.strftime('%Y-%m-%d') for r in _rows}
    
    batch_session_item_counts = {}
    if open_batch_sessions:
        open_session_ids = [s.id for s in open_batch_sessions]
        try:
            queue_rows = db.session.execute(
                db.text(
                    "SELECT batch_session_id, COUNT(*) AS total, SUM(CASE WHEN qty_picked > 0 THEN 1 ELSE 0 END) AS picked "
                    "FROM batch_pick_queue WHERE batch_session_id = ANY(:sids) GROUP BY batch_session_id"
                ),
                {"sids": open_session_ids}
            ).fetchall()
            for row in queue_rows:
                batch_session_item_counts[row[0]] = {'total': row[1], 'picked': int(row[2] or 0)}
        except Exception as _be:
            import logging as _blog
            _blog.warning(f"admin_dashboard: batch queue count query failed: {_be}")
            db.session.rollback()
    
    total_remaining_time = 0
    for invoice in invoices:
        if invoice.status == 'not_started':
            total_remaining_time += (invoice.total_exp_time or 0)
        elif invoice.status == 'picking':
            picked_items = picked_lines_count.get(invoice.invoice_no, 0)
            total_items = total_lines_count.get(invoice.invoice_no, invoice.total_lines)
            if total_items > 0:
                remaining_percentage = ((total_items - picked_items) / total_items)
                remaining_time = (invoice.total_exp_time or 0) * remaining_percentage
                total_remaining_time += remaining_time
    
    use_shipments_raw = Setting.get(db.session, 'use_shipments', 'false')
    use_shipments = str(use_shipments_raw).strip().lower() in ('true', '1', 'yes', 'on')
    
    unresolved_issues_count = 0
    if current_user.role == 'warehouse_manager':
        from models import DeliveryDiscrepancy
        unresolved_issues_count = DeliveryDiscrepancy.query.filter_by(is_resolved=False).count()
    
    review_issues_count = 0
    if current_user.role == 'admin':
        from models import DeliveryDiscrepancy
        review_issues_count = DeliveryDiscrepancy.query.filter_by(status='review').count()
    
    stop_sequences = {}
    for invoice in invoices:
        if invoice.stop_id and invoice.stop_id in route_stops_cache:
            stop = route_stops_cache[invoice.stop_id]
            stop_sequences[invoice.invoice_no] = stop.seq_no
    
    active_pickers_data = []
    total_idle_time = 0
    active_shifts = Shift.query.filter_by(status='active').all()
    for shift in active_shifts:
        on_break = db.session.query(IdlePeriod).filter(
            IdlePeriod.shift_id == shift.id,
            IdlePeriod.is_break == True,
            IdlePeriod.end_time.is_(None)
        ).order_by(IdlePeriod.start_time.desc()).first()
        
        shift_idle_time = shift.current_idle_minutes()
        total_idle_time += shift_idle_time
        
        from timezone_utils import get_utc_now, format_utc_datetime_to_local
        elapsed_minutes = int(((get_utc_now() - shift.check_in_time).total_seconds() / 60)) if shift.check_in_time else 0
        active_pickers_data.append({
            'username': shift.picker_username,
            'check_in_time': format_utc_datetime_to_local(shift.check_in_time, '%H:%M') if shift.check_in_time else '-',
            'elapsed_minutes': elapsed_minutes,
            'idle_time': shift_idle_time,
            'on_break': bool(on_break)
        })
    
    routes_data = []
    for route_id in sorted_route_ids:
        route = shipment_cache.get(route_id)
        if not route:
            continue
        route_invoices = route_groups[route_id]
        cooler_session = next((s for s in open_batch_sessions if getattr(s, 'session_type', None) == 'cooler_route' and s.route_id == route_id), None)
        cooler_counts = batch_session_item_counts.get(cooler_session.id, {}) if cooler_session else {}
        route_batch = next((s for s in open_batch_sessions if getattr(s, 'session_type', None) == 'route_batch' and s.route_id == route_id), None)
        inv_data = []
        for inv in route_invoices:
            inv_data.append({
                'invoice': inv,
                'picked_lines': picked_lines_count.get(inv.invoice_no, 0),
                'total_lines': total_lines_count.get(inv.invoice_no, 0),
                'has_cooler': inv.invoice_no in cooler_invoice_nos,
                'exceptions': invoice_exceptions.get(inv.invoice_no, 0),
                'stop_seq': stop_sequences.get(inv.invoice_no),
            })
        routes_data.append({
            'route': route,
            'invoices': inv_data,
            'total_orders': len(route_invoices),
            'ready_count': sum(1 for i in route_invoices if i.status == 'ready_for_dispatch'),
            'not_started_count': sum(1 for i in route_invoices if i.status == 'not_started'),
            'in_progress_count': sum(1 for i in route_invoices if i.status in ['picking', 'awaiting_batch_items', 'awaiting_packing']),
            'total_weight': sum(i.total_weight or 0 for i in route_invoices),
            'cooler_session': cooler_session,
            'cooler_picked': cooler_counts.get('picked', 0),
            'cooler_total': cooler_counts.get('total', 0),
            'cooler_date': route_date_map.get(route_id),
            'route_batch': route_batch,
        })

    unassigned_route_batch = next((s for s in open_batch_sessions if getattr(s, 'session_type', None) == 'route_batch' and not s.route_id), None)
    
    return render_template('admin_dashboard.html', 
                          invoices=invoices, 
                          completed_invoices=completed_invoices,
                          shipped_invoices=route_assigned_invoices,
                          pickers=pickers,
                          drivers=drivers,
                          planned_routes=planned_routes,
                          route_info=route_info,
                          invoice_exceptions=invoice_exceptions,
                          batch_picked_info=batch_picked_info,
                          picked_lines_count=picked_lines_count,
                          total_lines_count=total_lines_count,
                          picking_times=picking_times,
                          stop_sequences=stop_sequences,
                          current_time=get_local_time(),
                          total_remaining_time=total_remaining_time,
                          total_idle_time=total_idle_time,
                          sort_by=sort_by,
                          sort_dir=sort_dir,
                          use_shipments=use_shipments,
                          unresolved_issues_count=unresolved_issues_count,
                          review_issues_count=review_issues_count,
                          active_pickers_data=active_pickers_data,
                          cooler_invoice_nos=cooler_invoice_nos,
                          cooler_fully_picked_invoice_nos=cooler_fully_picked_invoice_nos,
                          batch_fully_picked_invoice_nos=batch_fully_picked_invoice_nos,
                          open_batch_sessions=open_batch_sessions,
                          batch_session_item_counts=batch_session_item_counts,
                          route_date_map=route_date_map,
                          routes_data=routes_data,
                          unassigned_route_batch=unassigned_route_batch)
