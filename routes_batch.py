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
from models import User, Invoice, InvoiceItem, PickingException, BatchPickingSession, BatchSessionInvoice, BatchPickedItem, Setting, ActivityLog, OrderTimeBreakdown, ItemTimeTracking, RouteStop, RouteStopInvoice
from sorting_utils import sort_batch_items, get_sorting_config
from services.permissions import require_permission
from services.picking_utils import get_picking_eligible_users
from services.batch_status import ACTIVE_BATCH_STATUSES

# Create a blueprint for batch picking routes
batch_bp = Blueprint('batch', __name__)


def _build_stop_seq_lookup(batch_session):
    """FIX-008: return ``{invoice_no: {'seq': float|None, 'route_name': str}}``.

    Route-bound batches (cooler/route) read stops from their own route.
    Standard batches fall back to each invoice's ACTIVE route link (the
    Routes module is now the single source of truth — the legacy
    ``Invoice.routing`` import field is dead).
    """
    from models import Shipment
    route_id = getattr(batch_session, 'route_id', None)
    if route_id:
        rows = db.session.query(
            RouteStopInvoice.invoice_no, RouteStop.seq_no, Shipment.route_name,
        ).join(
            RouteStop, RouteStop.route_stop_id == RouteStopInvoice.route_stop_id
        ).join(
            Shipment, Shipment.id == RouteStop.shipment_id
        ).filter(
            RouteStop.shipment_id == route_id,
            RouteStopInvoice.is_active.is_(True),
            RouteStop.deleted_at.is_(None),
        ).all()
        out = {}
        for inv_no, seq, rname in rows:
            try:
                fseq = float(seq) if seq is not None else None
            except (TypeError, ValueError):
                fseq = None
            out[inv_no] = {'seq': fseq, 'route_name': rname or ''}
        return out
    # Fallback: standard batch — invoices may still be on routes
    from services.route_links import route_links_for_invoices
    inv_nos = [bi.invoice_no for bi in batch_session.invoices]
    return route_links_for_invoices(inv_nos)


def _routing_label_for_invoice(invoice, stop_seq_lookup):
    """Return the routing label printed in the batch report header.

    Priority: active route link ("ROUTE · STOP n") → legacy
    ``invoice.routing`` (old data) → 'NO-ROUTING'.
    """
    from services.route_links import stop_label
    entry = stop_seq_lookup.get(invoice.invoice_no) if stop_seq_lookup else None
    label = stop_label(entry)
    if label:
        return label
    routing = getattr(invoice, 'routing', None)
    if routing not in (None, ''):
        return str(routing)
    return 'NO-ROUTING'


# Helper functions for sequential batch picking
def get_sorted_batch_invoices(batch_session):
    """Get all invoices in a batch in picking order.

    FIX-008: invoices with an active route link order by
    (route_name, stop_seq) ascending; unrouted invoices keep the legacy
    routing-number-descending order after them.
    """
    from services.route_links import route_links_for_invoices, stop_sort_key, UNROUTED_SORT_KEY
    batch_invoices = db.session.query(BatchSessionInvoice).join(Invoice).filter(
        BatchSessionInvoice.batch_session_id == batch_session.id
    ).all()
    links = route_links_for_invoices([bi.invoice_no for bi in batch_invoices])

    def _key(bi):
        entry = links.get(bi.invoice_no)
        if entry:
            rname, seq = stop_sort_key(entry)
            return (0, rname, seq)
        try:
            legacy = -float(bi.invoice.routing)
        except (TypeError, ValueError):
            legacy = float('inf')
        return (1, UNROUTED_SORT_KEY[0], legacy)

    return sorted(batch_invoices, key=_key)

def get_remaining_locked_items_count(batch_session, invoice_no):
    """Get count of remaining locked items for a specific invoice in this batch"""
    return db.session.query(InvoiceItem).filter(
        InvoiceItem.invoice_no == invoice_no,
        InvoiceItem.locked_by_batch_id == batch_session.id,
        InvoiceItem.is_picked == False,
        func.upper(InvoiceItem.pick_status).in_(['NOT_PICKED', 'RESET', 'SKIPPED_PENDING', 'SENT_TO_BATCH'])
    ).count()

def find_next_incomplete_invoice_index(batch_session, invoice_order):
    """Find the index of the next invoice with remaining locked items - optimized with single query"""
    # Get all invoices with remaining items in ONE query instead of checking each one
    invoices_with_items = db.session.query(InvoiceItem.invoice_no).filter(
        InvoiceItem.locked_by_batch_id == batch_session.id,
        InvoiceItem.is_picked == False,
        func.upper(InvoiceItem.pick_status).in_(['NOT_PICKED', 'RESET', 'SKIPPED_PENDING', 'SENT_TO_BATCH'])
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


def _enqueue_locked_items(batch_id):
    """Insert batch_pick_queue rows for every item currently locked to
    *batch_id* that does not already have a queue row.  Called after
    ``lock_items_for_batch`` in the admin create / add-invoices paths which
    lock items but historically never created the queue rows the picker UI
    needs.  Safe to call more than once — the NOT EXISTS guard prevents
    duplicates.

    Returns the number of rows inserted.
    """
    try:
        # FIX-007: sequence_no must reflect the admin-configured walking
        # order (zone → corridor → shelf → level → bin), which raw SQL
        # ROW_NUMBER() cannot express. Sort the locked items in Python and
        # insert with the loop index as sequence_no.
        from sorting_utils import sort_items_for_picking
        locked_items = InvoiceItem.query.filter(
            InvoiceItem.locked_by_batch_id == batch_id
        ).all()
        locked_items = sort_items_for_picking(locked_items)

        base_seq = db.session.execute(
            text(
                "SELECT COALESCE(MAX(sequence_no), 0) FROM batch_pick_queue "
                "WHERE batch_session_id = :bid"
            ),
            {"bid": batch_id},
        ).scalar() or 0

        inserted = 0
        for offset, ii in enumerate(locked_items, start=1):
            qty_required = ii.expected_pick_pieces if ii.expected_pick_pieces is not None else ii.qty
            result = db.session.execute(
                text("""
                    INSERT INTO batch_pick_queue (
                        batch_session_id, invoice_no, item_code, pick_zone_type,
                        sequence_no, status, qty_required, wms_zone
                    )
                    SELECT :bid, :inv, :code, 'normal', :seq, 'pending', :qty, :zone
                    WHERE NOT EXISTS (
                        SELECT 1 FROM batch_pick_queue bpq
                        WHERE bpq.batch_session_id = :bid
                          AND bpq.invoice_no   = :inv
                          AND bpq.item_code    = :code
                    )
                """),
                {
                    "bid": batch_id,
                    "inv": ii.invoice_no,
                    "code": ii.item_code,
                    "seq": base_seq + offset,
                    "qty": float(qty_required) if qty_required is not None else None,
                    "zone": ii.zone,
                },
            )
            inserted += result.rowcount
        current_app.logger.info(
            "_enqueue_locked_items: inserted %d queue rows for batch %d",
            inserted, batch_id,
        )
        return inserted
    except Exception as exc:
        current_app.logger.error(
            "_enqueue_locked_items: failed for batch %d: %s", batch_id, exc
        )
        raise

@batch_bp.route('/admin/batch/quick-view/<int:batch_id>', methods=['GET'])
@login_required
@require_permission('picking.manage_batches')
def batch_quick_view(batch_id):
    """Return JSON summary of a batch session for the dashboard quick-view modal."""
    session_obj = BatchPickingSession.query.get_or_404(batch_id)
    try:
        rows = db.session.execute(
            text("""
                SELECT
                    bpq.invoice_no,
                    bpq.item_code,
                    COALESCE(ii.item_name, bpq.item_code)  AS item_name,
                    COALESCE(bpq.qty_required, 0)           AS qty,
                    COALESCE(bpq.qty_picked,   0)           AS qty_picked,
                    bpq.status,
                    COALESCE(ii.location, '')               AS location
                FROM batch_pick_queue bpq
                LEFT JOIN invoice_items ii
                    ON ii.invoice_no = bpq.invoice_no AND ii.item_code = bpq.item_code
                WHERE bpq.batch_session_id = :bid
                ORDER BY bpq.invoice_no, ii.location, bpq.item_code
            """),
            {"bid": batch_id},
        ).fetchall()
    except Exception as _e:
        current_app.logger.warning("batch_quick_view: query failed for %s: %s", batch_id, _e)
        rows = []

    # Group by invoice
    invoices = {}
    totals = {"total": 0, "picked": 0, "pending": 0, "skipped": 0, "exception": 0}
    for r in rows:
        inv = r[0]
        if inv not in invoices:
            invoices[inv] = {"invoice_no": inv, "items": []}
        status = r[5] or "pending"
        qty = int(r[3] or 0)
        qty_picked = int(r[4] or 0)
        invoices[inv]["items"].append({
            "item_code":  r[1],
            "item_name":  r[2] or "",
            "qty":        qty,
            "qty_picked": qty_picked,
            "status":     status,
            "location":   r[6] or "",
        })
        totals["total"] += 1
        totals[status] = totals.get(status, 0) + 1
        if status == "picked":
            totals["picked"] += 1

    from flask import jsonify as _jsonify
    return _jsonify({
        "session": {
            "id":           session_obj.id,
            "name":         session_obj.name,
            "batch_number": session_obj.batch_number or "",
            "status":       session_obj.status,
            "session_type": getattr(session_obj, "session_type", "") or "",
            "picking_mode": session_obj.picking_mode or "",
            "assigned_to":  session_obj.assigned_to or "",
            "created_by":   session_obj.created_by or "",
        },
        "totals":   totals,
        "invoices": list(invoices.values()),
    })


@batch_bp.route('/admin/batch/manage')
@login_required
@require_permission('picking.manage_batches')
def batch_picking_manage():
    """Admin page to manage batch picking sessions"""
    # Only admin users can access this page
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))

    # Get active batch sessions
    active_sessions = BatchPickingSession.query.filter(
        BatchPickingSession.status.in_(ACTIVE_BATCH_STATUSES)
    ).order_by(BatchPickingSession.created_at.desc()).all()

    # Get completed batch sessions
    completed_sessions = BatchPickingSession.query.filter_by(
        status='Completed'
    ).order_by(BatchPickingSession.created_at.desc()).limit(10).all()

    # Filter out batches whose linked route is closed (COMPLETED / CANCELLED)
    # or already reconciled. Batches not linked to any route are kept.
    # Those hidden batches are still reachable via the Find Invoice/Route page.
    from models import Shipment
    _route_ids = [s.route_id for s in (list(active_sessions) + list(completed_sessions)) if s.route_id]
    _closed_route_ids = set()
    if _route_ids:
        _closed_rows = Shipment.query.filter(
            Shipment.id.in_(list(set(_route_ids))),
            db.or_(
                Shipment.status.in_(['COMPLETED', 'CANCELLED']),
                Shipment.reconciliation_status == 'RECONCILED',
            ),
        ).with_entities(Shipment.id).all()
        _closed_route_ids = {r.id for r in _closed_rows}

    active_sessions = [s for s in active_sessions if not s.route_id or s.route_id not in _closed_route_ids]
    completed_sessions = [s for s in completed_sessions if not s.route_id or s.route_id not in _closed_route_ids]

    # Get pickers for the assign dropdown
    pickers = get_picking_eligible_users()

    # Build route_id → delivery_date string map for cooler sessions so the
    # template can construct the cooler route URL without a lazy FK load.
    all_sessions = list(active_sessions) + list(completed_sessions)
    cooler_route_ids = [s.route_id for s in all_sessions
                        if s.session_type == 'cooler_route' and s.route_id]
    route_batch_route_ids = [s.route_id for s in all_sessions
                             if s.session_type == 'route_batch' and s.route_id]
    route_date_map = {}
    if cooler_route_ids or route_batch_route_ids:
        from models import Shipment
        rows = Shipment.query.filter(Shipment.id.in_(list(set(cooler_route_ids + route_batch_route_ids)))).with_entities(
            Shipment.id, Shipment.delivery_date).all()
        route_date_map = {r.id: r.delivery_date.strftime('%Y-%m-%d') for r in rows}

    # Item counts per session so the table can show how many items are
    # still in each batch (and flag empty ones, e.g. after invoices were
    # returned to warehouse and cooler queue rows were released).
    item_counts = {}
    session_ids = [s.id for s in all_sessions]
    if session_ids:
        from sqlalchemy import text as _text
        # batch_pick_queue is the source of truth for Phase 4+ sessions.
        q_rows = db.session.execute(
            _text(
                "SELECT batch_session_id, COUNT(*) "
                "FROM batch_pick_queue "
                "WHERE batch_session_id = ANY(:ids) "
                "GROUP BY batch_session_id"
            ),
            {"ids": session_ids},
        ).fetchall()
        for sid, cnt in q_rows:
            item_counts[sid] = int(cnt)
        # Fallback for legacy batches that don't use batch_pick_queue:
        # count locked InvoiceItems instead.
        missing_ids = [sid for sid in session_ids if sid not in item_counts]
        if missing_ids:
            l_rows = db.session.execute(
                _text(
                    "SELECT locked_by_batch_id, COUNT(*) "
                    "FROM invoice_items "
                    "WHERE locked_by_batch_id = ANY(:ids) "
                    "GROUP BY locked_by_batch_id"
                ),
                {"ids": missing_ids},
            ).fetchall()
            for sid, cnt in l_rows:
                item_counts[sid] = int(cnt)
        # Any session with no rows in either source is genuinely empty.
        for sid in session_ids:
            item_counts.setdefault(sid, 0)

    return render_template('batch_picking_manage.html',
                          active_sessions=active_sessions,
                          completed_sessions=completed_sessions,
                          pickers=pickers,
                          route_date_map=route_date_map,
                          item_counts=item_counts)

