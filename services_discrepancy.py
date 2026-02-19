"""
Discrepancy-driven settlement service.
Handles behavior mapping for discrepancy types and manages post-delivery cases.
"""

from decimal import Decimal
from app import db
from models import DiscrepancyType, DeliveryDiscrepancy, InvoicePostDeliveryCase, InvoiceItem
from timezone_utils import get_utc_now


def get_discrepancy_type_behavior(discrepancy_type_name):
    """
    Get behavior configuration for a discrepancy type.
    
    Returns dict with:
    - deducts_from_collection: bool - whether amount should be deducted from driver collection
    - cn_required: bool - whether credit note is required
    - return_expected: bool - whether physical return is expected
    - requires_actual_item: bool - whether actual_item fields are required
    """
    dtype = DiscrepancyType.query.filter_by(name=discrepancy_type_name).first()
    
    if dtype:
        return {
            'deducts_from_collection': dtype.deducts_from_collection,
            'cn_required': dtype.cn_required,
            'return_expected': dtype.return_expected,
            'requires_actual_item': dtype.requires_actual_item
        }
    
    # Default behavior (conservative - deducts and requires CN)
    return {
        'deducts_from_collection': True,
        'cn_required': True,
        'return_expected': False,
        'requires_actual_item': False
    }


def calculate_deduct_amount(invoice_no, item_code, qty_affected):
    """
    Calculate the deduction amount for a discrepancy based on invoice line value.
    
    Args:
        invoice_no: Invoice number
        item_code: Item code
        qty_affected: Quantity affected by the discrepancy
        
    Returns:
        Decimal amount to deduct (unit_price * qty_affected)
    """
    from sqlalchemy import text
    
    # Try to get exact unit price from dw_invoice_line
    result = db.session.execute(text("""
        SELECT price_incl
        FROM dw_invoice_line
        WHERE invoice_no_365 = :invoice_no
          AND item_code_365 = :item_code
        LIMIT 1
    """), {'invoice_no': invoice_no, 'item_code': item_code}).fetchone()
    
    if result and result.price_incl:
        return Decimal(str(result.price_incl)) * Decimal(str(qty_affected))
    
    # Try to get unit price from PS365 invoice data
    result = db.session.execute(text("""
        SELECT 
            i.total_grand,
            COALESCE((SELECT SUM(ii2.qty) FROM invoice_items ii2 WHERE ii2.invoice_no = i.invoice_no), 0) as total_qty,
            COALESCE((SELECT COUNT(*) FROM invoice_items ii2 WHERE ii2.invoice_no = i.invoice_no), 0) as total_lines
        FROM invoices i
        WHERE i.invoice_no = :invoice_no
    """), {'invoice_no': invoice_no}).fetchone()
    
    if not result or not result.total_grand:
        return Decimal('0')
    
    # Calculate approximate unit value
    # For simplicity, use average value per unit across the invoice
    total_qty = result.total_qty or 1
    avg_unit_price = Decimal(str(result.total_grand)) / Decimal(str(total_qty))
    
    return avg_unit_price * Decimal(str(qty_affected))


def create_or_update_post_delivery_case(invoice_no, route_id=None, route_stop_id=None, 
                                         created_by=None, reason=None):
    """
    Create or update a post-delivery case for an invoice.
    Called when a discrepancy is created.
    
    Args:
        invoice_no: Invoice number
        route_id: Route/shipment ID
        route_stop_id: Route stop ID
        created_by: Username who created the case
        reason: Reason for the case
        
    Returns:
        InvoicePostDeliveryCase instance
    """
    # Check if case already exists for this invoice
    case = InvoicePostDeliveryCase.query.filter_by(invoice_no=invoice_no).first()
    
    if not case:
        case = InvoicePostDeliveryCase(
            invoice_no=invoice_no,
            route_id=route_id,
            route_stop_id=route_stop_id,
            status='OPEN',
            reason=reason,
            created_by=created_by
        )
        db.session.add(case)
    
    # Recalculate CN required and expected amount based on all discrepancies
    update_case_cn_requirements(case)
    
    return case


