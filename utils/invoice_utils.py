import logging

def recalculate_invoice_totals(session, invoice_no):
    """
    Recalculates and updates the invoice totals based on its items.
    
    Args:
        session: SQLAlchemy session
        invoice_no: Invoice number to update
    """
    # Import here to avoid circular imports
    from models import Invoice, InvoiceItem
    
    items = InvoiceItem.query.filter_by(invoice_no=invoice_no).all()
    invoice = Invoice.query.get(invoice_no)
    
    if not invoice:
        logging.warning(f"Cannot recalculate totals: Invoice {invoice_no} not found")
        return
    
    # Calculate totals
    total_lines = len(items)
    
    # Handle potentially problematic values safely
    total_items = 0
    for item in items:
        try:
            if item.qty is not None:
                total_items += int(item.qty)
        except (ValueError, TypeError) as e:
            logging.warning(f"Error adding qty for {invoice_no}/{item.item_code}: {str(e)}")
    
    total_weight = 0
    for item in items:
        try:
            if item.line_weight is not None:
                total_weight += float(item.line_weight)
        except (ValueError, TypeError) as e:
            logging.warning(f"Error adding line_weight for {invoice_no}/{item.item_code}: {str(e)}")
    
    total_exp_time = 0
    for item in items:
        try:
            if item.exp_time is not None:
                total_exp_time += float(item.exp_time)
        except (ValueError, TypeError) as e:
            logging.warning(f"Error adding exp_time for {invoice_no}/{item.item_code}: {str(e)}")
    
    # Update invoice
    invoice.total_lines = total_lines
    invoice.total_items = total_items
    invoice.total_weight = total_weight
    invoice.total_exp_time = total_exp_time
    
    try:
        session.commit()
        logging.info(f"Successfully updated totals for invoice {invoice_no}")
    except Exception as e:
        logging.error(f"Error updating invoice totals: {str(e)}")
        session.rollback()