@batch_bp.route('/admin/batch/edit/<int:batch_id>', methods=['GET', 'POST'])
@login_required
@require_permission('picking.manage_batches')
def batch_edit(batch_id):
    """Edit an existing batch picking session"""
    if current_user.role not in ['admin', 'warehouse_manager']:
        flash('Access denied. Admin privileges required.', 'danger')
        return redirect(url_for('index'))
    
    batch = BatchPickingSession.query.get_or_404(batch_id)

    # Cooler batches are managed through the cooler route page, not batch edit.
    if batch.session_type == 'cooler_route':
        flash('Cooler batches cannot be edited here. Use the cooler route page instead.', 'warning')
        return redirect(url_for('batch.batch_picking_manage'))

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
    pickers = get_picking_eligible_users()
    
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
@require_permission('picking.manage_batches')
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

    # Cooler batches: invoice membership is controlled by the cooler-route
    # extraction service, not by manual add. Block manual adds until
    # sequencing is locked (matches the picker-assignment gate).
    if batch.session_type == 'cooler_route' and not batch.sequence_locked_at:
        flash('Cannot add invoices to a cooler batch until cooler sequencing is locked.', 'warning')
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

                # Populate batch_pick_queue so the picker UI has rows to show.
                # lock_items_for_batch only stamps locked_by_batch_id; without
                # queue rows the batch appears empty to the picker.
                enqueued = _enqueue_locked_items(batch_id)
                db.session.commit()
                current_app.logger.info(
                    "add_invoices_to_batch: %d queue rows created for batch %d",
                    enqueued, batch_id,
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
    from models import RouteStop, RouteStopInvoice, Shipment as _Shipment
    from datetime import date as _date

    zones_list = batch.zones.split(',') if batch.zones else []
    corridors_list = batch.corridors.split(',') if batch.corridors else []
    existing_invoices = [bi.invoice_no for bi in batch.invoices]

    # Filters from query-string
    date_filter     = request.args.get('date_filter', '').strip()
    route_filter    = request.args.get('route_filter', '').strip()
    driver_filter   = request.args.get('driver_filter', '').strip()
    shipment_filter = request.args.get('shipment_filter', '').strip()
    search_filter   = request.args.get('search', '').strip()

    # Step 1 — available invoices matching batch zone / corridor criteria
    base_conds = [
        InvoiceItem.zone.in_(zones_list),
        InvoiceItem.is_picked == False,
        func.upper(InvoiceItem.pick_status).in_(['NOT_PICKED', 'RESET', 'SKIPPED_PENDING']),
        InvoiceItem.locked_by_batch_id == None,
    ]
    if existing_invoices:
        base_conds.append(~Invoice.invoice_no.in_(existing_invoices))
    if corridors_list:
        base_conds.append(InvoiceItem.corridor.in_(corridors_list))
    if search_filter:
        base_conds.append(or_(
            Invoice.customer_name.ilike(f'%{search_filter}%'),
            Invoice.invoice_no.ilike(f'%{search_filter}%'),
        ))

    available_invoices = db.session.query(
        Invoice.invoice_no,
        Invoice.customer_name,
        Invoice.routing,
        func.count(func.distinct(InvoiceItem.item_code)).label('item_count'),
        func.coalesce(func.sum(InvoiceItem.qty), 0).label('total_qty'),
    ).join(InvoiceItem, InvoiceItem.invoice_no == Invoice.invoice_no).filter(
        and_(*base_conds)
    ).group_by(Invoice.invoice_no, Invoice.customer_name, Invoice.routing).all()

    # Step 2 — route / stop data for those invoices
    route_lookup = {}
    shipment_stop_counts = {}
    if available_invoices:
        inv_nos = [r.invoice_no for r in available_invoices]
        rd_rows = db.session.query(
            RouteStopInvoice.invoice_no,
            RouteStop.route_stop_id,
            RouteStop.seq_no,
            RouteStop.stop_name,
            RouteStop.customer_code,
            _Shipment.id.label('shipment_id'),
            _Shipment.route_name,
            _Shipment.driver_name,
            _Shipment.delivery_date,
            _Shipment.status.label('route_status'),
        ).join(RouteStop, RouteStop.route_stop_id == RouteStopInvoice.route_stop_id
        ).join(_Shipment, _Shipment.id == RouteStop.shipment_id
        ).filter(
            RouteStopInvoice.invoice_no.in_(inv_nos),
            RouteStopInvoice.is_active == True,
        ).all()
        route_lookup = {r.invoice_no: r for r in rd_rows}

        all_sids = list({r.shipment_id for r in rd_rows})
        if all_sids:
            for sc in db.session.query(
                RouteStop.shipment_id,
                func.count(RouteStop.route_stop_id).label('cnt'),
            ).filter(RouteStop.shipment_id.in_(all_sids)).group_by(RouteStop.shipment_id).all():
                shipment_stop_counts[sc.shipment_id] = sc.cnt

    # Step 3 — group into route_groups
    route_groups = {}
    unrouted = []

    for inv in available_invoices:
        inv_dict = {
            'invoice_no': inv.invoice_no,
            'customer_name': inv.customer_name or '',
            'routing': inv.routing,
            'item_count': inv.item_count or 0,
            'total_qty': float(inv.total_qty or 0),
        }
        rd = route_lookup.get(inv.invoice_no)
        if rd is None:
            unrouted.append(inv_dict)
            continue

        # Apply route-based filters
        if date_filter:
            try:
                from datetime import datetime as _dt
                df = _dt.strptime(date_filter, '%Y-%m-%d').date()
                if rd.delivery_date != df:
                    continue
            except (ValueError, TypeError):
                pass
        if route_filter and route_filter.lower() not in (rd.route_name or '').lower():
            continue
        if driver_filter and driver_filter.lower() not in (rd.driver_name or '').lower():
            continue
        if shipment_filter:
            try:
                if int(shipment_filter) != rd.shipment_id:
                    continue
            except (ValueError, TypeError):
                pass

        sid = rd.shipment_id
        if sid not in route_groups:
            route_groups[sid] = {
                'shipment_id': sid,
                'route_name': rd.route_name or f'Route #{sid}',
                'driver_name': rd.driver_name or 'N/A',
                'delivery_date': rd.delivery_date,
                'route_status': rd.route_status or '',
                'stop_count': shipment_stop_counts.get(sid, 0),
                'stops': {},
                'total_invoices': 0,
                'total_lines': 0,
                'total_qty': 0.0,
            }
        stop_id = rd.route_stop_id
        if stop_id not in route_groups[sid]['stops']:
            route_groups[sid]['stops'][stop_id] = {
                'route_stop_id': stop_id,
                'seq_no': float(rd.seq_no),
                'stop_name': rd.stop_name or f'Stop {float(rd.seq_no):.0f}',
                'customer_code': rd.customer_code or '',
                'invoices': [],
            }
        route_groups[sid]['stops'][stop_id]['invoices'].append(inv_dict)
        route_groups[sid]['total_invoices'] += 1
        route_groups[sid]['total_lines'] += inv.item_count or 0
        route_groups[sid]['total_qty'] += float(inv.total_qty or 0)

    # Sort stops by seq_no; sort groups by delivery_date then route_name
    for grp in route_groups.values():
        grp['stops'] = dict(sorted(grp['stops'].items(), key=lambda kv: kv[1]['seq_no']))
    route_groups_list = sorted(
        route_groups.values(),
        key=lambda g: (g['delivery_date'] or _date(1900, 1, 1), g['route_name']),
    )

    return render_template('batch_add_invoices.html',
        batch=batch,
        route_groups=route_groups_list,
        unrouted_invoices=unrouted,
        zones=zones_list,
        corridors=corridors_list,
        total_available=len(available_invoices),
        date_filter=date_filter,
        route_filter=route_filter,
        driver_filter=driver_filter,
        shipment_filter=shipment_filter,
        search_filter=search_filter,
    )

@batch_bp.route('/admin/batch/delete/<int:batch_id>', methods=['POST'])
@login_required
@require_permission('picking.delete_empty_batch')
def batch_delete(batch_id):
    """Phase 4: hard-delete is now gated behind ``picking.delete_empty_batch``
    and only allowed for empty batches. The default UI path uses
    ``cancel_batch`` (see ``services/batch_picking.py``) so unpicked locks
    are released and audit history is preserved.
    """
    from services import batch_status as _bs
    from services.batch_picking import cancel_batch as _cancel_batch

    if current_user.role != 'admin':
        flash('Hard delete is admin-only. Use Cancel instead.', 'danger')
        return redirect(url_for('index'))

    batch = BatchPickingSession.query.get_or_404(batch_id)

    if _bs.is_terminal(batch.status) and batch.status != 'Cancelled':
        flash('Cannot delete completed/archived batches.', 'warning')
        return redirect(url_for('batch.batch_picking_manage'))

    # Truly-empty contract — anything else routes through cancel.
    picked_count = BatchPickedItem.query.filter_by(batch_session_id=batch_id).count()
    sess_inv_count = BatchSessionInvoice.query.filter_by(batch_session_id=batch_id).count()
    locks_count = InvoiceItem.query.filter_by(locked_by_batch_id=batch_id).count()
    queue_count = 0
    try:
        queue_count = db.session.execute(
            text("SELECT COUNT(*) FROM batch_pick_queue WHERE batch_session_id = :bid"),
            {"bid": batch_id},
        ).scalar() or 0
    except Exception:
        queue_count = 0

    if picked_count or sess_inv_count or locks_count or queue_count:
        try:
            _cancel_batch(batch_id, current_user.username,
                          reason=f'Hard-delete on non-empty batch (picks={picked_count}, '
                                 f'invoices={sess_inv_count}, locks={locks_count}, queue={queue_count})')
            flash(f'Batch "{batch.name}" cancelled instead of hard-deleted (audit preserved).', 'info')
        except Exception as e:
            flash(f'Error cancelling batch: {e}', 'danger')
        return redirect(url_for('batch.batch_picking_manage'))

    try:
        BatchSessionInvoice.query.filter_by(batch_session_id=batch_id).delete()
        # Note: BatchPickedItem rows are NOT deleted — empty batch path,
        # but if any historical rows linger they remain for audit.
        InvoiceItem.query.filter_by(locked_by_batch_id=batch_id).update(
            {InvoiceItem.locked_by_batch_id: None}, synchronize_session=False)
        try:
            db.session.execute(
                text("DELETE FROM batch_pick_queue WHERE batch_session_id = :bid"),
                {"bid": batch_id},
            )
        except Exception:
            pass
        batch_name = batch.name
        db.session.delete(batch)
        db.session.add(ActivityLog(
            picker_username=current_user.username,
            activity_type='batch.hard_deleted',
            details=f'Empty batch "{batch_name}" (id={batch_id}) hard-deleted by '
                    f'{current_user.username} via picking.delete_empty_batch (audit logs preserved)'
        ))
        db.session.commit()
        flash(f'Empty batch "{batch_name}" deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting batch: {str(e)}', 'danger')

    return redirect(url_for('batch.batch_picking_manage'))

@batch_bp.route('/admin/batch/simple', methods=['GET', 'POST'])
@login_required
@require_permission('picking.manage_batches')
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

        # Phase 4: drain block — non-admins cannot create batches while
        # the system is draining for maintenance.
        from services.maintenance import drain as _drain
        if not _drain.is_creation_allowed_for(current_user):
            flash(_drain.get_drain_banner() or
                  'Batch creation is paused — maintenance mode is draining.', 'warning')
            return redirect(url_for('batch.batch_picking_create_simple'))

        # Phase 4: when ``use_db_backed_picking_queue`` is ON, route
        # creation through the all-or-nothing service. BatchConflict is
        # surfaced as a flash and the user is sent back to the form.
        from services.batch_picking import (
            BatchConflict as _BatchConflict,
            create_batch_atomic as _create_batch_atomic,
            is_db_queue_enabled as _is_db_queue_enabled,
        )
        if _is_db_queue_enabled():
            zone_list_dispatch = [z.strip() for z in zones.split(',') if z.strip()]
            try:
                batch = _create_batch_atomic(
                    filters={"zones": zone_list_dispatch},
                    created_by=current_user.username,
                    mode=picking_mode,
                    name=name,
                )
                flash(f'Batch picking session "{name}" created (DB-backed queue).', 'success')
                return redirect(url_for('batch.batch_picking_manage'))
            except _BatchConflict as bc:
                flash(bc.message, 'danger')
                return redirect(url_for('batch.batch_picking_create_simple'))
            except ValueError as ve:
                flash(str(ve), 'warning')
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
            func.upper(InvoiceItem.pick_status).in_(['NOT_PICKED', 'RESET', 'SKIPPED_PENDING']),
            InvoiceItem.locked_by_batch_id == None,  # Only unlocked items
            func.upper(Invoice.status).in_(['NOT_STARTED', 'PICKING'])
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

                # Populate batch_pick_queue so the picker UI has rows to show.
                enqueued = _enqueue_locked_items(batch_session.id)
                current_app.logger.info(
                    "batch_picking_create_simple: %d queue rows created for batch %d",
                    enqueued, batch_session.id,
                )
            except Exception as lock_error:
                current_app.logger.error(f"Failed to lock/enqueue items for batch {batch_session.id}: {str(lock_error)}")
                # Continue with batch creation even if locking fails, but log the error
            
            # Commit changes
            db.session.commit()
            
            flash(f'Batch picking session "{name}" created successfully with {len(invoice_numbers)} invoices.', 'success')
            return redirect(url_for('batch.batch_picking_manage'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating batch picking session: {str(e)}', 'danger')
            return redirect(url_for('batch.batch_picking_create_simple'))
    
    # GET request - show the form
    return render_template('batch_picking_create.html')

@batch_bp.route('/admin/batch/filter', methods=['GET', 'POST'])
@login_required
@require_permission('picking.manage_batches')
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

@batch_bp.route('/admin/batch/filter-invoices', methods=['GET', 'POST'])
@login_required
@require_permission('picking.manage_batches')
def filter_invoices_for_batch():
    """Filter invoices for batch picking and show selection interface"""
    if request.method == 'GET':
        return redirect(url_for('batch.batch_picking_filter'))
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
        BatchPickingSession.status.in_(ACTIVE_BATCH_STATUSES)
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

    # --- Build route groups ---
    from models import Shipment
    invoice_nos = [inv.invoice_no for inv in invoices]
    inv_map = {inv.invoice_no: inv for inv in invoices}

    # Fetch active route-stop links for these invoices
    rsi_rows = (
        db.session.query(RouteStopInvoice, RouteStop, Shipment)
        .join(RouteStop, RouteStop.route_stop_id == RouteStopInvoice.route_stop_id)
        .join(Shipment, Shipment.id == RouteStop.shipment_id)
        .filter(
            RouteStopInvoice.invoice_no.in_(invoice_nos),
            RouteStopInvoice.is_active.is_(True),
            RouteStop.deleted_at.is_(None),
        )
        .all()
    )

    # Map invoice_no → (RouteStop, Shipment); keep first hit per invoice
    inv_to_stop = {}
    for rsi, stop, shipment in rsi_rows:
        if rsi.invoice_no not in inv_to_stop:
            inv_to_stop[rsi.invoice_no] = (stop, shipment)

    # Build route_groups: shipment_id → {shipment, stops: {route_stop_id → {stop, invoices[]}}}
    shipment_map = {}
    for inv_no, (stop, shipment) in inv_to_stop.items():
        sid = shipment.id
        if sid not in shipment_map:
            shipment_map[sid] = {'shipment': shipment, 'stops': {}}
        stops_dict = shipment_map[sid]['stops']
        rsid = stop.route_stop_id
        if rsid not in stops_dict:
            stops_dict[rsid] = {'stop': stop, 'invoices': []}
        stops_dict[rsid]['invoices'].append(inv_map[inv_no])

    # Sort groups by delivery_date then route_name; stops by seq_no
    route_groups = []
    for sid, gdata in shipment_map.items():
        ship = gdata['shipment']
        sorted_stops = sorted(gdata['stops'].values(), key=lambda s: s['stop'].seq_no or 0)
        all_inv = [i for s in sorted_stops for i in s['invoices']]
        route_groups.append({
            'shipment': ship,
            'stops': sorted_stops,
            'invoice_count': len(all_inv),
            'total_lines': sum(getattr(i, 'total_lines', 0) or 0 for i in all_inv),
            'eligible_items': sum(getattr(i, 'eligible_item_count', 0) or 0 for i in all_inv),
        })
    route_groups.sort(key=lambda g: (
        g['shipment'].delivery_date or '',
        g['shipment'].route_name or '',
    ))

    unrouted_invoices = [inv for inv in invoices if inv.invoice_no not in inv_to_stop]
    # --- End route groups ---

    # Get picker list for assignment
    pickers = get_picking_eligible_users()
    
    # Create a default session name based on zones and timestamp
    now = get_local_time().strftime('%Y-%m-%d_%H:%M')
    zone_prefix = zones[0] if len(zones) == 1 else f"{zones[0]}_Plus_{len(zones)-1}"
    default_name = f"{zone_prefix}_Batch_{now}"
    
    # Pass invoices to the create batch page for selection
    return render_template('batch_picking_create.html',
                          invoices=invoices,
                          route_groups=route_groups,
                          unrouted_invoices=unrouted_invoices,
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
@require_permission('picking.manage_batches')
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

    # Phase 4: drain block (non-admins blocked from creating batches in drain mode)
    from services.maintenance import drain as _drain
    if not _drain.is_creation_allowed_for(current_user):
        flash(_drain.get_drain_banner() or
              'Batch creation is paused — maintenance mode is draining.', 'warning')
        return redirect(url_for('batch.batch_picking_filter'))

    # Phase 4 dispatcher: DB-backed atomic path when flag is ON
    from services.batch_picking import (
        BatchConflict as _BatchConflict,
        create_batch_atomic as _create_batch_atomic,
        is_db_queue_enabled as _is_db_queue_enabled,
    )
    if _is_db_queue_enabled():
        clean_zones_dispatch = [str(z).strip('{}[]').strip() for z in zones if str(z).strip('{}[]').strip()]
        clean_corridors_dispatch = [str(c).strip('{}[]').strip() for c in corridors if str(c).strip('{}[]').strip()]
        clean_unit_types_dispatch = [str(u).strip('{}[]').strip() for u in unit_types if str(u).strip('{}[]').strip()]
        try:
            batch = _create_batch_atomic(
                filters={
                    "zones": clean_zones_dispatch,
                    "corridors": clean_corridors_dispatch or None,
                    "unit_types": clean_unit_types_dispatch or None,
                    "invoice_nos": invoice_nos,
                },
                created_by=current_user.username,
                mode=picking_mode,
                name=name,
            )
            flash(f'Batch "{name}" created with DB-backed queue.', 'success')
            return redirect(url_for('batch.batch_picking_manage'))
        except _BatchConflict as bc:
            flash(bc.message, 'danger')
            return redirect(url_for('batch.batch_picking_filter'))
        except ValueError as ve:
            flash(str(ve), 'warning')
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
            BatchPickingSession.status.in_(ACTIVE_BATCH_STATUSES)
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
@require_permission('picking.manage_batches')
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


@batch_bp.route('/admin/batch/assign/<int:batch_id>', methods=['POST'])
@login_required
@require_permission('picking.manage_batches')
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
    
    _back = request.form.get('next') or request.referrer or url_for('batch.batch_picking_manage')

    if not picker_username:
        flash('Please select a picker to assign.', 'warning')
        return redirect(_back)
    
    # Get the batch session
    batch_session = BatchPickingSession.query.get_or_404(batch_id)

    # Cooler batches cannot be assigned to a picker until cooler sequencing
    # has been locked. The cooler queue rows have no delivery_sequence
    # before that point, so picking would be unordered.
    if batch_session.session_type == 'cooler_route' and not batch_session.sequence_locked_at:
        flash('Cannot assign a picker: cooler sequencing must be locked first.', 'warning')
        return redirect(_back)

    # Assign the picker and activate the batch
    batch_session.assigned_to = picker_username
    batch_session.status = 'Active'  # Automatically activate when assigned
    
    # Save changes to database
    db.session.commit()
    
    flash(f'Picker {picker_username} assigned to batch picking session and activated.', 'success')
    return redirect(_back)

@batch_bp.route('/admin/batch/unassign/<int:batch_id>', methods=['POST'])
@login_required
@require_permission('picking.manage_batches')
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
    return redirect(request.referrer or url_for('batch.batch_picking_manage'))

@batch_bp.route('/picker/batch/list')
@login_required
def picker_batch_list():
    """Picker page: assigned batch sessions + (Phase 4) claimable batches.

    When ``batch_claim_required`` is ON, also surface unassigned /
    unclaimed Created+Active batches so any picker can claim one.
    Admins and warehouse managers see ALL non-terminal batches so they
    can reassign or claim on behalf of pickers.
    """
    if current_user.role not in ['admin', 'warehouse_manager'] and current_user.role != 'picker':
        flash('Access denied. Picker privileges required.', 'danger')
        return redirect(url_for('index'))

    from services.batch_picking import is_claim_required as _is_claim_required
    from sqlalchemy import or_ as _or_

    is_admin_like = current_user.role == 'admin'
    claim_on = _is_claim_required()

    base_q = BatchPickingSession.query.filter(
        BatchPickingSession.status.notin_(['Completed', 'Cancelled', 'Archived'])
    )

    if is_admin_like:
        batch_sessions = base_q.order_by(BatchPickingSession.created_at.desc()).all()
    elif claim_on:
        # Picker sees: own assignments + claimable (unassigned/unclaimed)
        batch_sessions = base_q.filter(
            _or_(
                BatchPickingSession.assigned_to == current_user.username,
                BatchPickingSession.assigned_to.is_(None),
                BatchPickingSession.assigned_to == '',
            )
        ).order_by(BatchPickingSession.created_at.desc()).all()
    else:
        batch_sessions = base_q.filter(
            BatchPickingSession.assigned_to == current_user.username
        ).order_by(BatchPickingSession.created_at.desc()).all()

    return render_template(
        'batch_picking_list.html',
        batch_sessions=batch_sessions,
        claim_required=claim_on,
        current_username=current_user.username,
    )

@batch_bp.route('/picker/batch/clear_cache/<int:batch_id>')
@login_required
def clear_batch_cache_route(batch_id):
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

    # Phase 4: when ``batch_claim_required`` is ON, ALL users (including
    # admin / WM acting as picker) must explicitly claim before starting.
    from services.batch_picking import is_claim_required as _is_claim_required
    if _is_claim_required() and not getattr(batch_session, 'claimed_by', None):
        flash('This batch requires explicit claim. Please claim before picking.', 'warning')
        return redirect(url_for('batch.picker_batch_list'))

    # Update the status to picking
    batch_session.status = 'picking'
    
    # Calculate ALL items in the batch - all zones for all invoices
    zone_list = [zone.strip() for zone in batch_session.zones.split(',')]
    all_batch_items = []
    
    current_app.logger.warning(f"🔍 BATCH START DEBUG: Zone list = {zone_list}")
    
    # Get all invoices in this batch - sort by routing number descending to maintain picking order
    # Cast routing to numeric for proper sorting (not string sorting).
    # Legacy Invoice.routing can contain free text (e.g. driver notes), so only
    # cast values that actually look numeric — everything else sorts as NULL.
    from sqlalchemy import case as _case
    _routing_numeric = func.cast(
        _case(
            (Invoice.routing.op('~')(r'^\s*[0-9]+(\.[0-9]+)?\s*$'), Invoice.routing),
            else_=None,
        ),
        db.Numeric,
    )
    batch_invoices = BatchSessionInvoice.query.join(Invoice).filter(
        BatchSessionInvoice.batch_session_id == batch_id
    ).order_by(_routing_numeric.desc().nulls_last()).all()
    
    for bi in batch_invoices:
        # Get items that are actually locked by THIS batch
        invoice_items = InvoiceItem.query.filter(
            InvoiceItem.invoice_no == bi.invoice_no,
            InvoiceItem.locked_by_batch_id == batch_session.id,
            InvoiceItem.is_picked == False,
            InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending', 'sent_to_batch'])
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

def build_cooler_box_picking_items(batch_session):
    """Box-first picking list for cooler_route sessions.

    Returns items ordered by box_no ASC, then warehouse location within each box.
    Grouping key is (box_id, item_code, location) — no cross-box consolidation.
    Only returns items not yet picked (cbi.status = 'planned').
    """
    from collections import OrderedDict
    route_id   = batch_session.route_id
    session_id = batch_session.id

    rows = db.session.execute(
        text(
            "SELECT "
            "  cb.id        AS box_id, "
            "  cb.box_no, "
            "  COALESCE(cbt.name, 'Box') AS box_type_name, "
            "  cb.status    AS box_status, "
            "  cbi.invoice_no, "
            "  cbi.item_code, "
            "  COALESCE(cbi.item_name, ii.item_name, 'Unknown') AS item_name, "
            "  COALESCE(ii.location, '') AS location, "
            "  cbi.expected_qty AS qty, "
            "  COALESCE(ii.barcode, '') AS barcode, "
            "  COALESCE(ii.zone, '') AS zone, "
            "  COALESCE(ii.unit_type, '') AS unit_type, "
            "  COALESCE(ii.pack, '') AS pack, "
            "  COALESCE(inv.customer_name, '') AS customer_name, "
            "  cb.first_stop_sequence, "
            "  cb.last_stop_sequence "
            "FROM cooler_boxes cb "
            "JOIN cooler_box_items cbi ON cbi.cooler_box_id = cb.id "
            "LEFT JOIN cooler_box_types cbt ON cbt.id = cb.box_type_id "
            "LEFT JOIN invoice_items ii  ON ii.invoice_no = cbi.invoice_no "
            "                           AND ii.item_code  = cbi.item_code "
            "LEFT JOIN invoices inv       ON inv.invoice_no = cbi.invoice_no "
            "LEFT JOIN batch_pick_queue bpq ON bpq.id = cbi.queue_item_id "
            "WHERE cb.route_id          = :rid "
            "  AND cb.status           != 'cancelled' "
            "  AND cbi.status           = 'planned' "
            "  AND bpq.batch_session_id = :sid "
            "ORDER BY cb.box_no ASC, "
            "         COALESCE(ii.location, 'ZZZ') ASC, "
            "         cbi.invoice_no ASC, "
            "         cbi.item_code  ASC"
        ),
        {"rid": route_id, "sid": session_id},
    ).fetchall()

    if not rows:
        return []

    box_meta = {}
    grouped  = OrderedDict()

    for r in rows:
        box_id    = r[0]
        item_code = r[5]
        location  = r[7] or ''
        key       = (box_id, item_code, location)

        if box_id not in box_meta:
            fsq = r[14]
            lsq = r[15]
            if fsq is not None and lsq is not None:
                fi, li = int(float(fsq)), int(float(lsq))
                stop_display = f"Stop {fi}" if fi == li else f"Stops {max(fi,li)} → {min(fi,li)}"
            else:
                stop_display = ""
            # Use db box_no when available; fall back to sequential position
            # (box_no can be NULL on plans created before the column was populated)
            _box_no = r[1] if r[1] is not None else (len(box_meta) + 1)
            box_meta[box_id] = {
                "box_no":         _box_no,
                "box_type_name":  r[2],
                "box_status":     r[3],
                "stop_display":   stop_display,
            }

        if key not in grouped:
            bm = box_meta[box_id]
            grouped[key] = {
                "box_id":           box_id,
                "box_no":           bm["box_no"],
                "box_type_name":    bm["box_type_name"],
                "box_status":       bm["box_status"],
                "box_stop_display": bm["stop_display"],
                "item_code":        item_code,
                "item_name":        r[6],
                "location":         location or None,
                "barcode":          r[9],
                "zone":             r[10],
                "unit_type":        r[11],
                "pack":             r[12],
                "customer_name":    r[13],
                "total_qty":        0,
                "source_items":     [],
                "current_invoice":  None,
            }

        qty = float(r[8] or 0)
        grouped[key]["total_qty"] += qty
        grouped[key]["source_items"].append({
            "invoice_no": r[4],
            "item_code":  item_code,
            "qty":        qty,
        })
        if grouped[key]["current_invoice"] is None:
            grouped[key]["current_invoice"] = r[4]

    items = list(grouped.values())

    # Annotate each item with its in-box position and box-transition metadata
    by_box = OrderedDict()
    for idx, item in enumerate(items):
        by_box.setdefault(item["box_id"], []).append(idx)

    box_ids_ordered = list(by_box.keys())

    for box_pos, bid in enumerate(box_ids_ordered):
        idxs         = by_box[bid]
        total_in_box = len(idxs)
        if box_pos + 1 < len(box_ids_ordered):
            next_bid = box_ids_ordered[box_pos + 1]
            next_bno  = items[by_box[next_bid][0]]["box_no"]
            next_btype = items[by_box[next_bid][0]]["box_type_name"]
        else:
            next_bno = next_btype = None

        for i, item_idx in enumerate(idxs):
            it = items[item_idx]
            it["item_index_in_box"]  = i + 1
            it["total_items_in_box"] = total_in_box
            it["is_first_item_in_box"] = (i == 0)
            it["is_last_item_in_box"]  = (i == total_in_box - 1)
            it["next_box_no"]          = next_bno
            it["next_box_type_name"]   = next_btype

    return items


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
    
    # ── Cooler box-first: intercept pending box-complete transition ───────────
    if getattr(batch_session, 'session_type', None) == 'cooler_route':
        _pending_bc = session.pop('cooler_box_complete_pending', None)
        if _pending_bc:
            return render_template('cooler_box_complete.html',
                                   batch_session=batch_session,
                                   box_info=_pending_bc)

    # ── Cooler box-first: block picking if no confirmed box plan exists ───────
    if getattr(batch_session, 'session_type', None) == 'cooler_route' and batch_session.route_id:
        try:
            _has_box_plan = db.session.execute(
                text(
                    "SELECT 1 FROM cooler_box_items cbi "
                    "JOIN cooler_boxes cb ON cb.id = cbi.cooler_box_id "
                    "WHERE cb.route_id = :rid LIMIT 1"
                ),
                {"rid": batch_session.route_id},
            ).fetchone()
        except Exception:
            _has_box_plan = True
        if not _has_box_plan:
            flash(
                'A confirmed box plan is required before picking can start. '
                'Please plan and confirm the boxes first.',
                'warning',
            )
            from models import Shipment as _Shipment
            _route = _Shipment.query.get(batch_session.route_id)
            if _route and getattr(_route, 'delivery_date', None):
                return redirect(url_for(
                    'cooler.route_picking',
                    route_id=batch_session.route_id,
                    delivery_date=_route.delivery_date.strftime('%Y-%m-%d'),
                ))
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

        # ── Box-first cooler picking: build from cooler_box_items ─────────────
        if getattr(batch_session, 'session_type', None) == 'cooler_route':
            _cbp_items = build_cooler_box_picking_items(batch_session)
            if _cbp_items:
                _cbp_serialized = []
                for _ci in _cbp_items:
                    _cbp_serialized.append({
                        'box_id':             _ci['box_id'],
                        'box_no':             _ci['box_no'],
                        'box_type_name':      _ci['box_type_name'],
                        'box_status':         _ci['box_status'],
                        'box_stop_display':   _ci['box_stop_display'],
                        'item_index_in_box':  _ci['item_index_in_box'],
                        'total_items_in_box': _ci['total_items_in_box'],
                        'is_first_item_in_box': _ci['is_first_item_in_box'],
                        'is_last_item_in_box':  _ci['is_last_item_in_box'],
                        'next_box_no':          _ci['next_box_no'],
                        'next_box_type_name':   _ci['next_box_type_name'],
                        'item_code':    _ci['item_code'],
                        'item_name':    _ci['item_name'],
                        'location':     _ci.get('location'),
                        'zone':         _ci.get('zone', ''),
                        'barcode':      _ci.get('barcode', ''),
                        'unit_type':    _ci.get('unit_type', ''),
                        'pack':         _ci.get('pack', ''),
                        'total_qty':    _ci['total_qty'],
                        'current_invoice': _ci['current_invoice'],
                        'customer_name':   _ci.get('customer_name', ''),
                        'source_items':    _ci['source_items'],
                        'order_total_items':  None,
                        'order_total_weight': None,
                        'routing':            None,
                    })
                session[fixed_batch_key] = _cbp_serialized
                batch_session.current_item_index = 0
                db.session.commit()
                current_app.logger.info(
                    "cooler box-first: cached %d items for batch %s",
                    len(_cbp_serialized), batch_id,
                )
        else:
            # Generate the complete list of ALL items in the batch
            all_batch_items = batch_session.get_grouped_items()

        # Save these items in the session
        if getattr(batch_session, 'session_type', None) != 'cooler_route' and all_batch_items:
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
                        {
                            'invoice_no': s['invoice_no'],
                            'item_code': s['item_code'],
                            'qty': s['qty'],
                            'expected_pick_pieces': s.get('expected_pick_pieces', s['qty']),
                        }
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
    
    def _serialize_cooler_box_items(ci_list):
        """Serialize build_cooler_box_picking_items output for session storage."""
        out = []
        for _ci in ci_list:
            out.append({
                'box_id':             _ci['box_id'],
                'box_no':             _ci['box_no'],
                'box_type_name':      _ci['box_type_name'],
                'box_status':         _ci['box_status'],
                'box_stop_display':   _ci['box_stop_display'],
                'item_index_in_box':  _ci['item_index_in_box'],
                'total_items_in_box': _ci['total_items_in_box'],
                'is_first_item_in_box': _ci['is_first_item_in_box'],
                'is_last_item_in_box':  _ci['is_last_item_in_box'],
                'next_box_no':          _ci['next_box_no'],
                'next_box_type_name':   _ci['next_box_type_name'],
                'item_code':    _ci['item_code'],
                'item_name':    _ci['item_name'],
                'location':     _ci.get('location'),
                'zone':         _ci.get('zone', ''),
                'barcode':      _ci.get('barcode', ''),
                'unit_type':    _ci.get('unit_type', ''),
                'pack':         _ci.get('pack', ''),
                'total_qty':    _ci['total_qty'],
                'current_invoice': _ci['current_invoice'],
                'customer_name':   _ci.get('customer_name', ''),
                'source_items':    _ci['source_items'],
                'order_total_items':  None,
                'order_total_weight': None,
                'routing':            None,
            })
        return out

    # Phase A: DB-backed batches read directly from the queue every request.
    # This eliminates the cookie-overflow bug (>15-item batches losing order).
    # Cooler-route batches keep the cookie path because box metadata (box_id,
    # box_no, is_last_item_in_box) lives outside the queue.
    from services.batch_picking import (
        is_db_backed_batch as _is_db_backed,
        rebuild_items_from_queue as _rebuild_from_queue,
    )
    if _is_db_backed(batch_id) and getattr(batch_session, 'session_type', None) != 'cooler_route':
        # Queue IS the pointer — pending rows only, always items[0] is current.
        items = _rebuild_from_queue(batch_id)
        batch_session.current_item_index = 0
        current_app.logger.info(
            "queue-primary: %d pending item(s) for batch %s",
            len(items), batch_id,
        )
    elif fixed_batch_key not in session and _is_db_backed(batch_id):
        # Cooler-route DB-backed resume via box plan (box metadata not in queue).
        _resume_items = build_cooler_box_picking_items(batch_session)
        rebuilt = _serialize_cooler_box_items(_resume_items)
        session[fixed_batch_key] = rebuilt
        batch_session.current_item_index = 0
        batch_session.current_invoice_index = 0
        db.session.commit()
        current_app.logger.info(
            "queue-resume cooler: rebuilt %d item(s) for batch %s",
            len(rebuilt), batch_id,
        )
        items = session[fixed_batch_key]
    elif fixed_batch_key in session:
        items = session[fixed_batch_key]
        current_app.logger.info(f"Using fixed batch list: {len(items)} items, current index: {batch_session.current_item_index}")
    else:
        if getattr(batch_session, 'session_type', None) == 'cooler_route':
            items = _serialize_cooler_box_items(build_cooler_box_picking_items(batch_session))
        else:
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
        # For cooler batches: if the session cache was empty (stale from before the
        # Cooler picking_mode fix) but items are actually locked to this batch,
        # clear the stale cache and regenerate rather than falling through to the
        # "no items found" error path or treating the batch as complete prematurely.
        if (not items
                and batch_session.current_item_index == 0
                and getattr(batch_session, 'picking_mode', None) == 'Cooler'):
            if getattr(batch_session, 'session_type', None) == 'cooler_route':
                # Box-first flow: rebuild from confirmed box plan
                _regen_cbp = build_cooler_box_picking_items(batch_session)
                serialized_regen = _serialize_cooler_box_items(_regen_cbp)
            else:
                regenerated = batch_session.get_grouped_items()
                serialized_regen = []
                for _ri in (regenerated or []):
                    _first_inv = _ri['source_items'][0]['invoice_no'] if _ri['source_items'] else None
                    _inv = Invoice.query.filter_by(invoice_no=_first_inv).first() if _first_inv else None
                    serialized_regen.append({
                        'item_code': _ri['item_code'],
                        'item_name': _ri.get('item_name', _ri['item_code']),
                        'location': _ri['location'],
                        'zone': _ri['zone'],
                        'barcode': _ri.get('barcode', ''),
                        'unit_type': _ri.get('unit_type', ''),
                        'pack': _ri.get('pack', ''),
                        'total_qty': _ri['total_qty'],
                        'current_invoice': _first_inv,
                        'customer_name': _inv.customer_name if _inv else None,
                        'routing': _inv.routing if _inv else None,
                        'source_items': [
                            {'invoice_no': s['invoice_no'], 'item_code': s['item_code'], 'qty': s['qty']}
                            for s in _ri['source_items']
                        ],
                        'order_total_items': _inv.total_items if _inv else None,
                        'order_total_weight': _inv.total_weight if _inv else None,
                    })
            if serialized_regen:
                session[fixed_batch_key] = serialized_regen
                items = serialized_regen
                batch_session.current_item_index = 0
                db.session.commit()
                # Fall through to normal rendering below (items is now non-empty).

        # Check for skipped items that need to be collected later.
        batch_invoices = BatchSessionInvoice.query.filter_by(batch_session_id=batch_id).all()
        invoice_nos = [bi.invoice_no for bi in batch_invoices]

        if getattr(batch_session, 'session_type', None) == 'cooler_route':
            # Cooler-route batches: bring SKIPPED (collect-later) queue rows
            # back so the picker resolves them at the END of the run. This
            # mirrors normal picking's skipped_pending recycle — skipped items
            # keep returning every pass UNTIL the picker either picks them or
            # reports them unavailable (exception). Genuine exceptions
            # (status='exception') are terminal and are never recycled.
            # There is no infinite code loop: it is a manual loop the picker
            # exits by resolving each skipped item.
            skipped_rows = db.session.execute(
                text(
                    "SELECT invoice_no, item_code FROM batch_pick_queue "
                    "WHERE batch_session_id = :bid "
                    "  AND status = 'skipped_pending' "
                    "  AND pick_zone_type = 'cooler'"
                ),
                {"bid": batch_id},
            ).fetchall()
            if skipped_rows:
                db.session.execute(
                    text(
                        "UPDATE batch_pick_queue "
                        "SET status = 'pending', updated_at = NOW() "
                        "WHERE batch_session_id = :bid "
                        "  AND status = 'skipped_pending' "
                        "  AND pick_zone_type = 'cooler'"
                    ),
                    {"bid": batch_id},
                )
                db.session.commit()
                # Box-first rebuild so re-presented skipped items keep their box
                # metadata (box_id / box_no / is_last_item_in_box). The flat
                # rebuild_items_from_queue path strips box context, which breaks
                # the box-first picking UI and prevents the box-close transition
                # from firing when the recovered item is the box's last one.
                recycled = _serialize_cooler_box_items(
                    build_cooler_box_picking_items(batch_session)
                )
                session[fixed_batch_key] = recycled
                batch_session.current_item_index = 0
                db.session.commit()
                flash(
                    f'{len(skipped_rows)} skipped item(s) have been brought back — '
                    'please pick or report unavailable for each.',
                    'warning',
                )
                return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
            skipped_items = []
        elif getattr(batch_session, 'picking_mode', None) == 'Cooler':
            skipped_items = InvoiceItem.query.filter(
                InvoiceItem.locked_by_batch_id == batch_id,
                InvoiceItem.pick_status == 'skipped_pending',
            ).all()
        else:
            # Scope by batch lock — mirrors the Cooler branch directly above and
            # FIX-002 principle: the lock, not zone filters, defines membership.
            # This prevents a different batch's skipped item in the same zone/invoice
            # from being presented to this picker.
            skipped_items = InvoiceItem.query.filter(
                InvoiceItem.locked_by_batch_id == batch_id,
                InvoiceItem.pick_status == 'skipped_pending',
            ).all()
        
        if skipped_items:
            # There are skipped items - add them back to the batch for resolution
            current_app.logger.info(f"Skip and collect: Found {len(skipped_items)} skipped items to resolve")

            # For DB-backed non-cooler batches the queue is the work-list.
            # Reset 'skipped_pending' rows to 'pending' so the next
            # rebuild_items_from_queue call surfaces them again — no cookie update needed.
            from services.batch_picking import is_db_backed_batch as _is_db_backed_recycle
            if (
                _is_db_backed_recycle(batch_id)
                and getattr(batch_session, 'session_type', None) != 'cooler_route'
            ):
                db.session.execute(
                    text(
                        "UPDATE batch_pick_queue "
                        "SET status = 'pending', updated_at = NOW() "
                        "WHERE batch_session_id = :bid "
                        "  AND status = 'skipped_pending'"
                    ),
                    {"bid": batch_id},
                )
                db.session.commit()
                flash(
                    f'Please resolve {len(skipped_items)} skipped item(s) before completing the batch.',
                    'warning',
                )
                return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))

            # Legacy path: rebuild the cookie list from InvoiceItem skipped rows.
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
                
                # Record confirmation_time for all tracking records in this batch
                # This captures time from last item confirm → batch completion
                from timezone_utils import get_utc_now
                now = get_utc_now()
                
                # Get all invoice numbers in this batch
                batch_invoice_nos = [bi.invoice_no for bi in batch_session.invoices]
                
                # For each invoice, find the last tracking record and set confirmation_time
                for invoice_no in batch_invoice_nos:
                    last_tracking = ItemTimeTracking.query.filter_by(
                        invoice_no=invoice_no,
                        picker_username=current_user.username
                    ).filter(
                        ItemTimeTracking.item_completed.isnot(None)
                    ).order_by(ItemTimeTracking.item_completed.desc()).first()
                    
                    if last_tracking and last_tracking.item_completed:
                        # Divide confirmation time by number of invoices in batch
                        confirmation_seconds = max((now - last_tracking.item_completed).total_seconds(), 0)
                        confirmation_per_invoice = confirmation_seconds / len(batch_invoice_nos) if batch_invoice_nos else confirmation_seconds
                        last_tracking.confirmation_time = confirmation_per_invoice
                
                # Update order statuses for all invoices in this batch
                from batch_aware_order_status import update_all_orders_after_batch_completion
                updated_orders = update_all_orders_after_batch_completion(batch_id)
                
                db.session.commit()
                current_app.logger.info(f"Batch {batch_id} completed: All items picked successfully")

                # Recalculate warehouse readiness after batch completion
                if batch_session.route_id:
                    try:
                        from services.route_warehouse_readiness import recalculate_route_warehouse_status
                        recalculate_route_warehouse_status(batch_session.route_id)
                    except Exception as _wre:
                        current_app.logger.warning("warehouse readiness recalc failed after batch %s: %s", batch_id, _wre)
                if getattr(batch_session, 'session_type', None) == 'cooler_route' and batch_session.route_id:
                    from models import Shipment
                    route = Shipment.query.get(batch_session.route_id)
                    if route and route.delivery_date:
                        pack_mode = getattr(batch_session, 'cooler_pack_mode', 'location_order')
                        msg = "✅ Cooler picking complete — all boxes have been picked and sealed."
                        flash(msg, "success")
                        return redirect(url_for(
                            "cooler.route_picking",
                            route_id=batch_session.route_id,
                            delivery_date=route.delivery_date.strftime("%Y-%m-%d"),
                        ))
                
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

    # ── Cooler box-first: extract current-box context from item dict ──────────
    # (The old location-first banner logic has been replaced by the box-first
    # card + start-modal rendered in the template.)
    cooler_current_box = None
    if getattr(batch_session, 'session_type', None) == 'cooler_route':
        cooler_current_box = {
            'box_id':             current_item.get('box_id'),
            'box_no':             current_item.get('box_no'),
            'box_type_name':      current_item.get('box_type_name', 'Box'),
            'box_stop_display':   current_item.get('box_stop_display', ''),
            'item_index_in_box':  current_item.get('item_index_in_box', 1),
            'total_items_in_box': current_item.get('total_items_in_box', 1),
            'is_first_item_in_box': current_item.get('is_first_item_in_box', False),
            'is_last_item_in_box':  current_item.get('is_last_item_in_box', False),
            'next_box_no':          current_item.get('next_box_no'),
            'next_box_type_name':   current_item.get('next_box_type_name'),
        }
    
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
    
    # VPACK: instruct in pieces like normal picking ("Pick 12 Pieces")
    from services.batch_picking import apply_vpack_display
    try:
        apply_vpack_display(current_item)
    except Exception as _vpe:
        current_app.logger.warning(f"VPACK display calc failed for batch {batch_id}: {_vpe}")

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
                          tracking_ids=tracking_ids,
                          cooler_current_box=cooler_current_box)

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
    prev = ItemTimeTracking.query.filter(
        ItemTimeTracking.picker_username == current_user.username,
        ItemTimeTracking.item_completed.isnot(None),
        ItemTimeTracking.id.notin_(tracking_ids)
    ).order_by(ItemTimeTracking.item_completed.desc()).first()

    if prev:
        # Not the first item - use previous item's completion time
        walking_time = max((now - prev.item_completed).total_seconds(), 0)
    else:
        # First item in batch - use batch session created_at as baseline
        batch_session = BatchPickingSession.query.get(batch_id)
        if batch_session and batch_session.created_at:
            walking_time = max((now - batch_session.created_at).total_seconds(), 0)
        else:
            walking_time = 0.0
    
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

    # Phase 4: claim-required gate on the final pick-commit step too
    from services.batch_picking import is_claim_required as _is_claim_required
    if _is_claim_required() and not getattr(batch_session, 'claimed_by', None):
        flash('This batch requires explicit claim before picking.', 'warning')
        return redirect(url_for('batch.picker_batch_list'))

    # FIX-007: for DB-backed non-cooler batches the queue is the work-list.
    # The display route always shows the queue head, so the confirm action
    # must resolve the same item from the queue head — never the cookie/index.
    from services.batch_picking import (
        is_db_backed_batch as _is_db_backed_confirm,
        rebuild_items_from_queue as _rebuild_from_queue_confirm,
    )
    _queue_primary = (
        _is_db_backed_confirm(batch_id)
        and getattr(batch_session, 'session_type', None) != 'cooler_route'
    )
    if _queue_primary:
        items = _rebuild_from_queue_confirm(batch_id)
        if not items:
            return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
        current_item = items[0]  # queue head == what the screen showed
        # Belt-and-braces: the form posts the identity of the item it displayed.
        # If the queue head changed since the page rendered (another device,
        # refresh, recycle), refuse to write and re-show the current head.
        _posted_code = (request.form.get('item_code') or '').strip()
        _posted_inv = (request.form.get('current_invoice') or '').strip()
        _stale = False
        if not _posted_code or _posted_code != current_item['item_code']:
            _stale = True
        if (not _stale
                and batch_session.picking_mode == 'Sequential'
                and _posted_inv != str(current_item.get('current_invoice') or '')):
            _stale = True
        if _stale:
            flash('The picking list was refreshed — please re-check the current item.', 'warning')
            return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
        current_app.logger.info(
            f"Complete confirm (queue-primary): head item {current_item['item_code']} "
            f"of {len(items)} pending"
        )
    else:
        # Legacy + cooler path: fixed cookie list with server-side index
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

    # ── Cooler box-first: read box-transition flags ───────────────────────────
    _cooler_is_last_in_box = (
        getattr(batch_session, 'session_type', None) == 'cooler_route'
        and (
            request.form.get('is_last_item_in_box') == 'true'
            or current_item.get('is_last_item_in_box', False)
        )
    )
    _cooler_box_id = request.form.get('cooler_box_id') or (
        str(current_item['box_id']) if current_item.get('box_id') else None
    )

    # Get the picked quantity from the form
    try:
        picked_qty = int(request.form.get('picked_qty', 0))
    except ValueError:
        picked_qty = 0
    
    # Get exception reason from form (for exception reports)
    exception_reason = (request.form.get("reason", "") or "").strip()
    is_exception = bool(exception_reason) or picked_qty <= 0
    if picked_qty <= 0 and not is_exception:
        flash('Please enter a valid picked quantity.', 'danger')
        return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
    if is_exception and not exception_reason:
        exception_reason = 'Exception reported by picker'
    
    # Get the source items for this batch item
    source_items = current_item['source_items']
    total_required = current_item['total_qty']

    # Per-source allocation map. Populated in Consolidated mode; used later
    # by record_pick_to_queue so the queue shows real picked quantities.
    allocated_map = {}

    try:
        # Process the picked items
        if batch_session.picking_mode == 'Sequential':
            # Sequential mode - one invoice at a time
            invoice_no = source_items[0]['invoice_no']
            item_code = source_items[0]['item_code']
            # VPACK: required is in PIECES (same authority as the pick
            # screen's "Pick N Pieces" instruction) so quantity checks
            # compare like with like.
            from services.batch_picking import pieces_required_for_source
            required_qty = pieces_required_for_source(
                invoice_no, item_code,
                source_items[0].get('expected_pick_pieces', source_items[0]['qty']),
            )
            if required_qty != source_items[0]['qty']:
                current_app.logger.info(
                    f"VPACK pieces drift: {invoice_no}/{item_code} queue qty "
                    f"{source_items[0]['qty']} vs required pieces {required_qty}"
                )
            
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
                invoice_item.pick_status = 'exception' if is_exception else 'picked'

                # For cooler-route batches: mirror the pick into batch_pick_queue
                # so the cooler screen can show "Picked" status and enable Assign.
                # record_pick_to_queue() handles standard DB-backed batches, but
                # cooler queue rows need an explicit update here as a safety net.
                if batch_session.session_type == 'cooler_route':
                    db.session.execute(
                        text(
                            "UPDATE batch_pick_queue "
                            "SET status = 'picked', qty_picked = qty_required, "
                            "    picked_by = :picker, picked_at = :now "
                            "WHERE invoice_no = :inv "
                            "  AND item_code = :ic "
                            "  AND batch_session_id = :sid "
                            "  AND pick_zone_type = 'cooler'"
                        ),
                        {
                            "inv": invoice_no,
                            "ic": item_code,
                            "sid": batch_session.id,
                            "picker": current_user.username,
                            "now": datetime.utcnow(),
                        },
                    )

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
            
            # Check if all items for this invoice in the batch are picked.
            # Deferred-route batches use a 'DEFERRED' sentinel zone, so the
            # zone filter would always yield 0 and mark the invoice complete
            # prematurely — count by batch lock instead.
            if getattr(batch_session, 'session_type', None) == 'deferred_route':
                unpicked_count = InvoiceItem.query.filter(
                    InvoiceItem.invoice_no == invoice_no,
                    InvoiceItem.locked_by_batch_id == batch_session.id,
                    InvoiceItem.is_picked == False,
                    InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending', 'sent_to_batch'])
                ).count()
            else:
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
            # Allocate picked_qty to invoices in invoice-number order (earliest first).
            # Every source — including zero-allocation ones — gets an InvoiceItem update,
            # a BatchPickedItem record, and (when short/excepted) a PickingException.
            # No early break: customers cut to zero must still advance out of
            # awaiting_batch_items and appear in reports.
            sorted_sources = sorted(source_items, key=lambda x: x['invoice_no'])

            # VPACK: picker entered PIECES (screen said "Pick N Pieces"),
            # so allocate/compare against per-source pieces, like normal
            # picking does.
            from services.batch_picking import pieces_required_for_source
            source_pieces = {
                (s['invoice_no'], s['item_code']): pieces_required_for_source(
                    s['invoice_no'], s['item_code'], s['qty'])
                for s in sorted_sources
            }

            # First pass: compute allocations
            remaining_qty = picked_qty
            allocated_map = {}
            for source in sorted_sources:
                alloc = min(remaining_qty, source_pieces[(source['invoice_no'], source['item_code'])])
                allocated_map[(source['invoice_no'], source['item_code'])] = alloc
                remaining_qty -= alloc

            # Second pass: persist every source
            for source in sorted_sources:
                invoice_no = source['invoice_no']
                item_code = source['item_code']
                required_qty = source_pieces[(invoice_no, item_code)]
                allocated_qty = allocated_map[(invoice_no, item_code)]

                invoice_item = InvoiceItem.query.filter_by(
                    invoice_no=invoice_no,
                    item_code=item_code
                ).first()

                if not invoice_item:
                    continue

                # Exception for any discrepancy (short pick or zero allocation)
                if allocated_qty != required_qty or is_exception:
                    db.session.add(PickingException(
                        invoice_no=invoice_no,
                        item_code=item_code,
                        expected_qty=required_qty,
                        picked_qty=allocated_qty,
                        picker_username=current_user.username,
                        reason=(exception_reason if is_exception
                                else f"Batch picking (consolidated): {allocated_qty} allocated, {required_qty} required"),
                    ))

                invoice_item.picked_qty = allocated_qty
                invoice_item.is_picked = True
                invoice_item.pick_status = (
                    'exception'
                    if (is_exception or allocated_qty != required_qty)
                    else 'picked'
                )

                # BatchPickedItem upsert (runs for ALL sources, including qty 0)
                existing_batch_picked = BatchPickedItem.query.filter_by(
                    batch_session_id=batch_id,
                    invoice_no=invoice_no,
                    item_code=item_code
                ).first()
                if existing_batch_picked:
                    existing_batch_picked.picked_qty = allocated_qty
                else:
                    db.session.add(BatchPickedItem(
                        batch_session_id=batch_id,
                        invoice_no=invoice_no,
                        item_code=item_code,
                        picked_qty=allocated_qty,
                    ))
        
        # Phase 4: mirror this line's outcome into ``batch_pick_queue`` (and,
        # for cooler routes, ``cooler_box_items``) so refresh / restart resumes
        # from the correct state.
        _is_cooler_route = getattr(batch_session, 'session_type', None) == 'cooler_route'
        if _is_cooler_route and is_exception:
            # Reported unavailable / zero-pick on a cooler route: this line was
            # NOT picked, so it must NOT be treated as a picked-but-unboxed item.
            # Mark the queue row + any pre-planned box item 'exception' (mirroring
            # the canonical cooler queue_exception path) so it is excluded from the
            # "picked but unboxed" count, the Generate Box Plan list, and the box
            # manifest. Without this it would still get boxed and shipped despite
            # picked_qty = 0.
            from timezone_utils import get_utc_now as _utcnow_exc
            _exc_now = _utcnow_exc()
            for _src in source_items:
                db.session.execute(
                    text(
                        "UPDATE batch_pick_queue "
                        "SET status = 'exception', updated_at = :now "
                        "WHERE batch_session_id = :bid "
                        "  AND invoice_no = :inv "
                        "  AND item_code = :ic "
                        "  AND status IN ('pending', 'skipped_pending')"
                    ),
                    {"now": _exc_now, "bid": batch_id,
                     "inv": _src['invoice_no'], "ic": _src['item_code']},
                )
                db.session.execute(
                    text(
                        "UPDATE cooler_box_items cbi "
                        "SET status = 'exception', updated_at = :now "
                        "WHERE cbi.status = 'planned' "
                        "  AND cbi.queue_item_id = ("
                        "    SELECT bpq.id FROM batch_pick_queue bpq "
                        "    WHERE bpq.invoice_no       = :inv "
                        "      AND bpq.item_code        = :ic "
                        "      AND bpq.batch_session_id = :bid "
                        "    LIMIT 1"
                        "  )"
                    ),
                    {"now": _exc_now, "inv": _src['invoice_no'],
                     "ic": _src['item_code'], "bid": batch_id},
                )
        else:
            from services.batch_picking import record_pick_to_queue as _record_pick_to_queue
            for _src in source_items:
                _claimed = _record_pick_to_queue(
                    batch_id=batch_id,
                    invoice_no=_src['invoice_no'],
                    item_code=_src['item_code'],
                    picker=current_user.username,
                    # Consolidated mode: record the actually allocated qty
                    # (short picks show real numbers in the quick-view modal);
                    # Sequential falls back to the required/picked qty.
                    qty_picked=allocated_map.get(
                        (_src['invoice_no'], _src['item_code']),
                        picked_qty,
                    ),
                )
                # CAS guard: on the queue-primary path the UPDATE above is
                # the atomic claim (row lock serialises concurrent posts;
                # the loser matches 0 rows because status is already
                # 'picked'). If the claim failed, roll back EVERY side
                # effect of this request — otherwise a double-submit would
                # double-count picked quantities and duplicate logs.
                if _queue_primary and _claimed == 0:
                    db.session.rollback()
                    current_app.logger.warning(
                        f"Confirm lost the queue claim for "
                        f"{_src['invoice_no']}/{_src['item_code']} in batch "
                        f"{batch_id} — likely a double submit; no changes saved."
                    )
                    flash('This item was already recorded — showing the next item.', 'info')
                    return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))

            # ── Mirror into cooler_box_items ──────────────────────────────────
            # For cooler-route batches the close-box guard checks
            # cooler_box_items.status = 'planned' OR picked_qty = 0.
            # Without this mirror, items stay 'planned' forever even after
            # being physically picked, blocking box closure.
            if _is_cooler_route:
                from timezone_utils import get_utc_now as _utcnow_cbi
                _cbi_now = _utcnow_cbi()
                for _src in source_items:
                    db.session.execute(
                        text(
                            "UPDATE cooler_box_items cbi "
                            "SET status     = 'picked', "
                            "    picked_qty = cbi.expected_qty, "
                            "    picked_by  = :picker, "
                            "    picked_at  = :now, "
                            "    updated_at = :now "
                            "WHERE cbi.status = 'planned' "
                            "  AND cbi.queue_item_id = ("
                            "    SELECT bpq.id FROM batch_pick_queue bpq "
                            "    WHERE bpq.invoice_no       = :inv "
                            "      AND bpq.item_code        = :ic "
                            "      AND bpq.batch_session_id = :bid "
                            "    LIMIT 1"
                            "  )"
                        ),
                        {
                            "picker": current_user.username,
                            "now":    _cbi_now,
                            "inv":    _src["invoice_no"],
                            "ic":     _src["item_code"],
                            "bid":    batch_id,
                        },
                    )

        # ── Cooler box-first: close box when its last item is confirmed ──────────
        if _cooler_is_last_in_box and _cooler_box_id:
            try:
                _still_planned = db.session.execute(
                    text(
                        "SELECT COUNT(*) FROM cooler_box_items "
                        "WHERE cooler_box_id = :bid AND status = 'planned'"
                    ),
                    {"bid": int(_cooler_box_id)},
                ).scalar() or 0
                if _still_planned == 0:
                    from timezone_utils import get_utc_now as _utcnow_cls
                    db.session.execute(
                        text(
                            "UPDATE cooler_boxes "
                            "SET status = 'closed', "
                            "    closed_by = :who, closed_at = :now "
                            "WHERE id = :bid AND status != 'cancelled'"
                        ),
                        {
                            "bid": int(_cooler_box_id),
                            "who": current_user.username,
                            "now": _utcnow_cls(),
                        },
                    )
                    session['cooler_box_complete_pending'] = {
                        'box_no':            current_item.get('box_no'),
                        'box_type_name':     current_item.get('box_type_name', 'Box'),
                        'box_stop_display':  current_item.get('box_stop_display', ''),
                        'next_box_no':       current_item.get('next_box_no'),
                        'next_box_type_name': current_item.get('next_box_type_name'),
                        'is_final_box':      current_item.get('next_box_no') is None,
                    }
                    current_app.logger.info(
                        "cooler box %s closed by %s (batch %s)",
                        _cooler_box_id, current_user.username, batch_id,
                    )

                    # ── Advance invoice status after box close ─────────────────
                    # For cooler routes, closing the box IS the packing step.
                    # Any invoice whose cooler_box_items are ALL in closed boxes
                    # should be promoted from awaiting_packing → ready_for_dispatch.
                    try:
                        _box_invoice_nos = db.session.execute(
                            text(
                                "SELECT DISTINCT cbi.invoice_no "
                                "FROM cooler_box_items cbi "
                                "WHERE cbi.cooler_box_id = :bid"
                            ),
                            {"bid": int(_cooler_box_id)},
                        ).fetchall()
                        for (_inv_no,) in _box_invoice_nos:
                            # Count any cooler_box_items for this invoice that are
                            # NOT yet in a closed box
                            _open_cbi = db.session.execute(
                                text(
                                    "SELECT COUNT(*) FROM cooler_box_items cbi "
                                    "JOIN cooler_boxes cb ON cb.id = cbi.cooler_box_id "
                                    "WHERE cbi.invoice_no = :inv "
                                    "  AND cb.status != 'closed'"
                                    "  AND cb.status != 'cancelled'"
                                ),
                                {"inv": _inv_no},
                            ).scalar() or 0
                            if _open_cbi == 0:
                                _adv_inv = Invoice.query.filter_by(
                                    invoice_no=_inv_no
                                ).first()
                                if _adv_inv and _adv_inv.status == 'awaiting_packing':
                                    try:
                                        from services.order_readiness import is_order_ready as _ior
                                        _ready = _ior(_inv_no)
                                    except Exception:
                                        _ready = True
                                    if _ready:
                                        _adv_inv.status = 'ready_for_dispatch'
                                        current_app.logger.info(
                                            "invoice %s advanced to ready_for_dispatch"
                                            " (all cooler boxes closed)", _inv_no
                                        )
                    except Exception as _adv_err:
                        current_app.logger.warning(
                            "invoice advance after box close failed: %s", _adv_err
                        )
            except Exception as _close_err:
                current_app.logger.warning(
                    "cooler box close failed for box %s: %s",
                    _cooler_box_id, _close_err,
                )

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
                    if not _queue_primary:
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
                if not _queue_primary:
                    batch_session.current_item_index += 1
        else:
            # Consolidated mode - continue normally
            if not _queue_primary:
                batch_session.current_item_index += 1
        
        # Check if entire batch is complete.
        # Queue-primary batches never use the index — the queue row leaving
        # 'pending' advances the list, and batch_picking_item's empty-queue
        # check handles the skip recycle and batch completion.
        if not _queue_primary and batch_session.current_item_index >= len(items):
            # ── Cooler box-first: never auto-complete while skipped (collect-later)
            # items are still outstanding. The final pick action would otherwise
            # mark the batch Completed and redirect away, bypassing the end-of-run
            # recycle — leaving the skipped_pending item unresolved (shown as
            # "Collect later" and blocking box closure). Route back through
            # batch_picking_item so the recycle re-presents them for resolution.
            if getattr(batch_session, 'session_type', None) == 'cooler_route':
                _outstanding_skipped = db.session.execute(
                    text(
                        "SELECT COUNT(*) FROM batch_pick_queue "
                        "WHERE batch_session_id = :bid "
                        "  AND status = 'skipped_pending' "
                        "  AND pick_zone_type = 'cooler'"
                    ),
                    {"bid": batch_id},
                ).scalar() or 0
                if _outstanding_skipped > 0:
                    db.session.commit()
                    return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
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
                    InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending', 'sent_to_batch'])
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

                    # Refresh invoice status for picked invoice(s) so orders
                    # with all their items done don't stay at awaiting_batch_items
                    from batch_aware_order_status import update_order_status_batch_aware as _uosa_mid
                    for _inv_mid in {s['invoice_no'] for s in source_items}:
                        try:
                            _uosa_mid(_inv_mid)
                        except Exception as _e_mid:
                            current_app.logger.warning(
                                "pick_confirm mid-batch: status refresh for %s failed: %s",
                                _inv_mid, _e_mid,
                            )

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
                if getattr(batch_session, 'session_type', None) == 'deferred_route':
                    # Deferred batches use a 'DEFERRED' sentinel zone; scope
                    # by batch lock instead of warehouse zone.
                    filter_conditions = [
                        InvoiceItem.invoice_no == invoice_no,
                        InvoiceItem.locked_by_batch_id == batch_id,
                        InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending', 'sent_to_batch', 'picked'])
                    ]
                else:
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

            # Batch fully complete — promote all invoice statuses now
            from batch_aware_order_status import update_all_orders_after_batch_completion as _uoabc
            try:
                _uoabc(batch_id)
            except Exception as _e_cmp:
                current_app.logger.warning(
                    "pick_confirm batch-complete: status refresh failed for batch %s: %s",
                    batch_id, _e_cmp,
                )

            return redirect(url_for('batch.picker_batch_list'))
        
        # Save changes to database
        db.session.commit()

        # Refresh invoice status after each item pick so orders whose last
        # batch item was just picked advance out of 'awaiting_batch_items'
        # immediately rather than waiting for the whole batch to finish.
        from batch_aware_order_status import update_order_status_batch_aware as _uosa_item
        for _inv_item in {s['invoice_no'] for s in source_items}:
            try:
                _uosa_item(_inv_item)
            except Exception as _e_item:
                current_app.logger.warning(
                    "pick_confirm per-item: status refresh for %s failed: %s",
                    _inv_item, _e_item,
                )

        # Redirect to the next item (batch_picking_item will intercept the
        # cooler_box_complete_pending session flag and show the transition screen)
        return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
        
    except Exception as e:
        # Roll back the transaction
        db.session.rollback()
        flash(f'Error processing this item: {str(e)}', 'danger')
        current_app.logger.error(f"Error processing batch item: {str(e)}")
        return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))


