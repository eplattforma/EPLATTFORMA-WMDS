"""
Warehouse Intake Service
Helper functions for post-delivery case management and invoice remainder calculation
"""

from decimal import Decimal
from app import db
from models import (
    Invoice, InvoiceItem, DeliveryLine, InvoicePostDeliveryCase, 
    InvoiceRouteHistory, RerouteRequest
)
from flask_login import current_user
from datetime import datetime
from sqlalchemy import text


def compute_invoice_remainder(invoice_no: str):
    """
    Compute if invoice is fully satisfied by comparing ordered vs delivered quantities.
    
    Returns:
        dict: {
            'invoice_no': str,
            'status': str,
            'has_remainder': bool,
            'items': list of item details,
            'summary': {
                'ordered': Decimal,
                'delivered': Decimal,
                'remainder': Decimal
            }
        }
    """
    # Get invoice details
    invoice = Invoice.query.get(invoice_no)
    if not invoice:
        return {
            "invoice_no": invoice_no,
            "status": "NOT_FOUND",
            "has_remainder": False,
            "items": [],
            "summary": {
                "ordered": 0,
                "delivered": 0,
                "remainder": 0
            }
        }
    
    # Get item-level details
    items_result = db.session.execute(text("""
        SELECT 
            ii.item_code,
            ii.item_name,
            ii.qty AS ordered,
            COALESCE(SUM(dl.qty_delivered), 0)::numeric AS delivered
        FROM invoice_items ii
        LEFT JOIN delivery_lines dl ON ii.invoice_no = dl.invoice_no 
            AND ii.item_code = dl.item_code
        WHERE ii.invoice_no = :invoice_no
        GROUP BY ii.item_code, ii.item_name, ii.qty
        ORDER BY ii.item_code
    """), {"invoice_no": invoice_no}).fetchall()
    
    items = []
    total_ordered = Decimal("0")
    total_delivered = Decimal("0")
    
    for row in items_result:
        ordered = Decimal(str(row.ordered))
        delivered = Decimal(str(row.delivered))
        remainder = ordered - delivered
        
        items.append({
            "item_code": row.item_code,
            "item_name": row.item_name or "",
            "ordered": float(ordered),
            "delivered": float(delivered),
            "remainder": float(remainder)
        })
        
        total_ordered += ordered
        total_delivered += delivered
    
    total_remainder = total_ordered - total_delivered
    if total_remainder < 0:
        total_remainder = Decimal("0")
    
    return {
        "invoice_no": invoice_no,
        "status": invoice.status if invoice else "UNKNOWN",
        "has_remainder": total_remainder > 0,
        "items": items,
        "summary": {
            "ordered": float(total_ordered),
            "delivered": float(total_delivered),
            "remainder": float(total_remainder)
        }
    }


def open_post_delivery_case_if_needed(invoice_no: str, route_id: int, route_stop_id: int, reason: str, notes: str = None):
    """
    Create or update a post-delivery case for warehouse intake if invoice has remainder.
    This is idempotent - only one OPEN/INTAKE/REROUTE case per invoice.
    
    Returns:
        case_id or None if no remainder exists
    """
    # Check if invoice has remainder
    remainder_info = compute_invoice_remainder(invoice_no)
    
    if not remainder_info["has_remainder"]:
        # Fully satisfied; nothing to open
        return None
    
    actor = current_user.username if current_user and current_user.is_authenticated else 'system'
    
    # Idempotent upsert: only one OPEN/INTAKE/REROUTE per invoice
    result = db.session.execute(text("""
        INSERT INTO invoice_post_delivery_cases 
            (invoice_no, route_id, route_stop_id, status, reason, notes, created_by)
        VALUES 
            (:invoice_no, :route_id, :route_stop_id, 'OPEN', :reason, :notes, :actor)
        ON CONFLICT (invoice_no) 
        WHERE status IN ('OPEN','INTAKE_RECEIVED','REROUTE_QUEUED')
        DO UPDATE SET 
            reason = EXCLUDED.reason,
            notes = EXCLUDED.notes,
            updated_at = NOW()
        RETURNING id
    """), {
        "invoice_no": invoice_no,
        "route_id": route_id,
        "route_stop_id": route_stop_id,
        "reason": reason,
        "notes": notes,
        "actor": actor
    })
    
    case_id = result.scalar_one()
    
    # Log history: SENT_TO_WAREHOUSE
    db.session.execute(text("""
        INSERT INTO invoice_route_history 
            (invoice_no, route_id, route_stop_id, action, reason, notes, actor_username)
        VALUES 
            (:invoice_no, :route_id, :route_stop_id, 'SENT_TO_WAREHOUSE', :reason, :notes, :actor)
    """), {
        "invoice_no": invoice_no,
        "route_id": route_id,
        "route_stop_id": route_stop_id,
        "reason": reason,
        "notes": notes or "",
        "actor": actor
    })
    
    db.session.commit()
    return case_id


