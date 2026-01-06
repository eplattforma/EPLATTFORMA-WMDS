"""
Automated routing service for delivery route creation
Groups invoices by customer and auto-creates stops with sequence numbers
"""
from datetime import datetime
from sqlalchemy import func
from models import RouteStop, RouteStopInvoice, PSCustomer, Shipment, Invoice
from app import db
import logging

logger = logging.getLogger(__name__)


def get_customer_address_snapshot(customer_code: str):
    """
    Get address information from ps_customers for a customer code.
    Returns dict with address components or None values if not found.
    """
    customer = PSCustomer.query.filter_by(customer_code_365=customer_code).first()
    
    if not customer:
        return {"address": None, "town": None}
    
    # Build full address from available components
    address_parts = [
        customer.address_line_1,
        customer.address_line_2,
        customer.address_line_3
    ]
    full_address = " ".join(part for part in address_parts if part and part.strip())
    
    return {
        "address": full_address or None,
        "town": customer.town,
        "company_name": customer.company_name
    }


def get_max_stop_sequence(shipment_id: int):
    """Get the maximum stop sequence number for a route"""
    max_seq = db.session.query(func.max(RouteStop.seq_no))\
        .filter(RouteStop.shipment_id == shipment_id)\
        .scalar()
    
    return max_seq or 0


def match_customer_code_for_invoice(invoice):
    """
    Try to find customer_code for an invoice.
    First checks if invoice already has customer_code.
    Otherwise, attempts to match by customer_name against ps_customers.
    """
    if invoice.customer_code:
        return invoice.customer_code
    
    if not invoice.customer_name:
        return None
    
    # Try to match by company name
    customer = PSCustomer.query.filter_by(company_name=invoice.customer_name).first()
    
    if customer:
        # Update the invoice with the found customer_code
        invoice.customer_code = customer.customer_code_365
        db.session.commit()
        return customer.customer_code_365
    
    # Try matching by combining last_name and first_name (less common but possible)
    # This is a fallback for individual customers
    customers = PSCustomer.query.filter(
        PSCustomer.company_name.is_(None) | (PSCustomer.company_name == '')
    ).all()
    
    for cust in customers:
        full_name = f"{cust.last_name} {cust.first_name}".strip()
        if full_name and full_name == invoice.customer_name:
            invoice.customer_code = cust.customer_code_365
            db.session.commit()
            return cust.customer_code_365
    
    return None