@batch_bp.route('/picker/batch/<int:batch_id>/cooler/box-complete', methods=['GET'])
@login_required
def cooler_box_complete(batch_id):
    """Box-transition screen shown between boxes in cooler box-first picking.

    The session key ``cooler_box_complete_pending`` is set by
    ``complete_batch_confirm`` when the last item of a box is confirmed.
    ``batch_picking_item`` pops this key and renders this template directly.
    This GET endpoint is a fallback in case the user navigates here directly.
    """
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    if current_user.role not in ['admin', 'warehouse_manager'] and \
            batch_session.assigned_to != current_user.username:
        flash('You are not assigned to this batch.', 'danger')
        return redirect(url_for('batch.picker_batch_list'))
    box_info = session.pop('cooler_box_complete_pending', {})
    return render_template('cooler_box_complete.html',
                           batch_session=batch_session,
                           box_info=box_info)


@batch_bp.route('/batch/<int:batch_id>/force_complete')
@login_required
@require_permission('picking.manage_batches')
def force_complete_batch(batch_id):
    """Force complete a batch even if items remain unpicked"""
    try:
        batch_session = db.session.get(BatchPickingSession, batch_id)
        if not batch_session:
            flash('Batch session not found', 'danger')
            return redirect(url_for('batch.picker_batch_list'))
        
        # Check if user has permission to access this batch
        if (current_user.role not in ['admin', 'warehouse_manager']
                and batch_session.assigned_to != current_user.username):
            flash('You are not assigned to this batch', 'danger')
            return redirect(url_for('batch.picker_batch_list'))
        
        # Count all items that should be in this batch based on selected zones and corridors
        total_items_in_batch = 0
        picked_items_in_batch = 0
        
        for invoice_no in [bi.invoice_no for bi in batch_session.invoices]:
            # Build filter conditions for items that are actually in scope for this batch
            if getattr(batch_session, 'session_type', None) == 'deferred_route':
                # Deferred batches use a 'DEFERRED' sentinel zone; scope by
                # batch lock instead of warehouse zone.
                filter_conditions = [
                    InvoiceItem.invoice_no == invoice_no,
                    InvoiceItem.locked_by_batch_id == batch_id,
                    InvoiceItem.pick_status.in_(['not_picked', 'reset', 'skipped_pending', 'sent_to_batch', 'picked'])
                ]
            else:
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