def update_case_cn_requirements(case):
    """
    Update credit note requirements on a post-delivery case based on its discrepancies.
    
    Calculates:
    - credit_note_required: True if any discrepancy type requires CN
    - credit_note_expected_amount: Sum of deduct_amounts where CN is required
    """
    from sqlalchemy import text
    
    # Get all discrepancies for this invoice with their type behaviors
    result = db.session.execute(text("""
        SELECT 
            d.deduct_amount,
            dt.cn_required
        FROM delivery_discrepancies d
        LEFT JOIN discrepancy_types dt ON dt.name = d.discrepancy_type
        WHERE d.invoice_no = :invoice_no
          AND d.is_resolved = false
    """), {'invoice_no': case.invoice_no}).fetchall()
    
    cn_required = False
    cn_expected_amount = Decimal('0')
    
    for row in result:
        type_cn_required = row.cn_required if row.cn_required is not None else True
        if type_cn_required:
            cn_required = True
            cn_expected_amount += Decimal(str(row.deduct_amount or 0))
    
    case.credit_note_required = cn_required
    case.credit_note_expected_amount = cn_expected_amount


def check_and_close_case_if_complete(invoice_no):
    """
    Checks if all blocking conditions for a post-delivery case are met 
    (discrepancies verified and returns received) and closes the case.
    """
    case = InvoicePostDeliveryCase.query.filter_by(invoice_no=invoice_no).first()
    if not case or case.status in ('CLOSED', 'CANCELLED'):
        return

    # Check for unverified discrepancies
    from models import DeliveryDiscrepancy
    unverified_discrepancy = DeliveryDiscrepancy.query.filter_by(
        invoice_no=invoice_no,
        warehouse_checked_at=None
    ).first()

    if unverified_discrepancy:
        return

    # Check for pending returns
    from models import RouteReturnHandover
    pending_return = RouteReturnHandover.query.filter(
        RouteReturnHandover.invoice_no == invoice_no,
        RouteReturnHandover.driver_confirmed_at.isnot(None),
        RouteReturnHandover.warehouse_received_at.is_(None)
    ).first()

    if pending_return:
        return

    # If we got here, everything is verified/received
    case.status = 'CLOSED'
    case.updated_at = get_utc_now()
    db.session.commit()


def process_discrepancy_for_settlement(discrepancy):
    """
    Process a discrepancy for settlement purposes.
    Called after a discrepancy is created/updated.
    
    Sets:
    - deduct_amount based ONLY on what driver provided (no fallbacks)
    - credit_note_required based on type
    - Creates/updates post-delivery case
    
    Args:
        discrepancy: DeliveryDiscrepancy instance
    """
    behavior = get_discrepancy_type_behavior(discrepancy.discrepancy_type)
    
    # Use reported_value if provided by driver, otherwise 0
    # No fallback calculations allowed
    if discrepancy.reported_value:
        discrepancy.deduct_amount = Decimal(str(discrepancy.reported_value))
    else:
        discrepancy.deduct_amount = Decimal('0')
    
    # Set CN required flag on discrepancy
    discrepancy.credit_note_required = behavior['cn_required']