def assign_invoices_to_route_grouped_by_customer(shipment_id: int, invoice_nos: list):
    """
    Main function: Assign invoices to a route with automatic customer grouping.
    
    Process:
    1. Load invoices by invoice_no
    2. Ensure each invoice has a customer_code (match if missing)
    3. Group invoices by customer_code
    4. For each customer group:
       - Find existing stop for that customer in this route, OR
       - Create new stop with auto-incremented sequence number
       - Attach all customer's invoices to that stop
    5. Update invoice records with route_id and stop_id
    
    Returns: dict with success status and details
    """
    # Load invoices
    invoices = Invoice.query.filter(Invoice.invoice_no.in_(invoice_nos)).all()
    
    if not invoices:
        return {"ok": False, "message": "No invoices found"}
    
    # Ensure all invoices have customer_code
    missing_code_invoices = []
    for inv in invoices:
        if not inv.customer_code:
            # Try to match and assign customer_code
            code = match_customer_code_for_invoice(inv)
            if not code:
                missing_code_invoices.append(inv.invoice_no)
    
    if missing_code_invoices:
        return {
            "ok": False,
            "message": f"Cannot determine customer for invoices: {', '.join(missing_code_invoices)}. Please ensure customer data is synced."
        }
    
    # Group invoices by customer_code
    customer_groups = {}
    for inv in invoices:
        code = inv.customer_code
        if code not in customer_groups:
            customer_groups[code] = []
        customer_groups[code].append(inv)
    
    # Get current max sequence for this route
    current_max_seq = get_max_stop_sequence(shipment_id)
    next_seq = current_max_seq
    
    created_stops = []
    updated_stops = []
    potentially_empty_stops = set()  # Track stops that may have become empty
    
    for customer_code, customer_invoices in customer_groups.items():
        # Check if stop already exists for this customer in this route
        existing_stop = RouteStop.query.filter_by(
            shipment_id=shipment_id,
            customer_code=customer_code
        ).first()
        
        if existing_stop:
            # Stop exists, just attach more invoices to it
            stop = existing_stop
            updated_stops.append(stop)
        else:
            # Create new stop for this customer
            next_seq += 1
            address_info = get_customer_address_snapshot(customer_code)
            
            stop = RouteStop(
                shipment_id=shipment_id,
                customer_code=customer_code,
                seq_no=next_seq,
                stop_name=address_info.get('company_name') or f"Customer {customer_code}",
                stop_addr=address_info.get('address'),
                stop_city=address_info.get('town')
            )
            db.session.add(stop)
            db.session.flush()  # Get the stop ID
            created_stops.append(stop)
        
        # Attach all invoices for this customer to the stop
        for inv in customer_invoices:
            # Check if invoice is already on ANY route (not just this stop)
            existing_link = RouteStopInvoice.query.filter_by(
                invoice_no=inv.invoice_no
            ).first()
            
            if existing_link:
                # Track the old stop that may become empty
                potentially_empty_stops.add(existing_link.route_stop_id)
                # Invoice is already on another route - remove old assignment first
                db.session.delete(existing_link)
                db.session.flush()
            
            # Update invoice with route and stop references
            inv.route_id = shipment_id
            inv.stop_id = stop.route_stop_id
            
            # Create new RouteStopInvoice link
            link = RouteStopInvoice(
                route_stop_id=stop.route_stop_id,
                invoice_no=inv.invoice_no,
                status="ASSIGNED"
            )
            db.session.add(link)
            
            # Close any open warehouse intake cases for this invoice
            from models import InvoicePostDeliveryCase, RerouteRequest, InvoiceRouteHistory
            open_case = InvoicePostDeliveryCase.query.filter_by(
                invoice_no=inv.invoice_no
            ).filter(
                InvoicePostDeliveryCase.status.in_(['OPEN', 'INTAKE_RECEIVED', 'REROUTE_QUEUED'])
            ).first()
            
            if open_case:
                # Close the warehouse intake case
                open_case.status = 'CLOSED'
                open_case.updated_at = datetime.utcnow()
                
                # Mark reroute request as completed if exists
                reroute_req = RerouteRequest.query.filter_by(
                    invoice_no=inv.invoice_no,
                    status='OPEN'
                ).first()
                if reroute_req:
                    reroute_req.status = 'COMPLETED'
                    reroute_req.assigned_to_route_id = shipment_id
                    reroute_req.completed_at = datetime.utcnow()
                
                # Log to invoice history
                history_entry = InvoiceRouteHistory(
                    invoice_no=inv.invoice_no,
                    route_id=shipment_id,
                    route_stop_id=stop.route_stop_id,
                    action='REROUTED',
                    reason='Assigned to new route',
                    notes=f'Invoice assigned to route {shipment_id}, warehouse intake case closed',
                    actor_username='system'
                )
                db.session.add(history_entry)
    
    db.session.commit()
    
    # Clean up any stops that became empty after moving invoices
    from services import delete_stop
    for stop_id in potentially_empty_stops:
        remaining_invoices = RouteStopInvoice.query.filter_by(route_stop_id=stop_id).count()
        if remaining_invoices == 0:
            delete_stop(stop_id)
    
    return {
        "ok": True,
        "created_stops": len(created_stops),
        "updated_stops": len(updated_stops),
        "total_stops": len(created_stops) + len(updated_stops),
        "total_invoices": len(invoices),
        "details": {
            "created": [
                {
                    "stop_id": s.route_stop_id,
                    "seq_no": s.seq_no,
                    "customer_code": s.customer_code,
                    "stop_name": s.stop_name
                } for s in created_stops
            ],
            "updated": [
                {
                    "stop_id": s.route_stop_id,
                    "seq_no": s.seq_no,
                    "customer_code": s.customer_code,
                    "stop_name": s.stop_name
                } for s in updated_stops
            ]
        }
    }