@batch_bp.route('/batch/delete/<int:batch_id>', methods=['POST'])
@login_required
@require_permission('picking.delete_empty_batch')
def delete_batch(batch_id):
    """Phase 4: hard-delete is admin-only and gated behind
    ``picking.delete_empty_batch``. Empty batches in 'Created' status
    only — every other path goes through ``cancel_batch``. Audit logs
    and picking exceptions are PRESERVED."""
    if current_user.role != 'admin':
        flash('Hard delete is admin-only. Use Cancel instead.', 'danger')
        return redirect(url_for('batch.picker_batch_list'))

    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    label = batch_session.batch_number or f"BATCH-{batch_session.id}"

    # Truly-empty contract: no picked rows, no queue rows, no session
    # invoice links, no active locks. Anything else routes through cancel.
    picked_items_count = BatchPickedItem.query.filter_by(batch_session_id=batch_id).count()
    sess_inv_count = BatchSessionInvoice.query.filter_by(batch_session_id=batch_id).count()
    locks_count = InvoiceItem.query.filter_by(locked_by_batch_id=batch_id).count()
    queue_count = 0
    try:
        queue_count = db.session.execute(
            text("SELECT COUNT(*) FROM batch_pick_queue WHERE batch_session_id = :bid"),
            {"bid": batch_id},
        ).scalar() or 0
    except Exception:
        queue_count = 0

    _is_terminal = batch_session.status in ('Cancelled', 'Completed')
    _has_active_content = picked_items_count or locks_count or (sess_inv_count and not _is_terminal)

    if not _is_terminal and (picked_items_count or sess_inv_count or locks_count or queue_count or batch_session.status not in ['Created']):
        # Batch is live and non-empty — route through cancel so locks are
        # properly released and audit columns are stamped.
        flash(f'Batch "{label}" is not truly empty (picks={picked_items_count}, '
              f'invoices={sess_inv_count}, locks={locks_count}, queue={queue_count}, '
              f'status={batch_session.status}). Routing through Cancel to preserve audit.', 'info')
        try:
            from services.batch_picking import cancel_batch as _cancel_batch
            _cancel_batch(batch_id, current_user.username,
                          reason='Hard-delete request on non-empty batch — routed to cancel')
            flash(f'Batch "{label}" cancelled.', 'success')
        except Exception as e:
            flash(f'Cancel failed: {e}', 'danger')
        return redirect(url_for('batch.picker_batch_list'))

    if _is_terminal and _has_active_content:
        # Already terminal but somehow still has picked items or active locks
        # (shouldn't happen in normal flow, but guard it).
        flash(f'Batch "{label}" is already {batch_session.status} but still has '
              f'active content (picks={picked_items_count}, locks={locks_count}). '
              'Please contact an administrator.', 'warning')
        return redirect(url_for('batch.picker_batch_list'))

    batch_name = batch_session.batch_number or f"BATCH-{batch_session.id}"

    try:
        # Audit-preserving cleanup: release locks, drop session-invoice
        # links + queue rows, then the empty session itself. Activity
        # logs and PickingExceptions are NOT touched.
        from batch_locking_utils import unlock_items_for_batch
        unlocked = unlock_items_for_batch(batch_id, preserve_picked=False)

        # Cooler-specific teardown: cancel open boxes and recompute invoice
        # statuses so they resolve back to not_started / picking /
        # ready_for_dispatch rather than staying stuck.
        if getattr(batch_session, 'session_type', None) == 'cooler_route':
            db.session.execute(
                text("""
                    UPDATE cooler_boxes
                    SET status = 'cancelled'
                    WHERE cooler_session_id = :sid
                      AND status NOT IN ('closed', 'loaded', 'delivered')
                """),
                {"sid": batch_id},
            )
            _affected = db.session.execute(
                text("""
                    SELECT DISTINCT invoice_no
                    FROM batch_session_invoices
                    WHERE batch_session_id = :sid
                """),
                {"sid": batch_id},
            ).fetchall()
            from batch_aware_order_status import update_order_status_batch_aware
            for _row in _affected:
                try:
                    update_order_status_batch_aware(_row[0])
                except Exception as _e:
                    current_app.logger.warning("delete_batch: status recompute failed for %s: %s", _row[0], _e)

        BatchSessionInvoice.query.filter_by(batch_session_id=batch_id).delete()
        try:
            db.session.execute(
                text("DELETE FROM batch_pick_queue WHERE batch_session_id = :bid"),
                {"bid": batch_id},
            )
        except Exception:
            pass
        db.session.delete(batch_session)
        log = ActivityLog(
            picker_username=current_user.username,
            activity_type='BATCH_DELETED',
            details=f'Hard delete of empty batch {batch_name} (ID: {batch_id}); released {unlocked} lock(s). Audit logs preserved.',
        )
        db.session.add(log)
        db.session.commit()
        flash(f'Batch "{batch_name}" deleted. {unlocked} lock(s) released; audit logs preserved.', 'success')
        current_app.logger.info(f"Admin {current_user.username} deleted batch {batch_name} (ID: {batch_id})")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting batch {batch_id}: {e}")
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

    # FIX-008: routing label/grouping comes from the Routes module
    from services.route_links import route_links_for_invoices, stop_label, stop_sort_key
    route_links = route_links_for_invoices(invoice_nos)
    
    items = []
    picked_count = 0
    skipped_count = 0
    total_weight = 0
    
    # Process all filtered items consistently regardless of mode
    for item in filtered_items:
        # Get invoice details for routing and customer name from preloaded data
        invoice = invoices_dict.get(item.invoice_no)
        # FIX-008: prefer the active route link label ("ROUTE · STOP n");
        # fall back to legacy Invoice.routing for old data.
        routing = stop_label(route_links.get(item.invoice_no)) or (invoice.routing if invoice else None)
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
    
    # FIX-008: route-linked groups first, ordered (route_name, stop_seq)
    # ascending; legacy routing-number groups after them, descending.
    def get_routing_sort_key(group):
        entry = route_links.get(group.get('invoice_no'))
        if entry:
            rname, seq = stop_sort_key(entry)
            return (0, rname, seq)
        routing = group.get('routing')
        try:
            return (1, '', -float(routing))
        except (ValueError, TypeError):
            return (1, '', float('inf'))

    grouped_items.sort(key=get_routing_sort_key)
    
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
    
    # FIX-007: for DB-backed non-cooler batches the queue is the work-list.
    # Resolve the current item from the queue head — same list the screen shows.
    from services.batch_picking import (
        is_db_backed_batch as _is_db_backed_skiphead,
        rebuild_items_from_queue as _rebuild_from_queue_skip,
    )
    _queue_primary = (
        _is_db_backed_skiphead(batch_id)
        and getattr(batch_session, 'session_type', None) != 'cooler_route'
    )
    if _queue_primary:
        items = _rebuild_from_queue_skip(batch_id)
        if not items:
            return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
        current_item = items[0]  # queue head == what the screen showed
        # Stale-form guard: only write if the posted identity matches the head.
        _posted_code = (request.form.get('item_code') or '').strip()
        _posted_inv = (request.form.get('current_invoice') or '').strip()
        _stale = False
        if not _posted_code or _posted_code != current_item['item_code']:
            _stale = True
        if (not _stale
                and batch_session.picking_mode == 'Sequential'
                and _posted_inv != str(current_item.get('current_invoice') or '')):
            _stale = True
        if _stale:
            flash('The picking list was refreshed — please re-check the current item.', 'warning')
            return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
        current_app.logger.warning(
            f"🔄 SKIP (queue-primary): head item {current_item['item_code']} of {len(items)} pending"
        )
    else:
        # Legacy + cooler path: fixed cookie list with server-side index
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
    
    is_cooler_batch = getattr(batch_session, 'session_type', None) == 'cooler_route'
    from services.batch_picking import is_db_backed_batch as _is_db_backed_skip
    _is_queue_backed = _is_db_backed_skip(batch_id)

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

            # For DB-backed batches (including cooler-route) the queue is the
            # source of truth. Mark the queue row skipped_pending so that
            # rebuild_items_from_queue excludes it from the active pass and
            # the end-of-run recycle brings it back until the picker resolves.
            if is_cooler_batch or _is_queue_backed:
                from sqlalchemy import text as _text
                _skip_res = db.session.execute(
                    _text(
                        "UPDATE batch_pick_queue "
                        "SET status = 'skipped_pending', updated_at = NOW() "
                        "WHERE batch_session_id = :bid "
                        "  AND invoice_no = :inv "
                        "  AND item_code = :ic "
                        "  AND status = 'pending'"
                    ),
                    {"bid": batch_id, "inv": invoice_no, "ic": item_code},
                )
                # CAS guard: this UPDATE is the atomic claim on the
                # queue-primary path. A double submit (or a pick that
                # landed first from another device) leaves 0 matching
                # rows — roll back so skip_count isn't double-incremented
                # and the pick isn't silently overwritten.
                if _queue_primary and (_skip_res.rowcount or 0) == 0:
                    db.session.rollback()
                    current_app.logger.warning(
                        f"🔄 SKIP lost the queue claim for {invoice_no}/{item_code} "
                        f"in batch {batch_id} — already skipped or picked; no changes saved."
                    )
                    flash('This item was already handled — showing the next item.', 'info')
                    return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
        
        # Move to the next item (legacy/cooler index path only — for
        # queue-primary batches the row leaving 'pending' advances the queue)
        if not _queue_primary:
            batch_session.current_item_index += 1
        
        # Save changes to database
        db.session.commit()
        
        # Flash success message
        flash(f'Item {current_item["item_code"]} skipped and will be picked later.', 'info')
        
        # Always redirect to batch_picking_item — that view handles both cases:
        # more items remaining → shows next item; end of list → re-queues
        # skipped items, resets index to 0, shows first skipped item.
        return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))
        
    except Exception as e:
        # Roll back the transaction
        db.session.rollback()
        flash(f'Error skipping this item: {str(e)}', 'danger')
        current_app.logger.error(f"Error skipping batch item: {str(e)}")
        return redirect(url_for('batch.batch_picking_item', batch_id=batch_id))