def log_invoice_history(invoice_no: str, action: str, route_id: int = None, route_stop_id: int = None, 
                        reason: str = None, notes: str = None):
    """
    Log an immutable history entry for invoice routing movement.
    
    Actions: PARTIAL_DELIVERED, FAILED, SENT_TO_WAREHOUSE, INTAKE_RECEIVED, 
             REROUTE_QUEUED, REROUTED, RETURN_TO_STOCK, CLOSED
    """
    actor = current_user.username if current_user and current_user.is_authenticated else 'system'
    
    history = InvoiceRouteHistory(
        invoice_no=invoice_no,
        route_id=route_id,
        route_stop_id=route_stop_id,
        action=action,
        reason=reason,
        notes=notes,
        actor_username=actor
    )
    
    db.session.add(history)
    db.session.commit()
    return history


def mark_intake_received(case_id: int, notes: str = None):
    """Mark a post-delivery case as received at warehouse"""
    case = InvoicePostDeliveryCase.query.get_or_404(case_id)
    
    if case.status not in ('OPEN',):
        raise ValueError("Case is not open for intake")
    
    actor = current_user.username if current_user and current_user.is_authenticated else 'system'
    
    # Update case status
    case.status = 'INTAKE_RECEIVED'
    if notes:
        case.notes = (case.notes or "") + "\n" + notes if case.notes else notes
    case.updated_at = datetime.utcnow()
    
    # Log history
    log_invoice_history(
        invoice_no=case.invoice_no,
        action='INTAKE_RECEIVED',
        route_id=case.route_id,
        route_stop_id=case.route_stop_id,
        notes=f"Warehouse intake received. {notes or ''}"
    )
    
    db.session.commit()
    return case


def queue_for_reroute(case_id: int, notes: str = None):
    """Queue invoice for reroute (will be dispatched again later)"""
    case = InvoicePostDeliveryCase.query.get_or_404(case_id)
    
    if case.status not in ('OPEN', 'INTAKE_RECEIVED'):
        raise ValueError("Case not in reroutable state")
    
    actor = current_user.username if current_user and current_user.is_authenticated else 'system'
    
    # Get invoice and set status to ready_for_dispatch
    invoice = Invoice.query.get(case.invoice_no)
    if invoice:
        invoice.status = 'ready_for_dispatch'
    
    # Create reroute request
    reroute_request = RerouteRequest(
        invoice_no=case.invoice_no,
        requested_by=actor,
        status='OPEN',
        notes=notes
    )
    db.session.add(reroute_request)
    db.session.flush()
    
    # Update case
    case.status = 'REROUTE_QUEUED'
    case.updated_at = datetime.utcnow()
    
    # Log history
    log_invoice_history(
        invoice_no=case.invoice_no,
        action='REROUTE_QUEUED',
        route_id=case.route_id,
        route_stop_id=case.route_stop_id,
        notes=f"Reroute request #{reroute_request.id}. {notes or ''}"
    )
    
    db.session.commit()
    return reroute_request


def return_to_stock(case_id: int, notes: str = None):
    """Return remainder to stock and close the logistics obligation"""
    case = InvoicePostDeliveryCase.query.get_or_404(case_id)
    
    if case.status not in ('OPEN', 'INTAKE_RECEIVED'):
        raise ValueError("Case not in returnable state")
    
    # Get invoice and update status
    invoice = Invoice.query.get(case.invoice_no)
    if invoice:
        invoice.status = 'returned_to_warehouse'
    
    # Update case
    case.status = 'RETURN_TO_STOCK'
    case.updated_at = datetime.utcnow()
    
    # Log history: RETURN_TO_STOCK
    log_invoice_history(
        invoice_no=case.invoice_no,
        action='RETURN_TO_STOCK',
        route_id=case.route_id,
        route_stop_id=case.route_stop_id,
        notes=notes or "Remainder returned to stock"
    )
    
    # Close the case
    log_invoice_history(
        invoice_no=case.invoice_no,
        action='CLOSED',
        route_id=case.route_id,
        route_stop_id=case.route_stop_id,
        notes="Case closed after return to stock"
    )
    
    case.status = 'CLOSED'
    db.session.commit()
    
    return case
