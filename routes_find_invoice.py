"""
Flask blueprint for Find Invoice/Route functionality
Allows searching completed invoices with route and delivery details
"""
from flask import Blueprint, request, render_template, jsonify
from flask_login import login_required, current_user
from functools import wraps
from datetime import datetime, timedelta
from sqlalchemy import or_, and_, desc, asc
from models import Invoice, InvoiceItem, Shipment
from app import db

bp = Blueprint("find_invoice", __name__)

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role not in ['admin', 'warehouse_manager']:
            from flask import abort
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


@bp.route("/find-invoice-route")
@login_required
@admin_required
def find_invoice_route():
    """Display find invoice/route search page"""
    # Get available routes for dropdown
    routes = db.session.query(Shipment.id, Shipment.route_name, Shipment.driver_name, Shipment.delivery_date).filter(
        Shipment.status.in_(['PLANNED', 'DISPATCHED', 'IN_TRANSIT', 'COMPLETED'])
    ).order_by(Shipment.delivery_date.desc()).limit(100).all()
    
    return render_template("find_invoice_route.html", routes=routes)


@bp.route("/api/search-completed", methods=["POST"])
@login_required
@admin_required
def search_completed():
    """
    Search completed invoices with filters
    
    Expected JSON body:
    {
        "q": "search text",
        "completed_from": "YYYY-MM-DD",
        "completed_to": "YYYY-MM-DD",
        "delivered_from": "YYYY-MM-DD",
        "delivered_to": "YYYY-MM-DD",
        "customer_code": "C001",
        "route_id": 123,
        "driver": "driver_name",
        "status": "delivered",
        "preset": "today|yesterday|last7|this_month|last_month",
        "page": 1,
        "size": 50,
        "sort": "completed_desc|completed_asc|invoice_asc|invoice_desc"
    }
    """
    data = request.get_json(force=True) if request.is_json else {}
    
    # Extract filters
    q = data.get("q", "").strip()
    completed_from = data.get("completed_from")
    completed_to = data.get("completed_to")
    delivered_from = data.get("delivered_from")
    delivered_to = data.get("delivered_to")
    customer_code = data.get("customer_code", "").strip()
    route_id = data.get("route_id")
    driver = data.get("driver", "").strip()
    status_filter = data.get("status", "").strip()
    preset = data.get("preset", "").strip()
    page = int(data.get("page", 1))
    size = min(int(data.get("size", 50)), 200)  # Max 200 per page
    sort = data.get("sort", "completed_desc")
    
    # Build base query - only completed/delivered invoices
    query = db.session.query(Invoice).filter(
        Invoice.status.in_(['ready_for_dispatch', 'shipped', 'out_for_delivery', 'delivered', 'delivery_failed', 'returned_to_warehouse', 'cancelled'])
    )
    
    # Apply preset date filters
    if preset:
        today = datetime.now().date()
        if preset == "today":
            completed_from = today.isoformat()
            completed_to = today.isoformat()
        elif preset == "yesterday":
            yesterday = today - timedelta(days=1)
            completed_from = yesterday.isoformat()
            completed_to = yesterday.isoformat()
        elif preset == "last7":
            completed_from = (today - timedelta(days=7)).isoformat()
            completed_to = today.isoformat()
        elif preset == "this_month":
            completed_from = today.replace(day=1).isoformat()
            completed_to = today.isoformat()
        elif preset == "last_month":
            first_this_month = today.replace(day=1)
            last_month_end = first_this_month - timedelta(days=1)
            last_month_start = last_month_end.replace(day=1)
            completed_from = last_month_start.isoformat()
            completed_to = last_month_end.isoformat()
    
    # Text search across invoice_no, customer_name, customer_code, route number
    if q:
        search_filters = []
        search_filters.append(Invoice.invoice_no.ilike(f"%{q}%"))
        search_filters.append(Invoice.customer_name.ilike(f"%{q}%"))
        search_filters.append(Invoice.customer_code.ilike(f"%{q}%"))
        # Search by route number (if q is numeric)
        try:
            route_num = int(q)
            search_filters.append(Invoice.route_id == route_num)
        except ValueError:
            pass
        query = query.filter(or_(*search_filters))
    
    # Date filters for completion
    if completed_from:
        try:
            from_date = datetime.strptime(completed_from, "%Y-%m-%d")
            query = query.filter(Invoice.picking_complete_time >= from_date)
        except ValueError:
            pass
    
    if completed_to:
        try:
            to_date = datetime.strptime(completed_to, "%Y-%m-%d")
            to_date = to_date.replace(hour=23, minute=59, second=59)
            query = query.filter(Invoice.picking_complete_time <= to_date)
        except ValueError:
            pass
    
    # Date filters for delivery
    if delivered_from:
        try:
            from_date = datetime.strptime(delivered_from, "%Y-%m-%d")
            query = query.filter(Invoice.delivered_at >= from_date)
        except ValueError:
            pass
    
    if delivered_to:
        try:
            to_date = datetime.strptime(delivered_to, "%Y-%m-%d")
            to_date = to_date.replace(hour=23, minute=59, second=59)
            query = query.filter(Invoice.delivered_at <= to_date)
        except ValueError:
            pass
    
    # Customer filter
    if customer_code:
        query = query.filter(Invoice.customer_code == customer_code)
    
    # Route filter - search by route ID (number)
    if route_id:
        try:
            route_id_int = int(route_id)
            query = query.filter(Invoice.route_id == route_id_int)
        except ValueError:
            # If not a valid integer, ignore the filter
            pass
    
    # Driver filter - LEFT join with Shipment to include invoices without routes
    if driver:
        query = query.join(Shipment, Invoice.route_id == Shipment.id, isouter=True).filter(
            Shipment.driver_name.ilike(f"%{driver}%")
        )
    
    # Status filter
    if status_filter:
        query = query.filter(Invoice.status == status_filter)
    
    # Apply sorting
    if sort == "completed_desc":
        query = query.order_by(desc(Invoice.picking_complete_time))
    elif sort == "completed_asc":
        query = query.order_by(asc(Invoice.picking_complete_time))
    elif sort == "invoice_desc":
        query = query.order_by(desc(Invoice.invoice_no))
    elif sort == "invoice_asc":
        query = query.order_by(asc(Invoice.invoice_no))
    else:
        query = query.order_by(desc(Invoice.picking_complete_time))
    
    # Get total count before pagination
    total_count = query.count()
    
    # Apply pagination
    offset = (page - 1) * size
    invoices = query.offset(offset).limit(size).all()
    
    # Get route info for all invoices
    route_ids = [inv.route_id for inv in invoices if inv.route_id]
    routes = {}
    if route_ids:
        route_objs = Shipment.query.filter(Shipment.id.in_(route_ids)).all()
        routes = {r.id: r for r in route_objs}
    
    # Build result items
    items = []
    total_items_count = 0
    total_weight = 0
    
    for inv in invoices:
        route = routes.get(inv.route_id) if inv.route_id else None
        
        items.append({
            "invoice_no": inv.invoice_no,
            "completed_at": inv.picking_complete_time.isoformat() if inv.picking_complete_time else None,
            "delivered_at": inv.delivered_at.isoformat() if inv.delivered_at else None,
            "customer_code": inv.customer_code or "",
            "customer_name": inv.customer_name or "",
            "route_id": inv.route_id,
            "route_name": route.route_name if route else "",
            "driver_name": route.driver_name if route else "",
            "delivery_date": route.delivery_date.isoformat() if route and route.delivery_date else None,
            "picker_username": inv.assigned_to or "",
            "status": inv.status,
            "total_lines": inv.total_lines or 0,
            "total_items": inv.total_items or 0,
            "total_weight": round(inv.total_weight or 0, 2),
        })
        
        total_items_count += inv.total_items or 0
        total_weight += inv.total_weight or 0
    
    # Calculate totals
    totals = {
        "total_invoices": total_count,
        "total_items": round(total_items_count, 2),
        "total_weight": round(total_weight, 2)
    }
    
    return jsonify({
        "ok": True,
        "page": page,
        "size": size,
        "count": total_count,
        "totals": totals,
        "items": items
    })