@batch_bp.route('/admin/batch/verify/<int:batch_id>')
@login_required
@require_permission('picking.manage_batches')
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
    
    # For cooler/route batches: order invoices by delivery stop sequence (ascending).
    # For standard batches: keep legacy routing-number-descending order.
    stop_seq_lookup = _build_stop_seq_lookup(batch_session)
    use_stop_seq = bool(stop_seq_lookup)

    def get_routing_key(bi):
        from services.route_links import stop_sort_key, UNROUTED_SORT_KEY
        if use_stop_seq:
            entry = stop_seq_lookup.get(bi.invoice_no)
            if entry:
                # Group pages per route, stop order within each route
                return stop_sort_key(entry)
            # Unrouted invoices go to the end
            return UNROUTED_SORT_KEY
        routing = bi.invoice.routing
        if routing is None or routing == '':
            return -1
        try:
            return float(routing)
        except (ValueError, TypeError):
            return -1
    
    sorted_batch_invoices = sorted(batch_invoices, key=get_routing_key, reverse=not use_stop_seq)
    
    # Build invoices data using the canonical order from actual picking
    invoices_data = []
    
    for bi in sorted_batch_invoices:
        invoice = Invoice.query.get(bi.invoice_no)
        if not invoice:
            continue
        
        # Get all items for this invoice that are in the batch
        invoice_items = [item for item in all_invoice_items if item.invoice_no == invoice.invoice_no]
        
        # Sort items using the shared sorting utility
        from sorting_utils import sort_items_for_picking
        invoice_items = sort_items_for_picking(invoice_items)
        
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
            'unpicked': unpicked,
            'routing_label': _routing_label_for_invoice(invoice, stop_seq_lookup),
        })
    
    # Build unified report contract from invoices_data
    invoices_out = []
    for inv_data in invoices_data:
        invoice = inv_data['invoice']
        picked = []
        problems = []
        total_lines = 0
        total_units = 0
        total_weight = 0.0
        picked_count = 0

        for d in inv_data['batch_picked']:
            itm = d['item']
            total_lines += 1
            total_units += itm.qty or 0
            total_weight += (itm.item_weight or 0) * (itm.qty or 0)
            picked_count += 1
            picked.append({
                'item_code': itm.item_code,
                'item_name': itm.item_name or '',
                'location': itm.location or '',
                'unit_type': itm.unit_type or '',
                'pack': itm.pack or '',
                'qty': itm.qty or 0,
                'picked_qty': d['picked_qty'],
                'source': 'batch',
            })

        for d in inv_data['manually_picked']:
            itm = d['item']
            total_lines += 1
            total_units += itm.qty or 0
            total_weight += (itm.item_weight or 0) * (itm.qty or 0)
            picked_count += 1
            picked.append({
                'item_code': itm.item_code,
                'item_name': itm.item_name or '',
                'location': itm.location or '',
                'unit_type': itm.unit_type or '',
                'pack': itm.pack or '',
                'qty': itm.qty or 0,
                'picked_qty': d['picked_qty'],
                'source': 'manual',
            })

        for itm in inv_data['unpicked']:
            total_lines += 1
            total_units += itm.qty or 0
            total_weight += (itm.item_weight or 0) * (itm.qty or 0)
            problems.append({
                'item_code': itm.item_code,
                'item_name': itm.item_name or '',
                'location': itm.location or '',
                'unit_type': itm.unit_type or '',
                'pack': itm.pack or '',
                'qty': itm.qty or 0,
                'picked_qty': itm.picked_qty or 0,
                'status': itm.pick_status or 'unpicked',
                'reason': itm.skip_reason or '',
            })

        completion_pct = (picked_count / total_lines * 100) if total_lines > 0 else 0
        _entry = stop_seq_lookup.get(invoice.invoice_no) or {}
        invoices_out.append({
            'invoice': invoice,
            'routing_label': inv_data['routing_label'],
            'stop_seq': _entry.get('seq'),
            'route_name': _entry.get('route_name') or '',
            'picked': picked,
            'problems': problems,
            'total_lines': total_lines,
            'total_units': total_units,
            'total_weight': total_weight,
            'completion_pct': completion_pct,
        })

    return render_template('batch_report.html',
                           batch=batch_session,
                           generated_at=get_local_time(),
                           picker_name=batch_session.assigned_to or 'Unassigned',
                           invoices=invoices_out)