def get_invoice_settlement_summary(invoice_no):
    """
    Get settlement summary for an invoice including discrepancy deductions.
    
    Returns:
        dict with:
        - original_amount: Original invoice total
        - total_deductions: Sum of discrepancy deduct_amounts
        - amount_due: original_amount - total_deductions
        - discrepancy_count: Number of open discrepancies
        - cn_required: Whether credit note is required
        - cn_expected_amount: Expected credit note amount
    """
    from sqlalchemy import text
    from models import Invoice
    
    invoice = Invoice.query.get(invoice_no)
    if not invoice:
        return None
    
    original_amount = Decimal(str(invoice.total_grand or 0))
    
    # Get discrepancy summary
    result = db.session.execute(text("""
        SELECT 
            COUNT(*) as discrepancy_count,
            COALESCE(SUM(deduct_amount), 0) as total_deductions,
            BOOL_OR(credit_note_required) as cn_required
        FROM delivery_discrepancies
        WHERE invoice_no = :invoice_no
          AND is_resolved = false
    """), {'invoice_no': invoice_no}).fetchone()
    
    total_deductions = Decimal(str(result.total_deductions or 0))
    
    # Get CN expected amount from case
    case = InvoicePostDeliveryCase.query.filter_by(invoice_no=invoice_no).first()
    cn_expected_amount = Decimal(str(case.credit_note_expected_amount or 0)) if case else Decimal('0')
    
    return {
        'original_amount': original_amount,
        'total_deductions': total_deductions,
        'amount_due': original_amount - total_deductions,
        'discrepancy_count': result.discrepancy_count or 0,
        'cn_required': result.cn_required or False,
        'cn_expected_amount': cn_expected_amount
    }


def get_route_settlement_summary(route_id):
    """
    Get settlement summary for an entire route.
    
    Returns:
        dict with:
        - total_expected: Sum of invoice totals
        - total_deductions: Sum of all discrepancy deduct_amounts
        - total_due: total_expected - total_deductions
        - invoices: List of invoice summaries
        - payment_breakdown: Breakdown by payment method
    """
    from sqlalchemy import text
    
    # Get all delivered invoices on the route
    result = db.session.execute(text("""
        SELECT 
            i.invoice_no,
            i.customer_name,
            i.total_grand as original_amount,
            COALESCE(d.total_deductions, 0) as deductions,
            COALESCE(d.discrepancy_count, 0) as discrepancy_count,
            rsi.status as delivery_status,
            pt.payment_type_code_365 as expected_payment_group
        FROM invoices i
        JOIN route_stop_invoice rsi ON rsi.invoice_no = i.invoice_no AND rsi.is_active = true
        JOIN route_stop rs ON rs.route_stop_id = rsi.route_stop_id
        LEFT JOIN ps_customers pc ON pc.customer_code_365 = i.customer_code_365
        LEFT JOIN payment_customers pt ON pt.customer_code_365 = i.customer_code_365
        LEFT JOIN (
            SELECT 
                invoice_no,
                SUM(deduct_amount) as total_deductions,
                COUNT(*) as discrepancy_count
            FROM delivery_discrepancies
            WHERE is_resolved = false
            GROUP BY invoice_no
        ) d ON d.invoice_no = i.invoice_no
        WHERE rs.shipment_id = :route_id
          AND rs.deleted_at IS NULL
        ORDER BY rs.seq_no
    """), {'route_id': route_id}).fetchall()
    
    invoices = []
    total_expected = Decimal('0')
    total_deductions = Decimal('0')
    
    for row in result:
        original = Decimal(str(row.original_amount or 0))
        deductions = Decimal(str(row.deductions or 0))
        
        invoices.append({
            'invoice_no': row.invoice_no,
            'customer_name': row.customer_name,
            'original_amount': original,
            'deductions': deductions,
            'amount_due': original - deductions,
            'discrepancy_count': row.discrepancy_count,
            'delivery_status': row.delivery_status,
            'payment_group': 'CREDIT' if row.expected_payment_group == 'CREDIT' else 'POD'
        })
        
        if row.delivery_status == 'DELIVERED':
            total_expected += original
            total_deductions += deductions
    
    # Get payment breakdown from invoice allocations for more accuracy
    payment_breakdown = db.session.execute(text("""
        SELECT 
            payment_method,
            SUM(received_amount) as total_received
        FROM cod_invoice_allocations
        WHERE route_id = :route_id
        GROUP BY payment_method
    """), {'route_id': route_id}).fetchall()
    
    breakdown = {}
    for row in payment_breakdown:
        breakdown[row.payment_method] = float(row.total_received or 0)
    
    return {
        'total_expected': total_expected,
        'total_deductions': total_deductions,
        'total_due': total_expected - total_deductions,
        'invoices': invoices,
        'payment_breakdown': breakdown
    }