@bp.route("/api/invoice-detail/<invoice_no>")
@login_required
@admin_required
def invoice_detail(invoice_no):
    """Get detailed invoice information including line items and routing history"""
    invoice = Invoice.query.get_or_404(invoice_no)
    
    # Get route info
    route = None
    if invoice.route_id:
        route = Shipment.query.get(invoice.route_id)
    
    # Get line items
    items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
    
    # Get delivery discrepancies for this invoice
    from models import DeliveryDiscrepancy
    discrepancies = DeliveryDiscrepancy.query.filter_by(invoice_no=invoice_no).all()
    
    # Create a map of item_code to discrepancy info
    discrepancy_map = {}
    for disc in discrepancies:
        item_code = disc.item_code_expected
        if item_code not in discrepancy_map:
            discrepancy_map[item_code] = []
        discrepancy_map[item_code].append({
            'type': disc.discrepancy_type or '',
            'qty_affected': disc.qty_expected or 0,
            'note': disc.note or ''
        })
    
    line_items = []
    for item in items:
        line_items.append({
            "item_code": item.item_code,
            "item_name": item.item_name or "",
            "location": item.location or "",
            "qty": item.qty or 0,
            "picked_qty": item.picked_qty or 0,
            "unit_type": item.unit_type or "",
            "zone": item.zone or "",
            "corridor": item.corridor or "",
            "is_picked": item.is_picked,
            "pick_status": item.pick_status,
            "discrepancies": discrepancy_map.get(item.item_code, [])
        })
    
    # Get routing history
    from models import InvoiceRouteHistory, CODReceipt, PODRecord
    from sqlalchemy import cast, String
    
    routing_history_records = InvoiceRouteHistory.query.filter_by(
        invoice_no=invoice_no
    ).order_by(InvoiceRouteHistory.created_at.desc()).all()
    
    routing_history = []
    for record in routing_history_records:
        routing_history.append({
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "action": record.action or "",
            "route_id": record.route_id,
            "reason": record.reason or "",
            "notes": record.notes or "",
            "actor_username": record.actor_username or ""
        })
    
    # Get COD receipts (payment records) - invoice_nos is JSON array
    cod_receipts_records = CODReceipt.query.filter(
        cast(CODReceipt.invoice_nos, String).like(f'%"{invoice_no}"%')
    ).order_by(CODReceipt.created_at.desc()).all()
    
    cod_receipts = []
    for receipt in cod_receipts_records:
        cod_receipts.append({
            "created_at": receipt.created_at.isoformat() if receipt.created_at else None,
            "expected_amount": float(receipt.expected_amount) if receipt.expected_amount else 0,
            "received_amount": float(receipt.received_amount) if receipt.received_amount else 0,
            "variance": float(receipt.variance) if receipt.variance else 0,
            "payment_method": receipt.payment_method or "",
            "driver_username": receipt.driver_username or "",
            "ps365_receipt_id": receipt.ps365_receipt_id or "",
            "route_id": receipt.route_id
        })
    
    # Get POD records (proof of delivery) - invoice_nos is JSON array
    pod_records_list = PODRecord.query.filter(
        cast(PODRecord.invoice_nos, String).like(f'%"{invoice_no}"%')
    ).order_by(PODRecord.collected_at.desc()).all()
    
    pod_records = []
    for pod in pod_records_list:
        pod_records.append({
            "collected_at": pod.collected_at.isoformat() if pod.collected_at else None,
            "receiver_name": pod.receiver_name or "",
            "has_physical_signed_invoice": pod.has_physical_signed_invoice or False,
            "collected_by": pod.collected_by or "",
            "notes": pod.notes or "",
            "route_id": pod.route_id
        })
    
    result = {
        "invoice_no": invoice.invoice_no,
        "completed_at": invoice.picking_complete_time.isoformat() if invoice.picking_complete_time else None,
        "picker_username": invoice.assigned_to or "",
        "delivered_at": invoice.delivered_at.isoformat() if invoice.delivered_at else None,
        "shipped_at": invoice.shipped_at.isoformat() if invoice.shipped_at else None,
        "customer_code": invoice.customer_code or "",
        "customer_name": invoice.customer_name or "",
        "route_id": invoice.route_id,
        "route_name": route.route_name if route else "",
        "driver_name": route.driver_name if route else "",
        "delivery_date": route.delivery_date.isoformat() if route and route.delivery_date else None,
        "status": invoice.status,
        "total_lines": invoice.total_lines or 0,
        "total_items": invoice.total_items or 0,
        "total_weight": round(invoice.total_weight or 0, 2),
        "assigned_to": invoice.assigned_to or "",
        "items": line_items,
        "routing_history": routing_history,
        "cod_receipts": cod_receipts,
        "pod_records": pod_records
    }
    
    return jsonify(result)