@batch_bp.route('/admin/batch/print-report/<int:batch_id>')
@login_required
@require_permission('picking.manage_batches')
def batch_admin_print_report(batch_id):
    """Admin picking report for completed batches"""
    # Allow admin, warehouse_manager and picker users to access print reports
    if current_user.role not in ['admin', 'warehouse_manager', 'picker']:
        flash('Access denied. Insufficient privileges.', 'danger')
        return redirect(url_for('index'))
    
    batch_session = BatchPickingSession.query.get_or_404(batch_id)
    
    current_time = get_local_time()
    
    # Prepare invoice data for the report
    invoices = []
    
    # For cooler/route batches: order by delivery stop sequence (ascending).
    # For standard batches: keep legacy routing-number-descending order.
    stop_seq_lookup = _build_stop_seq_lookup(batch_session)
    use_stop_seq = bool(stop_seq_lookup)

    def get_routing_key(bi):
        from services.route_links import stop_sort_key, UNROUTED_SORT_KEY
        if use_stop_seq:
            entry = stop_seq_lookup.get(bi.invoice_no)
            if entry:
                # Group pages per route, stop order within each route
                return stop_sort_key(entry)
            return UNROUTED_SORT_KEY  # Unrouted invoices at the end
        routing = bi.invoice.routing
        if routing is None or routing == '':
            return -1  # Put empty routing at the end
        try:
            return float(routing)
        except (ValueError, TypeError):
            return -1  # Put invalid routing at the end
    
    sorted_batch_invoices = sorted(batch_session.invoices, 
                                 key=get_routing_key, 
                                 reverse=not use_stop_seq)
    

    
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
                        exception_data['pick_status'] = 'skipped' if item.pick_status == 'skipped_pending' else 'exception'
                        exception_data['skip_reason'] = item.skip_reason or ''
                        exception_items.append(exception_data)
                elif original_qty > 0:
                    # No allocation but was required - full exception
                    exception_data = item_data.copy()
                    exception_data['shortage_qty'] = original_qty
                    exception_data['reason'] = f"Not allocated: 0 received, {original_qty} required"
                    exception_data['pick_status'] = 'skipped' if item.pick_status == 'skipped_pending' else 'exception'
                    exception_data['skip_reason'] = item.skip_reason or ''
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
        
        if total_lines > 0:
            picked_out = []
            for item in picked_items:
                picked_out.append({
                    'item_code': item['item_code'],
                    'item_name': item['item_name'],
                    'location': item.get('location') or '',
                    'unit_type': item.get('unit_type') or '',
                    'pack': item.get('pack') or '',
                    'qty': item['qty'],
                    'picked_qty': item['picked_qty'],
                    'source': 'batch',
                })
            problems_out = []
            for item in exception_items:
                problems_out.append({
                    'item_code': item['item_code'],
                    'item_name': item['item_name'],
                    'location': item.get('location') or '',
                    'unit_type': item.get('unit_type') or '',
                    'pack': item.get('pack') or '',
                    'qty': item['qty'],
                    'picked_qty': item['picked_qty'],
                    'status': item.get('pick_status') or 'exception',
                    'reason': item.get('reason') or '',
                })
            _entry = stop_seq_lookup.get(invoice.invoice_no) or {}
            invoices.append({
                'invoice': invoice,
                'routing_label': _routing_label_for_invoice(invoice, stop_seq_lookup),
                'stop_seq': _entry.get('seq'),
                'route_name': _entry.get('route_name') or '',
                'picked': picked_out,
                'problems': problems_out,
                'total_lines': total_lines,
                'total_units': total_units,
                'total_weight': total_weight,
                'completion_pct': completion_percentage,
            })

    return render_template('batch_report.html',
                           batch=batch_session,
                           generated_at=current_time,
                           picker_name=batch_session.assigned_to or 'Unassigned',
                           invoices=invoices)


@batch_bp.route('/route-batch/<int:route_id>/lock', methods=['POST'])
@login_required
@require_permission('picking.manage_batches')
def lock_route_batch(route_id):
    """Lock a route batch so it becomes assignable to a picker.

    Stamps ``sequence_locked_at`` / ``sequence_locked_by`` on the active
    ROUTE-BATCH session for *route_id*.  After locking, late-joining
    invoices will spawn a sibling session (ROUTE-BATCH-<id>-2, etc.).
    """
    from timezone_utils import get_utc_now

    session = BatchPickingSession.query.filter(
        BatchPickingSession.route_id == route_id,
        BatchPickingSession.session_type == 'route_batch',
        BatchPickingSession.status.notin_(['Completed', 'Cancelled', 'Archived']),
        BatchPickingSession.sequence_locked_at.is_(None),
    ).order_by(BatchPickingSession.id.desc()).first()

    if not session:
        flash("No unlocked route batch found for this route.", "warning")
        return redirect(request.referrer or url_for('admin_dashboard'))

    session.sequence_locked_at = get_utc_now()
    session.sequence_locked_by = current_user.username

    try:
        db.session.commit()
        flash(
            f"Route batch {session.name} locked. "
            "Assign it to a picker from Manage Batches.",
            "success",
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.error("lock_route_batch failed: %s", e)
        flash("Failed to lock route batch. Please try again.", "danger")

    return redirect(request.referrer or url_for('admin_dashboard'))